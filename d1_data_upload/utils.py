import json
import re
from pygeodesy.namedTuples import LatLon3Tuple
from logging import getLogger

from d1_client.mnclient_2_0 import MemberNodeClient_2_0
from d1_common.types import dataoneTypes
from d1_common import const

from pathlib import Path
from logging import getLogger
from datetime import datetime, timedelta

from .defs import GROUP_ID, CN_URL, CONFIG_LOC, CONFIG


def get_token():
    """
    Get the DataONE token from the token file.
    Paste your auth token into ``{CONFIG_LOC}/.d1_token``.

    :return: The DataONE token.
    :rtype: str
    """
    # Set the D1 token
    with open(Path(CONFIG_LOC / '.d1_token'), 'r') as tf:
        return tf.read().split('\n')[0]


def get_ll_token():
    """
    Get the long-lived DataONE token from the token file.
    Paste the long-lived token into ``{CONFIG_LOC}/.ll_token``.

    :return: The DataONE token.
    :rtype: str
    """
    # Set the D1 token
    with open(Path(CONFIG_LOC / '.ll_token'), 'r') as tf:
        return tf.read().split('\n')[0]


def get_config():
    """
    Config values not including the DataONE token are stored in
    ``{CONFIG_LOC}/config.json``.

    :return: The ORCID, node identifier, Member Node URL, and metadata JSON file.
    :rtype: tuple
    """
    global DATA_ROOT
    global CN_URL
    global CONFIG
    # Set your ORCID
    CONFIG_F = CONFIG_LOC.joinpath('config.json')
    with open(CONFIG_F, 'r') as lc:
        config = json.load(lc)
    CONFIG = config
    # set the data root and CN URL
    DATA_ROOT = Path(config['data_root'])
    CN_URL = config['cnurl'] if config.get('cnurl') else CN_URL
    config['metadata_json'] = str(Path(config['metadata_json']).expanduser().absolute())
    return config


def create_client(mn_url: str, auth_token: str):
    """
    Instantiate a DataONE Member Node client.

    :param str mn_url: The URL of the Member Node.
    :param str auth_token: The authentication token.
    :return: The Member Node client.
    :rtype: MemberNodeClient_2_0
    """
    options: dict = {
        "headers": {"Authorization": "Bearer " + auth_token},
        "timeout_sec": 9999,
        }
    return MemberNodeClient_2_0(mn_url, **options)


def parse_name(fullname: str):
    """
    Parse full names into given and family designations.

    This function parses full names into given and family names. It supports
    various formats of names, including those with multiple given names and
    family names.

    Supported formats:
    Multiple given names: ``John Jacob Jingleheimer Schmidt``
    Given name and family name: ``John Schmidt``
    Family name and given name: ``Schmidt, John``
    Given name and family name with prefix: ``John von Schmidt``

    :param fullname: The full name to be parsed.
    :type fullname: str
    :return: A tuple containing the given name and family name.
    :rtype: tuple[str, str]
    """
    given, family = None, None
    if ', ' in fullname:
        # split the fullname by comma and space, assign the family name and given name
        [family, given] = fullname.title().split(', ')[:2]
    if (given == None) and (family == None):
        for q in [' del ', ' van ', ' de ', ' von ', ' der ', ' di ', ' la ', ' le ', ' da ', ' el ', ' al ', ' bin ']:
            if q in fullname.lower():
                # split the fullname by the query string, assign the given name and family name
                [given, family] = fullname.lower().split(q)
                # capitalize the and concat the query string to the family name
                given = given.title()
                family = f'{q.strip()}{family.title()}'
    if (given == None) and (family == None):
        # split the fullname by space and capitalize each part
        nlist = fullname.title().split()
        # assign the last part as the family name and the first part as the given name
        family = nlist[-1]
        if len(nlist) >= 2:
            given = nlist[0]
            for i in range(1, len(nlist)-1):
                # concatenate the remaining parts as the given name
                given = f'{given} {nlist[i]}'
    if (not given) or (not family):
        L = getLogger()
        L.warning(f'Could not parse name "{fullname}". Result of given name: "{given}" Family name: "{family}"')
    return given, family


def fix_datetime(date: str):
    """
    This function converts the Figshare datetime string format to
    a format that DataONE will accept.

    :param date: The date string, formatted as ``%Y-%m-%dT%H:%M:%SZ``.
    :type date: str
    :return: The fixed date string in ``%Y-%m-%d`` format.
    :rtype: str
    """
    return datetime.strptime(date, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d')


def dms_to_decimal(degrees, minutes, seconds, direction):
    """
    Convert degrees, minutes, and seconds to decimal degrees.
    
    :param degrees: The degrees value.
    :type degrees: str
    :param minutes: The minutes value.
    :type minutes: str
    :param seconds: The seconds value.
    :type seconds: str
    :param direction: Cardinal direction (N, S, E, W).
    :type direction: str
    :return: Decimal degrees.
    :rtype: float
    """
    decimal = float(degrees) + float(minutes)/60 + float(seconds)/3600
    if direction in ['S', 'W']:
        decimal *= -1
    return decimal


def dm_to_decimal(degrees, minutes, direction):
    """
    Convert degrees and decimal minutes to decimal degrees.
    
    :param degrees: The degrees value.
    :type degrees: str
    :param minutes: The minutes value.
    :type minutes: str
    :param direction: Cardinal direction (N, S, E, W).
    :type direction: str
    :return: Decimal degrees.
    :rtype: float
    """
    dd = int(degrees) + float(minutes) / 60
    if direction in ['S', 'W']:
        dd = -dd
    return dd


def get_lat_lon(desc: str):
    """
    Parse latitude and longitude from description and convert to decimal degrees.

    This function extracts latitude and longitude pairs from a given description string.
    It supports various formats of location strings and converts them to decimal degrees.
    The function returns all pairs found as a list of LatLon3Tuple objects.

    Supported formats:
    
    1. Decimal degrees: `8.994410°, -79.543000°`
    2. Decimal degrees with direction: `8.910718°N, -79.528919°`
    3. Degrees and decimal minutes with direction: `7° 38.422'N, 81° 42.079'W`
    4. Degrees, minutes, and seconds with direction: `9°9'42.36"N, 79°50'15.67"W`
    5. Degrees and minutes with direction (special format): `0°41′ S latitude, 76°24′ W longitude`
    6. Degrees and decimal minutes with direction (alternative format): `8° 38.743'N    79° 2.887'W`
    7. Location prefix with decimal degrees: `Location: 7.69633 -81.61603`

    :param desc: The description string containing latitude and longitude information.
    :type desc: str
    :returns: A list of LatLon3Tuple objects representing the extracted latitude and longitude pairs, or None if no pairs are found.
    :rtype: list of LatLon3Tuple or None
    """
    L = getLogger(__name__)
    patterns = [
        r'\b([+-]?\d+(\.\d+)?)°?\s*,\s*([+-]?\d+(\.\d+)?)°?\b',  # 8.994410°, - 79.543000°
        r'\b([+-]?\d+(\.\d+)?)°?\s*([NS]),\s*([+-]?\d+(\.\d+)?)°?\s*([EW])\b',  # 8.910718°N, -79.528919°
        r'\b(\d+)°\s*(\d+(\.\d+)?)\'?\s*([NS]),\s*(\d+)°\s*(\d+(\.\d+)?)\'?\s*([EW])\b',  # 7° 38.422'N, 81° 42.079'W
        r'\b(\d+)°\s*(\d+)\'\s*(\d+(\.\d+)?)\"?\s*([NS]),\s*(\d+)°\s*(\d+)\'\s*(\d+(\.\d+)?)\"?\s*([EW])\b',  # 9°9'42.36"N, 79°50'15.67"W
        r'\b(\d+)°\s*(\d+)′\s*([NS])\s*latitude,\s*(\d+)°\s*(\d+)′\s*([EW])\s*longitude\b',  # 0°41′ S latitude, 76°24′ W longitude
        r'\b(\d+)°\s*(\d+(\.\d+)?)\'?\s*([NS])\s+(\d+)°\s*(\d+(\.\d+)?)\'?\s*([EW])\b',  # 8° 38.743'N    79° 2.887'W
        r'\bLocation:\s*([+-]?\d+(\.\d+)?)\s+([+-]?\d+(\.\d+)?)\b'  # Location: 7.69633 -81.61603
    ]
    latlon = []
    if ('°' in desc) or ('Location:' in desc):
        for pattern in patterns:
            matches = re.findall(pattern, desc)
            for match in matches:
                L.info(f'Match list: {match}')
                if len(match) == 4:
                    # Decimal degrees format
                    lat = float(match[0])
                    lon = float(match[2])
                    L.info(f'Found decimal degrees; lat, lon: {lat}, {lon}')
                    latlon.append(LatLon3Tuple(lat, lon, 0))
                elif len(match) == 6 and 'latitude' in desc and 'longitude' in desc:
                    # Degrees and minutes with direction (special format)
                    lat = dm_to_decimal(match[0], match[1], match[2])
                    lon = dm_to_decimal(match[3], match[4], match[5])
                    L.info(f'Found degrees and minutes; lat, lon: {lat}, {lon}')
                    latlon.append(LatLon3Tuple(lat, lon, 0))
                elif len(match) == 6:
                    # Decimal degrees with direction
                    lat = float(match[0]) * (-1 if match[2] == 'S' else 1)
                    lon = float(match[3]) * (-1 if match[5] == 'W' else 1)
                    L.info(f'Found directional decimal degrees; lat, lon: {lat}, {lon}')
                    latlon.append(LatLon3Tuple(lat, lon, 0))
                elif len(match) == 8:
                    # Degrees and decimal minutes with direction
                    lat = dms_to_decimal(match[0], match[1], 0, match[3])
                    lon = dms_to_decimal(match[4], match[5], 0, match[7])
                    L.info(f'Found DDM; lat, lon: {lat}, {lon}')
                    latlon.append(LatLon3Tuple(lat, lon, 0))
                elif len(match) == 10:
                    # Degrees, minutes, and seconds with direction
                    lat = dms_to_decimal(match[0], match[1], match[2], match[4])
                    lon = dms_to_decimal(match[5], match[6], match[7], match[9])
                    L.info(f'Found DMS; lat, lon: {lat}, {lon}')
                    latlon.append(LatLon3Tuple(lat, lon, 0))
        # consolidate duplicates
        latlon = list(set(latlon))
        return latlon
    else:
        L.info('No lat/lon pairs found in description.')
        return None


def pathify(title: str):
    """
    Convert a title to a file path.

    :param title: The title to convert.
    :type title: str
    :return: The pathified title.
    :rtype: str
    """
    return re.sub(r'[^\w\s]', '', title).replace(' ', '_')[:48]


def get_doipath(doi: str):
    """
    Get the path to the data directory for a given DOI.

    :param doi: The DOI to search for.
    :type doi: str
    :return: The path to the data directory.
    :rtype: Path
    """
    L = getLogger(__name__)
    global DATA_ROOT
    doidir = Path(DATA_ROOT / doi)
    if not doidir.exists():
        L.info(f'{doidir} does not exist. Trying other versions...')
        doidir = search_versions(doi)
    return doidir


def search_versions(doi: str):
    """
    Search the directory structure for a given DOI. If no dir is found, then
    decrease the version at the end of the DOI until a directory is found that
    matches. Return a path with the appropriate DOI version.

    :param str doi: The DOI to search for.
    :return: The path to the data directory.
    :rtype: Path
    """
    global DATA_ROOT
    L = getLogger(__name__)
    doidir = Path(DATA_ROOT / doi)
    if not doidir.exists():
        # we need to figure out where the closest version is (or if it exists?)
        try:
            # split the doi into the root and version
            [doiroot, version] = doidir.__str__().split('.v')
            version = int(version)
            versions = 0
            L.info(f'{doi} starting with version {version}')
            while True:
                # decrement the version and check if the directory exists
                version -= 1
                moddir = Path(DATA_ROOT / f'{doiroot}.v{version}')
                L.info(f'Trying {moddir}')
                if moddir.exists():
                    return moddir
                else:
                    if version > 0:
                        continue
                    else:
                        L.info(f'Found {versions} versions of doi root {doi}')
                        break
        except ValueError:
            L.info(f'{doi} has no version.')
        except Exception as e:
            L.error(f'{repr(e)} has occurred: {e}')
    return doidir


def write_article(article: dict | str, doi: str, title: str, fmt: str):
    """
    Writes the article dictionary to a file.
    This function can write the article in either JSON or XML format.
    It is used to preserve the original Figshare article in JSON format
    and to write the EML XML file for upload to DataONE.

    :param article: The article data to write.
    :type article: dict
    :param path: The format to write the article in (e.g., 'json', 'xml').
    :type path: Path
    """
    L = getLogger(__name__)
    doipath = get_doipath(doi)
    path = Path(doipath / f"{pathify(title)}.{fmt}")
    L.info(f'Writing {fmt} file to {path}')
    with open(str(Path(path)), 'w') as f:
        if fmt == 'json':
            json.dump(article, fp=f, indent=2)
        elif fmt == 'xml':
            f.write(article)
    L.info(f'Wrote {fmt}.')
    return path


def get_article_list(article_file: Path | str):
    """
    Retrieves the list of articles from the provided metadata file.
    The metadata file location is specified in the config file.

    :param articles: The file containing article metadata.
    :type articles: dict or list
    :return: The list of articles.
    :rtype: list
    """
    L = getLogger(__name__)
    af = Path(article_file)
    articles = json.loads(af.read_bytes())
    alist = None
    try:
        alist = articles.get('articles')
    except:
        L.info(f'articles object is not a dict but {type(articles)}. Trying to use it as a list...')
    if alist:
        articles = alist
    else:
        if (type(articles) == list) and (len(articles) >= 1):
            L.info(f'articles object is a list of length {len(articles)}')
    L.info(f'Found {len(articles)} article records')
    return articles


def rectify_uploads(uploads: Path | str, client: MemberNodeClient_2_0 | None=None):
    """
    Rectify the uploads dictionary with DataONE object identifiers.
    Write the updated uploads dictionary to the original file.

    .. warning::
        
        This script does not check for duplicates, or whether the document is
        at the head of the version chain! Running this when there are multiple
        EMLs with the same MD5 on the server can lead to hazardous
        consequences.

    :param Path uploads: A dictionary containing upload information.
    :param MemberNodeClient_2_0 client: A DataONE MemberNodeClient_2_0 object.
    :return: A dictionary with updated upload information.
    :rtype: dict
    """
    if type(uploads) == str:
        uploads = Path(uploads)
    if not uploads.exists():
        raise FileNotFoundError(f'Could not find an uploads info json file at {uploads}')
    if not client:
        config = get_config()
        mn_url = config['mnurl']
        options: dict = {
            "headers": {"Authorization": "Bearer " + get_token()},
            "timeout_sec": 9999,
            }
        client = MemberNodeClient_2_0(mn_url, **options)
    uploads_dict = load_uploads(uploads)
    uploads_dict = get_d1_ids(uploads_dict, client)
    client._session.close()
    save_uploads(uploads_dict, fp=uploads)
    return uploads_dict


def get_d1_ids(filedict: dict, client: MemberNodeClient_2_0):
    """
    Retrieve DataONE object identifiers for hashed files in a file dictionary.
    This function uses a :py:mod:`d1_client.mnclient_2_0.MemberNodeClient_2_0`
    client to retrieve a list of objects from the Member Node and updates the
    file dictionary with the object identifiers, formatIds, and URLs.

    :param dict filedict: A dictionary containing file information.
    :param MemberNodeClient_2_0 client: A DataONE MemberNodeClient_2_0 object.
    :return: A dictionary with updated file information including identifiers, formatIds, and URLs.
    :rtype: dict
    """
    L = getLogger(__name__)
    sep = '' if CN_URL.endswith('/') else '/'
    try:
        object_list = client.listObjects(start=0, count=1)
        tot = object_list.total
        L.info(f'Total number of objects in MN {client.base_url}: {tot}')
        object_list_list = []
        object_list_list.append(client.listObjects(start=0, count=tot))
        if not (object_list_list[0].total == tot):
            L.info(f'Got {object_list_list[0].total} of {tot} objects, requesting the rest...')
            object_list_list.append([client.listObjects(start=i, count=tot) for i in range(object_list_list[0].count, tot, object_list_list[0].count)])
            L.info('Done.')
    except Exception as e:
        L.error(f"Failed to retrieve object list from DataONE: {e}")
        return filedict
    # Create a dictionary to map MD5 sums to identifiers, formatIds, and URLs
    obj_info = {}
    for object_list in object_list_list:
        for obj in object_list.objectInfo:
            obj_info[obj.checksum.value()] = {
                'identifier': obj.identifier.value(),
                'formatId': obj.formatId,
                'url': f"{CN_URL}{sep}v2/resolve/{obj.identifier.value()}"
            }
    # Update the file info dictionary
    for doi, files in filedict.items():
        for md5, file_info in files.items():
            if md5 in obj_info:
                file_info['identifier'] = obj_info[md5]['identifier']
                file_info['formatId'] = obj_info[md5]['formatId']
                file_info['url'] = obj_info[md5]['url']
    return filedict


def generate_access_policy():
    """
    Creates the access policy for the object. Note that the permission is set to 'read'.
    
    :return: The access policy.
    :rtype: dataoneTypes.accessPolicy
    """
    accessPolicy = dataoneTypes.accessPolicy()
    accessRule = dataoneTypes.AccessRule()
    accessRule.subject.append(const.SUBJECT_PUBLIC)
    permission = dataoneTypes.Permission('read')
    accessRule.permission.append(permission)
    accessPolicy.append(accessRule)
    accessRule = dataoneTypes.AccessRule()
    config = get_config()
    if config.get('write_groups'):
        for group in config.get('write_groups'):
            writeGroup = dataoneTypes.Subject(group)
            accessRule.subject.append(writeGroup)
            permission = dataoneTypes.Permission('write')
            accessRule.permission.append(permission)
            accessPolicy.append(accessRule)
    if config.get('changePermission_groups'):
        for group in config.get('changePermission_groups'):
            changePermissionGroup = dataoneTypes.Subject(group)
            accessRule.subject.append(changePermissionGroup)
            permission = dataoneTypes.Permission('changePermission')
            accessRule.permission.append(permission)
            accessPolicy.append(accessRule)
    return accessPolicy


def fix_access_policies():
    """
    Fix the access policies for objects uploaded in the last three days.

    This function uses a :py:mod:`d1_client.mnclient_2_0.MemberNodeClient_2_0`
    client to retrieve a list of objects uploaded in the last three days, and
    modifies their access policies to include the groups specified in the
    config document.
    """
    config = get_config()
    token = get_ll_token()
    mnurl = config['mnurl']
    # Initialize the mn client
    options: dict = {
        "headers": {"Authorization": "Bearer " + token},
        "timeout_sec": 9999,
        }
    client: MemberNodeClient_2_0 = MemberNodeClient_2_0(mnurl, **options)
    # Retrieve the list of objects uploaded in the last three days
    three_days_ago = datetime.now() - timedelta(days=3)
    object_list = client.listObjects(fromDate=three_days_ago)
    for obj in object_list.objectInfo:
        # Retrieve the system metadata
        sysmeta = client.getSystemMetadata(obj.identifier.value())
        # Modify the access policy
        sysmeta.accessPolicy = generate_access_policy()
        # Update the system metadata with the new access policy
        client.updateSystemMetadata(obj.identifier.value(), sysmeta)
    client._session.close()


def save_uploads(uploads: dict, fp: Path='./uploads.json'):
    """
    Save the uploads dictionary to a file.
    This function is called multiple times throughout the script operation to
    save information about the files uploaded to the DataONE Member Node.

    :param dict uploads: A dictionary containing upload information.
    :param Path fp: The file path to write the uploads dictionary to.
    :return: The file path to the uploads dictionary.
    :rtype: Path
    """
    L = getLogger(__name__)
    l = len(uploads)
    if fp.parent.exists():
        with open(fp, 'w') as f:
            json.dump(uploads, fp=f, indent=2)
        L.info(f'Wrote {l} uploads to {fp}')
        #L.debug(f'Saved upload dump:\n{json.dumps(uploads,indent=2)}') # very verbose
        return fp
    else:
        L.error(f'Could not find folder to write uploads file! Dumping them here:\n{json.dumps(uploads, indent=2)}')


def load_uploads(fp: Path='./uploads.json'):
    """
    Load the uploads dictionary from a file.
    This function is called at the beginning of the script to load information
    about the files uploaded to the DataONE Member Node.

    :param Path fp: The file path to read the uploads dictionary from.
    :return: The uploads dictionary.
    :rtype: dict
    """
    L = getLogger(__name__)
    if fp.exists():
        L.info(f'Loading uploads from {fp}')
        with open(fp, 'r') as f:
            try:
                uploads = json.load(fp=f)
            except json.JSONDecodeError as e:
                L.warning(f'Caught JSONDecodeError: {e}. This probably happened because there is no file to read. Continuing with empty dict...')
                uploads = {}
        l = len(uploads)
        L.info(f'Loaded info for {l} uploads.')
        L.debug(f'Loaded upload dump:\n{json.dumps(uploads,indent=2)}')
        return uploads
    else:
        L.error('Could not find uploads file!')
        raise FileNotFoundError('Could not find an uploads info json file!')

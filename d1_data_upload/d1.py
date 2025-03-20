import uuid
import hashlib
import datetime
from pathlib import Path

from d1_client.mnclient_2_0 import *
from d1_common.types import dataoneTypes, exceptions
from d1_common.resource_map import createSimpleResourceMap
from d1_common.resource_map import ResourceMap

from logging import getLogger
from copy import deepcopy

from .defs import fmts, CN_URL, DATA_ROOT, WORK_LOC
from .utils import get_article_list, load_uploads, save_uploads, \
            write_article, get_token, get_config, create_client, \
            generate_access_policy, get_format, get_files_from_dir
from .eml import update_eml

rpt_txt = """
Package creation report:
Failed uploads:     %s
Successful uploads: %s

Failed packages:
%s

Successful packages:
%s
"""
"""
The package creation report text template.
"""

def get_system_metadata(client, pid):
    """
    Get the system metadata for the given PID.
    """
    return client.getSystemMetadata(pid)


def get_access_policy(client, pid):
    """
    Get the access policy for the given PID.
    """
    return client.getSystemMetadata(pid).accessPolicy


def get_latest_version(client, pid):
    """
    Get the latest version of the given PID.
    """
    sm = get_system_metadata(client, pid)
    if sm.obsoletedBy:
        while True:
            print(f'Obsoleted by: {sm.obsoletedBy}')
            sm = get_system_metadata(client, sm.obsoletedBy)
            if not sm.obsoletedBy:
                break
    print(f'Latest version: {sm.identifier.value()}')
    return sm.identifier.value()


def get_object(client: MemberNodeClient_2_0, pid):
    """
    Get the object for the given PID.
    """
    return client.get(pid)


def generate_sys_meta(pid: str, sid: str, format_id: str, size: int, md5, now, orcid: str, ap=None):
    """
    Fills out the system metadata object with the needed properties

    :param pid: The pid of the system metadata document
    :param sid: The series id of the document being described
    :param format_id: The format of the document being described
    :param size: The size of the document that is being described
    :param md5: The md5 hash of the document being described
    :param now: The current time
    :param orcid: The uploader's orcid
    :param ap: The access policy of the document being described (``None`` will default to public read)
    :return: The system metadata document
    :rtype: dataoneTypes.systemMetadata
    """
    # create sysmeta and fill out relevant fields
    sys_meta = dataoneTypes.systemMetadata()
    sys_meta.identifier = str(pid)
    #sys_meta.seriesId = sid
    sys_meta.formatId = format_id
    sys_meta.size = size
    sys_meta.rightsHolder = orcid
    # calculate checksums, set dates, and set public access
    sys_meta.checksum = dataoneTypes.checksum(str(md5))
    sys_meta.checksum.algorithm = 'MD5'
    sys_meta.dateUploaded = now
    sys_meta.dateSysMetadataModified = now
    if ap:
        sys_meta.accessPolicy = ap
    else:
        sys_meta.accessPolicy = generate_access_policy()
    return sys_meta


def generate_system_metadata(pid: str, sid: str, format_id: str, science_object: bytes, orcid: str, ap=None):
    """
    Generates a system metadata document.

    :param pid: The pid that the object will have
    :param sid: The series id of the object
    :param format_id: The format of the object (e.g text/csv)
    :param science_object: The object that is being described
    :param orcid: The orcid of the uploader
    :param ap: The access policy of the object (``None`` will default to public read)
    :return: The system metadata document, MD5 sum, and size of the object
    :rtype: tuple
    """
    L = getLogger(__name__)
    # Check that the science_object is unicode, attempt to convert it if it's a str
    if not isinstance(science_object, bytes):
        if isinstance(science_object, str):
            science_object = science_object.encode("utf-8")
        else:
            raise ValueError('Supplied science_object is not unicode')
    size = len(science_object)
    L.debug(f'Object is {size} bytes ({round(size/(1024*1024), 1)} MB)')
    md5 = hashlib.md5()
    md5.update(science_object)
    md5 = md5.hexdigest()
    now = datetime.datetime.now()
    sys_meta = generate_sys_meta(pid, sid, format_id, size, md5, now, orcid, ap=ap)
    return sys_meta, md5, size


def sysmeta_obsolete_updates(client: MemberNodeClient_2_0, old_pid: str, new_pid: str):
    """
    Update the system metadata of the old object to include the obsoletedBy field.

    :param client: The Member Node client.
    :type client: MemberNodeClient_2_0
    :param old_pid: The PID of the old object.
    :type old_pid: str
    :param new_pid: The PID of the new object.
    :type new_pid: str
    """
    L = getLogger(__name__)
    # Retrieve old sysmeta
    old_system_metadata = client.getSystemMetadata(old_pid)
    if not old_system_metadata:
        L.error(f'Failed to retrieve system metadata for old PID: {old_pid}')
        return None
    new_system_metadata = client.getSystemMetadata(new_pid)
    if not new_system_metadata:
        L.error(f'Failed to retrieve system metadata for new PID: {new_pid}')
        return None
    # Add obsoletedBy pid to old sysmeta
    old_system_metadata.obsoletedBy = new_pid
    new_system_metadata.obsoletes = old_pid
    try:
        # Update old sysmeta object
        client.updateSystemMetadata(old_pid, old_system_metadata)
        L.info(f'Successfully updated system metadata for old PID: {old_pid} with obsoletedBy: {new_pid}')
    except Exception as e:
        L.error(f'Failed to update system metadata for old PID: {old_pid}: {repr(e)}')
        return None
    try:
        # Update new sysmeta object
        client.updateSystemMetadata(new_pid, new_system_metadata)
        L.info(f'Successfully updated system metadata for new PID: {new_pid} with obsoletes: {old_pid}')
    except Exception as e:
        L.error(f'Failed to update system metadata for new PID: {new_pid}: {repr(e)}')
        return None


def get_filepaths(files: list, pid_dir: Path):
    """
    Get the paths to the files in the data directory.

    :param list files: The list of files to search for.
    :param Path pid_dir: The path to the data directory.
    :return: The list of paths to the files.
    :rtype: list
    """
    L = getLogger(__name__)
    paths = []
    for f in files:
        p = Path(pid_dir / f['name'])
        L.debug(f'Path: {p}')
        if p.exists():
            paths.append(p)
        else:
            for pa in Path(p.parent / p.stem).glob('*'):
                paths.append(pa)
    return paths


def upload_files(orcid: str, pid: str, files: list, client: MemberNodeClient_2_0, ap=None, data_root=DATA_ROOT):
    """
    Upload the files to the Member Node.

    :param str orcid: The ORCID of the uploader.
    :param str pid: The pid of the record.
    :param list files: The list of files to upload.
    :param client: The Member Node client.
    :type client: MemberNodeClient_2_0
    :param ap: The access policy of the record. Defaults to public read (``None``).
    :return: A dictionary of checksums and file information.
    :rtype: dict
    """
    L = getLogger(__name__)
    mn_url = client.base_url
    sep = '' if mn_url.endswith('/') else '/'
    data_pids = None
    sm_dict = {}
    # get the path to the data directory
    pid_dir = Path(data_root / pid)
    # get the paths to the files
    files = get_filepaths(files=files, pid_dir=pid_dir)
    flen = len(files)
    if flen == 0:
        raise FileNotFoundError(f'{pid} No files found for this version chain!')
    # keep track of data pids for resource mapping
    data_pids = []
    i = 0
    for f in files:
        i += 1
        try:
            # get the format of the file
            fformat = get_format(f)
            data_pid = f"urn:uuid:{str(uuid.uuid4())}"
            L.debug(f'{pid} Reading {f.name} ({fformat})')
            data_bytes = f.read_bytes()
            L.debug(f'{pid} Generating sysmeta for {f.name}')
            data_sm, md5, size = generate_system_metadata(pid=data_pid,
                                                        sid=pid,
                                                        format_id=fformat,
                                                        science_object=data_bytes,
                                                        orcid=orcid,
                                                        ap=ap)
            L.info(f'{pid} ({i}/{flen}) Uploading {f.name} ({round(size/(1024*1024), 1)} MB)')
            dmd = client.create(data_pid, data_bytes, data_sm)
            if isinstance(dmd, dataoneTypes.Identifier):
                # if the response is an identifier, the upload was successful
                try:
                    L.info(f'{pid} Received response for science object upload: {dmd.value()}')
                except Exception as e:
                    L.error(f'{pid} Received <d1_common.types.generated.dataoneTypes_v1.Identifier> but could not print value: {repr(e)}')
                # add the data pid to the list of data pids
                data_pids.append(data_pid)
                sm_dict[md5] = {
                    'filename': f.name,
                    'size': size,
                    'pid': pid,
                    'identifier': data_pid,
                    'formatId': fformat,
                    'url': f"{mn_url}{sep}v2/object/{data_pid}",
                }
            else:
                L.error(f'{pid} Unexpected response type: {type(dmd)}')
                try:
                    L.debug(f'{pid} Received response:\n{dmd.value()}')
                except Exception as e:
                    L.error(f'{pid} Could not print response: {repr(e)}')
        except Exception as e:
            L.error(f'{pid} upload failed ({e})')
            raise BaseException(e)
    return sm_dict


def upload_eml(orcid: str, pid: str, eml: str|bytes, eml_pid: str, client: MemberNodeClient_2_0, ap=None):
    """
    Upload the EML to the Member Node.
    
    :param str orcid: The ORCID of the uploader.
    :param str pid: The pid of the metadata record.
    :param str eml: The EML to upload.
    :param client: The Member Node client.
    :type client: MemberNodeClient_2_0
    :param ap: The access policy of the record. Defaults to public read (``None``).
    :return: The identifier of the EML.
    :rtype: str
    """
    L = getLogger(__name__)
    if not isinstance(eml, str):
        eml = eml.decode("utf-8")
    eml_bytes = eml.encode("utf-8")
    eml_sm, eml_md5, eml_size = generate_system_metadata(pid=eml_pid,
                                                         sid=None,
                                                         format_id="https://eml.ecoinformatics.org/eml-2.2.0",
                                                         science_object=eml_bytes,
                                                         orcid=orcid,
                                                         ap=ap)
    try:
        eml_dmd = client.create(eml_pid, eml_bytes, eml_sm)
        L.info(f'{pid} Received response for EML upload: {eml_dmd.value()}\n{eml_dmd}')
        if eml_dmd.value() == eml_pid:
            L.info(f'{pid} EML uploaded successfully: {eml_pid}')
        else:
            L.error(f'{pid} EML identifier does not match D1 identifier! {eml_pid} != {eml_dmd.value()}')
    except exceptions.DataONEException as e:
        L.error(f'{pid} EML upload failed: {e}')
    except Exception as e:
        L.error(f'{pid} Got non DataONEException error: {e}')
    return eml_md5, eml_size


def generate_resource_map(eml_pid: str, data_pids: list):
    """
    Generate the resource map XML for the given record, EML PID, and data PIDs.

    :param str eml_pid: The PID of the EML.
    :param list data_pids: The list of data PIDs.
    :return: The resource map XML.
    :rtype: str
    """
    L = getLogger(__name__)
    resource_map: ResourceMap = createSimpleResourceMap(
        ore_pid=f"resource_map_urn:uuid:{str(uuid.uuid4())}",
        scimeta_pid=eml_pid,
        sciobj_pid_list=data_pids,
    )
    L.debug(f"Generated resource map:\n{resource_map}")
    return resource_map


def upload_resource_map(pid: str, resource_map: ResourceMap, client: MemberNodeClient_2_0, orcid: str, ap=None):
    """
    Upload the resource map to the Member Node.

    :param str pid: The pid of the record.
    :param resource_map: The resource map XML.
    :type resource_map: ResourceMap
    :param client: The Member Node client.
    :type client: MemberNodeClient_2_0
    :return: The identifier of the resource map.
    :rtype: str
    """
    L = getLogger(__name__)
    resource_map_pid = resource_map.getResourceMapPid()
    resource_map_bytes = resource_map.serialize(format="xml")
    resource_map_sm, resource_map_md5, resource_map_size = generate_system_metadata(pid=resource_map_pid,
                                                                                    sid=pid,
                                                                                    format_id="http://www.openarchives.org/ore/terms",
                                                                                    science_object=resource_map_bytes,
                                                                                    orcid=orcid,
                                                                                    ap=ap)
    resource_map_dmd = client.create(resource_map_pid, resource_map_bytes, resource_map_sm)
    if isinstance(resource_map_dmd, dataoneTypes.Identifier):
        try:
            L.info(f'{pid} Received response for resource map upload: {resource_map_dmd.value()}')
            if resource_map_dmd.value() == resource_map_pid:
                L.info(f'{pid} Resource map uploaded successfully: {resource_map_pid}')
            else:
                L.error(f'{pid} Resource map identifier does not match D1 identifier! {resource_map_pid} != {resource_map_dmd.value()}')
        except Exception as e:
            L.error(f'{pid} Received <d1_common.types.generated.dataoneTypes_v1.Identifier> but could not print value: {repr(e)}')
        return resource_map_pid, resource_map_md5, resource_map_size
    else:
        L.error(f'{pid} Unexpected response type: {type(resource_map_dmd)}')
        try:
            L.debug(f'{pid} Received response:\n{resource_map_dmd.value()}')
        except Exception as e:
            L.error(f'{pid} Could not print response: {repr(e)}')
        return None


def report(succ: int, fail: int, finished_pids: list, failed_pids: list):
    """
    Generate and print a short report with the successes and failures of the
    process.

    :param int succ: The number of successful uploads.
    :param int fail: The number of failed uploads.
    :param list finished_pids: The pids that were successfully uploaded.
    :param list failed_pids: The pids that failed to upload.
    """
    L = getLogger(__name__)
    finished_str = "\n".join(str(x) for x in finished_pids)
    failed_str = "\n".join(str(x) for x in failed_pids)
    L.info(rpt_txt % (fail, succ, failed_str, finished_str))


def upload_manager(pids: list, orcid: str, client: MemberNodeClient_2_0, node: str, data_root: Path, eml_override=False):
    """
    Package creation and upload loop. This function will create packages and
    upload them to the Member Node.

    :param list pids: The list of pids to upload.
    :param str orcid: The ORCID of the uploader.
    :param client: The Member Node client.
    :type client: MemberNodeClient_2_0
    :param str node: The node identifier.
    """
    L = getLogger(__name__)
    mn_url = client.base_url
    sep = '' if mn_url.endswith('/') else '/'
    n = len(pids)
    i = 0
    er = 0
    succ_list = []
    err_list = []
    uploads_loc = Path(WORK_LOC / f'{node}.json')
    try:
        uploads = load_uploads(uploads_loc)
    except FileNotFoundError:
        uploads = {}
    try:
        # Three EML pids exist.
        # 1. The pid of the original EML (pid)
        # 2. The pid of the latest EML on the server (latest_pid)
        # 3. The pid of the EML to be uploaded (eml_pid)
        # Take care to use the correct one in the correct context.
        for pid in pids:
            old_eml_pid, old_resource_map_pid = None, None
            i += 1
            L.debug(f'PID: {pid}')
            latest_pid = get_latest_version(client, pid)
            ap = get_access_policy(client, pid)
            # get the files to be uploaded
            files = get_files_from_dir(Path(data_root / pid))
            L.debug(f'{pid} found files: {files}')
            # check if the article has already been uploaded
            ulist = deepcopy(files)
            if not (uploads.get(pid)):
                uploads[pid] = {}
            else:
                prev_upls = 0
                for uf in uploads.get(pid):
                    L.debug(f'Already uploaded file: {uploads[pid][uf]}')
                    for f in files:
                        # if the file is already uploaded and its md5 matches the current file
                        # add the pid and url to the files object for inclusion in the resource map
                        if f.get('computed_md5') in uploads[pid]:
                            f['d1_url'] = uploads[pid][f.get('computed_md5')]['url']
                            f['pid'] = uploads[pid][f.get('computed_md5')]['identifier']
                        else:
                            # otherwise, add the file to the list of files related to the article
                            files.append({
                                'pid': uploads[pid][uf]['identifier'],
                                'name': uploads[pid][uf]['filename'],
                                'computed_md5': uf,
                                'd1_url': uploads[pid][uf]['url'],
                                'size': uploads[pid][uf]['size'],
                                'mimetype': uploads[pid][uf]['formatId'],
                            })
                    fn = 0
                    for f in ulist:
                        # if the file is already uploaded, remove it from the list of files to upload
                        if uploads[pid][uf]['filename'] == f['name']:
                            prev_upls += 1
                            del ulist[fn]
                        fn += 1
                L.info(f'Found {prev_upls} files that were already uploaded associated with {pid}')
            if True:
            # try:
                if len(ulist) > 0:
                    # Upload the data files to the Member Node if there are any in the ulist
                    sm_dict = upload_files(orcid, pid, ulist, client, ap=ap, data_root=data_root)
                    L.info(f'{pid} done. Uploaded {len(sm_dict)} files.')
                    for fi in sm_dict:
                        # add the uploaded files to the uploads dictionary
                        uploads[pid][fi] = sm_dict[fi]
                        for f in files:
                            # add the dataone url to the files object for inclusion in the resource map
                            if (f['name'] == sm_dict[fi]['filename']) and (f['computed_md5'] == fi):
                                f['d1_url'] = sm_dict[fi]['url']
                                f['pid'] = sm_dict[fi]['identifier']
                    save_uploads(uploads, fp=uploads_loc)
                else:
                    L.info(f'No data files to upload for {pid}')
                sm_dict = uploads[pid]
                sm_delete = []
                for f in sm_dict:
                    if (sm_dict[f].get('filename') in 'eml.xml') or (sm_dict[f].get('filename') in 'resource_map.xml'):
                        sm_delete.append(f)
                for f in sm_delete:
                    del sm_dict[f]
                # Convert the article to EML
                eml_string = client.get(latest_pid).text
                old_eml_pid = latest_pid
                eml_mod, eml_pid = update_eml(eml_string, sm_dict)
                # Upload the EML to the Member Node
                eml_md5, eml_size = upload_eml(orcid, pid, eml_mod, eml_pid, client, ap=ap)
                L.info(f'{pid} New EML: {eml_pid}')
                if old_eml_pid:
                    L.info(f'{pid} Adding obsoletedBy to old EML sysmeta object')
                    # Update old sysmeta with obsoletedBy
                    sysmeta_obsolete_updates(client, old_eml_pid, eml_pid)
                if eml_pid:
                    uploads[pid]['eml'] = {
                        'filename': 'eml.xml',
                        'size': eml_size,
                        'pid': pid,
                        'identifier': eml_pid,
                        'md5': eml_md5,
                        'formatId': "https://eml.ecoinformatics.org/eml-2.2.0",
                        'url': f"{mn_url}{sep}v2/object/{eml_pid}",
                    }
                    save_uploads(uploads, fp=uploads_loc)
                    # Generate the DataONE resource map
                    pid_list = [eml_pid]
                    for f in files:
                        # add the data pids to the list of pids for the resource map
                        if f.get('pid'):
                            pid_list.append(f.get('pid'))
                        else:
                            L.warning(f'{pid} No pid key found for {f["name"]}')
                            if f.get('computed_md5') in sm_dict:
                                pid_list.append(sm_dict[f['computed_md5']]['identifier'])
                            else:
                                L.error(f'{pid} No PID found for {f["name"]}')
                    resource_map = generate_resource_map(eml_pid=eml_pid, data_pids=pid_list)
                    if uploads[pid].get('resource_map'):
                        old_resource_map_pid = uploads[pid]['resource_map']['identifier']
                        L.info(f'{pid} Found previous resource map: {old_resource_map_pid}')
                    # Upload the resource map to the Member Node
                    resource_map_pid, resource_map_md5, resource_map_size = upload_resource_map(
                        pid=pid,
                        resource_map=resource_map,
                        client=client,
                        orcid=orcid,
                        ap=ap
                    )
                    if old_resource_map_pid:
                        L.info(f'{pid} Adding obsoletedBy to old resource map sysmeta object: {old_resource_map_pid}')
                        sysmeta_obsolete_updates(client, old_pid=old_resource_map_pid, new_pid=resource_map.getResourceMapPid())
                    # Put the resource map info in the uploads dictionary
                    if resource_map_pid:
                        uploads[pid]['resource_map'] = {
                            'filename': 'resource_map.xml',
                            'size': resource_map_size,
                            'pid': pid,
                            'identifier': resource_map_pid,
                            'md5': resource_map_md5,
                            'formatId': "http://www.openarchives.org/ore/terms",
                            'url': f"{mn_url}{sep}v2/object/{resource_map_pid}",
                        }
                        save_uploads(uploads, fp=uploads_loc)
                        L.info(f'{pid} Resource map uploaded successfully: {resource_map_pid}')
                    else:
                        uploads[pid]['resource_map'] = None
                        raise exceptions.DataONEException(f'{pid} Resource map upload failed')
                else:
                    uploads[pid]['eml'] = None
                    raise exceptions.DataONEException(f'{pid} EML upload failed')
                succ_list.append(pid)
            # except Exception as e:
            #     er += 1
            #     err_list.append(pid)
            #     L.error(f'{pid} / {repr(e)}')
    except KeyboardInterrupt:
        L.info('Caught KeyboardInterrupt; generating report...')
    finally:
        save_uploads(uploads, fp=uploads_loc)
        report(succ=i-er, fail=er, finished_pids=succ_list, failed_pids=err_list)


def run_data_upload():
    """
    Set config items then start upload loop. This function is called when the
    script is run directly.
    """
    L = getLogger(__name__)
    # Set config items
    auth_token = get_token()
    config = get_config()
    orcid = config.get('rightsholder_orcid')
    node = config.get('nodeid')
    mn_url = config.get('mnurl')
    metadata_json = config.get('metadata_json')
    L.info(f'Rightsholder ORCiD {orcid}')
    L.info(f'Using {node} at {mn_url}')
    L.info(f'Root path: {DATA_ROOT}')
    # Create the Member Node Client
    client: MemberNodeClient_2_0 = create_client(mn_url, auth_token=auth_token)
    L.info(f'The preferred way to run this script is to use the Jupyter Notebook (upload.ipynb). Shutting down.')
    client._session.close()


if __name__ == "__main__":
    """
    Running directly.
    """
    run_data_upload()

from logging import getLogger
import d1_client.mnclient as mn
import xml.etree.ElementTree as ET
from xml.dom import minidom
from html2text import html2text
from pathlib import Path

from .utils import parse_name, get_lat_lon, get_article_list, write_article, fix_datetime
from .defs import fmts

def get_etype(fileobj: Path):
    """
    Get the entity type from the filename.
    """
    ext = Path(fileobj).suffix
    if fmts.get(ext):
        return fmts[ext]
    else:
        return 'application/octet-stream'


def update_eml(eml: Path, pids: dict):
    """
    Update the EML with the new resource PIDs.
    """
    L = getLogger(__name__)
    root = ET.XML(eml)
    # Get the dataset element
    dataset = root.find('.//dataset')
    # Clear the dataset subelement of previous content
    for child in dataset.iter('otherEntity'):
        dataset.remove(child)
    # Add the new resource PIDs
    L.info(f'Adding {len(pids)} resources to the EML')
    for pid in pids:
        entity = ET.SubElement(dataset, 'otherEntity', id=pids[pid]['identifier'])
        entityName = ET.SubElement(entity, 'entityName')
        entityName.text = pids[pid]['filename']
        entityType = ET.SubElement(entity, 'entityType')
        entityType.text = get_etype(pids[pid]['filename'])
    rough_string = ET.tostring(root, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")

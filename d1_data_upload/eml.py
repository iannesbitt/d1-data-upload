from logging import getLogger
import d1_client.mnclient as mn
from xml.etree.ElementTree import Element, SubElement, tostring, ElementTree
from html2text import html2text
from pathlib import Path

from .utils import parse_name, get_lat_lon, get_article_list, write_article, fix_datetime
from .defs import GROUP_ID, fmts

def get_etype(fileobj: Path):
    """
    Get the entity type from the filename.
    """
    ext = fileobj.suffix
    if fmts.get(ext):
        return fmts[ext]
    else:
        return 'application/octet-stream'


def update_eml(eml, pids: dict):
    """
    Update the EML with the new resource PIDs.
    """
    # Get the root element
    root = eml.getroot()
    # Get the dataset element
    dataset = SubElement(root, 'dataset')
    for pid in pids:
        entity = SubElement(dataset, 'otherEntity', id=pid)
        entityName = SubElement(entity, 'entityName')
        entityName.text = pids[pid]
        entityType = SubElement(entity, 'entityType')
        entityType.text = get_etype(pids[pid])
    return tostring(eml, encoding='unicode')
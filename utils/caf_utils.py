# utils/caf_utils.py
from lxml import etree


def obtener_datos_caf_desde_bytes(xml_bytes):
    tree = etree.fromstring(xml_bytes)
    da = tree.find('.//DA')
    return {
        "RE": da.findtext("RE"),
        "RS": da.findtext("RS"),
        "TD": da.findtext("TD"),
        "RNG_D": da.findtext("RNG/D"),
        "RNG_H": da.findtext("RNG/H"),
        "FA": da.findtext("FA"),
        "RSAPK_M": da.findtext("RSAPK/M"),
        "RSAPK_E": da.findtext("RSAPK/E"),
        "IDK": da.findtext("IDK"),
        "FRMA": tree.findtext('.//FRMA'),
        "CAF": etree.tostring(tree.find('.//CAF'), encoding='unicode')
    }

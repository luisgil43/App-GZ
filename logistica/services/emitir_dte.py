# logistica/services/emitir_dte.py

from utils.xml_generator import generar_xml_guia_despacho
from utils.caf_utils import obtener_datos_caf_desde_bytes
from utils.cert_utils import cargar_certificado_desde_bytes
from utils.ted_generator import generar_ted
from utils.firma_xml import firmar_xml_documento, firmar_xml_setdte

from lxml import etree
from django.utils.timezone import now
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def generar_y_firmar_dte(ingreso, caf_path, pfx_path, pfx_pass, output_path):
    # Paso 1: generar XML base
    xml_bytes = generar_xml_guia_despacho(ingreso)
    xml_root = etree.fromstring(xml_bytes)

    # Paso 2: cargar CAF desde Cloudinary
    with default_storage.open(caf_path, 'rb') as f:
        caf_bytes = f.read()
    caf_data = obtener_datos_caf_desde_bytes(caf_bytes)

    # Paso 3: generar TED y agregarlo al XML
    ted = generar_ted(ingreso, caf_data)
    doc = xml_root.find('.//{http://www.sii.cl/SiiDte}Documento')
    doc.insert(len(doc) - 1, ted)

    # Paso 4: agregar TmstFirma
    tmst = etree.Element("TmstFirma")
    tmst.text = now().isoformat()
    doc.append(tmst)

    # Paso 5: cargar certificado desde Cloudinary
    with default_storage.open(pfx_path, 'rb') as f:
        pfx_bytes = f.read()
    private_key, cert = cargar_certificado_desde_bytes(pfx_bytes, pfx_pass)

    # Paso 6: firmar DTE
    xml_root = firmar_xml_documento(xml_root, private_key, cert)
    xml_root = firmar_xml_setdte(xml_root, private_key, cert)

    # Paso 7: guardar XML firmado en Cloudinary
    final_xml = etree.tostring(
        xml_root, pretty_print=True, xml_declaration=True, encoding="ISO-8859-1")
    archivo_xml_path = default_storage.save(
        output_path, ContentFile(final_xml))
    return archivo_xml_path

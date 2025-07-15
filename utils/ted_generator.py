# utils/ted_generator.py
from lxml import etree
from hashlib import sha1
from base64 import b64encode
from datetime import datetime
from django.conf import settings


def generar_ted(salida, caf_data):
    dd = etree.Element("DD")

    # Datos del emisor (desde CAF)
    etree.SubElement(dd, "RE").text = caf_data["RE"]  # RUT emisor desde el CAF

    # Tipo de documento
    etree.SubElement(dd, "TD").text = "52"  # Guía de despacho electrónica

    # Folio
    etree.SubElement(dd, "F").text = str(salida.numero_documento)

    # Fecha de emisión
    etree.SubElement(dd, "FE").text = salida.fecha_salida.strftime("%Y-%m-%d")

    # Receptor
    etree.SubElement(dd, "RR").text = salida.rut_receptor
    # máximo 40 caracteres
    etree.SubElement(dd, "RSR").text = salida.nombre_receptor[:40]

    # Monto total (si no manejas montos, puede ir "0")
    etree.SubElement(dd, "MNT").text = "0"

    # Descripción del primer ítem (usamos el primero del formset)
    primer_detalle = salida.detallesalida_set.first()
    if primer_detalle:
        descripcion = primer_detalle.material.nombre
    else:
        descripcion = "Sin ítems"
    etree.SubElement(dd, "IT1").text = descripcion[:90]  # máximo 90 caracteres

    # Insertar CAF completo
    caf_node = etree.fromstring(caf_data["CAF"])
    dd.append(caf_node)

    # Timestamp TED
    etree.SubElement(dd, "TSTED").text = datetime.now().isoformat()

    # Firma simulada (SHA1 base64)
    dd_str = etree.tostring(dd, method="c14n")
    frmt = etree.Element("FRMT", algoritmo="SHA1withRSA")
    frmt.text = b64encode(sha1(dd_str).digest()).decode()

    # Nodo TED
    ted = etree.Element("TED", version="1.0")
    ted.append(dd)
    ted.append(frmt)

    return ted

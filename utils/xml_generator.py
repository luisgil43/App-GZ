# utils/xml_generator.py

from lxml import etree
from django.utils.timezone import now
from django.conf import settings


def generar_xml_guia_despacho(salida):
    NSMAP = {
        None: "http://www.sii.cl/SiiDte",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    }

    envio_dte = etree.Element("EnvioDTE", nsmap=NSMAP, version="1.0")
    set_dte = etree.SubElement(envio_dte, "SetDTE", ID="SetDoc")

    # === Carátula ===
    caratula = etree.SubElement(set_dte, "Caratula", version="1.0")
    etree.SubElement(caratula, "RutEmisor").text = settings.EMPRESA_RUT
    etree.SubElement(caratula, "RutEnvia").text = salida.emitido_por.identidad
    etree.SubElement(caratula, "RutReceptor").text = "60803000-K"
    etree.SubElement(
        caratula, "FchResol").text = settings.EMPRESA_FECHA_RESOLUCION
    etree.SubElement(caratula, "NroResol").text = str(
        settings.EMPRESA_NUM_RESOLUCION)
    etree.SubElement(caratula, "TmstFirmaEnv").text = now().strftime(
        "%Y-%m-%dT%H:%M:%S")

    subtotal = etree.SubElement(caratula, "SubTotDTE")
    etree.SubElement(subtotal, "TpoDTE").text = "52"
    etree.SubElement(subtotal, "NroDTE").text = "1"

    # === Documento ===
    dte = etree.SubElement(set_dte, "DTE", version="1.0")
    documento = etree.SubElement(
        dte, "Documento", ID=f"F{salida.numero_documento}T52")

    # === Encabezado ===
    encabezado = etree.SubElement(documento, "Encabezado")

    # IdDoc
    iddoc = etree.SubElement(encabezado, "IdDoc")
    etree.SubElement(iddoc, "TipoDTE").text = "52"
    etree.SubElement(iddoc, "Folio").text = str(salida.numero_documento)
    etree.SubElement(
        iddoc, "FchEmis").text = salida.fecha_salida.strftime('%Y-%m-%d')
    # Despacho por traslado interno
    etree.SubElement(iddoc, "TipoDespacho").text = "2"
    # Traslado interno sin venta
    etree.SubElement(iddoc, "IndTraslado").text = "6"

    # Emisor
    emisor = etree.SubElement(encabezado, "Emisor")
    etree.SubElement(emisor, "RUTEmisor").text = settings.EMPRESA_RUT
    etree.SubElement(emisor, "RznSoc").text = settings.EMPRESA_NOMBRE
    etree.SubElement(emisor, "GiroEmis").text = settings.EMPRESA_GIRO
    etree.SubElement(emisor, "Acteco").text = settings.EMPRESA_ACTECO
    etree.SubElement(emisor, "DirOrigen").text = settings.EMPRESA_DIR
    etree.SubElement(emisor, "CmnaOrigen").text = settings.EMPRESA_COMUNA
    etree.SubElement(emisor, "CiudadOrigen").text = settings.EMPRESA_CIUDAD

    # Receptor
    receptor = etree.SubElement(encabezado, "Receptor")
    etree.SubElement(receptor, "RUTRecep").text = salida.rut_receptor
    etree.SubElement(receptor, "RznSocRecep").text = salida.nombre_receptor
    etree.SubElement(receptor, "GiroRecep").text = salida.giro_receptor
    etree.SubElement(receptor, "DirRecep").text = salida.direccion_receptor
    etree.SubElement(receptor, "CmnaRecep").text = salida.comuna_receptor
    etree.SubElement(receptor, "CiudadRecep").text = salida.ciudad_receptor

    # Totales (sin valores porque es guía sin precios)
    totales = etree.SubElement(encabezado, "Totales")
    etree.SubElement(totales, "MntNeto").text = "0"
    etree.SubElement(totales, "TasaIVA").text = "19"
    etree.SubElement(totales, "IVA").text = "0"
    etree.SubElement(totales, "MntTotal").text = "0"

    # === Detalle ===
    for i, detalle in enumerate(salida.detalles.all(), start=1):
        linea = etree.SubElement(documento, "Detalle")
        etree.SubElement(linea, "NroLinDet").text = str(i)
        etree.SubElement(linea, "NmbItem").text = detalle.material.nombre
        etree.SubElement(linea, "QtyItem").text = str(detalle.cantidad)
        etree.SubElement(linea, "UnmdItem").text = "UN"
        etree.SubElement(linea, "PrcItem").text = "0"
        etree.SubElement(linea, "MontoItem").text = "0"

    # === Transporte (opcional pero recomendado) ===
    transporte = etree.SubElement(encabezado, "Transporte")
    etree.SubElement(transporte, "Patente").text = salida.patente or ""
    etree.SubElement(transporte, "Chofer").text = salida.chofer or ""
    etree.SubElement(transporte, "DirDest").text = salida.destino
    etree.SubElement(transporte, "CmnaDest").text = salida.comuna_receptor
    etree.SubElement(transporte, "CiudadDest").text = salida.ciudad_receptor

    # === Convertir a string ===
    xml_string = etree.tostring(
        envio_dte,
        pretty_print=True,
        xml_declaration=True,
        encoding="ISO-8859-1"
    )
    return xml_string

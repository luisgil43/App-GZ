import re

import fitz  # PyMuPDF

# Ejemplo de texto en la liquidación Nubox:
# LIQUIDACION DE SUELDO
# REMUNERACIONES MES DE: OCTUBRE del 2025
# ...
# R.U.T.
# TRABAJADOR
# C.C.
# 25.973.603-K
# ZAPATA HERNANDEZ EDGARDO JOSE

RUT_NOMBRE_RE = re.compile(
    r"R\.U\.T\.\s*\nTRABAJADOR\s*\nC\.C\.\s*\n([0-9\.\-Kk]+)\s*\n([A-ZÁÉÍÓÚÑ ]+)"
)

MES_ANIO_RE = re.compile(
    r"REMUNERACIONES MES DE:\s+([A-ZÁÉÍÓÚÑ]+)\s+del\s+(\d{4})"
)

MESES_MAP = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def normalizar_rut(rut: str) -> str:
    """
    Quita puntos, deja guion y mayúsculas.

    '26.724.679-3' -> '26724679-3'
    '25973603k'    -> '25973603K'
    """
    rut = (rut or "").strip().upper()
    rut = rut.replace(".", "")
    return rut


def rut_clave(rut: str) -> str:
    """
    Clave de comparación de RUT:
    - minúscula
    - sin puntos ni guiones
    - solo dígitos y 'k'

    '26.724.679-3' -> '267246793'
    '26724679-3'   -> '267246793'
    '267246793'    -> '267246793'
    '25973603K'    -> '25973603k'
    """
    rut = (rut or "").strip().lower()
    return "".join(ch for ch in rut if ch.isdigit() or ch == "k")


def extraer_paginas_liquidaciones(archivo_pdf):
    """
    Recibe un InMemoryUploadedFile (PDF con una o muchas liquidaciones).

    Devuelve una lista de dicts, uno por página:

    {
      'ok': True/False,
      'pagina': 1-based,
      'rut': '26724679-3' o None,   # normalizado (sin puntos, con guion)
      'nombre': 'ZAPATA HERNANDEZ EDGARDO JOSE' o None,
      'mes': 10 o None,
      'anio': 2025 o None,
      'pdf_bytes': b'...' o None,   # PDF solo de esa página
      'motivo': 'texto de error si ok=False'
    }
    """
    data = archivo_pdf.read()
    doc = fitz.open(stream=data, filetype="pdf")

    resultados = []

    for i in range(doc.page_count):
        pagina_num = i + 1
        page = doc.load_page(i)
        text = page.get_text("text") or ""

        # 1) Buscar RUT + nombre
        m = RUT_NOMBRE_RE.search(text)
        if not m:
            resultados.append({
                "ok": False,
                "pagina": pagina_num,
                "rut": None,
                "nombre": None,
                "mes": None,
                "anio": None,
                "pdf_bytes": None,
                "motivo": "No se encontró el patrón R.U.T. TRABAJADOR en la página.",
            })
            continue

        rut_raw, nombre_raw = m.groups()
        rut = normalizar_rut(rut_raw)          # Ej: 25.973.603-K -> 25973603-K
        nombre = " ".join(nombre_raw.split())  # Limpia espacios dobles

        # 2) Mes/año (solo informativo)
        mes = None
        anio = None
        m2 = MES_ANIO_RE.search(text)
        if m2:
            mes_str, anio_str = m2.groups()
            mes = MESES_MAP.get(mes_str.strip().upper())
            try:
                anio = int(anio_str)
            except (TypeError, ValueError):
                anio = None

        # 3) Crear PDF solo con esta página (en memoria)
        single = fitz.open()
        single.insert_pdf(doc, from_page=i, to_page=i)
        pdf_bytes = single.tobytes()
        single.close()

        resultados.append({
            "ok": True,
            "pagina": pagina_num,
            "rut": rut,
            "nombre": nombre,
            "mes": mes,
            "anio": anio,
            "pdf_bytes": pdf_bytes,
            "motivo": None,
        })

    doc.close()
    return resultados
import logging
import re
import unicodedata
from io import BytesIO

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ==========================
# Normalización de RUT
# ==========================
def rut_clave(rut: str) -> str:
    """
    Normaliza cualquier formato de RUT para usarlo como clave:
    - 26.724.679-3
    - 26724679-3
    - 267246793
    - 25973603k
    => "267246793"
    """
    if not rut:
        return ""

    limpio = re.sub(r"[^0-9kK]", "", str(rut))
    return limpio.lower()


def formatear_rut_chile(rut: str) -> str:
    """
    Recibe:
    - 197738081
    - 19.773.8081
    - 19.773.808-1
    - 19773808-1

    Devuelve:
    - 19.773.808-1
    """
    clave = rut_clave(rut)

    if len(clave) < 2:
        return ""

    cuerpo = clave[:-1]
    dv = clave[-1].upper()

    if not cuerpo.isdigit():
        return ""

    cuerpo_formateado = f"{int(cuerpo):,}".replace(",", ".")
    return f"{cuerpo_formateado}-{dv}"


def quitar_acentos(texto: str) -> str:
    if not texto:
        return ""

    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


# ==========================
# Mes / Año desde el texto
# ==========================
MESES_NOMBRE = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def detectar_mes_anio(texto: str):
    """
    Busca formatos como:
    REMUNERACIONES MES DE: MAYO del 2026
    REMUNERACIONES MES DE : MAYO DEL 2026
    """
    if not texto:
        return None, None

    texto_normalizado = quitar_acentos(texto).upper()

    patron = re.compile(
        r"REMUNERACIONES\s+MES\s+DE\s*:?\s*([A-ZÑ]+)\s+DEL\s+(\d{4})",
        re.IGNORECASE,
    )

    m = patron.search(texto_normalizado)

    if not m:
        return None, None

    nombre_mes = m.group(1).strip().upper()
    anio_txt = m.group(2).strip()

    mes_num = MESES_NOMBRE.get(nombre_mes)

    if not mes_num:
        return None, None

    try:
        anio_num = int(anio_txt)
    except ValueError:
        return None, None

    return mes_num, anio_num


# ==========================
# RUT / Nombre trabajador
# ==========================
def extraer_rut_y_nombre(texto: str):
    """
    Soporta varios formatos de Nubox:

    Formato A:
    R.U.T. TRABAJADOR C.C.
    19.773.808-1 TORRES GUERRA EMILIO JAVIER EXT

    Formato B:
    R.U.T.
    TRABAJADOR
    C.C.
    19.773.808-1
    TORRES GUERRA EMILIO JAVIER

    Formato C:
    RUT EMPRESA:
    77.084.679-K
    R.U.T. TRABAJADOR C.C.
    19.773.808-1 TORRES GUERRA EMILIO JAVIER EXT
    """

    if not texto:
        return None, None

    lineas = [l.strip() for l in texto.splitlines() if l and l.strip()]

    rut_regex = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]\b")

    palabras_bloqueadas_nombre = [
        "A.F.P",
        "AFP",
        "ISAPRE",
        "FECHA",
        "TIPO DE CONTRATO",
        "PLANVITAL",
        "FONASA",
        "HABERES",
        "DESCUENTOS",
        "DIAS",
        "HH",
        "TOTAL",
    ]

    centros_costo = {
        "ADM",
        "EXT",
        "OPER",
        "OP",
        "TEC",
        "RRHH",
        "PM",
    }

    # ======================================================
    # 1) Buscar cerca del encabezado R.U.T. TRABAJADOR
    # ======================================================
    for i, linea in enumerate(lineas):
        up = quitar_acentos(linea).upper()
        up_sin_espacios = up.replace(" ", "")

        es_header_trabajador = ("R.U.T" in up and "TRABAJADOR" in up) or (
            "RUTTRABAJADOR" in up_sin_espacios
        )

        if not es_header_trabajador:
            continue

        # Revisar las próximas líneas después del encabezado
        ventana = lineas[i + 1 : i + 8]

        for candidato in ventana:
            match = rut_regex.search(candidato)

            if not match:
                continue

            rut = formatear_rut_chile(match.group())

            resto = candidato[match.end() :].strip()

            # Si el nombre viene en la misma línea:
            # 19.773.808-1 TORRES GUERRA EMILIO JAVIER EXT
            nombre = resto

            # Si el nombre no viene en la misma línea, tomar línea siguiente
            if not nombre:
                indice_candidato = lineas.index(candidato)
                if indice_candidato + 1 < len(lineas):
                    nombre = lineas[indice_candidato + 1].strip()

            nombre = limpiar_nombre_liquidacion(nombre, centros_costo)

            if nombre and any(p in nombre.upper() for p in palabras_bloqueadas_nombre):
                nombre = None

            return rut, nombre

    # ======================================================
    # 2) Formato separado:
    # R.U.T.
    # TRABAJADOR
    # C.C.
    # 19.773.808-1
    # NOMBRE
    # ======================================================
    for i, linea in enumerate(lineas):
        up = quitar_acentos(linea).upper()

        if not up.startswith("R.U.T"):
            continue

        ventana = lineas[i : i + 10]

        for j, candidato in enumerate(ventana):
            match = rut_regex.search(candidato)

            if not match:
                continue

            rut = formatear_rut_chile(match.group())
            nombre = candidato[match.end() :].strip()

            if not nombre:
                indice_global = i + j + 1
                if indice_global < len(lineas):
                    nombre = lineas[indice_global].strip()

            nombre = limpiar_nombre_liquidacion(nombre, centros_costo)

            if nombre and any(p in nombre.upper() for p in palabras_bloqueadas_nombre):
                nombre = None

            return rut, nombre

    # ======================================================
    # 3) Fallback global:
    # Buscar todos los RUT del PDF y evitar RUT EMPRESA.
    # Normalmente:
    # - Primer RUT: empresa
    # - Segundo RUT: trabajador
    # ======================================================
    ruts = rut_regex.findall(texto)

    if not ruts:
        return None, None

    ruts_formateados = [formatear_rut_chile(r) for r in ruts if formatear_rut_chile(r)]

    if not ruts_formateados:
        return None, None

    # Si hay más de un RUT, normalmente el primero es empresa y el segundo trabajador
    rut_trabajador = (
        ruts_formateados[1] if len(ruts_formateados) >= 2 else ruts_formateados[0]
    )

    nombre = None

    clave_trabajador = rut_clave(rut_trabajador)

    for i, linea in enumerate(lineas):
        if clave_trabajador in rut_clave(linea):
            match = rut_regex.search(linea)
            if match:
                nombre = linea[match.end() :].strip()

            if not nombre and i + 1 < len(lineas):
                nombre = lineas[i + 1].strip()

            nombre = limpiar_nombre_liquidacion(nombre, centros_costo)

            if nombre and any(p in nombre.upper() for p in palabras_bloqueadas_nombre):
                nombre = None

            break

    return rut_trabajador, nombre


def limpiar_nombre_liquidacion(nombre: str, centros_costo: set):
    if not nombre:
        return None

    nombre = nombre.strip()

    # Separar por espacios
    partes = nombre.split()

    # Quitar centro de costo al final: EXT, ADM, etc.
    if partes and partes[-1].upper().replace(".", "") in centros_costo:
        partes = partes[:-1]

    nombre_limpio = " ".join(partes).strip()

    if not nombre_limpio:
        return None

    return nombre_limpio


# ==========================
# Extracción por página
# ==========================
def extraer_paginas_liquidaciones(archivo_subido):
    """
    Recibe un InMemoryUploadedFile y devuelve una lista por página:

    {
      "ok": True/False,
      "pagina": 1,
      "rut": "19.773.808-1",
      "nombre": "TORRES GUERRA EMILIO JAVIER",
      "mes": 5,
      "anio": 2026,
      "motivo": None,
      "pdf_bytes": b"..."
    }
    """
    resultados = []

    try:
        try:
            archivo_subido.seek(0)
        except Exception:
            pass

        contenido = archivo_subido.read()
        doc = fitz.open(stream=contenido, filetype="pdf")

    except Exception as e:
        logger.error(f"[extraer_paginas_liquidaciones] No se pudo abrir el PDF: {e}")
        return [
            {
                "ok": False,
                "pagina": None,
                "rut": None,
                "nombre": None,
                "mes": None,
                "anio": None,
                "motivo": f"No se pudo abrir el PDF: {e}",
                "pdf_bytes": b"",
            }
        ]

    num_paginas = doc.page_count

    for idx in range(num_paginas):
        pagina_num = idx + 1

        try:
            page = doc.load_page(idx)
            texto = page.get_text("text") or ""

            # Mes / año
            mes, anio = detectar_mes_anio(texto)

            # RUT / nombre
            rut, nombre = extraer_rut_y_nombre(texto)

            motivo = None
            ok = True

            if not rut:
                motivo = "No se pudo leer el RUT del trabajador en la página."
                ok = False

            # Sacar PDF de solo esa página
            single_doc = fitz.open()
            single_doc.insert_pdf(doc, from_page=idx, to_page=idx)

            buffer = BytesIO()
            single_doc.save(buffer)
            single_doc.close()

            pdf_bytes = buffer.getvalue()

            resultados.append(
                {
                    "ok": ok,
                    "pagina": pagina_num,
                    "rut": rut,
                    "nombre": nombre,
                    "mes": mes,
                    "anio": anio,
                    "motivo": motivo,
                    "pdf_bytes": pdf_bytes,
                }
            )

        except Exception as e:
            logger.error(
                f"[extraer_paginas_liquidaciones] Error en página {pagina_num}: {e}"
            )
            resultados.append(
                {
                    "ok": False,
                    "pagina": pagina_num,
                    "rut": None,
                    "nombre": None,
                    "mes": None,
                    "anio": None,
                    "motivo": f"Error al procesar la página: {e}",
                    "pdf_bytes": b"",
                }
            )

    doc.close()
    return resultados

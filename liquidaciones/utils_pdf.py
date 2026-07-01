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

    Devuelve:
    - 267246793
    - 25973603k
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


def normalizar_linea(texto: str) -> str:
    return quitar_acentos(texto).upper().strip()


def normalizar_compacto(texto: str) -> str:
    return normalizar_linea(texto).replace(" ", "")


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
    - REMUNERACIONES MES DE: JUNIO del 2026
    - REMUNERACIONES MES DE : JUNIO DEL 2026

    Devuelve:
    - (6, 2026)
    """
    if not texto:
        return None, None

    texto_normalizado = quitar_acentos(texto).upper()

    patron = re.compile(
        r"REMUNERACIONES\s+MES\s+DE\s*:?\s*([A-ZÑ]+)\s+DEL\s+(\d{4})",
        re.IGNORECASE,
    )

    match = patron.search(texto_normalizado)

    if not match:
        return None, None

    nombre_mes = match.group(1).strip().upper()
    anio_txt = match.group(2).strip()

    mes_num = MESES_NOMBRE.get(nombre_mes)

    if not mes_num:
        return None, None

    try:
        anio_num = int(anio_txt)
    except ValueError:
        return None, None

    return mes_num, anio_num


# ==========================
# Limpieza de nombre
# ==========================
def limpiar_nombre_liquidacion(nombre: str):
    if not nombre:
        return None

    nombre = str(nombre).strip()

    if not nombre:
        return None

    centros_costo = {
        "ADM",
        "EXT",
        "MATRIZ",
        "OPER",
        "OP",
        "TEC",
        "RRHH",
        "PM",
    }

    palabras_bloqueadas = [
        "R.U.T.",
        "R.U.T",
        "RUT",
        "TRABAJADOR",
        "C.C.",
        "C.C",
        "FECHA INGRESO",
        "TIPO DE CONTRATO",
        "CARGO FUNCIONARIO",
        "FECHA TERMINO CONTRATO",
        "A.F.P.",
        "AFP",
        "ISAPRE",
        "HABERES",
        "DESCUENTOS",
        "TOTAL",
        "DIAS",
        "HH",
        "IMPONIBLE",
        "TRIBUTABLE",
    ]

    nombre_compacto = normalizar_compacto(nombre)

    for palabra in palabras_bloqueadas:
        if palabra.replace(" ", "") in nombre_compacto:
            return None

    partes = nombre.split()

    # Quitar centro de costo si viene pegado al final del nombre
    # Ej: "TORRES GUERRA EMILIO JAVIER EXT"
    if partes and partes[-1].upper().replace(".", "") in centros_costo:
        partes = partes[:-1]

    nombre_limpio = " ".join(partes).strip()

    if not nombre_limpio:
        return None

    return nombre_limpio


def es_header_rut_trabajador(linea: str) -> bool:
    compacta = normalizar_compacto(linea)

    if compacta in {"R.U.T.", "R.U.T", "RUT", "RUT."}:
        return True

    if "R.U.T" in compacta and "TRABAJADOR" in compacta:
        return True

    if "RUTTRABAJADOR" in compacta:
        return True

    return False


def es_linea_trabajador(linea: str) -> bool:
    return normalizar_linea(linea) == "TRABAJADOR"


# ==========================
# RUT / Nombre trabajador
# ==========================
def extraer_rut_y_nombre(texto: str):
    """
    Extrae RUT y nombre del trabajador desde liquidaciones Nubox.

    Soporta formato nuevo:
        R.U.T.
        25.973.603-K
        TRABAJADOR
        ZAPATA HERNANDEZ EDGARDO JOSE
        C.C.
        ADM

    Soporta formato anterior:
        R.U.T.
        TRABAJADOR
        C.C.
        19.773.808-1
        TORRES GUERRA EMILIO JAVIER
        EXT

    Soporta formato en una línea:
        R.U.T. TRABAJADOR C.C.
        19.773.808-1 TORRES GUERRA EMILIO JAVIER EXT
    """
    if not texto:
        return None, None

    lineas = [linea.strip() for linea in texto.splitlines() if linea and linea.strip()]

    rut_regex = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-?[\dkK]\b")

    # ======================================================
    # 1) Buscar bloque de trabajador desde encabezado R.U.T.
    #    Este bloque soporta:
    #    - R.U.T. / RUT / TRABAJADOR / NOMBRE
    #    - R.U.T. / TRABAJADOR / C.C. / RUT / NOMBRE
    #    - R.U.T. TRABAJADOR C.C. / RUT NOMBRE
    # ======================================================
    for i, linea in enumerate(lineas):
        linea_norm = normalizar_linea(linea)

        # Evita tomar RUT EMPRESA
        if "EMPRESA" in linea_norm:
            continue

        if not es_header_rut_trabajador(linea):
            continue

        ventana_fin = min(i + 15, len(lineas))

        rut = None
        rut_index = None
        rut_match = None

        # Buscar el primer RUT dentro de la ventana del bloque
        for j in range(i, ventana_fin):
            match = rut_regex.search(lineas[j])
            if match:
                rut = formatear_rut_chile(match.group())
                rut_index = j
                rut_match = match
                break

        if not rut:
            continue

        nombre = None

        # Caso 1: nombre en la misma línea del RUT
        # Ej: 19.773.808-1 TORRES GUERRA EMILIO JAVIER EXT
        resto_misma_linea = lineas[rut_index][rut_match.end() :].strip()
        nombre = limpiar_nombre_liquidacion(resto_misma_linea)

        # Caso 2: formato nuevo:
        # R.U.T.
        # 25.973.603-K
        # TRABAJADOR
        # NOMBRE
        if not nombre:
            for k in range(rut_index + 1, min(rut_index + 8, len(lineas))):
                if es_linea_trabajador(lineas[k]):
                    if k + 1 < len(lineas):
                        nombre = limpiar_nombre_liquidacion(lineas[k + 1])
                    break

        # Caso 3: formato anterior:
        # R.U.T.
        # TRABAJADOR
        # C.C.
        # 19.773.808-1
        # NOMBRE
        if not nombre:
            for k in range(i, rut_index):
                if es_linea_trabajador(lineas[k]):
                    if rut_index + 1 < len(lineas):
                        nombre = limpiar_nombre_liquidacion(lineas[rut_index + 1])
                    break

        # Caso 4: último intento, línea siguiente al RUT
        if not nombre and rut_index + 1 < len(lineas):
            nombre = limpiar_nombre_liquidacion(lineas[rut_index + 1])

        return rut, nombre

    # ======================================================
    # 2) FALLBACK GLOBAL:
    # Buscar todos los RUT.
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

    if len(ruts_formateados) >= 2:
        rut_trabajador = ruts_formateados[1]
    else:
        rut_trabajador = ruts_formateados[0]

    nombre = None
    clave_trabajador = rut_clave(rut_trabajador)

    for i, linea in enumerate(lineas):
        if clave_trabajador in rut_clave(linea):
            match = rut_regex.search(linea)

            if match:
                nombre = linea[match.end() :].strip()

            if not nombre and i + 1 < len(lineas):
                nombre = lineas[i + 1].strip()

            nombre = limpiar_nombre_liquidacion(nombre)
            break

    return rut_trabajador, nombre


# ==========================
# Extracción por página
# ==========================
def extraer_paginas_liquidaciones(archivo_subido):
    """
    Recibe un InMemoryUploadedFile y devuelve una lista por página:

    {
      "ok": True/False,
      "pagina": 1,
      "rut": "25.973.603-K",
      "nombre": "ZAPATA HERNANDEZ EDGARDO JOSE",
      "mes": 6,
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

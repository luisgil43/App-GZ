import logging
import re
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
    => "267246793", siempre en minúscula para la k.
    """
    if not rut:
        return ""
    # deja solo dígitos y k/K
    limpio = re.sub(r"[^0-9kK]", "", str(rut))
    return limpio.lower()


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
    "SETIEMBRE": 9,  # por si acaso
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}


def detectar_mes_anio(texto: str):
    """
    Busca algo como:
      REMUNERACIONES MES DE: OCTUBRE del 2025
    y devuelve (10, 2025).
    """
    if not texto:
        return None, None

    patron = re.compile(
        r"REMUNERACIONES\s+MES\s+DE:\s+([A-ZÁÉÍÓÚÑ]+)\s+del\s+(\d{4})",
        re.IGNORECASE,
    )
    m = patron.search(texto)
    if not m:
        return None, None

    nombre_mes_raw = m.group(1)
    anio_txt = m.group(2)

    # normalizamos el nombre del mes (mayúsculas, sin acentos)
    nombre_mes = (
        nombre_mes_raw.upper()
        .replace("Á", "A")
        .replace("É", "E")
        .replace("Í", "I")
        .replace("Ó", "O")
        .replace("Ú", "U")
    )

    mes_num = MESES_NOMBRE.get(nombre_mes)
    if not mes_num:
        return None, None

    try:
        anio_num = int(anio_txt)
    except ValueError:
        return None, None

    return mes_num, anio_num


# ==========================
# Extracción por página
# ==========================
def extraer_paginas_liquidaciones(archivo_subido):
    """
    Recibe un InMemoryUploadedFile (el PDF que subes en el form) y devuelve
    una lista de diccionarios, uno por página, con:

    {
      "ok": True/False,
      "pagina": 1-based,
      "rut": "26.724.679-3",
      "nombre": "GIL MOYA LUIS ENRIQUE",
      "mes": 10,
      "anio": 2025,
      "motivo": None o texto de error,
      "pdf_bytes": b"...pdf con SOLO esa página..."
    }
    """
    resultados = []

    try:
        # por si el archivo ya fue leído en otro sitio
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
            texto = page.get_text("text")

            # --- mes / año ---
            mes, anio = detectar_mes_anio(texto)

            # --- RUT y nombre ---
            lineas = [l.strip() for l in texto.splitlines() if l.strip()]
            rut = None
            nombre = None
            motivo = None
            ok = False

            # 1️⃣ FORMATO NUEVO:
            #   R.U.T.
            #   TRABAJADOR
            #   C.C.
            #   25.973.603-K
            #   ZAPATA HERNANDEZ EDGARDO JOSE
            #   ADM
            for i, linea in enumerate(lineas):
                up = linea.upper()
                if up.startswith("R.U.T"):
                    # comprobamos que las siguientes líneas sean TRABAJADOR / C.C.
                    if i + 3 < len(lineas):
                        l1 = lineas[i + 1].strip().upper()
                        l2 = lineas[i + 2].strip().upper()
                        if "TRABAJADOR" in l1 and ("C.C." in l2 or "C.C" in l2):
                            rut_cand = lineas[i + 3].strip()
                            nombre_cand = lineas[i + 4].strip() if i + 4 < len(lineas) else ""

                            if rut_cand:
                                rut = rut_cand
                                nombre = nombre_cand or None
                                break

            # 2️⃣ FORMATO ANTIGUO (backup):
            #    "R.U.T. TRABAJADOR ..." todo en la misma línea y en la siguiente RUT+NOMBRE+CC
            if not rut:
                for i, linea in enumerate(lineas):
                    if linea.upper().startswith("R.U.T. TRABAJADOR"):
                        if i + 1 < len(lineas):
                            datos = lineas[i + 1]
                            partes = datos.split()
                            if partes:
                                rut = partes[0]
                                if len(partes) > 1:
                                    # normalmente el último token es el centro de costo: ADM, EXT, etc.
                                    if len(partes) >= 3:
                                        nombre = " ".join(partes[1:-1])
                                    else:
                                        nombre = partes[1]
                        break

            if not rut:
                motivo = "No se pudo leer el RUT del trabajador en la página."
                ok = False
            else:
                ok = True

            # --- sacar PDF de solo esa página ---
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
import logging
from contextlib import closing
from tempfile import TemporaryFile
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from botocore.exceptions import BotoCoreError, ClientError
from django.shortcuts import render
from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

logger = logging.getLogger(__name__)
MAX_ARCHIVO = 256 * 1024 * 1024
MAX_XML = 32 * 1024 * 1024
MAX_FILAS = 100
MAX_COLUMNAS = 30


class VistaPreviaNoDisponible(ValueError):
    pass


def _leer_hoja(archivo, hoja_elegida):
    with ZipFile(archivo) as paquete:
        xml = [entrada for entrada in paquete.infolist() if entrada.filename.lower().endswith((".xml", ".rels"))]
        if sum(entrada.file_size for entrada in xml) > MAX_XML:
            raise VistaPreviaNoDisponible("La estructura de este Excel es demasiado grande para la vista previa.")

    archivo.seek(0)
    libro = load_workbook(archivo, read_only=True, data_only=True, keep_links=False)
    try:
        hojas = [hoja.title for hoja in libro.worksheets if hoja.sheet_state == "visible"]
        if not hojas:
            raise VistaPreviaNoDisponible("Este Excel no tiene hojas visibles.")
        nombre = hoja_elegida if hoja_elegida in hojas else hojas[0]
        hoja = libro[nombre]
        filas = [
            [str(valor)[:500] if valor is not None else "" for valor in fila]
            for fila in hoja.iter_rows(
                max_row=min(hoja.max_row or MAX_FILAS, MAX_FILAS),
                max_col=min(hoja.max_column or MAX_COLUMNAS, MAX_COLUMNAS),
                values_only=True,
            )
        ]
        return hojas, nombre, filas
    finally:
        libro.close()


def vista_previa_excel(request, entregable, almacen, *, archivo_fuente=None, titulo=None):
    archivo_fuente = archivo_fuente if archivo_fuente is not None else entregable.archivo
    contexto = {
        "entregable": entregable, "titulo": titulo or entregable.titulo,
        "hojas": [], "filas": [],
    }
    try:
        respuesta = almacen.connection.meta.client.get_object(
            Bucket=almacen.bucket_name, Key=archivo_fuente.name,
        )
        with closing(respuesta["Body"]) as cuerpo, TemporaryFile() as archivo:
            if respuesta["ContentLength"] > MAX_ARCHIVO:
                raise VistaPreviaNoDisponible("Este Excel supera el tamaño que admite la vista previa.")
            total = 0
            for bloque in cuerpo.iter_chunks(chunk_size=1024 * 1024):
                total += len(bloque)
                if total > MAX_ARCHIVO:
                    raise VistaPreviaNoDisponible("Este Excel supera el tamaño que admite la vista previa.")
                archivo.write(bloque)
            hojas, hoja, filas = _leer_hoja(archivo, request.GET.get("hoja"))
            columnas = [get_column_letter(i) for i in range(1, len(filas[0]) + 1)] if filas else []
            contexto.update(hojas=hojas, hoja_actual=hoja, filas=filas, columnas=columnas)
    except VistaPreviaNoDisponible as error:
        contexto["error"] = str(error)
    except (BadZipFile, InvalidFileException, ParseError, XMLSyntaxError, KeyError, ValueError, TypeError, IndexError):
        contexto["error"] = "No se puede leer este archivo como un Excel válido."
    except (OSError, BotoCoreError, ClientError):
        logger.exception("Error preparando la vista previa del entregable %s", entregable.pk)
        contexto["error"] = "No se pudo cargar este Excel. Inténtalo nuevamente."
    return render(request, "operaciones/entregable_excel.html", contexto)

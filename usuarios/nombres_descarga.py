import os
import unicodedata


def limpiar_nombre_descarga(texto):
    texto = unicodedata.normalize("NFC", str(texto or ""))
    prohibidos = set('\\/:*?"<>|')
    texto = "".join(
        " " if c in prohibidos or unicodedata.category(c).startswith("C") else c
        for c in texto
    )
    return " ".join(texto.split()).strip(" .")


def nombre_corto_trabajador(trabajador):
    nombres = (getattr(trabajador, "first_name", "") or "").split()
    apellidos = (getattr(trabajador, "last_name", "") or "").split()
    nombre = " ".join(p for p in [
        nombres[0] if nombres else "",
        apellidos[0] if apellidos else "",
    ] if p)
    return limpiar_nombre_descarga(
        nombre or getattr(trabajador, "username", "") or "Trabajador"
    )


def nombre_documento_descarga(titulo, archivo, trabajador=None, extension_default=""):
    extension = os.path.splitext(str(archivo or ""))[1] or extension_default
    extension = "." + limpiar_nombre_descarga(extension.lstrip(".")) if extension else ""
    titulo = limpiar_nombre_descarga(titulo) or "Documento"
    if extension and titulo.lower().endswith(extension.lower()):
        titulo = titulo[:-len(extension)].rstrip()
    if trabajador is not None:
        titulo += " - " + nombre_corto_trabajador(trabajador)
    return titulo[:180].rstrip(" .") + extension

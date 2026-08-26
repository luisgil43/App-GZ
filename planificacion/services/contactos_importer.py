import hashlib
import re
import unicodedata
from datetime import date, datetime

import pandas as pd
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from operaciones.models import SitioMovil
from planificacion.models import (ContactoSitio, ImportacionContactosSitios,
                                  VersionContactoSitio)

# ============================================================
# CAMPOS DE LA BASE DE CONTACTOS
# ============================================================


CAMPOS_CONTACTO = [
    "region",
    "nombre_sitio",
    "propietario",
    "telefono",
    "correo",
    "fecha_informacion",
    "responsable",
    "observaciones",
    "accion",
]


# Si cambia alguno de estos campos,
# las reglas inteligentes de acceso deberán volver a analizarse.
CAMPOS_REANALISIS = {
    "propietario",
    "telefono",
    "correo",
    "responsable",
    "observaciones",
    "accion",
}


# ============================================================
# NORMALIZACIÓN GENERAL
# ============================================================


def normalizar_texto(value):
    """
    Convierte valores provenientes de pandas/Excel a texto limpio.

    Valores considerados vacíos:
    - None
    - NaN
    - "nan"
    - "none"
    - "null"
    """

    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = str(value).strip()

    if value.lower() in {
        "nan",
        "none",
        "null",
    }:
        return ""

    return value


def normalizar_clave(value):
    """
    Normalización para comparaciones.

    No modifica el valor almacenado.

    Ejemplos:
    "José Pérez" == "jose perez"
    "Correos - Confirmación" ~= "correos confirmacion"
    """

    value = normalizar_texto(value).lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        caracter for caracter in value if not unicodedata.combining(caracter)
    )

    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalizar_id(value):
    """
    Conserva el ID tal como viene desde la planilla.

    Ejemplo:
    01_001 permanece como 01_001.

    No transformamos este valor porque corresponde normalmente
    al ID Claro de SitioMovil.
    """

    value = normalizar_texto(value)

    if not value:
        return ""

    return value.strip()


def normalizar_telefono(value):
    """
    Normalización utilizada SOLO para comparación.

    Ejemplos:
    +56 9 1234 5678
    56912345678

    serán considerados el mismo teléfono.
    """

    value = normalizar_texto(value)

    if not value:
        return ""

    return re.sub(
        r"\D",
        "",
        value,
    )


def normalizar_correo(value):
    """
    Normalización utilizada SOLO para comparación.
    """

    value = normalizar_texto(value)

    if not value:
        return ""

    return value.lower().strip()


# ============================================================
# FECHAS
# ============================================================


def limpiar_fecha(value):
    """
    Convierte fechas provenientes de Excel a date.

    Soporta:
    - datetime
    - date
    - strings tipo 06-05-2024
    - strings tipo 06/05/2024
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    texto = normalizar_texto(value)

    if not texto:
        return None

    try:
        parsed = pd.to_datetime(
            texto,
            dayfirst=True,
            errors="coerce",
        )

        if not pd.isna(parsed):
            return parsed.date()

    except Exception:
        pass

    return None


# ============================================================
# MAPEO FLEXIBLE DE COLUMNAS
# ============================================================


def mapa_columnas(df):
    """
    Crea un mapa:

    encabezado normalizado -> encabezado real del Excel
    """

    resultado = {}

    for columna in df.columns:
        resultado[normalizar_clave(columna)] = columna

    return resultado


def obtener_columna(
    row,
    columnas_normalizadas,
    *nombres,
):
    """
    Permite aceptar distintas variantes de un encabezado.

    Ej:
    Teléfono / Telefono / Fono / Celular
    """

    for nombre in nombres:
        clave = normalizar_clave(nombre)

        columna_real = columnas_normalizadas.get(clave)

        if columna_real is not None:
            return row.get(columna_real)

    return None


# ============================================================
# LECTURA DE EXCEL
# ============================================================


def leer_excel_contactos(archivo):
    """
    Busca automáticamente la hoja que más se parezca
    a la base de contactos.

    La hoja esperada contiene aproximadamente:

    REGIÓN
    ID
    NOMBRE SITIO
    Propietario
    Teléfono
    Correo
    Fecha
    Responsable
    Observaciones
    ACCIÓN
    """

    xls = pd.ExcelFile(archivo)

    if not xls.sheet_names:
        raise ValueError("El archivo Excel no contiene hojas.")

    mejor_df = None
    mejor_hoja = None
    mejor_score = -1

    columnas_objetivo = {
        "region",
        "id",
        "nombre sitio",
        "propieatrio",
        "propietario",
        "telefono",
        "correo",
        "fecha",
        "responsable",
        "observaciones",
        "accion",
    }

    for hoja in xls.sheet_names:
        df = pd.read_excel(
            archivo,
            sheet_name=hoja,
        )

        columnas_norm = {normalizar_clave(columna) for columna in df.columns}

        score = len(columnas_norm.intersection(columnas_objetivo))

        if score > mejor_score:
            mejor_score = score
            mejor_df = df
            mejor_hoja = hoja

    if mejor_df is None:
        raise ValueError("No fue posible encontrar una hoja válida.")

    # Eliminamos solamente filas completamente vacías.
    mejor_df = mejor_df.dropna(how="all")

    return (
        mejor_df,
        mejor_hoja,
    )


# ============================================================
# CONVERTIR FILA EXCEL -> CONTACTO
# ============================================================


def fila_a_contacto(
    row,
    columnas_normalizadas,
):
    """
    Convierte una fila de la planilla de contactos
    al formato interno.

    No consulta ni modifica SitioMovil.
    """

    id_origen = normalizar_id(
        obtener_columna(
            row,
            columnas_normalizadas,
            "ID",
            "ID Claro",
            "ID Sitio",
            "ID Site",
        )
    )

    if not id_origen:
        return None

    return {
        "id_origen": id_origen,
        "region": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "REGIÓN",
                "Region",
            )
        ),
        "nombre_sitio": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "NOMBRE SITIO",
                "Nombre Sitio",
                "Nombre",
            )
        ),
        # La fuente original contiene el typo "Propieatrio".
        "propietario": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Propieatrio",
                "Propietario",
                "Propietaria",
            )
        ),
        "telefono": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Teléfono",
                "Telefono",
                "Fono",
                "Celular",
            )
        ),
        "correo": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Correo",
                "Email",
                "E-mail",
                "Mail",
            )
        ),
        "fecha_informacion": limpiar_fecha(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Fecha",
            )
        ),
        "responsable": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Responsable",
            )
        ),
        "observaciones": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "Observaciones",
                "Observación",
                "Observacion",
            )
        ),
        "accion": normalizar_texto(
            obtener_columna(
                row,
                columnas_normalizadas,
                "ACCIÓN",
                "ACCION",
                "Acción",
                "Accion",
            )
        ),
    }


# ============================================================
# VALIDAR / VINCULAR CONTRA SITIO MOVIL
# ============================================================


def buscar_sitio(data):
    """
    Busca un SitioMovil existente.

    IMPORTANTE:
    Esta función es ESTRICTAMENTE DE LECTURA.

    Nunca:
    - crea SitioMovil
    - actualiza SitioMovil
    - llama save() sobre SitioMovil
    - modifica ningún campo de SitioMovil

    El ID de la base del cliente, por ejemplo:

        01_001

    corresponde principalmente a:

        SitioMovil.id_claro

    Esa es por tanto la búsqueda prioritaria.
    """

    id_origen = normalizar_id(data.get("id_origen"))

    if not id_origen:
        return None, ""

    # ========================================================
    # 1. ID CLARO
    # ========================================================

    sitio = SitioMovil.objects.filter(id_claro__iexact=id_origen).first()

    if sitio:
        return (
            sitio,
            "id_claro",
        )

    # ========================================================
    # 2. ID SITES NEW
    # Fallback por compatibilidad con otras fuentes.
    # ========================================================

    sitio = SitioMovil.objects.filter(id_sites_new__iexact=id_origen).first()

    if sitio:
        return (
            sitio,
            "id_sites_new",
        )

    # ========================================================
    # 3. ID SITES
    # Último fallback.
    # ========================================================

    sitio = SitioMovil.objects.filter(id_sites__iexact=id_origen).first()

    if sitio:
        return (
            sitio,
            "id_sites",
        )

    return (
        None,
        "",
    )


# ============================================================
# FIRMA DEL CONTENIDO
# ============================================================


def generar_firma_contacto(data):
    """
    Genera un hash SHA256 del contenido relevante.

    Esto permitirá detectar posteriormente si el contenido
    que debe analizar el motor de acceso realmente cambió.
    """

    partes = [
        normalizar_clave(data.get("id_origen")),
        normalizar_clave(data.get("region")),
        normalizar_clave(data.get("nombre_sitio")),
        normalizar_clave(data.get("propietario")),
        normalizar_telefono(data.get("telefono")),
        normalizar_correo(data.get("correo")),
        str(data.get("fecha_informacion") or ""),
        normalizar_clave(data.get("responsable")),
        normalizar_clave(data.get("observaciones")),
        normalizar_clave(data.get("accion")),
    ]

    raw = "|".join(partes)

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# BUSCAR CONTACTO EXISTENTE
# ============================================================


def buscar_contacto_existente(
    data,
    sitio,
):
    """
    Busca un ContactoSitio existente sin fusionar
    accidentalmente personas distintas.

    Prioridades:

    1. ID + teléfono exacto
    2. ID + correo exacto
    3. ID + propietario + responsable
    4. ID + propietario

    Cuando el contacto todavía estaba sin vínculo y ahora
    encontramos el SitioMovil, permitimos recuperarlo para
    vincularlo en vez de crear un contacto duplicado.
    """

    qs = ContactoSitio.objects.filter(
        id_origen__iexact=data["id_origen"],
        activo=True,
    )

    if sitio:
        qs = qs.filter(Q(sitio=sitio) | Q(sitio__isnull=True))

    contactos = list(qs)

    telefono = normalizar_telefono(data.get("telefono"))

    correo = normalizar_correo(data.get("correo"))

    propietario = normalizar_clave(data.get("propietario"))

    responsable = normalizar_clave(data.get("responsable"))

    # ========================================================
    # TELÉFONO
    # ========================================================

    if telefono:
        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_telefono(contacto.telefono) == telefono
        ]

        if len(candidatos) == 1:
            return candidatos[0]

    # ========================================================
    # CORREO
    # ========================================================

    if correo:
        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_correo(contacto.correo) == correo
        ]

        if len(candidatos) == 1:
            return candidatos[0]

    # ========================================================
    # PROPIETARIO + RESPONSABLE
    # ========================================================

    if propietario and responsable:
        candidatos = [
            contacto
            for contacto in contactos
            if (
                normalizar_clave(contacto.propietario) == propietario
                and normalizar_clave(contacto.responsable) == responsable
            )
        ]

        if len(candidatos) == 1:
            return candidatos[0]

    # ========================================================
    # PROPIETARIO
    # ========================================================

    if propietario:
        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_clave(contacto.propietario) == propietario
        ]

        if len(candidatos) == 1:
            return candidatos[0]

    return None


# ============================================================
# DETECTAR CAMBIOS
# ============================================================


def detectar_cambios(
    contacto,
    data,
):
    """
    Importación NO DESTRUCTIVA.

    Regla fundamental:

    Si Excel viene vacío:
        mantener el dato anterior.

    Si Excel trae información:
        comparar y actualizar únicamente si cambió.
    """

    cambios = []

    for campo in CAMPOS_CONTACTO:
        nuevo = data.get(campo)

        # ====================================================
        # VACÍO NUNCA BORRA INFORMACIÓN
        # ====================================================

        if nuevo in (
            None,
            "",
        ):
            continue

        anterior = getattr(
            contacto,
            campo,
            None,
        )

        if campo == "fecha_informacion":
            iguales = anterior == nuevo

        else:
            iguales = normalizar_clave(anterior) == normalizar_clave(nuevo)

        if iguales:
            continue

        cambios.append(
            {
                "campo": campo,
                "antes": (anterior if anterior not in (None, "") else "—"),
                "despues": nuevo,
            }
        )

    return cambios


# ============================================================
# DATOS VISIBLES EN EL PREVIEW
# ============================================================


def construir_datos_preview(
    data,
    sitio=None,
    contacto=None,
):
    """
    Devuelve las 10 columnas originales de la planilla
    listas para mostrar en el preview.
    """

    return {
        "region": (data.get("region") or (contacto.region if contacto else "") or ""),
        "id_origen": (
            data.get("id_origen") or (contacto.id_origen if contacto else "") or ""
        ),
        "nombre_sitio": (
            data.get("nombre_sitio")
            or (contacto.nombre_sitio if contacto else "")
            or (sitio.nombre if sitio else "")
            or ""
        ),
        "propietario": (
            data.get("propietario") or (contacto.propietario if contacto else "") or ""
        ),
        "telefono": (
            data.get("telefono") or (contacto.telefono if contacto else "") or ""
        ),
        "correo": (data.get("correo") or (contacto.correo if contacto else "") or ""),
        "fecha_informacion": (
            data.get("fecha_informacion")
            or (contacto.fecha_informacion if contacto else None)
        ),
        "responsable": (
            data.get("responsable") or (contacto.responsable if contacto else "") or ""
        ),
        "observaciones": (
            data.get("observaciones")
            or (contacto.observaciones if contacto else "")
            or ""
        ),
        "accion": (data.get("accion") or (contacto.accion if contacto else "") or ""),
    }


# ============================================================
# GENERAR PREVIEW
# ============================================================


def generar_preview_contactos(df):
    """
    Analiza TODO el DataFrame.

    IMPORTANTE:
    Aquí NO existe límite de 300 registros.

    Si el Excel tiene 5.125 filas:
        se analizan las 5.125.

    El límite de 300 que existía era solamente del render
    de la vista Django.
    """

    columnas_normalizadas = mapa_columnas(df)

    preview = []
    errores = []

    resumen = {
        "total_filas": len(df),
        "nuevos": 0,
        "actualizados": 0,
        "sin_cambios": 0,
        "no_vinculados": 0,
        "errores": 0,
    }

    for index, row in df.iterrows():
        fila_excel = int(index) + 2

        data = fila_a_contacto(
            row,
            columnas_normalizadas,
        )

        # ====================================================
        # FILA SIN ID
        # ====================================================

        if not data:
            resumen["errores"] += 1

            errores.append(
                {
                    "fila": fila_excel,
                    "error": ("La fila no contiene " "un ID de sitio válido."),
                }
            )

            continue

        # ====================================================
        # VALIDACIÓN CONTRA SITIO MOVIL
        # SOLO LECTURA
        # ====================================================

        sitio, vinculo_por = buscar_sitio(data)

        if sitio is None:
            resumen["no_vinculados"] += 1

        # ====================================================
        # BUSCAR CONTACTO YA EXISTENTE
        # ====================================================

        contacto = buscar_contacto_existente(
            data,
            sitio,
        )

        # ====================================================
        # CONTACTO NUEVO
        # ====================================================

        if contacto is None:
            resumen["nuevos"] += 1

            datos_preview = construir_datos_preview(
                data=data,
                sitio=sitio,
            )

            preview.append(
                {
                    "fila": fila_excel,
                    "estado": "nuevo",
                    "contacto_id": None,
                    "sitio_id": (sitio.pk if sitio else None),
                    "vinculado": bool(sitio),
                    "vinculo_por": (vinculo_por),
                    # Las 10 columnas originales.
                    **datos_preview,
                    # Para un registro nuevo NO mostramos
                    # "antes — / nuevo valor".
                    "cambios": [],
                    # Datos originales usados al confirmar.
                    "data": data,
                }
            )

            continue

        # ====================================================
        # CONTACTO EXISTENTE
        # ====================================================

        cambios = detectar_cambios(
            contacto,
            data,
        )

        # Si antes estaba sin vincular pero ahora encontramos
        # el sitio, se actualiza SOLO ContactoSitio.sitio.
        #
        # NUNCA modificamos SitioMovil.
        cambio_vinculo = sitio is not None and contacto.sitio_id != sitio.pk

        if cambio_vinculo:
            cambios.append(
                {
                    "campo": "vinculo_sitio",
                    "antes": (
                        str(contacto.sitio) if contacto.sitio else "Sin vincular"
                    ),
                    "despues": str(sitio),
                }
            )

        if cambios:
            estado = "actualizar"

            resumen["actualizados"] += 1

        else:
            estado = "sin_cambios"

            resumen["sin_cambios"] += 1

        datos_preview = construir_datos_preview(
            data=data,
            sitio=sitio,
            contacto=contacto,
        )

        preview.append(
            {
                "fila": fila_excel,
                "estado": estado,
                "contacto_id": (contacto.pk),
                "sitio_id": (sitio.pk if sitio else contacto.sitio_id),
                "vinculado": bool(sitio or contacto.sitio_id),
                "vinculo_por": (vinculo_por),
                # Las 10 columnas originales.
                **datos_preview,
                "cambios": cambios,
                "data": data,
            }
        )

    return (
        preview,
        resumen,
        errores,
    )


# ============================================================
# CREAR SNAPSHOT / VERSIÓN
# ============================================================


def crear_version_contacto(
    contacto,
    importacion,
):
    """
    Conserva una fotografía del estado del contacto.

    No toca SitioMovil.
    """

    VersionContactoSitio.objects.create(
        contacto=contacto,
        sitio=contacto.sitio,
        id_origen=(contacto.id_origen),
        region=(contacto.region),
        nombre_sitio=(contacto.nombre_sitio),
        propietario=(contacto.propietario),
        telefono=(contacto.telefono),
        correo=(contacto.correo),
        responsable=(contacto.responsable),
        observaciones=(contacto.observaciones),
        accion=(contacto.accion),
        prioridad_contacto=(contacto.prioridad_contacto),
        tipo_contacto=(contacto.tipo_contacto),
        fecha_fuente=(contacto.fecha_informacion),
        importacion=importacion,
    )


# ============================================================
# CONSTRUIR FIRMA DEL CONTACTO EXISTENTE
# ============================================================


def construir_data_firma_desde_contacto(
    contacto,
):
    return {
        "id_origen": (contacto.id_origen),
        "region": (contacto.region),
        "nombre_sitio": (contacto.nombre_sitio),
        "propietario": (contacto.propietario),
        "telefono": (contacto.telefono),
        "correo": (contacto.correo),
        "fecha_informacion": (contacto.fecha_informacion),
        "responsable": (contacto.responsable),
        "observaciones": (contacto.observaciones),
        "accion": (contacto.accion),
    }


# ============================================================
# APLICAR IMPORTACIÓN
# ============================================================


@transaction.atomic
def aplicar_importacion_contactos(
    preview,
    user,
    nombre_archivo="",
):
    """
    Aplica una importación previamente revisada.

    IMPORTANTE:

    SitioMovil es SOLO DE LECTURA.

    Este método únicamente puede modificar:

    - ContactoSitio
    - VersionContactoSitio
    - ImportacionContactosSitios

    Nunca modifica operaciones.SitioMovil.

    OPTIMIZACIÓN
    ==========================================================

    Para evitar miles de consultas individuales:

    - todos los SitioMovil necesarios se cargan previamente;
    - todos los ContactoSitio existentes que serán actualizados
      se cargan previamente con select_for_update();
    - dentro del loop principal ya no se realizan búsquedas
      individuales de esos objetos.
    """

    # ========================================================
    # CREAR AUDITORÍA DE IMPORTACIÓN
    # ========================================================

    importacion = ImportacionContactosSitios.objects.create(
        nombre_archivo=nombre_archivo,
        estado="preview",
        creado_por=user,
        total_filas=len(preview),
    )

    resultado = {
        "creados": 0,
        "actualizados": 0,
        "sin_cambios": 0,
        "no_vinculados": 0,
    }

    try:

        # ====================================================
        # NORMALIZAR IDS NECESARIOS
        # ====================================================

        sitio_ids = set()

        contacto_ids = set()

        for item in preview:

            sitio_id = item.get(
                "sitio_id",
            )

            if sitio_id:

                try:
                    sitio_ids.add(int(sitio_id))

                except (
                    TypeError,
                    ValueError,
                ):
                    pass

            contacto_id = item.get(
                "contacto_id",
            )

            if contacto_id:

                try:
                    contacto_ids.add(int(contacto_id))

                except (
                    TypeError,
                    ValueError,
                ):
                    pass

        # ====================================================
        # CARGAR SITIOS EN UNA SOLA CONSULTA
        # ====================================================
        #
        # SitioMovil continúa siendo estrictamente
        # SOLO DE LECTURA.
        # ====================================================

        if sitio_ids:

            sitios_por_id = SitioMovil.objects.in_bulk(
                sitio_ids,
            )

        else:

            sitios_por_id = {}

        # ====================================================
        # CARGAR CONTACTOS EXISTENTES EN UNA SOLA CONSULTA
        # ====================================================
        #
        # Conservamos select_for_update porque seguimos dentro
        # de transaction.atomic.
        #
        # Así protegemos contra modificaciones concurrentes,
        # pero evitamos hacer un SELECT por cada fila.
        # ====================================================

        if contacto_ids:

            contactos_existentes = ContactoSitio.objects.select_for_update().filter(
                pk__in=contacto_ids,
            )

            contactos_por_id = {
                contacto.pk: contacto for contacto in contactos_existentes
            }

        else:

            contactos_por_id = {}

        # ====================================================
        # PROCESAR PREVIEW
        # ====================================================

        for item in preview:

            data = item.get("data") or {}

            estado = item.get("estado")

            sitio_id = item.get("sitio_id")

            sitio = None

            # =================================================
            # SITIO MOVIL: SOLO LECTURA DESDE MEMORIA
            # =================================================

            if sitio_id:

                try:

                    sitio = sitios_por_id.get(int(sitio_id))

                except (
                    TypeError,
                    ValueError,
                ):

                    sitio = None

            if sitio is None:

                resultado["no_vinculados"] += 1

            # =================================================
            # CONTACTO NUEVO
            # =================================================

            if estado == "nuevo":

                contacto = ContactoSitio(
                    sitio=sitio,
                    id_origen=data["id_origen"],
                    creado_por=user,
                    actualizado_por=user,
                )

                for campo in CAMPOS_CONTACTO:

                    valor = data.get(
                        campo,
                    )

                    # =========================================
                    # VACÍO PERMANECE VACÍO AL CREAR
                    # =========================================

                    if valor in (
                        None,
                        "",
                    ):
                        continue

                    setattr(
                        contacto,
                        campo,
                        valor,
                    )

                contacto.firma_contenido = generar_firma_contacto(data)

                contacto.requiere_reanalisis = True

                contacto.save()

                # =============================================
                # SNAPSHOT INICIAL
                # =============================================

                crear_version_contacto(
                    contacto,
                    importacion,
                )

                resultado["creados"] += 1

                continue

            # =================================================
            # SIN CAMBIOS
            # =================================================

            if estado == "sin_cambios":

                resultado["sin_cambios"] += 1

                continue

            # =================================================
            # ACTUALIZAR CONTACTO
            # =================================================

            contacto_id = item.get(
                "contacto_id",
            )

            if not contacto_id:

                continue

            try:

                contacto_id = int(contacto_id)

            except (
                TypeError,
                ValueError,
            ):

                continue

            contacto = contactos_por_id.get(contacto_id)

            if contacto is None:

                continue

            # =================================================
            # SNAPSHOT DEL ESTADO ANTERIOR
            # =================================================

            crear_version_contacto(
                contacto,
                importacion,
            )

            hubo_cambio = False

            hubo_cambio_reanalizable = False

            # =================================================
            # VINCULACIÓN
            #
            # Solo cambia ContactoSitio.sitio.
            # SitioMovil permanece intacto.
            # =================================================

            if sitio and contacto.sitio_id != sitio.pk:

                contacto.sitio = sitio

                hubo_cambio = True

            # =================================================
            # CAMPOS DEL CONTACTO
            # =================================================

            for campo in CAMPOS_CONTACTO:

                nuevo = data.get(
                    campo,
                )

                # =============================================
                # IMPORTACIÓN NO DESTRUCTIVA
                # =============================================

                if nuevo in (
                    None,
                    "",
                ):
                    continue

                anterior = getattr(
                    contacto,
                    campo,
                    None,
                )

                if campo == "fecha_informacion":

                    iguales = anterior == nuevo

                else:

                    iguales = normalizar_clave(anterior) == normalizar_clave(nuevo)

                if iguales:

                    continue

                setattr(
                    contacto,
                    campo,
                    nuevo,
                )

                hubo_cambio = True

                if campo in CAMPOS_REANALISIS:

                    hubo_cambio_reanalizable = True

            # =================================================
            # GUARDAR CONTACTO
            # =================================================

            if hubo_cambio:

                contacto.actualizado_por = user

                if hubo_cambio_reanalizable:

                    contacto.requiere_reanalisis = True

                contacto.firma_contenido = generar_firma_contacto(
                    construir_data_firma_desde_contacto(contacto)
                )

                contacto.save()

                resultado["actualizados"] += 1

            else:

                resultado["sin_cambios"] += 1

        # =====================================================
        # FINALIZAR AUDITORÍA DE IMPORTACIÓN
        # =====================================================

        importacion.estado = "aplicada"

        importacion.aplicado_en = timezone.now()

        importacion.nuevos = resultado["creados"]

        importacion.actualizados = resultado["actualizados"]

        importacion.sin_cambios = resultado["sin_cambios"]

        importacion.no_vinculados = resultado["no_vinculados"]

        importacion.save(
            update_fields=[
                "estado",
                "aplicado_en",
                "nuevos",
                "actualizados",
                "sin_cambios",
                "no_vinculados",
            ]
        )

        return {
            **resultado,
            "importacion_id": importacion.pk,
        }

    except Exception as exc:

        # =====================================================
        # IMPORTANTE
        # =====================================================
        #
        # Como estamos dentro de transaction.atomic,
        # cualquier error provoca rollback de toda la
        # importación.
        # =====================================================

        importacion.estado = "error"

        importacion.observaciones = str(exc)

        importacion.save(
            update_fields=[
                "estado",
                "observaciones",
            ]
        )

        raise

import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from operaciones.models import SitioMovil
from planificacion.models import (ContactoSitio, FilaImportacionContacto,
                                  ImportacionContactosSitios,
                                  VersionContactoSitio)

# ============================================================
# CONFIGURACIÓN
# ============================================================


TAMANO_LOTE_ANALISIS = 300
TAMANO_LOTE_APLICACION = 250


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
    value = normalizar_texto(
        value,
    ).lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        caracter
        for caracter in value
        if not unicodedata.combining(
            caracter,
        )
    )

    value = value.replace(
        "_",
        " ",
    )

    value = value.replace(
        "-",
        " ",
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalizar_id(value):
    value = normalizar_texto(
        value,
    )

    if not value:
        return ""

    return value.strip()


def normalizar_id_busqueda(value):
    return normalizar_id(
        value,
    ).lower()


def normalizar_telefono(value):
    value = normalizar_texto(
        value,
    )

    if not value:
        return ""

    return re.sub(
        r"\D",
        "",
        value,
    )


def normalizar_correo(value):
    value = normalizar_texto(
        value,
    )

    if not value:
        return ""

    return value.lower().strip()


# ============================================================
# FECHAS
# ============================================================


def limpiar_fecha(value):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(
        value,
        datetime,
    ):
        return value.date()

    if isinstance(
        value,
        date,
    ):
        return value

    texto = normalizar_texto(
        value,
    )

    if not texto:
        return None

    # ========================================================
    # FORMATOS CONOCIDOS
    # ========================================================

    formatos = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
    ]

    for formato in formatos:

        try:
            return datetime.strptime(
                texto,
                formato,
            ).date()

        except ValueError:
            continue

    # ========================================================
    # FALLBACK
    # ========================================================

    try:
        parsed = pd.to_datetime(
            texto,
            errors="coerce",
        )

        if not pd.isna(
            parsed,
        ):
            return parsed.date()

    except Exception:
        pass

    return None


# ============================================================
# MAPEO FLEXIBLE DE COLUMNAS
# ============================================================


def mapa_columnas(df):
    resultado = {}

    for columna in df.columns:

        resultado[
            normalizar_clave(
                columna,
            )
        ] = columna

    return resultado


def obtener_columna(
    row,
    columnas_normalizadas,
    *nombres,
):
    for nombre in nombres:

        clave = normalizar_clave(
            nombre,
        )

        columna_real = columnas_normalizadas.get(
            clave,
        )

        if columna_real is not None:

            return row.get(
                columna_real,
            )

    return None


# ============================================================
# LECTURA DE EXCEL
# ============================================================


def leer_excel_contactos(
    archivo,
):
    """
    Primero inspecciona solamente los encabezados.

    Después carga únicamente la hoja ganadora.

    Esto evita cargar completamente todas las hojas del Excel
    simultáneamente.
    """

    xls = pd.ExcelFile(
        archivo,
        engine="openpyxl",
    )

    if not xls.sheet_names:

        raise ValueError("El archivo Excel no contiene hojas.")

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

    # ========================================================
    # INSPECCIONAR SOLAMENTE ENCABEZADOS
    # ========================================================

    for hoja in xls.sheet_names:

        df_encabezado = pd.read_excel(
            xls,
            sheet_name=hoja,
            nrows=0,
        )

        columnas_norm = {
            normalizar_clave(
                columna,
            )
            for columna in df_encabezado.columns
        }

        score = len(
            columnas_norm.intersection(
                columnas_objetivo,
            )
        )

        if score > mejor_score:

            mejor_score = score

            mejor_hoja = hoja

    if mejor_hoja is None:

        raise ValueError("No fue posible encontrar una hoja válida.")

    # ========================================================
    # CARGAR SOLAMENTE LA HOJA CORRECTA
    # ========================================================

    mejor_df = pd.read_excel(
        xls,
        sheet_name=mejor_hoja,
    )

    mejor_df = mejor_df.dropna(
        how="all",
    )

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
# FIRMA DEL CONTENIDO
# ============================================================


def generar_firma_contacto(
    data,
):
    partes = [
        normalizar_clave(
            data.get(
                "id_origen",
            )
        ),
        normalizar_clave(
            data.get(
                "region",
            )
        ),
        normalizar_clave(
            data.get(
                "nombre_sitio",
            )
        ),
        normalizar_clave(
            data.get(
                "propietario",
            )
        ),
        normalizar_telefono(
            data.get(
                "telefono",
            )
        ),
        normalizar_correo(
            data.get(
                "correo",
            )
        ),
        str(
            data.get(
                "fecha_informacion",
            )
            or ""
        ),
        normalizar_clave(
            data.get(
                "responsable",
            )
        ),
        normalizar_clave(
            data.get(
                "observaciones",
            )
        ),
        normalizar_clave(
            data.get(
                "accion",
            )
        ),
    ]

    raw = "|".join(
        partes,
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8",
        )
    ).hexdigest()


# ============================================================
# DETECTAR CAMBIOS
# ============================================================


def detectar_cambios(
    contacto,
    data,
):
    cambios = []

    for campo in CAMPOS_CONTACTO:

        nuevo = data.get(
            campo,
        )

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

            iguales = normalizar_clave(
                anterior,
            ) == normalizar_clave(
                nuevo,
            )

        if iguales:
            continue

        cambios.append(
            {
                "campo": campo,
                "antes": (
                    anterior
                    if anterior
                    not in (
                        None,
                        "",
                    )
                    else "—"
                ),
                "despues": nuevo,
            }
        )

    return cambios


# ============================================================
# DATOS DESDE FILA TEMPORAL
# ============================================================


def construir_data_desde_fila_importacion(
    fila,
):
    return {
        "id_origen": fila.id_origen,
        "region": fila.region,
        "nombre_sitio": fila.nombre_sitio,
        "propietario": fila.propietario,
        "telefono": fila.telefono,
        "correo": fila.correo,
        "fecha_informacion": fila.fecha_informacion,
        "responsable": fila.responsable,
        "observaciones": fila.observaciones,
        "accion": fila.accion,
    }


# ============================================================
# DATOS DESDE CONTACTO
# ============================================================


def construir_data_firma_desde_contacto(
    contacto,
):
    return {
        "id_origen": contacto.id_origen,
        "region": contacto.region,
        "nombre_sitio": contacto.nombre_sitio,
        "propietario": contacto.propietario,
        "telefono": contacto.telefono,
        "correo": contacto.correo,
        "fecha_informacion": contacto.fecha_informacion,
        "responsable": contacto.responsable,
        "observaciones": contacto.observaciones,
        "accion": contacto.accion,
    }


# ============================================================
# MAPA DE SITIOS PARA UN LOTE
# ============================================================


def _cargar_sitios_lote(
    ids_origen,
):
    ids_normalizados = {
        normalizar_id_busqueda(
            valor,
        )
        for valor in ids_origen
        if normalizar_id_busqueda(
            valor,
        )
    }

    if not ids_normalizados:

        return {
            "id_claro": {},
            "id_sites_new": {},
            "id_sites": {},
        }

    queryset = SitioMovil.objects.annotate(
        id_claro_normalizado=Lower(
            "id_claro",
        ),
        id_sites_new_normalizado=Lower(
            "id_sites_new",
        ),
        id_sites_normalizado=Lower(
            "id_sites",
        ),
    ).filter(
        Q(
            id_claro_normalizado__in=ids_normalizados,
        )
        | Q(
            id_sites_new_normalizado__in=ids_normalizados,
        )
        | Q(
            id_sites_normalizado__in=ids_normalizados,
        )
    )

    mapa_id_claro = {}

    mapa_id_sites_new = {}

    mapa_id_sites = {}

    for sitio in queryset.iterator(
        chunk_size=TAMANO_LOTE_ANALISIS,
    ):

        id_claro = normalizar_id_busqueda(
            sitio.id_claro,
        )

        id_sites_new = normalizar_id_busqueda(
            sitio.id_sites_new,
        )

        id_sites = normalizar_id_busqueda(
            sitio.id_sites,
        )

        if id_claro:

            mapa_id_claro.setdefault(
                id_claro,
                sitio,
            )

        if id_sites_new:

            mapa_id_sites_new.setdefault(
                id_sites_new,
                sitio,
            )

        if id_sites:

            mapa_id_sites.setdefault(
                id_sites,
                sitio,
            )

    return {
        "id_claro": mapa_id_claro,
        "id_sites_new": mapa_id_sites_new,
        "id_sites": mapa_id_sites,
    }


# ============================================================
# BUSCAR SITIO EN MAPA
# ============================================================


def _buscar_sitio_en_mapas(
    data,
    mapas_sitios,
):
    id_origen = normalizar_id_busqueda(
        data.get(
            "id_origen",
        )
    )

    if not id_origen:

        return (
            None,
            "",
        )

    sitio = mapas_sitios["id_claro"].get(
        id_origen,
    )

    if sitio:

        return (
            sitio,
            "id_claro",
        )

    sitio = mapas_sitios["id_sites_new"].get(
        id_origen,
    )

    if sitio:

        return (
            sitio,
            "id_sites_new",
        )

    sitio = mapas_sitios["id_sites"].get(
        id_origen,
    )

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
# CARGAR CONTACTOS PARA UN LOTE
# ============================================================


def _cargar_contactos_lote(
    ids_origen,
):
    ids_normalizados = {
        normalizar_id_busqueda(
            valor,
        )
        for valor in ids_origen
        if normalizar_id_busqueda(
            valor,
        )
    }

    if not ids_normalizados:

        return {}

    queryset = (
        ContactoSitio.objects.filter(
            activo=True,
        )
        .annotate(
            id_origen_normalizado=Lower(
                "id_origen",
            )
        )
        .filter(
            id_origen_normalizado__in=ids_normalizados,
        )
        .select_related(
            "sitio",
        )
    )

    resultado = defaultdict(
        list,
    )

    for contacto in queryset.iterator(
        chunk_size=TAMANO_LOTE_ANALISIS,
    ):

        clave = normalizar_id_busqueda(
            contacto.id_origen,
        )

        resultado[clave].append(
            contacto,
        )

    return resultado


# ============================================================
# BUSCAR CONTACTO EXISTENTE EN MEMORIA
# ============================================================


def _buscar_contacto_existente_en_lista(
    *,
    data,
    sitio,
    contactos,
):
    contactos = list(contactos or [])

    if sitio:

        contactos = [
            contacto
            for contacto in contactos
            if (contacto.sitio_id == sitio.pk or contacto.sitio_id is None)
        ]

    telefono = normalizar_telefono(
        data.get(
            "telefono",
        )
    )

    correo = normalizar_correo(
        data.get(
            "correo",
        )
    )

    propietario = normalizar_clave(
        data.get(
            "propietario",
        )
    )

    responsable = normalizar_clave(
        data.get(
            "responsable",
        )
    )

    if telefono:

        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_telefono(
                contacto.telefono,
            )
            == telefono
        ]

        if (
            len(
                candidatos,
            )
            == 1
        ):

            return candidatos[0]

    if correo:

        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_correo(
                contacto.correo,
            )
            == correo
        ]

        if (
            len(
                candidatos,
            )
            == 1
        ):

            return candidatos[0]

    if propietario and responsable:

        candidatos = [
            contacto
            for contacto in contactos
            if (
                normalizar_clave(
                    contacto.propietario,
                )
                == propietario
                and normalizar_clave(
                    contacto.responsable,
                )
                == responsable
            )
        ]

        if (
            len(
                candidatos,
            )
            == 1
        ):

            return candidatos[0]

    if propietario:

        candidatos = [
            contacto
            for contacto in contactos
            if normalizar_clave(
                contacto.propietario,
            )
            == propietario
        ]

        if (
            len(
                candidatos,
            )
            == 1
        ):

            return candidatos[0]

    return None


# ============================================================
# GENERAR PREVIEW PERSISTENTE
# ============================================================


def generar_preview_contactos(
    df,
    importacion,
):
    """
    Analiza el Excel por lotes.

    NUNCA construye una lista gigante de preview en RAM.

    Cada lote se procesa y se guarda inmediatamente en
    FilaImportacionContacto.

    El preview visual posteriormente consulta solamente
    100 filas desde PostgreSQL.
    """

    columnas_normalizadas = mapa_columnas(
        df,
    )

    total_filas = len(
        df,
    )

    resumen = {
        "total_filas": total_filas,
        "nuevos": 0,
        "actualizados": 0,
        "sin_cambios": 0,
        "no_vinculados": 0,
        "errores": 0,
    }

    # En caso de reintento sobre la misma importación.
    importacion.filas_preview.all().delete()

    # ========================================================
    # PROCESAR DATAFRAME POR BLOQUES
    # ========================================================

    for inicio in range(
        0,
        total_filas,
        TAMANO_LOTE_ANALISIS,
    ):

        fin = min(
            inicio + TAMANO_LOTE_ANALISIS,
            total_filas,
        )

        df_lote = df.iloc[inicio:fin]

        filas_preparadas = []

        ids_origen = []

        # ====================================================
        # CONVERTIR FILAS EXCEL
        # ====================================================

        for index, row in df_lote.iterrows():

            fila_excel = (
                int(
                    index,
                )
                + 2
            )

            data = fila_a_contacto(
                row,
                columnas_normalizadas,
            )

            filas_preparadas.append(
                {
                    "fila_excel": fila_excel,
                    "data": data,
                }
            )

            if data:

                ids_origen.append(data["id_origen"])

        # ====================================================
        # PRECARGAR DB SOLAMENTE PARA ESTE LOTE
        # ====================================================

        mapas_sitios = _cargar_sitios_lote(
            ids_origen,
        )

        contactos_por_id_origen = _cargar_contactos_lote(
            ids_origen,
        )

        filas_bd = []

        # ====================================================
        # ANALIZAR FILAS
        # ====================================================

        for preparada in filas_preparadas:

            fila_excel = preparada["fila_excel"]

            data = preparada["data"]

            # =================================================
            # ERROR: SIN ID
            # =================================================

            if not data:

                resumen["errores"] += 1

                filas_bd.append(
                    FilaImportacionContacto(
                        importacion=importacion,
                        numero_fila=fila_excel,
                        estado="error",
                        error=("La fila no contiene " "un ID de sitio válido."),
                    )
                )

                continue

            # =================================================
            # SITIO
            # =================================================

            (
                sitio,
                vinculo_por,
            ) = _buscar_sitio_en_mapas(
                data,
                mapas_sitios,
            )

            if sitio is None:

                resumen["no_vinculados"] += 1

            # =================================================
            # CONTACTO EXISTENTE
            # =================================================

            clave_contacto = normalizar_id_busqueda(data["id_origen"])

            contacto = _buscar_contacto_existente_en_lista(
                data=data,
                sitio=sitio,
                contactos=(
                    contactos_por_id_origen.get(
                        clave_contacto,
                        [],
                    )
                ),
            )

            # =================================================
            # NUEVO
            # =================================================

            if contacto is None:

                estado = "nuevo"

                cambios = []

                resumen["nuevos"] += 1

            # =================================================
            # EXISTENTE
            # =================================================

            else:

                cambios = detectar_cambios(
                    contacto,
                    data,
                )

                cambio_vinculo = sitio is not None and contacto.sitio_id != sitio.pk

                if cambio_vinculo:

                    cambios.append(
                        {
                            "campo": "vinculo_sitio",
                            "antes": (
                                str(
                                    contacto.sitio,
                                )
                                if contacto.sitio
                                else "Sin vincular"
                            ),
                            "despues": str(
                                sitio,
                            ),
                        }
                    )

                if cambios:

                    estado = "actualizar"

                    resumen["actualizados"] += 1

                else:

                    estado = "sin_cambios"

                    resumen["sin_cambios"] += 1

            # =================================================
            # DATOS VISIBLES
            # =================================================

            nombre_sitio = (
                data.get(
                    "nombre_sitio",
                )
                or (contacto.nombre_sitio if contacto else "")
                or (sitio.nombre if sitio else "")
                or ""
            )

            propietario = (
                data.get(
                    "propietario",
                )
                or (contacto.propietario if contacto else "")
                or ""
            )

            telefono = (
                data.get(
                    "telefono",
                )
                or (contacto.telefono if contacto else "")
                or ""
            )

            correo = (
                data.get(
                    "correo",
                )
                or (contacto.correo if contacto else "")
                or ""
            )

            fecha_informacion = data.get(
                "fecha_informacion",
            ) or (contacto.fecha_informacion if contacto else None)

            responsable = (
                data.get(
                    "responsable",
                )
                or (contacto.responsable if contacto else "")
                or ""
            )

            observaciones = (
                data.get(
                    "observaciones",
                )
                or (contacto.observaciones if contacto else "")
                or ""
            )

            accion = (
                data.get(
                    "accion",
                )
                or (contacto.accion if contacto else "")
                or ""
            )

            # =================================================
            # FILA TEMPORAL
            # =================================================

            filas_bd.append(
                FilaImportacionContacto(
                    importacion=importacion,
                    numero_fila=fila_excel,
                    estado=estado,
                    sitio=sitio,
                    contacto=contacto,
                    vinculado=bool(sitio or (contacto and contacto.sitio_id)),
                    vinculo_por=vinculo_por,
                    id_origen=data["id_origen"],
                    region=(
                        data.get(
                            "region",
                        )
                        or (contacto.region if contacto else "")
                        or ""
                    ),
                    nombre_sitio=nombre_sitio,
                    propietario=propietario,
                    telefono=telefono,
                    correo=correo,
                    fecha_informacion=fecha_informacion,
                    responsable=responsable,
                    observaciones=observaciones,
                    accion=accion,
                    cambios=cambios,
                )
            )

        # ====================================================
        # INSERTAR SOLAMENTE EL LOTE ACTUAL
        # ====================================================

        if filas_bd:

            FilaImportacionContacto.objects.bulk_create(
                filas_bd,
                batch_size=TAMANO_LOTE_ANALISIS,
            )

        del filas_preparadas
        del filas_bd
        del mapas_sitios
        del contactos_por_id_origen
        del df_lote

    # ========================================================
    # ACTUALIZAR CABECERA DE IMPORTACIÓN
    # ========================================================

    importacion.total_filas = resumen["total_filas"]

    importacion.nuevos = resumen["nuevos"]

    importacion.actualizados = resumen["actualizados"]

    importacion.sin_cambios = resumen["sin_cambios"]

    importacion.no_vinculados = resumen["no_vinculados"]

    importacion.errores = resumen["errores"]

    importacion.save(
        update_fields=[
            "total_filas",
            "nuevos",
            "actualizados",
            "sin_cambios",
            "no_vinculados",
            "errores",
        ]
    )

    return resumen


# ============================================================
# CREAR SNAPSHOT EN MEMORIA
# ============================================================


def _construir_version_contacto(
    *,
    contacto,
    importacion,
):
    return VersionContactoSitio(
        contacto=contacto,
        sitio=contacto.sitio,
        id_origen=contacto.id_origen,
        region=contacto.region,
        nombre_sitio=contacto.nombre_sitio,
        propietario=contacto.propietario,
        telefono=contacto.telefono,
        correo=contacto.correo,
        responsable=contacto.responsable,
        observaciones=contacto.observaciones,
        accion=contacto.accion,
        prioridad_contacto=contacto.prioridad_contacto,
        tipo_contacto=contacto.tipo_contacto,
        fecha_fuente=contacto.fecha_informacion,
        importacion=importacion,
    )


# ============================================================
# APLICAR UN LOTE
# ============================================================


def _aplicar_lote_contactos(
    *,
    filas,
    importacion,
    user,
    resultado,
):
    filas = list(
        filas,
    )

    if not filas:
        return

    sitio_ids = {fila.sitio_id for fila in filas if fila.sitio_id}

    contacto_ids = {fila.contacto_id for fila in filas if fila.contacto_id}

    sitios_por_id = (
        SitioMovil.objects.in_bulk(
            sitio_ids,
        )
        if sitio_ids
        else {}
    )

    contactos_por_id = {}

    if contacto_ids:

        contactos_queryset = ContactoSitio.objects.select_for_update().filter(
            pk__in=contacto_ids,
        )

        contactos_por_id = {contacto.pk: contacto for contacto in contactos_queryset}

    nuevos_contactos = []

    filas_nuevos_contactos = []

    versiones_anteriores = []

    contactos_actualizados = []

    ahora = timezone.now()

    # ========================================================
    # PREPARAR OPERACIONES
    # ========================================================

    for fila in filas:

        if fila.estado == "error":
            continue

        sitio = (
            sitios_por_id.get(
                fila.sitio_id,
            )
            if fila.sitio_id
            else None
        )

        if sitio is None:

            resultado["no_vinculados"] += 1

        data = construir_data_desde_fila_importacion(
            fila,
        )

        # ====================================================
        # NUEVO
        # ====================================================

        if fila.estado == "nuevo":

            contacto = ContactoSitio(
                sitio=sitio,
                id_origen=data["id_origen"],
                creado_por=user,
                actualizado_por=user,
                firma_contenido=(
                    generar_firma_contacto(
                        data,
                    )
                ),
                requiere_reanalisis=True,
            )

            for campo in CAMPOS_CONTACTO:

                valor = data.get(
                    campo,
                )

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

            nuevos_contactos.append(
                contacto,
            )

            filas_nuevos_contactos.append(
                fila,
            )

            continue

        # ====================================================
        # SIN CAMBIOS
        # ====================================================

        if fila.estado == "sin_cambios":

            resultado["sin_cambios"] += 1

            continue

        # ====================================================
        # ACTUALIZAR
        # ====================================================

        contacto = contactos_por_id.get(
            fila.contacto_id,
        )

        if contacto is None:

            resultado["sin_cambios"] += 1

            continue

        versiones_anteriores.append(
            _construir_version_contacto(
                contacto=contacto,
                importacion=importacion,
            )
        )

        hubo_cambio = False

        hubo_cambio_reanalizable = False

        if sitio and contacto.sitio_id != sitio.pk:

            contacto.sitio = sitio

            hubo_cambio = True

        for campo in CAMPOS_CONTACTO:

            nuevo = data.get(
                campo,
            )

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

                iguales = normalizar_clave(
                    anterior,
                ) == normalizar_clave(
                    nuevo,
                )

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

        if hubo_cambio:

            contacto.actualizado_por = user

            contacto.actualizado_en = ahora

            if hubo_cambio_reanalizable:

                contacto.requiere_reanalisis = True

            contacto.firma_contenido = generar_firma_contacto(
                construir_data_firma_desde_contacto(
                    contacto,
                )
            )

            contactos_actualizados.append(
                contacto,
            )

            resultado["actualizados"] += 1

        else:

            # El preview pudo generarse antes de un cambio
            # concurrente. En ese caso no contamos una falsa
            # actualización.
            resultado["sin_cambios"] += 1

    # ========================================================
    # CREAR NUEVOS CONTACTOS
    # ========================================================

    if nuevos_contactos:

        ContactoSitio.objects.bulk_create(
            nuevos_contactos,
            batch_size=TAMANO_LOTE_APLICACION,
        )

        versiones_nuevas = [
            _construir_version_contacto(
                contacto=contacto,
                importacion=importacion,
            )
            for contacto in nuevos_contactos
        ]

        VersionContactoSitio.objects.bulk_create(
            versiones_nuevas,
            batch_size=TAMANO_LOTE_APLICACION,
        )

        resultado["creados"] += len(
            nuevos_contactos,
        )

    # ========================================================
    # VERSIONES ANTES DE ACTUALIZAR
    # ========================================================

    if versiones_anteriores:

        VersionContactoSitio.objects.bulk_create(
            versiones_anteriores,
            batch_size=TAMANO_LOTE_APLICACION,
        )

    # ========================================================
    # ACTUALIZACIONES
    # ========================================================

    if contactos_actualizados:

        ContactoSitio.objects.bulk_update(
            contactos_actualizados,
            fields=[
                "sitio",
                "region",
                "nombre_sitio",
                "propietario",
                "telefono",
                "correo",
                "fecha_informacion",
                "responsable",
                "observaciones",
                "accion",
                "requiere_reanalisis",
                "firma_contenido",
                "actualizado_por",
                "actualizado_en",
            ],
            batch_size=TAMANO_LOTE_APLICACION,
        )


# ============================================================
# APLICAR IMPORTACIÓN
# ============================================================


def aplicar_importacion_contactos(
    *,
    importacion,
    user,
):
    """
    Aplica las filas persistidas del preview por lotes.

    No carga toda la importación en memoria.

    Sigue siendo una única transacción lógica.
    """

    resultado = {
        "creados": 0,
        "actualizados": 0,
        "sin_cambios": 0,
        "no_vinculados": 0,
    }

    try:

        with transaction.atomic():

            importacion_bloqueada = (
                ImportacionContactosSitios.objects.select_for_update().get(
                    pk=importacion.pk,
                )
            )

            if importacion_bloqueada.estado != "preview":

                raise ValueError(
                    "Esta importación ya no está disponible " "para ser aplicada."
                )

            queryset = (
                FilaImportacionContacto.objects.filter(
                    importacion=importacion_bloqueada,
                )
                .select_related(
                    "sitio",
                    "contacto",
                )
                .order_by(
                    "numero_fila",
                    "id",
                )
            )

            lote = []

            for fila in queryset.iterator(
                chunk_size=TAMANO_LOTE_APLICACION,
            ):

                lote.append(
                    fila,
                )

                if (
                    len(
                        lote,
                    )
                    < TAMANO_LOTE_APLICACION
                ):

                    continue

                _aplicar_lote_contactos(
                    filas=lote,
                    importacion=importacion_bloqueada,
                    user=user,
                    resultado=resultado,
                )

                lote = []

            if lote:

                _aplicar_lote_contactos(
                    filas=lote,
                    importacion=importacion_bloqueada,
                    user=user,
                    resultado=resultado,
                )

            importacion_bloqueada.estado = "aplicada"

            importacion_bloqueada.aplicado_en = timezone.now()

            importacion_bloqueada.nuevos = resultado["creados"]

            importacion_bloqueada.actualizados = resultado["actualizados"]

            importacion_bloqueada.sin_cambios = resultado["sin_cambios"]

            importacion_bloqueada.no_vinculados = resultado["no_vinculados"]

            importacion_bloqueada.save(
                update_fields=[
                    "estado",
                    "aplicado_en",
                    "nuevos",
                    "actualizados",
                    "sin_cambios",
                    "no_vinculados",
                ]
            )

            importacion_id = importacion_bloqueada.pk

        # ====================================================
        # EL PREVIEW YA NO ES NECESARIO
        # ====================================================

        FilaImportacionContacto.objects.filter(
            importacion_id=importacion_id,
        ).delete()

        return {
            **resultado,
            "importacion_id": importacion_id,
        }

    except Exception as exc:

        ImportacionContactosSitios.objects.filter(
            pk=importacion.pk,
        ).update(
            estado="error",
            observaciones=str(
                exc,
            ),
        )

        raise

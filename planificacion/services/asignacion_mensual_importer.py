import re
import unicodedata
from collections import OrderedDict

import pandas as pd
from django.db import transaction
from django.utils import timezone

from operaciones.models import SitioMovil
from planificacion.models import ImportacionAsignacionMensual, SitioPlanificado

# ============================================================
# CONFIGURACIÓN
# ============================================================


MAX_FILAS_BUSQUEDA_ENCABEZADO = 20

COLUMNA_INTERNA_FILA_EXCEL = "__fila_excel__"


# ============================================================
# NORMALIZACIÓN
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
        "<na>",
    }:
        return ""

    return value


def normalizar_clave(value):
    value = normalizar_texto(value).lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(char for char in value if not unicodedata.combining(char))

    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalizar_id_claro(value):
    """
    Normalización exclusivamente para comparar el ID.

    NO modifica SitioMovil.

    Ejemplos equivalentes:

    05_008
    05-008
    05 008
    """

    value = normalizar_texto(value)

    if not value:
        return ""

    value = value.upper().strip()

    value = value.replace("-", "_")
    value = value.replace(" ", "_")

    value = re.sub(
        r"_+",
        "_",
        value,
    )

    return value


# ============================================================
# COLUMNAS ID VÁLIDAS
# ============================================================


COLUMNAS_ID_VALIDAS = {
    "id",
    "id claro",
    "id sitio",
    "id site",
}


def detectar_columna_id(df):
    """
    Busca una columna reconocible como ID Claro.
    """

    for columna in df.columns:
        normalizada = normalizar_clave(columna)

        if normalizada in COLUMNAS_ID_VALIDAS:
            return columna

    return None


# ============================================================
# FILAS / ENCABEZADOS
# ============================================================


def fila_tiene_contenido(row):
    """
    Determina si una fila contiene algún valor real.
    """

    for value in row.tolist():
        if normalizar_texto(value):
            return True

    return False


def generar_nombre_columna_unico(
    nombre,
    usados,
    indice,
):
    """
    Evita nombres duplicados dentro del DataFrame.
    """

    nombre = normalizar_texto(nombre)

    if not nombre:
        nombre = f"columna_{indice + 1}"

    base = nombre

    numero = 2

    while nombre in usados:
        nombre = f"{base}_{numero}"

        numero += 1

    usados.add(nombre)

    return nombre


def construir_encabezados_una_fila(
    raw_df,
    fila,
):
    usados = set()

    encabezados = []

    valores = raw_df.iloc[fila].tolist()

    for indice, value in enumerate(valores):
        encabezados.append(
            generar_nombre_columna_unico(
                value,
                usados,
                indice,
            )
        )

    return encabezados


def construir_encabezados_dos_filas(
    raw_df,
    fila_superior,
    fila_inferior,
):
    """
    Permite archivos donde el encabezado esté repartido
    entre dos filas.

    Ejemplo:

        FILA 1: ID       NOMBRE
        FILA 2: CLARO    SITIO

    Resultado:

        ID CLARO
        NOMBRE SITIO
    """

    usados = set()

    encabezados = []

    superior = raw_df.iloc[fila_superior].tolist()

    inferior = raw_df.iloc[fila_inferior].tolist()

    total_columnas = max(
        len(superior),
        len(inferior),
    )

    for indice in range(total_columnas):
        arriba = ""

        abajo = ""

        if indice < len(superior):
            arriba = normalizar_texto(superior[indice])

        if indice < len(inferior):
            abajo = normalizar_texto(inferior[indice])

        if arriba and abajo:
            if normalizar_clave(arriba) == normalizar_clave(abajo):
                nombre = arriba

            else:
                nombre = f"{arriba} {abajo}"

        elif arriba:
            nombre = arriba

        elif abajo:
            nombre = abajo

        else:
            nombre = ""

        encabezados.append(
            generar_nombre_columna_unico(
                nombre,
                usados,
                indice,
            )
        )

    return encabezados


def encabezados_contienen_id(
    encabezados,
):
    for columna in encabezados:
        if normalizar_clave(columna) in COLUMNAS_ID_VALIDAS:
            return True

    return False


def detectar_encabezado(
    raw_df,
):
    """
    Detecta automáticamente:

    - encabezado de una fila;
    - encabezado de dos filas;
    - encabezado ubicado dentro de las primeras 20 filas.
    """

    if raw_df.empty:
        return None

    limite = min(
        len(raw_df),
        MAX_FILAS_BUSQUEDA_ENCABEZADO,
    )

    # ========================================================
    # 1. PROBAR ENCABEZADO DE UNA FILA
    # ========================================================

    for fila in range(limite):
        if not fila_tiene_contenido(raw_df.iloc[fila]):
            continue

        encabezados = construir_encabezados_una_fila(
            raw_df,
            fila,
        )

        if encabezados_contienen_id(encabezados):
            return {
                "fila_inicio": fila,
                "filas_encabezado": 1,
                "encabezados": encabezados,
            }

    # ========================================================
    # 2. PROBAR ENCABEZADO DE DOS FILAS
    # ========================================================

    for fila in range(
        max(
            limite - 1,
            0,
        )
    ):
        fila_siguiente = fila + 1

        encabezados = construir_encabezados_dos_filas(
            raw_df,
            fila,
            fila_siguiente,
        )

        if encabezados_contienen_id(encabezados):
            return {
                "fila_inicio": fila,
                "filas_encabezado": 2,
                "encabezados": encabezados,
            }

    return None


# ============================================================
# LIMPIEZA DE FILAS
# ============================================================


def fila_dataframe_vacia(
    row,
):
    """
    Determina si una fila del DataFrame está realmente vacía.

    Ignora nuestra columna técnica __fila_excel__.
    """

    for columna, value in row.items():
        if columna == COLUMNA_INTERNA_FILA_EXCEL:
            continue

        if normalizar_texto(value):
            return False

    return True


def limpiar_dataframe_datos(
    df,
):
    """
    Elimina filas completamente vacías.

    IMPORTANTE:

    No determina qué filas pertenecen realmente a la
    asignación mensual.

    Esa decisión se toma después exclusivamente mediante
    la presencia de ID Claro.

    Conserva __fila_excel__ para mantener la referencia
    correcta de la fila original.
    """

    if df.empty:
        return df

    indices_validos = []

    for index, row in df.iterrows():
        if fila_dataframe_vacia(row):
            continue

        indices_validos.append(index)

    if not indices_validos:
        return df.iloc[0:0].copy()

    return df.loc[indices_validos].copy().reset_index(drop=True)


# ============================================================
# LEER EXCEL
# ============================================================


def leer_excel_asignacion(
    archivo,
):
    """
    Lee la planilla mensual.

    Soporta:

    - encabezado en una fila;
    - encabezado en dos filas;
    - encabezado ubicado más abajo;
    - filas vacías entre encabezado y datos;
    - cualquier cantidad de columnas adicionales.

    Para la asignación mensual solamente nos interesa
    localizar el ID / ID Claro.

    NO modifica SitioMovil.
    """

    xls = pd.ExcelFile(archivo)

    if not xls.sheet_names:
        raise ValueError("El archivo no contiene hojas.")

    for hoja in xls.sheet_names:
        # ====================================================
        # LEER SIN ASUMIR ENCABEZADO
        # ====================================================

        raw_df = pd.read_excel(
            archivo,
            sheet_name=hoja,
            header=None,
            dtype=object,
        )

        if raw_df.empty:
            continue

        # ====================================================
        # ELIMINAR COLUMNAS COMPLETAMENTE VACÍAS
        # ====================================================

        raw_df = raw_df.dropna(
            axis=1,
            how="all",
        )

        if raw_df.empty:
            continue

        # ====================================================
        # DETECTAR ENCABEZADO
        # ====================================================

        encabezado = detectar_encabezado(raw_df)

        if not encabezado:
            continue

        fila_inicio = encabezado["fila_inicio"]

        filas_encabezado = encabezado["filas_encabezado"]

        encabezados = encabezado["encabezados"]

        primera_fila_datos = fila_inicio + filas_encabezado

        # ====================================================
        # EXTRAER FILAS DE DATOS
        # ====================================================

        df = raw_df.iloc[primera_fila_datos:].copy()

        if df.empty:
            continue

        encabezados = encabezados[: len(df.columns)]

        df.columns = encabezados

        # ====================================================
        # FILA ORIGINAL DE EXCEL
        # ====================================================

        df[COLUMNA_INTERNA_FILA_EXCEL] = [int(indice) + 1 for indice in df.index]

        # ====================================================
        # ELIMINAR FILAS COMPLETAMENTE VACÍAS
        # ====================================================

        df = limpiar_dataframe_datos(df)

        if df.empty:
            continue

        # ====================================================
        # DETECTAR COLUMNA ID
        # ====================================================

        columna_id = detectar_columna_id(df)

        if columna_id:
            return (
                df,
                hoja,
                columna_id,
            )

    raise ValueError(
        (
            "No se encontró una columna válida para el ID. "
            "La planilla debe contener una columna llamada "
            "'ID' o 'ID Claro'. El encabezado puede ocupar "
            "una o dos filas."
        )
    )


# ============================================================
# CACHE DE SITIOS
# ============================================================


def construir_mapa_sitios():
    """
    Construye en memoria un mapa basado únicamente
    en SitioMovil.id_claro.

    NO modifica SitioMovil.
    """

    sitios = (
        SitioMovil.objects.exclude(
            id_claro__isnull=True,
        )
        .exclude(
            id_claro="",
        )
        .only(
            "id",
            "id_claro",
            "nombre",
            "region",
            "comuna",
            "tipo_zona",
            "direccion",
            "latitud",
            "longitud",
        )
    )

    resultado = {}

    for sitio in sitios:
        clave = normalizar_id_claro(sitio.id_claro)

        if clave:
            resultado[clave] = sitio

    return resultado


# ============================================================
# PREVIEW
# ============================================================


def generar_preview_asignacion(
    df,
    columna_id,
    planificacion,
):
    """
    Analiza los IDs de la asignación.

    REGLA PRINCIPAL:

    La asignación mensual se determina exclusivamente
    mediante ID Claro.

    Si una fila no contiene ID Claro:

    - se ignora completamente;
    - no genera error;
    - no incrementa total_filas;
    - no se muestra en preview;
    - no afecta estadísticas.

    No guarda ninguna información.
    """

    mapa_sitios = construir_mapa_sitios()

    sitios_mes_existentes = set(
        SitioPlanificado.objects.filter(
            planificacion=planificacion,
        ).values_list(
            "sitio_id",
            flat=True,
        )
    )

    preview = []

    errores = []

    repeticiones = OrderedDict()

    resumen = {
        "total_filas": 0,
        "total_ids_detectados": 0,
        "ids_unicos": 0,
        "ids_repetidos": 0,
        "vinculados": 0,
        "no_encontrados": 0,
        "ya_existentes_mes": 0,
        "nuevos_mes": 0,
        "errores": 0,
    }

    ids_unicos_detectados = set()

    # ========================================================
    # RECORRER FILAS
    # ========================================================

    for index, row in df.iterrows():
        fila_excel = row.get(COLUMNA_INTERNA_FILA_EXCEL)

        try:
            fila_excel = int(fila_excel)

        except (
            TypeError,
            ValueError,
        ):
            fila_excel = int(index) + 2

        # ====================================================
        # ID ORIGINAL
        # ====================================================

        valor_original = normalizar_texto(
            row.get(
                columna_id,
            )
        )

        id_normalizado = normalizar_id_claro(valor_original)

        # ====================================================
        # SIN ID = IGNORAR COMPLETAMENTE
        # ====================================================
        #
        # No importa si la fila contiene información en otras
        # columnas.
        #
        # Para este módulo, una fila solamente pertenece a la
        # asignación mensual si tiene un ID Claro.
        #
        # Esto también cubre:
        #
        # - filas auxiliares;
        # - encabezados secundarios;
        # - observaciones;
        # - celdas con fórmulas;
        # - estilos o contenido residual de Excel.
        # ====================================================

        if not id_normalizado:
            continue

        # ====================================================
        # FILA DE ASIGNACIÓN VÁLIDA
        # ====================================================

        resumen["total_filas"] += 1

        resumen["total_ids_detectados"] += 1

        ids_unicos_detectados.add(id_normalizado)

        # ====================================================
        # REPETICIONES
        # ====================================================

        if id_normalizado not in repeticiones:
            repeticiones[id_normalizado] = {
                "id": valor_original,
                "cantidad": 0,
                "filas": [],
            }

        repeticiones[id_normalizado]["cantidad"] += 1

        repeticiones[id_normalizado]["filas"].append(fila_excel)

        # ====================================================
        # BUSCAR SITIO
        # ====================================================

        sitio = mapa_sitios.get(id_normalizado)

        # ====================================================
        # NO ENCONTRADO
        # ====================================================

        if not sitio:
            resumen["no_encontrados"] += 1

            preview.append(
                {
                    "fila": fila_excel,
                    "id_original": valor_original,
                    "id_normalizado": id_normalizado,
                    "sitio_id": None,
                    "vinculado": False,
                    "estado": "no_encontrado",
                    "nombre": "",
                    "region": "",
                    "comuna": "",
                    "tipo_zona": "",
                    "direccion": "",
                    "latitud": None,
                    "longitud": None,
                    "ya_existe_mes": False,
                }
            )

            continue

        # ====================================================
        # VINCULADO
        # ====================================================

        resumen["vinculados"] += 1

        ya_existe_mes = sitio.pk in sitios_mes_existentes

        if ya_existe_mes:
            resumen["ya_existentes_mes"] += 1

            estado = "ya_existente"

        else:
            resumen["nuevos_mes"] += 1

            estado = "nuevo"

        preview.append(
            {
                "fila": fila_excel,
                "id_original": valor_original,
                "id_normalizado": id_normalizado,
                "sitio_id": sitio.pk,
                "vinculado": True,
                "estado": estado,
                "nombre": sitio.nombre or "",
                "region": sitio.region or "",
                "comuna": sitio.comuna or "",
                "tipo_zona": sitio.tipo_zona or "",
                "direccion": sitio.direccion or "",
                "latitud": sitio.latitud,
                "longitud": sitio.longitud,
                "ya_existe_mes": ya_existe_mes,
            }
        )

    # ========================================================
    # IDS ÚNICOS
    # ========================================================

    resumen["ids_unicos"] = len(ids_unicos_detectados)

    # ========================================================
    # REPETIDOS
    # ========================================================

    repetidos = []

    for item in repeticiones.values():
        if item["cantidad"] > 1:
            repetidos.append(item)

    resumen["ids_repetidos"] = len(repetidos)

    return (
        preview,
        resumen,
        errores,
        repetidos,
    )


# ============================================================
# APLICAR IMPORTACIÓN
# ============================================================


@transaction.atomic
def aplicar_importacion_asignacion(
    *,
    preview,
    planificacion,
    user,
    nombre_archivo="",
    nombre_hoja="",
    columna_id_detectada="",
):
    """
    Incorpora los SitioPlanificado que correspondan al mes.

    SitioMovil se usa exclusivamente como referencia.

    NUNCA modifica SitioMovil.
    """

    importacion = ImportacionAsignacionMensual.objects.create(
        planificacion=planificacion,
        nombre_archivo=nombre_archivo,
        nombre_hoja=nombre_hoja,
        columna_id_detectada=(columna_id_detectada),
        estado="preview",
        creado_por=user,
        total_filas=len(preview),
    )

    creados = 0

    ya_existentes = 0

    no_encontrados = 0

    # ========================================================
    # EVITAR DUPLICADOS DEL MISMO ARCHIVO
    # ========================================================

    sitios_procesados = set()

    try:
        for item in preview:
            sitio_id = item.get("sitio_id")

            if not sitio_id:
                no_encontrados += 1

                continue

            if sitio_id in sitios_procesados:
                continue

            sitios_procesados.add(sitio_id)

            (
                sitio_planificado,
                created,
            ) = SitioPlanificado.objects.get_or_create(
                planificacion=planificacion,
                sitio_id=sitio_id,
                defaults={
                    "estado": "pendiente",
                    "estado_permiso": "sin_gestion",
                    "prioridad": "normal",
                    "activo_en_mes": True,
                    "importacion_origen": importacion,
                    "creado_por": user,
                    "actualizado_por": user,
                },
            )

            if created:
                creados += 1

            else:
                ya_existentes += 1

                cambios = []

                if not sitio_planificado.activo_en_mes:
                    sitio_planificado.activo_en_mes = True

                    cambios.append("activo_en_mes")

                if not sitio_planificado.importacion_origen_id:
                    sitio_planificado.importacion_origen = importacion

                    cambios.append("importacion_origen")

                if cambios:
                    sitio_planificado.actualizado_por = user

                    cambios.append("actualizado_por")

                    sitio_planificado.save(update_fields=cambios)

        # ====================================================
        # ESTADÍSTICAS
        # ====================================================

        ids_detectados = [item for item in preview if item.get("id_normalizado")]

        ids_unicos = {item.get("id_normalizado") for item in ids_detectados}

        contador_ids = {}

        for item in ids_detectados:
            clave = item.get("id_normalizado")

            contador_ids[clave] = (
                contador_ids.get(
                    clave,
                    0,
                )
                + 1
            )

        ids_repetidos = sum(1 for cantidad in contador_ids.values() if cantidad > 1)

        # ====================================================
        # GUARDAR AUDITORÍA
        # ====================================================

        importacion.estado = "aplicada"

        importacion.aplicado_en = timezone.now()

        importacion.total_filas = len(ids_detectados)

        importacion.total_ids_detectados = len(ids_detectados)

        importacion.ids_unicos = len(ids_unicos)

        importacion.ids_repetidos = ids_repetidos

        importacion.vinculados = len([item for item in preview if item.get("sitio_id")])

        importacion.no_encontrados = no_encontrados

        importacion.ya_existentes_mes = ya_existentes

        importacion.creados = creados

        importacion.errores = 0

        importacion.save()

        return {
            "creados": creados,
            "ya_existentes": ya_existentes,
            "no_encontrados": no_encontrados,
            "importacion_id": (importacion.pk),
        }

    except Exception as exc:
        importacion.estado = "error"

        importacion.observaciones = str(exc)

        importacion.save(
            update_fields=[
                "estado",
                "observaciones",
            ]
        )

        raise

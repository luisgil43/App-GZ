from itertools import permutations
from math import ceil

from planificacion.services.motor_batch_semanal.cuadrillas import \
    cuadrilla_puede_ejecutar_grupo
from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km

# ============================================================
# CONFIGURACIÓN PROVISIONAL DE VIAJE
# ============================================================
#
# Mientras no conectemos Google Routes:
#
# distancia geográfica
#       ↓
# factor aproximado vial
#       ↓
# velocidad de referencia
#       ↓
# minutos estimados
#
# Cuando Google Routes entre en funcionamiento,
# debemos reemplazar únicamente el proveedor de tiempos,
# no la arquitectura de salidas.
# ============================================================

FACTOR_DISTANCIA_VIAL = 1.28

VELOCIDAD_PROMEDIO_URBANA_KMH = 40.0
VELOCIDAD_PROMEDIO_MIXTA_KMH = 55.0
VELOCIDAD_PROMEDIO_RURAL_KMH = 65.0


# ============================================================
# JORNADA EXTENDIDA
# ============================================================
#
# La jornada configurada de cada cuadrilla continúa siendo
# nuestra jornada OBJETIVO.
#
# Sin embargo, operacionalmente sabemos que:
#
# si una cuadrilla viaja lejos, no tiene sentido descartar
# una combinación de 2 sitios únicamente porque supera
# moderadamente la jornada nominal.
#
# Ejemplo:
#
# jornada habitual:
#     600 min
#
# salida de 2 sitios:
#     640 min
#
# Esa salida sigue siendo operacionalmente aceptable.
#
# Por eso:
#
# - 2 o 3 sitios pueden utilizar jornada extendida;
# - 1 sitio NO obtiene esa extensión;
# - nunca superamos 720 minutos.
#
# 720 minutos = 12 horas.
#
# Esto NO significa que queramos trabajar siempre 12 horas.
# Solamente representa el techo excepcional utilizado por
# el simulador para evitar viajes absurdos de un único sitio.
# ============================================================

MINUTOS_JORNADA_EXTENDIDA_MAX = 12 * 60


# ============================================================
# MÁXIMO OPERACIONAL ACTUAL
# ============================================================
#
# El sistema prueba como máximo 3 sitios en una salida.
#
# IMPORTANTE:
#
# Esto NO obliga a ejecutar 3.
#
# La capacidad particular de una cuadrilla puede ser menor
# mediante capacidad_diaria.
# ============================================================

MAX_SITIOS_POR_SALIDA = 3


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(valor):
    try:
        return float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# TIPO DE TRAYECTO
# ============================================================


def _tipo_trayecto(
    *,
    sitio_a=None,
    sitio_b=None,
    distancia_directa_km=None,
):
    """
    Clasificación provisional del trayecto.

    Reglas:

    1. Si alguno de los sitios es rural:
       rural.

    2. Si todos los sitios conocidos son urbanos:
       urbano para trayectos cortos.

    3. Si el desplazamiento urbano es largo:
       mixto, porque normalmente implica autopista /
       recorrido interurbano.

    4. Cuando no existe clasificación suficiente:
       mixto.

    Esto mantiene simétricos:

        BASE -> sitio
        sitio -> BASE
    """

    sitios_conocidos = [
        sitio
        for sitio in [
            sitio_a,
            sitio_b,
        ]
        if sitio is not None
    ]

    if any(sitio.rural for sitio in sitios_conocidos):
        return "rural"

    todos_urbanos = bool(sitios_conocidos) and all(
        sitio.urbano for sitio in sitios_conocidos
    )

    if todos_urbanos:

        if distancia_directa_km is not None and distancia_directa_km > 35:
            return "mixto"

        return "urbano"

    return "mixto"


# ============================================================
# VELOCIDAD DE REFERENCIA
# ============================================================


def _velocidad_referencia(
    tipo_trayecto,
):
    if tipo_trayecto == "urbano":
        return VELOCIDAD_PROMEDIO_URBANA_KMH

    if tipo_trayecto == "rural":
        return VELOCIDAD_PROMEDIO_RURAL_KMH

    return VELOCIDAD_PROMEDIO_MIXTA_KMH


# ============================================================
# ESTIMACIÓN DE TRAYECTO
# ============================================================


def _estimar_trayecto(
    latitud_a,
    longitud_a,
    latitud_b,
    longitud_b,
    *,
    sitio_a=None,
    sitio_b=None,
):
    """
    Proveedor provisional de tiempos y distancia.

    Devuelve:

    {
        "distancia_directa_km": ...,
        "distancia_vial_estimada_km": ...,
        "tipo": ...,
        "velocidad_kmh": ...,
        "minutos": ...,
    }

    Esta función será la pieza que posteriormente podremos
    reemplazar por Google Routes.
    """

    latitud_a = _float_seguro(latitud_a)

    longitud_a = _float_seguro(longitud_a)

    latitud_b = _float_seguro(latitud_b)

    longitud_b = _float_seguro(longitud_b)

    if None in [
        latitud_a,
        longitud_a,
        latitud_b,
        longitud_b,
    ]:
        return None

    distancia_directa = distancia_haversine_km(
        latitud_a,
        longitud_a,
        latitud_b,
        longitud_b,
    )

    if distancia_directa is None:
        return None

    distancia_vial_estimada = distancia_directa * FACTOR_DISTANCIA_VIAL

    tipo = _tipo_trayecto(
        sitio_a=sitio_a,
        sitio_b=sitio_b,
        distancia_directa_km=(distancia_directa),
    )

    velocidad = _velocidad_referencia(tipo)

    if velocidad <= 0:
        return None

    horas = distancia_vial_estimada / velocidad

    minutos = int(ceil(horas * 60))

    return {
        "distancia_directa_km": round(
            distancia_directa,
            2,
        ),
        "distancia_vial_estimada_km": round(
            distancia_vial_estimada,
            2,
        ),
        "tipo": tipo,
        "velocidad_kmh": velocidad,
        "minutos": minutos,
    }


# ============================================================
# COMPATIBILIDAD DE CANTIDAD
# ============================================================


def _cantidad_permitida_cuadrilla(
    *,
    configuracion_cuadrilla,
    cantidad_sitios,
):
    """
    Verifica el máximo nominal declarado para la cuadrilla.

    Ejemplo:

        capacidad_diaria = 2

    El motor NO intentará una salida con 3 sitios.

    Si capacidad_diaria = 3:

        puede intentar 1, 2 o 3.
    """

    try:
        capacidad_diaria = int(
            configuracion_cuadrilla.get(
                "capacidad_diaria",
                MAX_SITIOS_POR_SALIDA,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        capacidad_diaria = MAX_SITIOS_POR_SALIDA

    capacidad_diaria = max(
        capacidad_diaria,
        1,
    )

    maximo = min(
        capacidad_diaria,
        MAX_SITIOS_POR_SALIDA,
    )

    return cantidad_sitios <= maximo


# ============================================================
# LÍMITE EFECTIVO DE JORNADA
# ============================================================


def _limite_jornada_efectivo(
    *,
    minutos_jornada,
    cantidad_sitios,
):
    """
    Devuelve el límite que puede utilizar la salida.

    1 sitio:
        solamente jornada habitual.

    2 o 3 sitios:
        puede utilizar jornada extendida,
        hasta 720 minutos.

    Nunca reducimos una jornada configurada que ya sea
    superior a 720 minutos.
    """

    if cantidad_sitios <= 1:
        return minutos_jornada

    return max(
        minutos_jornada,
        MINUTOS_JORNADA_EXTENDIDA_MAX,
    )


# ============================================================
# EVALUAR UN ORDEN ESPECÍFICO
# ============================================================


def evaluar_orden_salida(
    *,
    sitios,
    configuracion_cuadrilla,
):
    """
    Evalúa una jornada completa:

        BASE
          ↓
        sitio 1
          ↓
        sitio 2
          ↓
        sitio 3
          ↓
        BASE

    El total considera:

    - ida;
    - traslados entre sitios;
    - regreso;
    - tiempo de trabajo de cada sitio.

    REGLA OPERACIONAL
    ==========================================================

    La capacidad objetivo es completar jornadas de 3 sitios.

    Para 3 sitios:

        - la jornada configurada es una referencia;
        - exceder esa jornada NO invalida la salida;
        - el exceso se conserva para mostrar una advertencia
          en la interfaz;
        - se siguen respetando capacidad, urbano/rural,
          coordenadas y demás restricciones operacionales.

    Ejemplo:

        jornada configurada:
            600 min

        jornada calculada:
            781 min

        resultado:
            viable = True
            jornada_extendida = True
            exceso_jornada_minutos = 181

    Para 2 sitios:

        - se mantiene el límite de jornada extendida
          actualmente configurado.

    Para 1 sitio:

        - no se permite jornada extendida.
    """

    sitios = list(sitios)

    if not sitios:
        return None

    cantidad_sitios = len(sitios)

    # ========================================================
    # LÍMITE DE CANTIDAD
    # ========================================================

    if not _cantidad_permitida_cuadrilla(
        configuracion_cuadrilla=configuracion_cuadrilla,
        cantidad_sitios=cantidad_sitios,
    ):
        return None

    # ========================================================
    # BASE
    # ========================================================

    if not configuracion_cuadrilla.get(
        "tiene_base",
        False,
    ):
        return None

    base_latitud = _float_seguro(configuracion_cuadrilla.get("base_latitud"))

    base_longitud = _float_seguro(configuracion_cuadrilla.get("base_longitud"))

    if base_latitud is None or base_longitud is None:
        return None

    # ========================================================
    # COMPATIBILIDAD TERRITORIAL
    # ========================================================

    if not cuadrilla_puede_ejecutar_grupo(
        configuracion_cuadrilla,
        sitios,
    ):
        return None

    # ========================================================
    # COORDENADAS
    # ========================================================

    for sitio in sitios:

        if (
            _float_seguro(sitio.latitud) is None
            or _float_seguro(sitio.longitud) is None
        ):
            return None

    # ========================================================
    # PARÁMETROS DE JORNADA
    # ========================================================

    try:
        minutos_trabajo_sitio = int(
            configuracion_cuadrilla.get(
                "minutos_trabajo_sitio",
                165,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        minutos_trabajo_sitio = 165

    try:
        minutos_jornada = int(
            configuracion_cuadrilla.get(
                "minutos_jornada",
                540,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        minutos_jornada = 540

    if minutos_trabajo_sitio <= 0:
        minutos_trabajo_sitio = 165

    if minutos_jornada <= 0:
        minutos_jornada = 540

    # ========================================================
    # ACUMULADORES
    # ========================================================

    minutos_viaje = 0

    distancia_directa_total = 0.0

    distancia_vial_total = 0.0

    tramos = []

    # ========================================================
    # BASE -> PRIMER SITIO
    # ========================================================

    primero = sitios[0]

    trayecto = _estimar_trayecto(
        base_latitud,
        base_longitud,
        primero.latitud,
        primero.longitud,
        sitio_b=primero,
    )

    if trayecto is None:
        return None

    minutos_viaje += trayecto["minutos"]

    distancia_directa_total += trayecto["distancia_directa_km"]

    distancia_vial_total += trayecto["distancia_vial_estimada_km"]

    tramos.append(
        {
            "origen": configuracion_cuadrilla.get(
                "base_nombre",
                "Base operacional",
            ),
            "destino": primero.id_claro,
            "tipo": trayecto["tipo"],
            "distancia_directa_km": (trayecto["distancia_directa_km"]),
            "distancia_vial_estimada_km": (trayecto["distancia_vial_estimada_km"]),
            "minutos": trayecto["minutos"],
        }
    )

    # ========================================================
    # ENTRE SITIOS
    # ========================================================

    for sitio_a, sitio_b in zip(
        sitios,
        sitios[1:],
    ):

        trayecto = _estimar_trayecto(
            sitio_a.latitud,
            sitio_a.longitud,
            sitio_b.latitud,
            sitio_b.longitud,
            sitio_a=sitio_a,
            sitio_b=sitio_b,
        )

        if trayecto is None:
            return None

        minutos_viaje += trayecto["minutos"]

        distancia_directa_total += trayecto["distancia_directa_km"]

        distancia_vial_total += trayecto["distancia_vial_estimada_km"]

        tramos.append(
            {
                "origen": sitio_a.id_claro,
                "destino": sitio_b.id_claro,
                "tipo": trayecto["tipo"],
                "distancia_directa_km": (trayecto["distancia_directa_km"]),
                "distancia_vial_estimada_km": (trayecto["distancia_vial_estimada_km"]),
                "minutos": trayecto["minutos"],
            }
        )

    # ========================================================
    # ÚLTIMO SITIO -> BASE
    # ========================================================

    ultimo = sitios[-1]

    trayecto = _estimar_trayecto(
        ultimo.latitud,
        ultimo.longitud,
        base_latitud,
        base_longitud,
        sitio_a=ultimo,
    )

    if trayecto is None:
        return None

    minutos_viaje += trayecto["minutos"]

    distancia_directa_total += trayecto["distancia_directa_km"]

    distancia_vial_total += trayecto["distancia_vial_estimada_km"]

    tramos.append(
        {
            "origen": ultimo.id_claro,
            "destino": (
                configuracion_cuadrilla.get(
                    "base_nombre",
                    "Base operacional",
                )
            ),
            "tipo": trayecto["tipo"],
            "distancia_directa_km": (trayecto["distancia_directa_km"]),
            "distancia_vial_estimada_km": (trayecto["distancia_vial_estimada_km"]),
            "minutos": trayecto["minutos"],
        }
    )

    # ========================================================
    # TRABAJO
    # ========================================================

    minutos_trabajo = cantidad_sitios * minutos_trabajo_sitio

    # ========================================================
    # TOTAL
    # ========================================================

    minutos_total = minutos_viaje + minutos_trabajo

    margen = minutos_jornada - minutos_total

    exceso_jornada = max(
        minutos_total - minutos_jornada,
        0,
    )

    minutos_jornada_limite = _limite_jornada_efectivo(
        minutos_jornada=minutos_jornada,
        cantidad_sitios=cantidad_sitios,
    )

    # ========================================================
    # VIABILIDAD NORMAL
    # ========================================================

    viable_normal = minutos_total <= minutos_jornada

    # ========================================================
    # VIABILIDAD OPERACIONAL
    # ========================================================
    #
    # REGLA DEFINIDA:
    #
    # 3 SITIOS
    # --------------------------------------------------------
    #
    # Si los 3 sitios:
    #
    # - son compatibles con la cuadrilla;
    # - cumplen capacidad;
    # - poseen coordenadas;
    # - pueden calcularse territorialmente;
    #
    # la salida NO se descarta por duración.
    #
    # El tiempo solamente determina cuánto excede la jornada.
    #
    #
    # 2 SITIOS
    # --------------------------------------------------------
    #
    # Conservamos el límite de jornada extendida.
    #
    #
    # 1 SITIO
    # --------------------------------------------------------
    #
    # No permitimos jornada extendida.
    # ========================================================

    if cantidad_sitios >= 3:

        viable_extendida = not viable_normal

        viable = True

    elif cantidad_sitios == 2:

        viable_extendida = not viable_normal and minutos_total <= minutos_jornada_limite

        viable = viable_normal or viable_extendida

    else:

        viable_extendida = False

        viable = viable_normal

    # ========================================================
    # JORNADA EXTENDIDA
    # ========================================================
    #
    # Esto es solamente una condición informativa.
    #
    # NO significa que una salida de 3 sea inválida.
    # ========================================================

    jornada_extendida = viable and not viable_normal

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        # ====================================================
        # CUADRILLA
        # ====================================================
        "disponibilidad_id": (configuracion_cuadrilla.get("disponibilidad_id")),
        "cuadrilla_operativa_id": (
            configuracion_cuadrilla.get("cuadrilla_operativa_id")
        ),
        "cuadrilla": (configuracion_cuadrilla["cuadrilla"]),
        "cuadrilla_display": (configuracion_cuadrilla["cuadrilla_display"]),
        "base_nombre": (
            configuracion_cuadrilla.get(
                "base_nombre",
                "",
            )
        ),
        # ====================================================
        # SITIOS
        # ====================================================
        "sitios": list(sitios),
        "sitio_ids": [sitio.sitio_planificado_id for sitio in sitios],
        "orden": [sitio.id_claro for sitio in sitios],
        "cantidad_sitios": (cantidad_sitios),
        # ====================================================
        # DISTANCIA
        # ====================================================
        "distancia_directa_km": round(
            distancia_directa_total,
            2,
        ),
        "distancia_vial_estimada_km": round(
            distancia_vial_total,
            2,
        ),
        # ====================================================
        # TIEMPOS
        # ====================================================
        "minutos_viaje": (minutos_viaje),
        "minutos_trabajo": (minutos_trabajo),
        "minutos_total": (minutos_total),
        "minutos_jornada": (minutos_jornada),
        "minutos_jornada_limite": (minutos_jornada_limite),
        "margen_minutos": (margen),
        "exceso_jornada_minutos": (exceso_jornada),
        # ====================================================
        # RESULTADO OPERACIONAL
        # ====================================================
        "viable_normal": (viable_normal),
        "viable_extendida": (viable_extendida),
        "jornada_extendida": (jornada_extendida),
        "viable": (viable),
        # ====================================================
        # TRAZABILIDAD
        # ====================================================
        "tramos": (tramos),
    }


# ============================================================
# MEJOR ORDEN PARA UN GRUPO
# ============================================================


def encontrar_mejor_salida(
    *,
    sitios,
    configuracion_cuadrilla,
):
    """
    Busca el mejor orden para una salida pequeña.

    1 sitio:
        1 orden

    2 sitios:
        2 órdenes

    3 sitios:
        6 órdenes

    El ordenamiento favorece:

    1. salida viable;
    2. jornada normal antes que extendida;
    3. menor tiempo total;
    4. menor viaje.
    """

    sitios = list(sitios)

    if not sitios:
        return None

    # ========================================================
    # LÍMITE GLOBAL
    # ========================================================

    if len(sitios) > MAX_SITIOS_POR_SALIDA:

        raise ValueError(
            "La simulación directa admite "
            "como máximo "
            f"{MAX_SITIOS_POR_SALIDA} "
            "sitios por salida."
        )

    # ========================================================
    # LÍMITE DE CUADRILLA
    # ========================================================

    if not _cantidad_permitida_cuadrilla(
        configuracion_cuadrilla=(configuracion_cuadrilla),
        cantidad_sitios=len(sitios),
    ):
        return None

    # ========================================================
    # PERMUTACIONES
    # ========================================================

    evaluaciones = []

    for orden in permutations(sitios):

        evaluacion = evaluar_orden_salida(
            sitios=orden,
            configuracion_cuadrilla=(configuracion_cuadrilla),
        )

        if evaluacion is not None:
            evaluaciones.append(evaluacion)

    if not evaluaciones:
        return None

    # ========================================================
    # ORDENAMIENTO
    # ========================================================

    evaluaciones.sort(
        key=lambda evaluacion: (
            not evaluacion["viable"],
            evaluacion["jornada_extendida"],
            evaluacion["minutos_total"],
            evaluacion["minutos_viaje"],
        )
    )

    return evaluaciones[0]

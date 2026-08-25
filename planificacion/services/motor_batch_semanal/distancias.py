from math import asin, cos, radians, sin, sqrt

# ============================================================
# CONFIGURACIÓN PRELIMINAR DE VIAJE
# ============================================================
#
# Mientras no utilicemos Google Routes, Haversine representa
# distancia en línea recta.
#
# Para aproximar recorrido vial utilizamos un factor de
# corrección conservador.
#
# Posteriormente estas funciones podrán recibir directamente
# tiempos/distancias reales desde Google Routes sin cambiar
# la lógica superior del motor.
# ============================================================

FACTOR_DISTANCIA_VIAL_ESTIMADA = 1.28

VELOCIDAD_URBANA_PROMEDIO_KMH = 35.0
VELOCIDAD_INTERURBANA_PROMEDIO_KMH = 70.0

DISTANCIA_URBANA_MAX_KM = 25.0

MINUTOS_BUFFER_TRAYECTO = 10


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
# HAVERSINE
# ============================================================


def distancia_haversine_km(
    lat1,
    lon1,
    lat2,
    lon2,
):
    """
    Distancia geodésica aproximada entre dos coordenadas.

    Devuelve kilómetros en línea recta.
    """

    lat1 = _float_seguro(lat1)
    lon1 = _float_seguro(lon1)
    lat2 = _float_seguro(lat2)
    lon2 = _float_seguro(lon2)

    if None in [
        lat1,
        lon1,
        lat2,
        lon2,
    ]:
        return None

    radio = 6371.0

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2

    c = 2 * asin(sqrt(a))

    return radio * c


# ============================================================
# DISTANCIA VIAL ESTIMADA
# ============================================================


def estimar_distancia_vial_km(
    distancia_lineal_km,
):
    """
    Aproxima distancia vial desde Haversine.

    Ejemplo:

    50 km línea recta
        ->
    ~64 km viales usando factor 1.28.

    Esto es temporal hasta conectar Google Routes.
    """

    if distancia_lineal_km is None:
        return None

    try:
        distancia_lineal_km = float(distancia_lineal_km)
    except (
        TypeError,
        ValueError,
    ):
        return None

    return distancia_lineal_km * FACTOR_DISTANCIA_VIAL_ESTIMADA


# ============================================================
# TIEMPO DE TRAYECTO ESTIMADO
# ============================================================


def estimar_minutos_trayecto(
    distancia_lineal_km,
):
    """
    Estima duración de viaje usando distancia lineal.

    Para recorridos cortos utiliza velocidad urbana.

    Para recorridos largos utiliza velocidad interurbana.

    Incluye un pequeño buffer operacional.
    """

    if distancia_lineal_km is None:
        return None

    distancia_vial = estimar_distancia_vial_km(distancia_lineal_km)

    if distancia_vial is None:
        return None

    if distancia_vial <= DISTANCIA_URBANA_MAX_KM:
        velocidad = VELOCIDAD_URBANA_PROMEDIO_KMH
    else:
        velocidad = VELOCIDAD_INTERURBANA_PROMEDIO_KMH

    horas = distancia_vial / velocidad

    minutos = horas * 60

    return round(
        minutos + MINUTOS_BUFFER_TRAYECTO,
        2,
    )


# ============================================================
# ENTRE DOS COORDENADAS
# ============================================================


def estimar_trayecto_entre_coordenadas(
    latitud_a,
    longitud_a,
    latitud_b,
    longitud_b,
):
    """
    Devuelve información preliminar del trayecto.

    {
        "distancia_lineal_km": ...,
        "distancia_vial_estimada_km": ...,
        "minutos_estimados": ...,
    }
    """

    distancia_lineal = distancia_haversine_km(
        latitud_a,
        longitud_a,
        latitud_b,
        longitud_b,
    )

    if distancia_lineal is None:
        return {
            "distancia_lineal_km": None,
            "distancia_vial_estimada_km": None,
            "minutos_estimados": None,
        }

    distancia_vial = estimar_distancia_vial_km(distancia_lineal)

    minutos = estimar_minutos_trayecto(distancia_lineal)

    return {
        "distancia_lineal_km": round(
            distancia_lineal,
            2,
        ),
        "distancia_vial_estimada_km": round(
            distancia_vial,
            2,
        ),
        "minutos_estimados": minutos,
    }


# ============================================================
# MATRIZ
# ============================================================


def construir_matriz_distancias(
    sitios,
):
    """
    Devuelve:

    {
        sitio_planificado_id: {
            otro_sitio_planificado_id: distancia_km
        }
    }

    Se conserva esta estructura para compatibilidad
    con servicios existentes.
    """

    matriz = {sitio.sitio_planificado_id: {} for sitio in sitios}

    total = len(sitios)

    for i in range(total):

        sitio_a = sitios[i]

        for j in range(
            i + 1,
            total,
        ):

            sitio_b = sitios[j]

            distancia = distancia_haversine_km(
                sitio_a.latitud,
                sitio_a.longitud,
                sitio_b.latitud,
                sitio_b.longitud,
            )

            matriz[sitio_a.sitio_planificado_id][
                sitio_b.sitio_planificado_id
            ] = distancia

            matriz[sitio_b.sitio_planificado_id][
                sitio_a.sitio_planificado_id
            ] = distancia

    return matriz

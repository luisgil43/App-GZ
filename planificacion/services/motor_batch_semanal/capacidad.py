from statistics import mean

from planificacion.services.motor_batch_semanal.distancias import (
    distancia_haversine_km, estimar_minutos_trayecto)

# ============================================================
# CONFIGURACIÓN OPERACIONAL
# ============================================================

MAX_SITIOS_EVALUACION_JORNADA = 4

BONUS_HOLGURA_BUENA = 8.0

PENALIZACION_SIN_BASE = 12.0


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


def _sitio_compatible_cuadrilla(
    sitio,
    cuadrilla,
):
    """
    Comprueba compatibilidad territorial básica.
    """

    if sitio.rural:
        return bool(
            cuadrilla.get(
                "permite_rural",
                False,
            )
        )

    if sitio.urbano:
        return bool(
            cuadrilla.get(
                "permite_urbano",
                True,
            )
        )

    # Si el sitio no está clasificado todavía,
    # no lo descartamos automáticamente.
    return True


# ============================================================
# ORDEN APROXIMADO DESDE BASE
# ============================================================


def _ordenar_sitios_desde_base(
    sitios,
    *,
    base_latitud,
    base_longitud,
):
    """
    Orden greedy tipo vecino más cercano.

    No intenta resolver TSP perfecto.

    Busca una secuencia operacional razonable:

    base -> sitio cercano -> siguiente cercano -> ...

    Posteriormente Google Routes podrá reemplazar esta
    aproximación.
    """

    pendientes = list(sitios)

    if not pendientes:
        return []

    actual_latitud = base_latitud
    actual_longitud = base_longitud

    ordenados = []

    while pendientes:

        mejor = None
        mejor_distancia = None

        for sitio in pendientes:

            distancia = distancia_haversine_km(
                actual_latitud,
                actual_longitud,
                sitio.latitud,
                sitio.longitud,
            )

            if distancia is None:
                continue

            if mejor is None or distancia < mejor_distancia:
                mejor = sitio
                mejor_distancia = distancia

        if mejor is None:
            ordenados.extend(pendientes)
            break

        ordenados.append(mejor)

        pendientes.remove(mejor)

        actual_latitud = mejor.latitud

        actual_longitud = mejor.longitud

    return ordenados


# ============================================================
# JORNADA ESTIMADA
# ============================================================


def estimar_jornada_cuadrilla(
    *,
    sitios,
    cuadrilla,
):
    """
    Estima si una cuadrilla puede ejecutar un conjunto de sitios
    dentro de una sola jornada.

    Considera:

    base
        ->
    sitios
        ->
    regreso a base

    más tiempo de ejecución por sitio.

    Devuelve una estructura detallada.
    """

    sitios = list(sitios)

    jornada_disponible = int(
        cuadrilla.get(
            "minutos_jornada",
            540,
        )
        or 540
    )

    minutos_sitio = int(
        cuadrilla.get(
            "minutos_trabajo_sitio",
            165,
        )
        or 165
    )

    base_latitud = _float_seguro(cuadrilla.get("base_latitud"))

    base_longitud = _float_seguro(cuadrilla.get("base_longitud"))

    # --------------------------------------------------------
    # COMPATIBILIDAD TERRITORIAL
    # --------------------------------------------------------

    incompatibles = [
        sitio
        for sitio in sitios
        if not _sitio_compatible_cuadrilla(
            sitio,
            cuadrilla,
        )
    ]

    if incompatibles:
        return {
            "viable": False,
            "motivo": ("La cuadrilla no es compatible " "con todos los sitios."),
            "cantidad_sitios": len(sitios),
            "minutos_totales": None,
            "minutos_trayecto": None,
            "minutos_trabajo": (len(sitios) * minutos_sitio),
            "minutos_holgura": None,
            "distancia_lineal_km": None,
            "sin_base": False,
        }

    # --------------------------------------------------------
    # SIN BASE
    # --------------------------------------------------------

    if base_latitud is None or base_longitud is None:
        return {
            "viable": False,
            "motivo": ("La cuadrilla no tiene una " "base operacional válida."),
            "cantidad_sitios": len(sitios),
            "minutos_totales": None,
            "minutos_trayecto": None,
            "minutos_trabajo": (len(sitios) * minutos_sitio),
            "minutos_holgura": None,
            "distancia_lineal_km": None,
            "sin_base": True,
        }

    # --------------------------------------------------------
    # SITIOS SIN COORDENADAS
    # --------------------------------------------------------

    for sitio in sitios:
        if sitio.latitud is None or sitio.longitud is None:
            return {
                "viable": False,
                "motivo": ("Uno o más sitios no tienen " "coordenadas válidas."),
                "cantidad_sitios": len(sitios),
                "minutos_totales": None,
                "minutos_trayecto": None,
                "minutos_trabajo": (len(sitios) * minutos_sitio),
                "minutos_holgura": None,
                "distancia_lineal_km": None,
                "sin_base": False,
            }

    # --------------------------------------------------------
    # ORDEN PRELIMINAR
    # --------------------------------------------------------

    ordenados = _ordenar_sitios_desde_base(
        sitios,
        base_latitud=base_latitud,
        base_longitud=base_longitud,
    )

    minutos_trayecto = 0.0
    distancia_lineal_total = 0.0

    actual_latitud = base_latitud

    actual_longitud = base_longitud

    # --------------------------------------------------------
    # BASE -> SITIOS
    # --------------------------------------------------------

    for sitio in ordenados:

        distancia = distancia_haversine_km(
            actual_latitud,
            actual_longitud,
            sitio.latitud,
            sitio.longitud,
        )

        minutos = estimar_minutos_trayecto(distancia)

        if distancia is None or minutos is None:
            continue

        distancia_lineal_total += distancia

        minutos_trayecto += minutos

        actual_latitud = sitio.latitud

        actual_longitud = sitio.longitud

    # --------------------------------------------------------
    # ÚLTIMO SITIO -> BASE
    # --------------------------------------------------------

    if ordenados:

        distancia_regreso = distancia_haversine_km(
            actual_latitud,
            actual_longitud,
            base_latitud,
            base_longitud,
        )

        minutos_regreso = estimar_minutos_trayecto(distancia_regreso)

        if distancia_regreso is not None:
            distancia_lineal_total += distancia_regreso

        if minutos_regreso is not None:
            minutos_trayecto += minutos_regreso

    minutos_trabajo = len(sitios) * minutos_sitio

    minutos_totales = minutos_trayecto + minutos_trabajo

    holgura = jornada_disponible - minutos_totales

    viable = minutos_totales <= jornada_disponible

    return {
        "viable": viable,
        "motivo": (
            "Jornada operacional viable."
            if viable
            else ("La jornada estimada excede " "el tiempo disponible.")
        ),
        "cantidad_sitios": len(sitios),
        "minutos_jornada": jornada_disponible,
        "minutos_totales": round(
            minutos_totales,
            2,
        ),
        "minutos_trayecto": round(
            minutos_trayecto,
            2,
        ),
        "minutos_trabajo": (minutos_trabajo),
        "minutos_holgura": round(
            holgura,
            2,
        ),
        "distancia_lineal_km": round(
            distancia_lineal_total,
            2,
        ),
        "sin_base": False,
        "orden_sitios": [sitio.sitio_planificado_id for sitio in ordenados],
    }


# ============================================================
# CAPACIDAD REAL APROXIMADA POR JORNADA
# ============================================================


def calcular_sitios_viables_jornada(
    *,
    sitios,
    cuadrilla,
):
    """
    Busca cuántos sitios del conjunto puede ejecutar
    aproximadamente la cuadrilla en una jornada.

    No utiliza automáticamente el antiguo límite de 3.

    Prueba progresivamente:

    1 sitio
    2 sitios
    3 sitios
    4 sitios

    hasta encontrar el máximo razonable.
    """

    sitios = [
        sitio
        for sitio in sitios
        if _sitio_compatible_cuadrilla(
            sitio,
            cuadrilla,
        )
    ]

    if not sitios:
        return {
            "max_sitios": 0,
            "evaluacion": None,
        }

    base_latitud = _float_seguro(cuadrilla.get("base_latitud"))

    base_longitud = _float_seguro(cuadrilla.get("base_longitud"))

    if base_latitud is None or base_longitud is None:
        return {
            "max_sitios": 0,
            "evaluacion": None,
        }

    sitios_ordenados = sorted(
        sitios,
        key=lambda sitio: (
            distancia_haversine_km(
                base_latitud,
                base_longitud,
                sitio.latitud,
                sitio.longitud,
            )
            if (sitio.latitud is not None and sitio.longitud is not None)
            else 999999
        ),
    )

    mejor = None
    max_sitios = 0

    limite = min(
        len(sitios_ordenados),
        MAX_SITIOS_EVALUACION_JORNADA,
    )

    for cantidad in range(
        1,
        limite + 1,
    ):

        evaluacion = estimar_jornada_cuadrilla(
            sitios=(sitios_ordenados[:cantidad]),
            cuadrilla=cuadrilla,
        )

        if not evaluacion["viable"]:
            break

        max_sitios = cantidad
        mejor = evaluacion

    return {
        "max_sitios": max_sitios,
        "evaluacion": mejor,
    }


# ============================================================
# SCORE DE COMPATIBILIDAD GLOBAL
# ============================================================


def score_compatibilidad_cuadrillas(
    *,
    sitios,
    capacidades,
):
    """
    Evalúa compatibilidad operacional de la propuesta.

    Mantiene la misma firma pública utilizada actualmente
    por propuestas.py.

    Ahora considera:

    - urbano/rural;
    - capacidad nominal;
    - disponibilidad;
    - bases operacionales;
    - jornada;
    - tiempo estimado por sitio;
    - cercanía preliminar desde base.
    """

    if not sitios:
        return 0.0

    cuadrillas_activas = [
        cuadrilla
        for cuadrilla in capacidades
        if cuadrilla.get(
            "activa",
            True,
        )
    ]

    if not cuadrillas_activas:
        return 0.0

    total_sitios = len(sitios)

    # ========================================================
    # 1. COMPATIBILIDAD TERRITORIAL
    # ========================================================

    sitios_con_cobertura = 0

    for sitio in sitios:

        if any(
            _sitio_compatible_cuadrilla(
                sitio,
                cuadrilla,
            )
            for cuadrilla in cuadrillas_activas
        ):
            sitios_con_cobertura += 1

    score_cobertura = sitios_con_cobertura / total_sitios * 100

    # ========================================================
    # 2. CAPACIDAD NOMINAL
    # ========================================================

    capacidad_total = sum(
        max(
            int(
                cuadrilla.get(
                    "capacidad_total",
                    0,
                )
                or 0
            ),
            0,
        )
        for cuadrilla in cuadrillas_activas
    )

    if capacidad_total <= 0:
        score_capacidad_nominal = 0.0

    elif capacidad_total >= total_sitios:
        score_capacidad_nominal = 100.0

    else:
        score_capacidad_nominal = capacidad_total / total_sitios * 100

    # ========================================================
    # 3. BASES CONFIGURADAS
    # ========================================================

    con_base = sum(
        1
        for cuadrilla in cuadrillas_activas
        if (
            cuadrilla.get("base_latitud") is not None
            and cuadrilla.get("base_longitud") is not None
        )
    )

    score_bases = con_base / len(cuadrillas_activas) * 100

    # ========================================================
    # 4. VIABILIDAD DE JORNADA
    # ========================================================

    capacidades_jornada = []

    for cuadrilla in cuadrillas_activas:

        resultado = calcular_sitios_viables_jornada(
            sitios=sitios,
            cuadrilla=cuadrilla,
        )

        capacidades_jornada.append(resultado["max_sitios"])

    if capacidades_jornada:

        promedio_sitios_jornada = mean(capacidades_jornada)

        # Consideramos 3 como una jornada muy buena,
        # pero no obligatoria.
        score_jornada = min(
            promedio_sitios_jornada / 3.0 * 100,
            100,
        )

    else:
        score_jornada = 0.0

    # ========================================================
    # SCORE FINAL INTERNO DE CAPACIDAD
    # ========================================================

    score = (
        score_cobertura * 0.35
        + score_capacidad_nominal * 0.20
        + score_bases * 0.15
        + score_jornada * 0.30
    )

    return round(
        max(
            min(
                score,
                100,
            ),
            0,
        ),
        2,
    )

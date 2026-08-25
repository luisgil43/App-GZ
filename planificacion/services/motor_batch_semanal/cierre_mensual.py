from statistics import mean

from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km

# ============================================================
# REFERENCIA TERRITORIAL
# ============================================================

# Centro aproximado de Santiago.
#
# No significa que todas las cuadrillas salgan desde aquí.
# Las bases reales siguen siendo consideradas posteriormente
# por el simulador operacional.
#
# Esta referencia solamente sirve para resolver situaciones
# excepcionales del cierre mensual:
#
#     ¿qué sitio conviene dejar como salida individual?
#
# Entre:
#
#     - un sitio aislado cerca de Santiago;
#     - un sitio aislado muy lejos de Santiago;
#
# preferimos NO dejar solo el sitio lejano.
#
# Es mejor intentar absorber territorialmente primero los
# sitios lejanos y, si inevitablemente debe existir una salida
# individual, que sea lo más cercana posible a Santiago.

SANTIAGO_REFERENCIA_LATITUD = -33.4489
SANTIAGO_REFERENCIA_LONGITUD = -70.6693


# ============================================================
# CONFIGURACIÓN DEL CIERRE
# ============================================================

VECINOS_AFINIDAD_CIERRE = 3

# Un sitio con vecinos muy cercanos tiene buena capacidad
# de incorporarse a un pequeño grupo territorial.
RADIO_VECINDAD_CERCANA_KM = 18.0

# Penalización creciente por quedar muy lejos de Santiago.
DISTANCIA_SANTIAGO_REFERENCIA_KM = 30.0


# ============================================================
# UTILIDADES
# ============================================================


def _id_sitio(sitio):
    return sitio.sitio_planificado_id


def _tiene_coordenadas(sitio):
    return sitio.latitud is not None and sitio.longitud is not None


def _distancia_santiago_km(sitio):
    """
    Distancia directa entre el sitio y Santiago.

    Se utiliza solamente como señal territorial para
    situaciones de cierre.

    NO representa tiempo por carretera.
    """

    if not _tiene_coordenadas(sitio):
        return None

    return distancia_haversine_km(
        SANTIAGO_REFERENCIA_LATITUD,
        SANTIAGO_REFERENCIA_LONGITUD,
        sitio.latitud,
        sitio.longitud,
    )


def _distancias_vecinos(
    sitio,
    universo,
):
    distancias = []

    for otro in universo:

        if _id_sitio(sitio) == _id_sitio(otro):
            continue

        if not _tiene_coordenadas(otro):
            continue

        distancia = distancia_haversine_km(
            sitio.latitud,
            sitio.longitud,
            otro.latitud,
            otro.longitud,
        )

        if distancia is not None:
            distancias.append(
                (
                    distancia,
                    otro,
                )
            )

    distancias.sort(key=lambda elemento: (elemento[0]))

    return distancias


# ============================================================
# AFINIDAD TERRITORIAL
# ============================================================


def _distancia_media_vecinos(
    sitio,
    universo,
):
    """
    Mide qué tan conectado está un sitio con sus vecinos
    territoriales más próximos.

    Cuanto menor sea el resultado, mejor conectado está.
    """

    if not _tiene_coordenadas(sitio):
        return float("inf")

    distancias = _distancias_vecinos(
        sitio,
        universo,
    )

    if not distancias:
        return float("inf")

    seleccionadas = [
        distancia
        for distancia, _ in distancias[
            : min(
                VECINOS_AFINIDAD_CIERRE,
                len(distancias),
            )
        ]
    ]

    return mean(seleccionadas)


def _cantidad_vecinos_cercanos(
    sitio,
    universo,
):
    """
    Cuenta cuántos sitios existen cerca del sitio.

    Nos ayuda a distinguir:

        Valparaíso con otros 4 sitios cerca

    de:

        Valparaíso completamente aislado.
    """

    distancias = _distancias_vecinos(
        sitio,
        universo,
    )

    return sum(
        1 for distancia, _ in distancias if distancia <= RADIO_VECINDAD_CERCANA_KM
    )


# ============================================================
# SCORE PARA SER SELECCIONADO
# ============================================================


def _score_prioridad_cierre(
    sitio,
    universo,
):
    """
    Mayor score = mayor prioridad para quedar dentro
    de la selección territorial del cierre.

    Principio:

    1. privilegiamos sitios que pueden formar agrupaciones;
    2. si un sitio lejano tiene vecinos, intentamos incluirlo
       junto con ellos y no dejarlo abandonado;
    3. un sitio muy aislado pierde prioridad;
    4. los sitios sin coordenadas quedan al final.
    """

    if not _tiene_coordenadas(sitio):
        return -100000.0

    afinidad = _distancia_media_vecinos(
        sitio,
        universo,
    )

    vecinos = _cantidad_vecinos_cercanos(
        sitio,
        universo,
    )

    distancia_santiago = _distancia_santiago_km(sitio) or 0.0

    # --------------------------------------------------------
    # CONECTIVIDAD
    # --------------------------------------------------------

    if afinidad == float("inf"):
        score_afinidad = 0.0

    else:
        score_afinidad = max(
            100 - afinidad * 3.0,
            0,
        )

    # --------------------------------------------------------
    # VECINOS CERCANOS
    # --------------------------------------------------------

    score_vecinos = min(
        vecinos * 18.0,
        72.0,
    )

    # --------------------------------------------------------
    # TERRITORIO LEJANO AGRUPADO
    # --------------------------------------------------------
    #
    # Si existe un grupo lejano, conviene resolverlo unido
    # antes que terminar dejando uno de esos sitios solo.
    # --------------------------------------------------------

    bonus_lejano_agrupado = 0.0

    if distancia_santiago > DISTANCIA_SANTIAGO_REFERENCIA_KM and vecinos >= 1:
        bonus_lejano_agrupado = min(
            distancia_santiago * 0.15,
            20.0,
        )

    # --------------------------------------------------------
    # AISLAMIENTO LEJANO
    # --------------------------------------------------------
    #
    # Un sitio lejano y sin vecinos es operacionalmente caro.
    # No queremos seleccionarlo alegremente cuando todavía
    # existen mejores alternativas.
    # --------------------------------------------------------

    penalizacion_aislado = 0.0

    if vecinos == 0:

        penalizacion_aislado = min(
            distancia_santiago * 0.40,
            55.0,
        )

    return score_afinidad + score_vecinos + bonus_lejano_agrupado - penalizacion_aislado


# ============================================================
# RIESGO DE QUEDAR COMO SALIDA INDIVIDUAL
# ============================================================


def _score_candidato_para_quedar_solo(
    sitio,
    universo,
):
    """
    Menor score = mejor candidato para ser el sitio
    individual de una eventual jornada.

    Esta es una regla operacional importante.

    Queremos que, si inevitablemente queda un único sitio:

        preferentemente esté cerca de Santiago.

    Por ejemplo:

        Puente Alto solo

    es preferible a:

        Valparaíso solo.
    """

    if not _tiene_coordenadas(sitio):
        return float("inf")

    distancia_santiago = _distancia_santiago_km(sitio)

    if distancia_santiago is None:
        return float("inf")

    afinidad = _distancia_media_vecinos(
        sitio,
        universo,
    )

    vecinos = _cantidad_vecinos_cercanos(
        sitio,
        universo,
    )

    # La distancia a Santiago domina la decisión.
    score = distancia_santiago

    # Si tiene muchos vecinos, preferimos NO dejarlo solo.
    score += vecinos * 10.0

    if afinidad != float("inf"):
        score += max(
            20 - afinidad,
            0,
        )

    return score


# ============================================================
# ORDENAMIENTO TERRITORIAL DE LA SELECCIÓN
# ============================================================


def _ordenar_seleccion_cierre(
    sitios,
):
    """
    Orden visual/operacional.

    No determina todavía los días.

    Simplemente coloca primero grupos con mejor afinidad
    territorial y deja al final los candidatos más razonables
    para salidas individuales.
    """

    sitios = list(sitios)

    if len(sitios) <= 1:
        return sitios

    return sorted(
        sitios,
        key=lambda sitio: (
            _score_candidato_para_quedar_solo(
                sitio,
                sitios,
            ),
        ),
        reverse=True,
    )


# ============================================================
# SELECCIÓN DE CIERRE
# ============================================================


def construir_seleccion_cierre_mensual(
    *,
    universo,
    objetivo,
):
    """
    Construye una selección excepcional para cierre mensual.

    Este motor NO reemplaza el motor territorial normal.

    Se utiliza cuando el remanente mensual ya no puede formar
    una única zona semanal suficientemente compacta.

    Objetivos:

    - consumir razonablemente el remanente del mes;
    - mantener agrupaciones naturales;
    - evitar dejar sitios lejanos completamente aislados;
    - si inevitablemente queda una salida individual,
      preferir que sea un sitio más cercano a Santiago;
    - mantener todos los sitios como PRINCIPALES.

    Ejemplo:

        13 sitios restantes.

        4 Melipilla
        4 Limache / Villa Alemana
        2 Pirque
        1 Colina
        1 San José de Maipo
        1 Santiago cercano

    El motor intenta conservar agrupaciones territoriales
    antes de decidir qué sitio podría terminar siendo una
    salida individual.
    """

    universo = list(universo or [])

    if not universo:
        return []

    try:
        objetivo = int(objetivo)

    except (
        TypeError,
        ValueError,
    ):
        return []

    if objetivo <= 0:
        return []

    objetivo = min(
        objetivo,
        len(universo),
    )

    con_coordenadas = [sitio for sitio in universo if _tiene_coordenadas(sitio)]

    sin_coordenadas = [sitio for sitio in universo if not _tiene_coordenadas(sitio)]

    # ========================================================
    # PRIORIZAR AGRUPACIONES TERRITORIALES
    # ========================================================

    ranking = sorted(
        con_coordenadas,
        key=lambda sitio: (
            -_score_prioridad_cierre(
                sitio,
                con_coordenadas,
            ),
            (
                _distancia_santiago_km(sitio)
                if _distancia_santiago_km(sitio) is not None
                else float("inf")
            ),
            sitio.id_claro or "",
        ),
    )

    seleccionados = ranking[:objetivo]

    # ========================================================
    # SI EL OBJETIVO CONSUME TODO EL UNIVERSO
    # ========================================================
    #
    # En este caso no hay elección sobre cuáles entran.
    #
    # Pero sí queremos devolverlos en un orden que ayude
    # posteriormente al motor operacional.
    # ========================================================

    if objetivo >= len(universo):

        seleccionados = con_coordenadas + sin_coordenadas

    # ========================================================
    # ORDEN OPERACIONAL
    # ========================================================

    seleccionados = _ordenar_seleccion_cierre(seleccionados)

    # Los que no tienen coordenadas siempre al final.
    seleccionados.extend(
        sitio for sitio in sin_coordenadas if sitio not in seleccionados
    )

    return seleccionados[:objetivo]

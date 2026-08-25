from statistics import mean

from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km

# ============================================================
# CONFIGURACIÓN DE AISLAMIENTO
# ============================================================

VECINOS_AISLAMIENTO = 3

DISTANCIA_VECINO_AISLADO_KM = 18.0


# ============================================================
# LEJANÍA DESDE LAS BASES
# ============================================================

DISTANCIA_LEJANA_BASE_KM = 65.0

DISTANCIA_MUY_LEJANA_BASE_KM = 120.0


# ============================================================
# SALUD GLOBAL DEL TERRITORIO RESTANTE
# ============================================================
#
# Antes solamente mirábamos con fuerza:
#
#     sitio aislado + lejano
#
# Eso no detectaba correctamente:
#
#     3 sitios juntos lejos de Santiago
#     4 sitios juntos lejos de Santiago
#     5 sitios juntos lejos de Santiago
#
# porque técnicamente no estaban aislados.
#
# Ahora también medimos:
#
# - distancia media de TODO el remanente a su base más cercana;
# - percentil 75;
# - punto máximo.
#
# Así, dejar para W4 un pequeño bloque remoto también
# empeora el score del resto del mes.
# ============================================================

DISTANCIA_MEDIA_RESTANTE_BUENA_KM = 25.0
DISTANCIA_MEDIA_RESTANTE_MALA_KM = 95.0

DISTANCIA_P75_RESTANTE_BUENA_KM = 35.0
DISTANCIA_P75_RESTANTE_MALA_KM = 110.0

DISTANCIA_MAX_RESTANTE_BUENA_KM = 55.0
DISTANCIA_MAX_RESTANTE_MALA_KM = 140.0


# ============================================================
# UTILIDADES
# ============================================================


def _id_sitio(
    sitio,
):
    return sitio.sitio_planificado_id


def _sitio_tiene_coordenadas(
    sitio,
):
    return sitio.latitud is not None and sitio.longitud is not None


def _distancia_sitios(
    sitio_a,
    sitio_b,
):
    if not (_sitio_tiene_coordenadas(sitio_a) and _sitio_tiene_coordenadas(sitio_b)):
        return None

    return distancia_haversine_km(
        sitio_a.latitud,
        sitio_a.longitud,
        sitio_b.latitud,
        sitio_b.longitud,
    )


def _percentil(
    valores,
    proporcion,
):
    valores = sorted(valor for valor in valores if valor is not None)

    if not valores:
        return 0.0

    if len(valores) == 1:
        return float(valores[0])

    posicion = (len(valores) - 1) * proporcion

    inferior = int(posicion)

    superior = min(
        inferior + 1,
        len(valores) - 1,
    )

    fraccion = posicion - inferior

    return valores[inferior] + (valores[superior] - valores[inferior]) * fraccion


def _score_inverso_distancia(
    *,
    distancia,
    buena,
    mala,
    minimo=10.0,
):
    """
    Convierte una distancia residual en score.

    Cerca:
        score alto.

    Lejos:
        score bajo.

    Se utiliza únicamente sobre EL TERRITORIO QUE QUEDA,
    no sobre la zona que estamos seleccionando ahora.

    Por eso favorece estratégicamente consumir primero
    los bloques exteriores.
    """

    if distancia is None:
        return 50.0

    if distancia <= buena:
        return 100.0

    if distancia >= mala:
        return minimo

    rango = mala - buena

    exceso = distancia - buena

    proporcion = exceso / rango

    return 100 - proporcion * (100 - minimo)


# ============================================================
# NORMALIZAR BASES
# ============================================================


def _normalizar_bases(
    bases_operacionales,
):
    """
    Convierte diferentes representaciones de bases
    a una estructura uniforme.

    Acepta:

    - DisponibilidadCuadrillaSemana;
    - diccionarios provenientes del motor;
    - estructuras con base_latitud/base_longitud.
    """

    resultado = []

    for base in bases_operacionales or []:

        if isinstance(
            base,
            dict,
        ):

            activa = base.get(
                "activa",
                True,
            )

            if not activa:
                continue

            latitud = base.get(
                "base_latitud",
            )

            longitud = base.get(
                "base_longitud",
            )

            codigo = base.get("cuadrilla") or base.get("codigo") or ""

            nombre = base.get("cuadrilla_display") or base.get("nombre") or codigo

        else:

            if not getattr(
                base,
                "activa",
                True,
            ):
                continue

            latitud = getattr(
                base,
                "base_latitud_efectiva",
                None,
            )

            longitud = getattr(
                base,
                "base_longitud_efectiva",
                None,
            )

            if latitud is None:

                latitud = getattr(
                    base,
                    "base_latitud",
                    None,
                )

            if longitud is None:

                longitud = getattr(
                    base,
                    "base_longitud",
                    None,
                )

            codigo = getattr(
                base,
                "codigo_cuadrilla",
                "",
            )

            nombre = getattr(
                base,
                "nombre_cuadrilla",
                codigo,
            )

        try:
            latitud = float(latitud)

            longitud = float(longitud)

        except (
            TypeError,
            ValueError,
        ):
            continue

        resultado.append(
            {
                "codigo": (codigo),
                "nombre": (nombre),
                "latitud": (latitud),
                "longitud": (longitud),
            }
        )

    return resultado


# ============================================================
# DISTANCIA A BASES
# ============================================================


def distancia_a_base_mas_cercana_km(
    sitio,
    bases_operacionales,
):
    """
    Retorna la distancia geográfica aproximada entre el sitio
    y la base operacional activa más cercana.

    No reemplaza Google Routes.

    Se utiliza para decisiones territoriales relativas.
    """

    if not _sitio_tiene_coordenadas(sitio):
        return None

    bases = _normalizar_bases(bases_operacionales)

    if not bases:
        return None

    distancias = []

    for base in bases:

        distancia = distancia_haversine_km(
            base["latitud"],
            base["longitud"],
            sitio.latitud,
            sitio.longitud,
        )

        if distancia is not None:
            distancias.append(distancia)

    if not distancias:
        return None

    return min(distancias)


# ============================================================
# VECINDAD DE UN SITIO
# ============================================================


def _distancias_a_vecinos(
    *,
    sitio,
    universo,
):
    distancias = []

    for otro in universo:

        if _id_sitio(otro) == _id_sitio(sitio):
            continue

        distancia = _distancia_sitios(
            sitio,
            otro,
        )

        if distancia is not None:
            distancias.append(distancia)

    distancias.sort()

    return distancias


# ============================================================
# MÉTRICAS DE AISLAMIENTO
# ============================================================


def _metricas_aislamiento_sitio(
    *,
    sitio,
    universo,
    bases_operacionales=None,
):
    distancias = _distancias_a_vecinos(
        sitio=sitio,
        universo=universo,
    )

    distancia_base = distancia_a_base_mas_cercana_km(
        sitio,
        bases_operacionales,
    )

    if not distancias:

        return {
            "aislado": True,
            "distancia_vecino_km": None,
            "distancia_media_vecinos_km": None,
            "distancia_base_km": (distancia_base),
        }

    distancia_vecino = distancias[0]

    vecinos = distancias[
        : min(
            VECINOS_AISLAMIENTO,
            len(distancias),
        )
    ]

    distancia_media_vecinos = mean(vecinos)

    aislado = distancia_vecino > DISTANCIA_VECINO_AISLADO_KM

    return {
        "aislado": (aislado),
        "distancia_vecino_km": (distancia_vecino),
        "distancia_media_vecinos_km": (distancia_media_vecinos),
        "distancia_base_km": (distancia_base),
    }


# ============================================================
# DISTANCIAS DE TODO EL RESTO A LAS BASES
# ============================================================


def _metricas_distancia_restante_bases(
    *,
    restantes,
    bases_operacionales,
):
    """
    Analiza TODO el territorio que quedará para después.

    Esta es una diferencia fundamental frente al algoritmo
    anterior.

    No esperamos a que un sitio quede completamente aislado.

    Si una propuesta deja, por ejemplo:

        5 sitios en Melipilla
        4 sitios en Limache
        3 sitios en Valparaíso

    ese territorio todavía puede tener vecinos, pero sigue
    siendo estratégicamente más caro dejarlo para las últimas
    semanas que dejar sitios próximos a Santiago.

    Por eso medimos la distancia global del remanente.
    """

    distancias = []

    sitios_sin_distancia = 0

    for sitio in restantes:

        distancia = distancia_a_base_mas_cercana_km(
            sitio,
            bases_operacionales,
        )

        if distancia is None:

            sitios_sin_distancia += 1

            continue

        distancias.append(distancia)

    if not distancias:

        return {
            "distancias": [],
            "cantidad_con_distancia": 0,
            "cantidad_sin_distancia": (sitios_sin_distancia),
            "media_km": 0.0,
            "p75_km": 0.0,
            "maxima_km": 0.0,
            "lejanos_total": 0,
            "score_media": 50.0,
            "score_p75": 50.0,
            "score_maxima": 50.0,
            "score": 50.0,
        }

    distancia_media = mean(distancias)

    distancia_p75 = _percentil(
        distancias,
        0.75,
    )

    distancia_maxima = max(distancias)

    lejanos_total = sum(
        1 for distancia in distancias if distancia >= DISTANCIA_LEJANA_BASE_KM
    )

    score_media = _score_inverso_distancia(
        distancia=distancia_media,
        buena=(DISTANCIA_MEDIA_RESTANTE_BUENA_KM),
        mala=(DISTANCIA_MEDIA_RESTANTE_MALA_KM),
        minimo=15.0,
    )

    score_p75 = _score_inverso_distancia(
        distancia=distancia_p75,
        buena=(DISTANCIA_P75_RESTANTE_BUENA_KM),
        mala=(DISTANCIA_P75_RESTANTE_MALA_KM),
        minimo=10.0,
    )

    score_maxima = _score_inverso_distancia(
        distancia=distancia_maxima,
        buena=(DISTANCIA_MAX_RESTANTE_BUENA_KM),
        mala=(DISTANCIA_MAX_RESTANTE_MALA_KM),
        minimo=10.0,
    )

    # Damos más importancia al P75 porque responde:
    #
    # ¿la cola del territorio que estamos dejando
    # sigue demasiado lejos?
    #
    score = score_media * 0.30 + score_p75 * 0.45 + score_maxima * 0.25

    return {
        "distancias": (distancias),
        "cantidad_con_distancia": (len(distancias)),
        "cantidad_sin_distancia": (sitios_sin_distancia),
        "media_km": round(
            distancia_media,
            2,
        ),
        "p75_km": round(
            distancia_p75,
            2,
        ),
        "maxima_km": round(
            distancia_maxima,
            2,
        ),
        "lejanos_total": (lejanos_total),
        "score_media": round(
            score_media,
            2,
        ),
        "score_p75": round(
            score_p75,
            2,
        ),
        "score_maxima": round(
            score_maxima,
            2,
        ),
        "score": round(
            max(
                min(
                    score,
                    100,
                ),
                0,
            ),
            2,
        ),
    }


# ============================================================
# ANALIZAR RESTO DEL MES
# ============================================================


def analizar_restante_mensual(
    *,
    universo,
    seleccionados,
    bases_operacionales=None,
):
    """
    Analiza cómo queda el mes después de retirar
    los sitios seleccionados.

    Evalúa CUATRO dimensiones:

    1. composición urbano/rural;

    2. sitios individualmente aislados;

    3. sitios aislados lejos de todas las bases;

    4. distancia global del territorio restante
       respecto de las bases.

    La dimensión 4 es clave para la nueva estrategia.

    Queremos que las primeras semanas vayan consumiendo
    progresivamente el territorio exterior, de manera que
    las últimas semanas queden cada vez más cerca de las
    bases operacionales.
    """

    ids_seleccionados = {_id_sitio(sitio) for sitio in seleccionados}

    restantes = [
        sitio for sitio in universo if (_id_sitio(sitio) not in ids_seleccionados)
    ]

    if not restantes:

        return {
            "restantes": [],
            "cantidad_restante": 0,
            "urbanos": 0,
            "rurales": 0,
            "sin_clasificar": 0,
            "aislados": [],
            "aislados_total": 0,
            "aislados_lejanos": 0,
            "sitios_lejanos_base_total": 0,
            "peor_distancia_base_km": 0.0,
            "distancia_media_base_restante_km": 0.0,
            "distancia_p75_base_restante_km": 0.0,
            "distancia_maxima_base_restante_km": 0.0,
            # Compatibilidad.
            "peor_distancia_santiago_km": 0.0,
            "score_composicion": 100.0,
            "score_aislamiento": 100.0,
            "score_distancia_residual": 100.0,
            "score_territorio_restante": 100.0,
            "score_total": 100.0,
        }

    # ========================================================
    # COMPOSICIÓN
    # ========================================================

    urbanos = sum(1 for sitio in restantes if sitio.urbano)

    rurales = sum(1 for sitio in restantes if sitio.rural)

    conocidos = urbanos + rurales

    sin_clasificar = max(
        len(restantes) - conocidos,
        0,
    )

    if conocidos:

        proporcion_urbana = urbanos / conocidos

        proporcion_rural = rurales / conocidos

        diferencia = abs(proporcion_urbana - proporcion_rural)

        score_composicion = 100 - diferencia * 55

    else:

        score_composicion = 65.0

    score_composicion = max(
        min(
            score_composicion,
            100,
        ),
        0,
    )

    # ========================================================
    # AISLAMIENTOS
    # ========================================================

    aislados = []

    for sitio in restantes:

        metricas = _metricas_aislamiento_sitio(
            sitio=sitio,
            universo=restantes,
            bases_operacionales=(bases_operacionales),
        )

        if not metricas["aislado"]:
            continue

        aislados.append(
            {
                "sitio": (sitio),
                "sitio_planificado_id": (_id_sitio(sitio)),
                "id_claro": (sitio.id_claro),
                "comuna": (sitio.comuna),
                "distancia_vecino_km": (metricas["distancia_vecino_km"]),
                "distancia_media_vecinos_km": (metricas["distancia_media_vecinos_km"]),
                "distancia_base_km": (metricas["distancia_base_km"]),
            }
        )

    aislados_total = len(aislados)

    # ========================================================
    # AISLADOS LEJANOS
    # ========================================================

    aislados_lejanos = [
        item
        for item in aislados
        if (
            item["distancia_base_km"] is not None
            and item["distancia_base_km"] >= DISTANCIA_LEJANA_BASE_KM
        )
    ]

    cantidad_aislados_lejanos = len(aislados_lejanos)

    distancias_base_aislados = [
        item["distancia_base_km"]
        for item in aislados
        if (item["distancia_base_km"] is not None)
    ]

    peor_distancia_base_aislado = (
        max(distancias_base_aislados) if distancias_base_aislados else 0.0
    )

    # ========================================================
    # SCORE AISLAMIENTO
    # ========================================================

    proporcion_aislados = aislados_total / max(
        len(restantes),
        1,
    )

    penalizacion_aislamiento = min(
        proporcion_aislados * 100,
        55,
    )

    penalizacion_aislados_lejanos = min(
        cantidad_aislados_lejanos * 18,
        55,
    )

    score_aislamiento = 100 - penalizacion_aislamiento - penalizacion_aislados_lejanos

    score_aislamiento = max(
        min(
            score_aislamiento,
            100,
        ),
        0,
    )

    # ========================================================
    # TERRITORIO RESTANTE COMPLETO
    # ========================================================

    metricas_restante_bases = _metricas_distancia_restante_bases(
        restantes=restantes,
        bases_operacionales=(bases_operacionales),
    )

    distancia_media_restante = metricas_restante_bases["media_km"]

    distancia_p75_restante = metricas_restante_bases["p75_km"]

    distancia_maxima_restante = metricas_restante_bases["maxima_km"]

    sitios_lejanos_base_total = metricas_restante_bases["lejanos_total"]

    score_territorio_restante = metricas_restante_bases["score"]

    # ========================================================
    # SCORE DISTANCIA RESIDUAL
    # ========================================================
    #
    # Conservamos esta métrica por compatibilidad.
    #
    # Ahora utiliza la peor distancia global restante,
    # no exclusivamente los sitios aislados.
    # ========================================================

    peor_distancia_base = max(
        peor_distancia_base_aislado,
        distancia_maxima_restante,
    )

    if peor_distancia_base <= 0:

        score_distancia_residual = 100.0

    elif peor_distancia_base <= DISTANCIA_LEJANA_BASE_KM:

        score_distancia_residual = 100.0

    elif peor_distancia_base >= DISTANCIA_MUY_LEJANA_BASE_KM:

        score_distancia_residual = 15.0

    else:

        rango = DISTANCIA_MUY_LEJANA_BASE_KM - DISTANCIA_LEJANA_BASE_KM

        exceso = peor_distancia_base - DISTANCIA_LEJANA_BASE_KM

        proporcion = exceso / rango

        score_distancia_residual = 100 - proporcion * 85

    score_distancia_residual = max(
        min(
            score_distancia_residual,
            100,
        ),
        0,
    )

    # ========================================================
    # SCORE TOTAL
    # ========================================================
    #
    # Antes:
    #
    # composición          25 %
    # aislamiento          45 %
    # peor residual        30 %
    #
    # Eso reaccionaba tarde: solo cuando un sitio ya estaba
    # prácticamente aislado.
    #
    # Ahora añadimos explícitamente la distancia global del
    # territorio restante.
    #
    # Esto hace que el motor entienda:
    #
    # "aunque esos 4 puntos sigan juntos, no me conviene
    # dejarlos para W4 si están mucho más lejos que otros
    # puntos que podría dejar".
    # ========================================================

    score_total = (
        score_composicion * 0.15
        + score_aislamiento * 0.25
        + score_distancia_residual * 0.20
        + score_territorio_restante * 0.40
    )

    return {
        "restantes": (restantes),
        "cantidad_restante": (len(restantes)),
        "urbanos": (urbanos),
        "rurales": (rurales),
        "sin_clasificar": (sin_clasificar),
        "aislados": (aislados),
        "aislados_total": (aislados_total),
        "aislados_lejanos": (cantidad_aislados_lejanos),
        "sitios_lejanos_base_total": (sitios_lejanos_base_total),
        "peor_distancia_base_km": round(
            peor_distancia_base,
            2,
        ),
        "distancia_media_base_restante_km": round(
            distancia_media_restante,
            2,
        ),
        "distancia_p75_base_restante_km": round(
            distancia_p75_restante,
            2,
        ),
        "distancia_maxima_base_restante_km": round(
            distancia_maxima_restante,
            2,
        ),
        # Compatibilidad temporal.
        "peor_distancia_santiago_km": round(
            peor_distancia_base,
            2,
        ),
        "score_composicion": round(
            score_composicion,
            2,
        ),
        "score_aislamiento": round(
            score_aislamiento,
            2,
        ),
        "score_distancia_residual": round(
            score_distancia_residual,
            2,
        ),
        "score_territorio_restante": round(
            score_territorio_restante,
            2,
        ),
        "score_total": round(
            max(
                min(
                    score_total,
                    100,
                ),
                0,
            ),
            2,
        ),
    }


# ============================================================
# SCORE PÚBLICO
# ============================================================


def score_balance_restante(
    *,
    universo,
    seleccionados,
    bases_operacionales=None,
):
    analisis = analizar_restante_mensual(
        universo=universo,
        seleccionados=seleccionados,
        bases_operacionales=(bases_operacionales),
    )

    return analisis["score_total"]

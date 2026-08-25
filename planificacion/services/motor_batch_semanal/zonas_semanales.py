from statistics import mean

from planificacion.services.motor_batch_semanal.balance_mensual import (
    analizar_restante_mensual, distancia_a_base_mas_cercana_km)
from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km

# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

VECINOS_DENSIDAD = 5


# ============================================================
# RADIO MÁXIMO DE UNA PROPUESTA SEMANAL
# ============================================================

RADIO_ZONA_URBANA_KM = 16.0
RADIO_ZONA_MIXTA_KM = 20.0
RADIO_ZONA_RURAL_KM = 26.0


# ============================================================
# DETECCIÓN DE SALTOS TERRITORIALES
# ============================================================

SALTO_ABSOLUTO_MAX_KM = 4.5
FACTOR_SALTO_RELATIVO = 1.35


# ============================================================
# PROPUESTAS DIFERENTES
# ============================================================

SOLAPAMIENTO_MAXIMO_PROPUESTAS = 0.78


# ============================================================
# COBERTURA MÍNIMA
# ============================================================

MINIMO_COBERTURA_PROPUESTA = 0.85
OBJETIVO_PEQUENO_EXACTO = 8


# ============================================================
# EXPANSIÓN CONTROLADA DE LA ZONA SEMANAL
# ============================================================
#
# IMPORTANTE:
#
# Estos límites pertenecen EXCLUSIVAMENTE a la construcción
# de la MACROZONA SEMANAL.
#
# NO representan:
#
# - distancia diaria entre sitios;
# - radio de un cluster operacional;
# - distancia permitida para una salida de cuadrilla.
#
# Esas restricciones continúan viviendo en clustering.py
# y salidas.py.
#
# Una semana puede contener varios subclusters separados
# entre sí, siempre que juntos formen un corredor territorial
# coherente.
# ============================================================

MARGEN_EXPANSION_FRONTERA_KM = 8.0


# ============================================================
# CONTACTO SEMANAL
# ============================================================
#
# Para un sitio aislado seguimos siendo conservadores.
#
# Pero si detrás del salto existe un BLOQUE REAL de sitios,
# permitimos que la macrozona semanal alcance ese bloque.
#
# Ejemplo real:
#
# El Monte
#     ↓
# 19-22 km
#     ↓
# Melipilla con 3 sitios juntos
#
# Eso puede ser perfectamente válido semanalmente aunque
# jamás queramos utilizar 19-22 km como distancia normal
# entre sitios de una salida diaria.
# ============================================================

DISTANCIA_CONTACTO_SEMANAL_NORMAL_KM = 13.60

DISTANCIA_CONTACTO_SEMANAL_BLOQUE_KM = 24.0


# ============================================================
# BLOQUE EXTERIOR SEMANAL
# ============================================================
#
# Un salto semanal largo solamente se justifica si al otro
# lado existe concentración.
#
# El candidato debe formar parte de un grupo de al menos
# 3 sitios dentro de aproximadamente 8 km.
# ============================================================

RADIO_VECINDAD_BLOQUE_SEMANAL_KM = 8.0

MIN_SITIOS_BLOQUE_SEMANAL = 3


# ============================================================
# RADIO EXTRA PARA BLOQUES SEMANALES
# ============================================================
#
# El radio normal de 16/20/26 km sigue siendo útil para una
# zona compacta.
#
# Sin embargo, cuando incorporamos un bloque exterior real,
# permitimos que la macrozona semanal se extienda más.
#
# La planificación diaria posteriormente dividirá esa
# macrozona en clusters operacionales pequeños.
# ============================================================

MARGEN_EXPANSION_BLOQUE_SEMANAL_KM = 18.0


# ============================================================
# REFINAMIENTO EXTERIOR
# ============================================================
#
# Si la zona ya alcanzó el objetivo, todavía debemos permitir
# que un bloque exterior coherente reemplace sitios interiores.
#
# De lo contrario:
#
# objetivo = 30
# zona alcanza 30
#
# y Melipilla jamás tiene oportunidad de entrar aunque sea
# estratégicamente mejor consumirla ahora.
# ============================================================

MEJORA_MINIMA_EXTERIOR_REEMPLAZO_KM = 8.0

PERDIDA_MAXIMA_CONCENTRACION_REEMPLAZO = 12.0


# ============================================================
# PESOS DE LA PROPUESTA
# ============================================================
#
# FILOSOFÍA ACTUAL
# ============================================================
#
# La selección semanal debe mirar tres cosas:
#
# 1. que la semana actual sea territorialmente coherente;
#
# 2. que NO dejemos para semanas futuras el territorio
#    más lejano respecto de las bases operacionales;
#
# 3. que, cuando existan varias alternativas equivalentes,
#    consumamos progresivamente el territorio desde afuera
#    hacia adentro.
#
# IMPORTANTE:
#
# Ya NO premiamos que la zona actual esté cerca de las bases.
#
# Eso producía exactamente el efecto contrario al deseado:
#
#     semana actual -> sitios cómodos/cercanos
#     últimas semanas -> residuos lejanos
#
# La accesibilidad desde bases se sigue calculando y mostrando,
# pero no se utiliza como premio por cercanía.
# ============================================================

PESO_CONCENTRACION_ACTUAL = 0.52
PESO_RESTO_MENSUAL = 0.38
PESO_PRIORIDAD_EXTERIOR = 0.10


# ============================================================
# PROTECCIÓN CONTRA RESIDUOS REMOTOS
# ============================================================

PENALIZACION_POR_AISLADO_LEJANO = 8.0
PENALIZACION_MAX_AISLADOS_LEJANOS = 28.0


# ============================================================
# PRIORIDAD TERRITORIAL DESDE LAS BASES
# ============================================================
#
# Una zona situada completamente cerca de las bases no tiene
# urgencia territorial.
#
# Una zona exterior sí debe recibir cierta prioridad porque,
# si la dejamos para después, puede convertirse en un bloque
# residual costoso.
#
# Esta señal NO sustituye concentración.
# Solo ayuda a decidir entre zonas razonablemente comparables.
# ============================================================

DISTANCIA_EXTERIOR_INICIO_KM = 20.0
DISTANCIA_EXTERIOR_ALTA_KM = 90.0


# ============================================================
# FRONTERA DESDE AFUERA HACIA ADENTRO
# ============================================================
#
# Cuando debemos completar una zona con sitios fronterizos,
# seguimos priorizando cercanía territorial.
#
# Pero entre dos candidatos parecidos, damos ventaja al que
# está más lejos de las bases para evitar dejarlo atrás.
# ============================================================

PESO_EXTERIORIDAD_FRONTERA = 0.10


# ============================================================
# TIEMPOS PROVISIONALES DESDE BASE
# ============================================================

FACTOR_DISTANCIA_VIAL = 1.28
VELOCIDAD_REFERENCIA_BASE_KMH = 55.0


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


def _distancia(
    sitio_a,
    sitio_b,
):
    return distancia_haversine_km(
        sitio_a.latitud,
        sitio_a.longitud,
        sitio_b.latitud,
        sitio_b.longitud,
    )


def _distancia_coordenadas(
    latitud_a,
    longitud_a,
    latitud_b,
    longitud_b,
):
    return distancia_haversine_km(
        latitud_a,
        longitud_a,
        latitud_b,
        longitud_b,
    )


def _percentil(
    valores,
    proporcion,
):
    valores = sorted(
        valor
        for valor in valores
        if valor is not None
    )

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

    return (
        valores[inferior]
        + (
            valores[superior]
            - valores[inferior]
        )
        * fraccion
    )


# ============================================================
# BASES OPERACIONALES
# ============================================================


def _normalizar_bases_operacionales(
    disponibilidades,
):
    bases = []

    for disponibilidad in disponibilidades or []:

        if not disponibilidad.activa:
            continue

        if not disponibilidad.tiene_base_operacional:
            continue

        try:
            latitud = float(
                disponibilidad.base_latitud_efectiva
            )

            longitud = float(
                disponibilidad.base_longitud_efectiva
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        bases.append(
            {
                "codigo": (
                    disponibilidad.codigo_cuadrilla
                ),
                "nombre": (
                    disponibilidad.nombre_cuadrilla
                ),
                "base_nombre": (
                    disponibilidad.base_nombre_efectiva
                ),
                "latitud": latitud,
                "longitud": longitud,
            }
        )

    return bases


# ============================================================
# DISTANCIA SITIO -> BASE MÁS CERCANA
# ============================================================


def _distancia_sitio_base_mas_cercana(
    *,
    sitio,
    disponibilidades,
):
    return distancia_a_base_mas_cercana_km(
        sitio,
        disponibilidades,
    )


# ============================================================
# DISTANCIA BASE -> CENTRO DE ZONA
# ============================================================


def analizar_accesibilidad_bases_zona(
    *,
    sitios,
    disponibilidades,
):
    """
    Calcula el punto medio geográfico de la zona
    y cuánto le costaría aproximadamente a cada cuadrilla
    llegar hasta ese territorio.

    Esta información continúa siendo importante para:

    - mostrarla al usuario;
    - evaluar operativamente la propuesta;
    - conocer el costo aproximado del desplazamiento.

    IMPORTANTE:

    Ya NO utilizamos este score para premiar automáticamente
    las zonas cercanas en la selección territorial mensual.

    La estrategia mensual busca consumir primero los bloques
    exteriores razonables y dejar los bloques cercanos para
    después.
    """

    metricas = calcular_metricas_zona(
        sitios
    )

    centro_latitud = metricas[
        "centro_latitud"
    ]

    centro_longitud = metricas[
        "centro_longitud"
    ]

    bases = _normalizar_bases_operacionales(
        disponibilidades
    )

    if (
        centro_latitud is None
        or centro_longitud is None
        or not bases
    ):

        return {
            "centro_latitud": (
                centro_latitud
            ),
            "centro_longitud": (
                centro_longitud
            ),
            "bases": [],
            "distancia_minima_km": None,
            "distancia_media_km": None,
            "minutos_minimos": None,
            "minutos_promedio": None,
            "score": 50.0,
        }

    resultados = []

    for base in bases:

        distancia_directa = (
            distancia_haversine_km(
                base["latitud"],
                base["longitud"],
                centro_latitud,
                centro_longitud,
            )
        )

        if distancia_directa is None:
            continue

        distancia_vial = (
            distancia_directa
            * FACTOR_DISTANCIA_VIAL
        )

        minutos = round(
            (
                distancia_vial
                / VELOCIDAD_REFERENCIA_BASE_KMH
            )
            * 60
        )

        resultados.append(
            {
                "codigo": (
                    base["codigo"]
                ),
                "nombre": (
                    base["nombre"]
                ),
                "base_nombre": (
                    base["base_nombre"]
                ),
                "distancia_directa_km": round(
                    distancia_directa,
                    2,
                ),
                "distancia_vial_estimada_km": round(
                    distancia_vial,
                    2,
                ),
                "minutos_estimados": (
                    minutos
                ),
            }
        )

    if not resultados:

        return {
            "centro_latitud": (
                centro_latitud
            ),
            "centro_longitud": (
                centro_longitud
            ),
            "bases": [],
            "distancia_minima_km": None,
            "distancia_media_km": None,
            "minutos_minimos": None,
            "minutos_promedio": None,
            "score": 50.0,
        }

    distancias = [
        item["distancia_directa_km"]
        for item in resultados
    ]

    minutos = [
        item["minutos_estimados"]
        for item in resultados
    ]

    distancia_minima = min(
        distancias
    )

    distancia_media = mean(
        distancias
    )

    minutos_minimos = min(
        minutos
    )

    minutos_promedio = mean(
        minutos
    )

    score_distancia = max(
        100
        - distancia_minima * 1.2,
        0,
    )

    score_promedio = max(
        100
        - distancia_media * 0.70,
        0,
    )

    score = (
        score_distancia * 0.65
        + score_promedio * 0.35
    )

    return {
        "centro_latitud": (
            centro_latitud
        ),
        "centro_longitud": (
            centro_longitud
        ),
        "bases": resultados,
        "distancia_minima_km": round(
            distancia_minima,
            2,
        ),
        "distancia_media_km": round(
            distancia_media,
            2,
        ),
        "minutos_minimos": int(
            minutos_minimos
        ),
        "minutos_promedio": round(
            minutos_promedio
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
# MÍNIMO DE SITIOS ACEPTABLE
# ============================================================


def _minimo_sitios_propuesta(
    objetivo,
):
    try:
        objetivo = int(
            objetivo
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0

    if objetivo <= 0:
        return 0

    if objetivo <= OBJETIVO_PEQUENO_EXACTO:
        return objetivo

    minimo = round(
        objetivo
        * MINIMO_COBERTURA_PROPUESTA
    )

    return max(
        min(
            minimo,
            objetivo,
        ),
        1,
    )


# ============================================================
# COMPOSICIÓN TERRITORIAL
# ============================================================


def _radio_maximo_para_sitios(
    sitios,
):
    if not sitios:
        return RADIO_ZONA_URBANA_KM

    urbanos = sum(
        1
        for sitio in sitios
        if sitio.urbano
    )

    rurales = sum(
        1
        for sitio in sitios
        if sitio.rural
    )

    total = len(
        sitios
    )

    proporcion_rural = (
        rurales / total
        if total
        else 0
    )

    if proporcion_rural >= 0.60:
        return RADIO_ZONA_RURAL_KM

    if rurales and urbanos:
        return RADIO_ZONA_MIXTA_KM

    if rurales:
        return RADIO_ZONA_RURAL_KM

    return RADIO_ZONA_URBANA_KM


# ============================================================
# CENTRO GEOGRÁFICO
# ============================================================


def centro_geografico_sitios(
    sitios,
):
    sitios_validos = [
        sitio
        for sitio in sitios
        if _sitio_tiene_coordenadas(
            sitio
        )
    ]

    if not sitios_validos:

        return (
            None,
            None,
        )

    latitud = mean(
        sitio.latitud
        for sitio in sitios_validos
    )

    longitud = mean(
        sitio.longitud
        for sitio in sitios_validos
    )

    return (
        latitud,
        longitud,
    )


# ============================================================
# MEDOIDE
# ============================================================


def _obtener_medoide(
    sitios,
):
    if not sitios:
        return None

    if len(sitios) == 1:
        return sitios[0]

    mejor = None

    for candidato in sitios:

        distancias = []

        for otro in sitios:

            if (
                _id_sitio(candidato)
                ==
                _id_sitio(otro)
            ):
                continue

            distancia = _distancia(
                candidato,
                otro,
            )

            if distancia is not None:
                distancias.append(
                    distancia
                )

        if not distancias:
            continue

        promedio = mean(
            distancias
        )

        if (
            mejor is None
            or promedio < mejor[0]
        ):

            mejor = (
                promedio,
                candidato,
            )

    if mejor is None:
        return sitios[0]

    return mejor[1]


# ============================================================
# DENSIDAD LOCAL
# ============================================================


def _score_densidad_sitio(
    sitio,
    universo,
):
    distancias = []

    for otro in universo:

        if (
            _id_sitio(sitio)
            ==
            _id_sitio(otro)
        ):
            continue

        distancia = _distancia(
            sitio,
            otro,
        )

        if distancia is not None:
            distancias.append(
                distancia
            )

    if not distancias:
        return float("inf")

    distancias.sort()

    vecinos = distancias[
        : min(
            VECINOS_DENSIDAD,
            len(distancias),
        )
    ]

    return mean(
        vecinos
    )


# ============================================================
# PRIORIDAD EXTERIOR DE UN SITIO
# ============================================================


def _prioridad_exterior_sitio(
    *,
    sitio,
    disponibilidades,
):
    """
    Devuelve qué tan exterior se encuentra el sitio
    respecto de la base operacional activa más cercana.

    Esta señal se utiliza para ampliar el universo de semillas.

    De esta manera no analizamos únicamente los núcleos más
    densos cercanos a Santiago.

    También aseguramos que zonas exteriores densas puedan
    convertirse en candidatas semanales.
    """

    distancia = (
        _distancia_sitio_base_mas_cercana(
            sitio=sitio,
            disponibilidades=disponibilidades,
        )
    )

    if distancia is None:
        return 0.0

    return distancia


# ============================================================
# MÉTRICAS DE UNA ZONA
# ============================================================


def calcular_metricas_zona(
    sitios,
):
    if not sitios:

        return {
            "cantidad": 0,
            "centro_latitud": None,
            "centro_longitud": None,
            "radio_km": 0.0,
            "distancia_media_km": 0.0,
            "distancia_p75_km": 0.0,
            "distancia_maxima_km": 0.0,
            "urbanos": 0,
            "rurales": 0,
        }

    (
        centro_latitud,
        centro_longitud,
    ) = centro_geografico_sitios(
        sitios
    )

    distancias_centro = []

    distancias_internas = []

    for sitio in sitios:

        distancia = (
            _distancia_coordenadas(
                centro_latitud,
                centro_longitud,
                sitio.latitud,
                sitio.longitud,
            )
        )

        if distancia is not None:
            distancias_centro.append(
                distancia
            )

    for indice, sitio_a in enumerate(
        sitios
    ):

        for sitio_b in sitios[
            indice + 1 :
        ]:

            distancia = _distancia(
                sitio_a,
                sitio_b,
            )

            if distancia is not None:
                distancias_internas.append(
                    distancia
                )

    return {
        "cantidad": len(
            sitios
        ),
        "centro_latitud": (
            centro_latitud
        ),
        "centro_longitud": (
            centro_longitud
        ),
        "radio_km": (
            max(distancias_centro)
            if distancias_centro
            else 0.0
        ),
        "distancia_media_km": (
            mean(distancias_internas)
            if distancias_internas
            else 0.0
        ),
        "distancia_p75_km": (
            _percentil(
                distancias_internas,
                0.75,
            )
            if distancias_internas
            else 0.0
        ),
        "distancia_maxima_km": (
            max(distancias_internas)
            if distancias_internas
            else 0.0
        ),
        "urbanos": sum(
            1
            for sitio in sitios
            if sitio.urbano
        ),
        "rurales": sum(
            1
            for sitio in sitios
            if sitio.rural
        ),
    }


# ============================================================
# CONSTRUCCIÓN DESDE SEMILLA
# ============================================================


def _construir_zona_desde_semilla(
    *,
    semilla,
    universo,
    objetivo,
):
    ranking = []

    for sitio in universo:

        distancia = _distancia(
            semilla,
            sitio,
        )

        if distancia is None:
            continue

        ranking.append(
            (
                distancia,
                sitio,
            )
        )

    ranking.sort(
        key=lambda elemento: (
            elemento[0]
        )
    )

    seleccionados = []

    distancia_anterior = None

    minimo_antes_de_detectar_salto = min(
        max(
            round(
                objetivo * 0.30
            ),
            6,
        ),
        objetivo,
    )

    for (
        distancia_actual,
        sitio,
    ) in ranking:

        if len(seleccionados) >= objetivo:
            break

        candidatos_temporales = (
            seleccionados
            + [sitio]
        )

        radio_maximo = (
            _radio_maximo_para_sitios(
                candidatos_temporales
            )
        )

        if (
            seleccionados
            and distancia_actual
            > radio_maximo
        ):
            break

        if (
            distancia_anterior is not None
            and len(seleccionados)
            >= minimo_antes_de_detectar_salto
        ):

            salto = (
                distancia_actual
                - distancia_anterior
            )

            factor = (
                distancia_actual
                / max(
                    distancia_anterior,
                    0.25,
                )
            )

            if (
                salto
                >= SALTO_ABSOLUTO_MAX_KM
                and factor
                >= FACTOR_SALTO_RELATIVO
            ):
                break

        seleccionados.append(
            sitio
        )

        distancia_anterior = (
            distancia_actual
        )

    return seleccionados


# ============================================================
# RECENTRADO
# ============================================================


def _recentrar_zona(
    *,
    sitios,
    universo,
    objetivo,
):
    if not sitios:
        return []

    centro_real = _obtener_medoide(
        sitios
    )

    if centro_real is None:
        return sitios

    return _construir_zona_desde_semilla(
        semilla=centro_real,
        universo=universo,
        objetivo=objetivo,
    )

# ============================================================
# DETECTAR BLOQUE SEMANAL ALREDEDOR DE UN CANDIDATO
# ============================================================


def _analizar_bloque_semanal_candidato(
    *,
    candidato,
    universo,
):
    """
    Determina si un sitio pertenece a una concentración
    territorial suficientemente clara como para justificar
    un salto dentro de la MACROZONA semanal.

    Ejemplo:

        Melipilla A
        Melipilla B
        Melipilla C

    si los tres están próximos entre sí, representan un
    bloque semanal.

    En cambio:

        un único sitio a 22 km

    no recibe esta excepción.
    """

    if not _sitio_tiene_coordenadas(candidato):
        return {
            "es_bloque": False,
            "cantidad": 0,
            "sitios": [],
        }

    vecinos = [candidato]

    for otro in universo:

        if _id_sitio(otro) == _id_sitio(candidato):
            continue

        if not _sitio_tiene_coordenadas(otro):
            continue

        distancia = _distancia(
            candidato,
            otro,
        )

        if distancia is None:
            continue

        if distancia <= RADIO_VECINDAD_BLOQUE_SEMANAL_KM:
            vecinos.append(otro)

    vecinos.sort(
        key=lambda sitio: (
            0
            if (_id_sitio(sitio) == _id_sitio(candidato))
            else (
                _distancia(
                    candidato,
                    sitio,
                )
                or 999999
            )
        )
    )

    es_bloque = len(vecinos) >= MIN_SITIOS_BLOQUE_SEMANAL

    return {
        "es_bloque": (es_bloque),
        "cantidad": (len(vecinos)),
        "sitios": (vecinos),
    }


# ============================================================
# COMPLETAR FRONTERA
# ============================================================


def _completar_zona_hasta_objetivo(
    *,
    zona,
    universo,
    objetivo,
    disponibilidades=None,
):
    """
    Completa la MACROZONA semanal.

    IMPORTANTE
    ==========================================================

    Aquí NO aplicamos las mismas restricciones de una salida
    diaria.

    Distinguimos dos casos:

    CASO 1
    ----------------------------------------------------------
    candidato normal / aislado

    Debe encontrarse relativamente cerca del territorio ya
    seleccionado.

    CASO 2
    ----------------------------------------------------------
    candidato perteneciente a un bloque territorial real

    Podemos atravesar una distancia mayor para incorporar
    dicho bloque dentro de la semana.

    Esto permite estructuras como:

        Talagante
            ↓
        El Monte
            ↓
        Isla de Maipo
            ↓
        Melipilla

    aunque posteriormente la planificación diaria divida todo
    eso en varios clusters y diferentes salidas.
    """

    zona = list(zona or [])

    universo = list(universo or [])

    if not zona:
        return []

    objetivo = min(
        int(objetivo),
        len(universo),
    )

    if len(zona) >= objetivo:
        return zona[:objetivo]

    ids_zona = {_id_sitio(sitio) for sitio in zona}

    while len(zona) < objetivo:

        metricas_actuales = calcular_metricas_zona(zona)

        centro_latitud = metricas_actuales["centro_latitud"]

        centro_longitud = metricas_actuales["centro_longitud"]

        ranking = []

        for candidato in universo:

            candidato_id = _id_sitio(candidato)

            if candidato_id in ids_zona:
                continue

            if not _sitio_tiene_coordenadas(candidato):
                continue

            # =================================================
            # DISTANCIA DEL CANDIDATO AL BORDE ACTUAL
            # =================================================

            distancias_zona = []

            for sitio_zona in zona:

                distancia = _distancia(
                    candidato,
                    sitio_zona,
                )

                if distancia is not None:

                    distancias_zona.append(distancia)

            if not distancias_zona:
                continue

            distancia_minima = min(distancias_zona)

            # =================================================
            # DISTANCIA AL CENTRO
            # =================================================

            distancia_centro = _distancia_coordenadas(
                centro_latitud,
                centro_longitud,
                candidato.latitud,
                candidato.longitud,
            )

            if distancia_centro is None:
                continue

            # =================================================
            # ¿PERTENECE A UN BLOQUE?
            # =================================================

            bloque = _analizar_bloque_semanal_candidato(
                candidato=candidato,
                universo=universo,
            )

            sitios_bloque_fuera = [
                sitio
                for sitio in bloque["sitios"]
                if _id_sitio(sitio) not in ids_zona
            ]

            es_bloque = (
                len(sitios_bloque_fuera)
                >= MIN_SITIOS_BLOQUE_SEMANAL
            )

            # =================================================
            # CONTACTO SEMANAL
            # =================================================

            if es_bloque:

                distancia_contacto_maxima = DISTANCIA_CONTACTO_SEMANAL_BLOQUE_KM

            else:

                distancia_contacto_maxima = DISTANCIA_CONTACTO_SEMANAL_NORMAL_KM

            if distancia_minima > distancia_contacto_maxima:
                continue

            # =================================================
            # RADIO RESULTANTE
            # =================================================

            zona_temporal = zona + [candidato]

            radio_base = _radio_maximo_para_sitios(zona_temporal)

            metricas_temporales = calcular_metricas_zona(zona_temporal)

            radio_resultante = metricas_temporales["radio_km"]

            # =================================================
            # RADIO SEMANAL PERMITIDO
            # =================================================

            if es_bloque:

                radio_expansion_maximo = radio_base + MARGEN_EXPANSION_BLOQUE_SEMANAL_KM

            else:

                # Si la macrozona ya fue extendida por un
                # bloque exterior, permitimos que los demás
                # integrantes cercanos de ese mismo bloque
                # continúen entrando.
                zona_ya_expandida = metricas_actuales["radio_km"] > (
                    radio_base + MARGEN_EXPANSION_FRONTERA_KM
                )

                if zona_ya_expandida:

                    radio_expansion_maximo = (
                        radio_base + MARGEN_EXPANSION_BLOQUE_SEMANAL_KM
                    )

                else:

                    radio_expansion_maximo = radio_base + MARGEN_EXPANSION_FRONTERA_KM

            if radio_resultante > radio_expansion_maximo:
                continue

            # =================================================
            # EXTERIORIDAD
            # =================================================

            distancia_base = _distancia_sitio_base_mas_cercana(
                sitio=candidato,
                disponibilidades=disponibilidades,
            )

            distancia_base = distancia_base if distancia_base is not None else 0.0

            # =================================================
            # SCORE
            # =================================================
            #
            # Seguimos protegiendo:
            #
            # - contacto con la zona;
            # - distancia al centro;
            # - radio resultante.
            #
            # Pero:
            #
            # - los candidatos exteriores tienen ventaja;
            # - un bloque real recibe una pequeña bonificación.
            # =================================================

            bonificacion_bloque = 4.0 if es_bloque else 0.0

            score_candidato = (
                distancia_minima * 0.46
                + distancia_centro * 0.22
                + radio_resultante * 0.17
                - distancia_base * PESO_EXTERIORIDAD_FRONTERA
                - bonificacion_bloque
            )

            ranking.append(
                (
                    score_candidato,
                    not es_bloque,
                    distancia_minima,
                    distancia_centro,
                    -distancia_base,
                    candidato,
                )
            )

        if not ranking:
            break

        ranking.sort(
            key=lambda elemento: (
                elemento[0],
                elemento[1],
                elemento[2],
                elemento[3],
                elemento[4],
            )
        )

        mejor_candidato = ranking[0][5]

        zona.append(mejor_candidato)

        ids_zona.add(_id_sitio(mejor_candidato))

    return zona

# ============================================================
# REFINAR ZONA COMPLETA CON BLOQUES EXTERIORES
# ============================================================


def _refinar_zona_con_bloques_exteriores(
    *,
    zona,
    universo,
    objetivo,
    disponibilidades=None,
):
    """
    Revisa una zona que YA alcanzó el objetivo.

    Problema que resuelve
    ==========================================================

    Supongamos:

        objetivo = 30

    El algoritmo llegó primero a:

        Maipú
        Buin
        Talagante
        El Monte
        ...

    y ya tiene 30 sitios.

    En ese momento _completar_zona_hasta_objetivo() no tiene
    ninguna razón para buscar más sitios.

    Sin embargo puede existir afuera:

        Melipilla A
        Melipilla B
        Melipilla C

    formando un bloque coherente.

    Estratégicamente puede ser preferible:

        incorporar los 3 de Melipilla ahora

    y retirar:

        3 sitios interiores próximos a las bases

    para dejarlos disponibles para semanas posteriores.

    Esta función realiza exactamente esa comparación.
    """

    zona = list(zona or [])

    universo = list(universo or [])

    if not zona:
        return []

    objetivo = min(
        int(objetivo),
        len(universo),
    )

    if len(zona) < objetivo:
        return zona

    zona = zona[:objetivo]

    ids_zona = {_id_sitio(sitio) for sitio in zona}

    # ========================================================
    # SCORE ORIGINAL
    # ========================================================

    score_original = score_zona_semanal(
        sitios=zona,
        objetivo=objetivo,
    )

    mejor_zona = list(zona)

    mejor_clave = None

    bloques_evaluados = set()

    # ========================================================
    # BUSCAR BLOQUES FUERA DE LA ZONA
    # ========================================================

    for candidato in universo:

        candidato_id = _id_sitio(candidato)

        if candidato_id in ids_zona:
            continue

        bloque_info = _analizar_bloque_semanal_candidato(
            candidato=candidato,
            universo=universo,
        )

        if not bloque_info["es_bloque"]:
            continue

        bloque = [
            sitio
            for sitio in bloque_info["sitios"]
            if (_id_sitio(sitio) not in ids_zona)
        ]

        if len(bloque) < MIN_SITIOS_BLOQUE_SEMANAL:
            continue

        # No necesitamos reemplazar más de unos pocos sitios
        # en una sola pasada.
        bloque = bloque[
            : min(
                len(bloque),
                5,
                objetivo,
            )
        ]

        firma_bloque = frozenset(_id_sitio(sitio) for sitio in bloque)

        if firma_bloque in bloques_evaluados:
            continue

        bloques_evaluados.add(firma_bloque)

        # ====================================================
        # EL BLOQUE DEBE ESTAR CONECTADO A LA MACROZONA
        # ====================================================

        distancias_contacto = []

        for sitio_bloque in bloque:

            for sitio_zona in zona:

                distancia = _distancia(
                    sitio_bloque,
                    sitio_zona,
                )

                if distancia is not None:
                    distancias_contacto.append(distancia)

        if not distancias_contacto:
            continue

        distancia_contacto = min(distancias_contacto)

        if distancia_contacto > DISTANCIA_CONTACTO_SEMANAL_BLOQUE_KM:
            continue

        # ====================================================
        # EXTERIORIDAD DEL BLOQUE
        # ====================================================

        distancias_base_bloque = []

        for sitio_bloque in bloque:

            distancia_base = _distancia_sitio_base_mas_cercana(
                sitio=sitio_bloque,
                disponibilidades=disponibilidades,
            )

            if distancia_base is not None:

                distancias_base_bloque.append(distancia_base)

        if not distancias_base_bloque:
            continue

        exterioridad_bloque = mean(distancias_base_bloque)

        cantidad_reemplazo = len(bloque)

        # ====================================================
        # CANDIDATOS INTERIORES A SALIR
        # ====================================================
        #
        # Los más cercanos a las bases son precisamente los
        # que resulta menos costoso dejar para otra semana.
        # ====================================================

        candidatos_salida = []

        for sitio_zona in zona:

            distancia_base = _distancia_sitio_base_mas_cercana(
                sitio=sitio_zona,
                disponibilidades=disponibilidades,
            )

            if distancia_base is None:
                continue

            candidatos_salida.append(
                (
                    distancia_base,
                    sitio_zona,
                )
            )

        candidatos_salida.sort(key=lambda elemento: (elemento[0]))

        sitios_a_retirar = [
            elemento[1] for elemento in candidatos_salida[:cantidad_reemplazo]
        ]

        if len(sitios_a_retirar) != cantidad_reemplazo:
            continue

        exterioridad_retirados = mean(
            elemento[0] for elemento in candidatos_salida[:cantidad_reemplazo]
        )

        mejora_exterior = exterioridad_bloque - exterioridad_retirados

        if mejora_exterior < MEJORA_MINIMA_EXTERIOR_REEMPLAZO_KM:
            continue

        ids_retirar = {_id_sitio(sitio) for sitio in sitios_a_retirar}

        zona_temporal = [
            sitio for sitio in zona if (_id_sitio(sitio) not in ids_retirar)
        ]

        zona_temporal.extend(bloque)

        # Protección por duplicados.
        temporal_unicos = []

        ids_temporales = set()

        for sitio in zona_temporal:

            sitio_id = _id_sitio(sitio)

            if sitio_id in ids_temporales:
                continue

            temporal_unicos.append(sitio)

            ids_temporales.add(sitio_id)

        zona_temporal = temporal_unicos[:objetivo]

        if len(zona_temporal) != objetivo:
            continue

        # ====================================================
        # RADIO DE LA MACROZONA
        # ====================================================

        metricas_temporales = calcular_metricas_zona(zona_temporal)

        radio_base = _radio_maximo_para_sitios(zona_temporal)

        radio_maximo_semanal = radio_base + MARGEN_EXPANSION_BLOQUE_SEMANAL_KM

        if metricas_temporales["radio_km"] > radio_maximo_semanal:
            continue

        # ====================================================
        # CONCENTRACIÓN RESULTANTE
        # ====================================================

        score_temporal = score_zona_semanal(
            sitios=zona_temporal,
            objetivo=objetivo,
        )

        perdida_concentracion = score_original - score_temporal

        if perdida_concentracion > PERDIDA_MAXIMA_CONCENTRACION_REEMPLAZO:
            continue

        # ====================================================
        # RANKING DEL REEMPLAZO
        # ====================================================
        #
        # Mayor:
        #
        # - exterioridad ganada;
        # - score territorial.
        #
        # Menor:
        #
        # - pérdida de concentración;
        # - salto hasta el bloque.
        # ====================================================

        clave = (
            mejora_exterior,
            -perdida_concentracion,
            score_temporal,
            -distancia_contacto,
        )

        if mejor_clave is None or clave > mejor_clave:

            mejor_clave = clave

            mejor_zona = zona_temporal

    return mejor_zona


# ============================================================
# SCORE DE CONCENTRACIÓN
# ============================================================


def score_zona_semanal(
    *,
    sitios,
    objetivo,
):
    if not sitios:
        return 0.0

    metricas = calcular_metricas_zona(
        sitios
    )

    cobertura = min(
        len(sitios)
        / max(
            objetivo,
            1,
        )
        * 100,
        100,
    )

    score_radio = max(
        100
        - metricas["radio_km"]
        * 3.0,
        0,
    )

    score_media = max(
        100
        - metricas["distancia_media_km"]
        * 4.0,
        0,
    )

    score_p75 = max(
        100
        - metricas["distancia_p75_km"]
        * 3.0,
        0,
    )

    score = (
        cobertura * 0.35
        + score_radio * 0.25
        + score_media * 0.20
        + score_p75 * 0.20
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


# ============================================================
# PRIORIDAD EXTERIOR DE UNA ZONA
# ============================================================


def _score_prioridad_exterior_zona(
    *,
    zona,
    disponibilidades,
):
    """
    Mide qué tan exterior es la zona respecto de las bases.

    Una zona lejana recibe mayor prioridad de consumo.

    Esto NO significa que siempre ganará.

    Todavía debe competir contra:

    - concentración;
    - continuidad;
    - salud del resto mensual;
    - cobertura del objetivo.

    La intención es únicamente establecer una estrategia
    mensual progresiva:

        exterior
            ->
        intermedio
            ->
        cercano a las bases
    """

    distancias = []

    for sitio in zona:

        distancia = (
            _distancia_sitio_base_mas_cercana(
                sitio=sitio,
                disponibilidades=disponibilidades,
            )
        )

        if distancia is not None:
            distancias.append(
                distancia
            )

    if not distancias:
        return {
            "score": 50.0,
            "distancia_media_km": None,
            "distancia_p75_km": None,
            "distancia_maxima_km": None,
        }

    distancia_media = mean(
        distancias
    )

    distancia_p75 = _percentil(
        distancias,
        0.75,
    )

    distancia_maxima = max(
        distancias
    )

    distancia_representativa = (
        distancia_media * 0.40
        + distancia_p75 * 0.45
        + distancia_maxima * 0.15
    )

    if (
        distancia_representativa
        <= DISTANCIA_EXTERIOR_INICIO_KM
    ):

        score = 20.0

    elif (
        distancia_representativa
        >= DISTANCIA_EXTERIOR_ALTA_KM
    ):

        score = 100.0

    else:

        rango = (
            DISTANCIA_EXTERIOR_ALTA_KM
            - DISTANCIA_EXTERIOR_INICIO_KM
        )

        avance = (
            distancia_representativa
            - DISTANCIA_EXTERIOR_INICIO_KM
        )

        score = (
            20
            + (
                avance
                / rango
            )
            * 80
        )

    return {
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
        "distancia_media_km": round(
            distancia_media,
            2,
        ),
        "distancia_p75_km": round(
            distancia_p75,
            2,
        ),
        "distancia_maxima_km": round(
            distancia_maxima,
            2,
        ),
    }


# ============================================================
# IMPACTO SOBRE EL RESTO
# ============================================================


def _evaluar_impacto_restante(
    *,
    universo,
    zona,
    disponibilidades,
):
    analisis = (
        analizar_restante_mensual(
            universo=universo,
            seleccionados=zona,
            bases_operacionales=(
                disponibilidades
            ),
        )
    )

    penalizacion_extra = min(
        analisis["aislados_lejanos"]
        * PENALIZACION_POR_AISLADO_LEJANO,
        PENALIZACION_MAX_AISLADOS_LEJANOS,
    )

    score = max(
        analisis["score_total"]
        - penalizacion_extra,
        0,
    )

    return {
        "score": round(
            score,
            2,
        ),
        "analisis": analisis,
        "penalizacion_aislados_lejanos": (
            penalizacion_extra
        ),
    }


# ============================================================
# SCORE GLOBAL DE CANDIDATO
# ============================================================


def _score_candidato_zona(
    *,
    universo,
    zona,
    objetivo,
    disponibilidades,
):
    score_concentracion = (
        score_zona_semanal(
            sitios=zona,
            objetivo=objetivo,
        )
    )

    impacto = (
        _evaluar_impacto_restante(
            universo=universo,
            zona=zona,
            disponibilidades=(
                disponibilidades
            ),
        )
    )

    score_restante = impacto[
        "score"
    ]

    accesibilidad = (
        analizar_accesibilidad_bases_zona(
            sitios=zona,
            disponibilidades=(
                disponibilidades
            ),
        )
    )

    prioridad_exterior = (
        _score_prioridad_exterior_zona(
            zona=zona,
            disponibilidades=(
                disponibilidades
            ),
        )
    )

    score_exterior = prioridad_exterior[
        "score"
    ]

    # ========================================================
    # SCORE ESTRATÉGICO
    # ========================================================
    #
    # Ya NO usamos:
    #
    #     + score por estar cerca de la base
    #
    # porque eso causaba que las primeras semanas consumieran
    # Santiago/interior y las últimas acumularan zonas remotas.
    #
    # Ahora:
    #
    # - buena concentración sigue siendo lo principal;
    # - dejar un resto sano tiene mucho peso;
    # - una buena zona exterior recibe una pequeña prioridad.
    # ========================================================

    score_total = (
        score_concentracion
        * PESO_CONCENTRACION_ACTUAL
        + score_restante
        * PESO_RESTO_MENSUAL
        + score_exterior
        * PESO_PRIORIDAD_EXTERIOR
    )

    return {
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
        "score_concentracion": (
            score_concentracion
        ),
        "score_restante": (
            score_restante
        ),
        "score_bases": (
            accesibilidad["score"]
        ),
        "score_exterior": (
            score_exterior
        ),
        "prioridad_exterior": (
            prioridad_exterior
        ),
        "impacto_restante": (
            impacto
        ),
        "accesibilidad_bases": (
            accesibilidad
        ),
    }


# ============================================================
# SOLAPAMIENTO
# ============================================================


def _solapamiento(
    zona_a,
    zona_b,
):
    ids_a = {
        _id_sitio(sitio)
        for sitio in zona_a
    }

    ids_b = {
        _id_sitio(sitio)
        for sitio in zona_b
    }

    if not ids_a or not ids_b:
        return 0.0

    interseccion = len(
        ids_a.intersection(
            ids_b
        )
    )

    menor = min(
        len(ids_a),
        len(ids_b),
    )

    if menor <= 0:
        return 0.0

    return (
        interseccion
        / menor
    )


# ============================================================
# CONSTRUIR SEMILLAS ESTRATÉGICAS
# ============================================================


def _construir_semillas_estrategicas(
    *,
    universo,
    cantidad,
    disponibilidades,
):
    """
    Antes solamente probábamos las semillas más densas.

    Eso puede dejar fuera una concentración exterior completa
    simplemente porque Santiago tiene mayor densidad absoluta.

    Ahora construimos la lista desde DOS perspectivas:

    1. mejores semillas por densidad;
    2. semillas más exteriores respecto de las bases.

    Luego eliminamos duplicados.

    Así una zona alejada y coherente sí entra al proceso de
    evaluación y puede competir contra las zonas interiores.
    """

    por_densidad = sorted(
        universo,
        key=lambda sitio: (
            _score_densidad_sitio(
                sitio,
                universo,
            )
        ),
    )

    por_exterioridad = sorted(
        universo,
        key=lambda sitio: (
            _prioridad_exterior_sitio(
                sitio=sitio,
                disponibilidades=(
                    disponibilidades
                ),
            )
        ),
        reverse=True,
    )

    limite_total = min(
        len(universo),
        max(
            cantidad * 24,
            48,
        ),
    )

    limite_densidad = min(
        len(por_densidad),
        max(
            cantidad * 16,
            30,
        ),
    )

    limite_exterior = min(
        len(por_exterioridad),
        max(
            cantidad * 10,
            18,
        ),
    )

    resultado = []

    ids_agregados = set()

    # Intercalamos exterioridad y densidad.
    #
    # Esto evita que cualquiera de las dos señales monopolice
    # completamente la búsqueda.
    max_iteraciones = max(
        limite_densidad,
        limite_exterior,
    )

    for indice in range(
        max_iteraciones
    ):

        if (
            indice
            < limite_exterior
        ):

            sitio = (
                por_exterioridad[
                    indice
                ]
            )

            sitio_id = _id_sitio(
                sitio
            )

            if sitio_id not in ids_agregados:

                resultado.append(
                    sitio
                )

                ids_agregados.add(
                    sitio_id
                )

        if (
            indice
            < limite_densidad
        ):

            sitio = (
                por_densidad[
                    indice
                ]
            )

            sitio_id = _id_sitio(
                sitio
            )

            if sitio_id not in ids_agregados:

                resultado.append(
                    sitio
                )

                ids_agregados.add(
                    sitio_id
                )

        if (
            len(resultado)
            >= limite_total
        ):
            break

    return resultado


# ============================================================
# GENERAR ZONAS SEMANALES
# ============================================================


def generar_zonas_semanales(
    *,
    universo,
    objetivo,
    cantidad=3,
    disponibilidades=None,
):
    """
    Genera alternativas para UNA zona semanal.

    ESTRATEGIA ACTUAL
    ==========================================================

    La planificación mensual debe avanzar territorialmente
    desde las zonas exteriores hacia las zonas interiores.

    Eso significa:

    NO queremos:

        W1 cerca de Santiago
        W2 cerca de Santiago
        W3 partes intermedias
        W4 residuos alejados

    Queremos, cuando la geografía lo permita:

        W1 bloque exterior coherente
        W2 siguiente bloque exterior
        W3 bloque intermedio
        W4 residuos más cercanos a las bases

    Pero sin sacrificar la concentración semanal.

    Por eso evaluamos:

    1. concentración de la zona actual;
    2. salud territorial de lo que dejamos;
    3. prioridad estratégica por exterioridad.

    La accesibilidad desde bases se sigue calculando para
    informar al usuario y para la simulación operacional,
    pero ya no premia automáticamente las zonas cercanas.
    """

    universo = [
        sitio
        for sitio in universo
        if _sitio_tiene_coordenadas(
            sitio
        )
    ]

    if not universo:
        return []

    objetivo = min(
        int(objetivo),
        len(universo),
    )

    if objetivo <= 0:
        return []

    minimo_aceptable = (
        _minimo_sitios_propuesta(
            objetivo
        )
    )

    # ========================================================
    # SEMILLAS ESTRATÉGICAS
    # ========================================================

    semillas = (
        _construir_semillas_estrategicas(
            universo=universo,
            cantidad=cantidad,
            disponibilidades=(
                disponibilidades
            ),
        )
    )

    candidatos = []

    firmas = set()

    for semilla in semillas:

        # ====================================================
        # ZONA NATURAL
        # ====================================================

        zona = (
            _construir_zona_desde_semilla(
                semilla=semilla,
                universo=universo,
                objetivo=objetivo,
            )
        )

        zona = (
            _recentrar_zona(
                sitios=zona,
                universo=universo,
                objetivo=objetivo,
            )
        )

        if not zona:
            continue

        # ====================================================
        # COMPLETAR FRONTERA
        # ====================================================

        if len(zona) < objetivo:

            zona = (
                _completar_zona_hasta_objetivo(
                    zona=zona,
                    universo=universo,
                    objetivo=objetivo,
                    disponibilidades=(
                        disponibilidades
                    ),
                )
            )

        if not zona:
            continue

        # ====================================================
        # REFINAR BLOQUES EXTERIORES
        # ====================================================
        #
        # Aunque ya hayamos alcanzado el objetivo, revisamos
        # si existe un bloque exterior coherente que sea mejor
        # consumir ahora y que pueda reemplazar sitios
        # interiores más fáciles de dejar para después.
        # ====================================================

        zona = (
            _refinar_zona_con_bloques_exteriores(
                zona=zona,
                universo=universo,
                objetivo=objetivo,
                disponibilidades=(
                    disponibilidades
                ),
            )
        )

        # ====================================================
        # COBERTURA MÍNIMA
        # ====================================================

        if (
            len(zona)
            < minimo_aceptable
        ):
            continue

        if len(zona) > objetivo:
            zona = zona[
                :objetivo
            ]

        firma = frozenset(
            _id_sitio(sitio)
            for sitio in zona
        )

        if firma in firmas:
            continue

        firmas.add(
            firma
        )

        metricas = (
            calcular_metricas_zona(
                zona
            )
        )

        evaluacion = (
            _score_candidato_zona(
                universo=universo,
                zona=zona,
                objetivo=objetivo,
                disponibilidades=(
                    disponibilidades
                ),
            )
        )

        impacto_restante = (
            evaluacion[
                "impacto_restante"
            ][
                "analisis"
            ]
        )

        accesibilidad = (
            evaluacion[
                "accesibilidad_bases"
            ]
        )

        prioridad_exterior = (
            evaluacion[
                "prioridad_exterior"
            ]
        )

        candidatos.append(
            {
                "sitios": zona,
                "score": (
                    evaluacion[
                        "score_total"
                    ]
                ),
                "score_concentracion": (
                    evaluacion[
                        "score_concentracion"
                    ]
                ),
                "score_restante": (
                    evaluacion[
                        "score_restante"
                    ]
                ),
                "score_bases": (
                    evaluacion[
                        "score_bases"
                    ]
                ),
                "score_exterior": (
                    evaluacion[
                        "score_exterior"
                    ]
                ),
                "prioridad_exterior": (
                    prioridad_exterior
                ),
                "metricas": (
                    metricas
                ),
                "semilla_id": (
                    semilla.sitio_planificado_id
                ),
                "objetivo": (
                    objetivo
                ),
                "cantidad_propuesta": (
                    len(zona)
                ),
                "cobertura_objetivo": round(
                    (
                        len(zona)
                        / max(
                            objetivo,
                            1,
                        )
                    )
                    * 100,
                    2,
                ),
                "accesibilidad_bases": (
                    accesibilidad
                ),
                "impacto_restante": {
                    "cantidad_restante": (
                        impacto_restante[
                            "cantidad_restante"
                        ]
                    ),
                    "urbanos": (
                        impacto_restante[
                            "urbanos"
                        ]
                    ),
                    "rurales": (
                        impacto_restante[
                            "rurales"
                        ]
                    ),
                    "aislados_total": (
                        impacto_restante[
                            "aislados_total"
                        ]
                    ),
                    "aislados_lejanos": (
                        impacto_restante[
                            "aislados_lejanos"
                        ]
                    ),
                    "peor_distancia_base_km": (
                        impacto_restante[
                            "peor_distancia_base_km"
                        ]
                    ),
                    "distancia_media_base_restante_km": (
                        impacto_restante.get(
                            "distancia_media_base_restante_km"
                        )
                    ),
                    "distancia_p75_base_restante_km": (
                        impacto_restante.get(
                            "distancia_p75_base_restante_km"
                        )
                    ),
                    "distancia_maxima_base_restante_km": (
                        impacto_restante.get(
                            "distancia_maxima_base_restante_km"
                        )
                    ),
                    # Compatibilidad temporal.
                    "peor_distancia_santiago_km": (
                        impacto_restante[
                            "peor_distancia_base_km"
                        ]
                    ),
                    "score_balance": (
                        impacto_restante[
                            "score_total"
                        ]
                    ),
                },
            }
        )

    # ========================================================
    # ORDENAR
    # ========================================================

    candidatos.sort(
        key=lambda candidato: (
            # El objetivo pedido por el usuario sigue
            # siendo la primera obligación.
            candidato[
                "cobertura_objetivo"
            ],
            # Después evaluamos la estrategia mensual completa.
            candidato[
                "score"
            ],
            # Ante resultados similares preferimos dejar
            # mejor territorio para las semanas futuras.
            candidato[
                "score_restante"
            ],
            # Después concentración.
            candidato[
                "score_concentracion"
            ],
            # Y finalmente consumimos primero la más exterior.
            candidato[
                "score_exterior"
            ],
            -candidato[
                "metricas"
            ][
                "radio_km"
            ],
        ),
        reverse=True,
    )

    # ========================================================
    # QUITAR DUPLICADAS
    # ========================================================

    seleccionadas = []

    for candidato in candidatos:

        repetida = False

        for existente in seleccionadas:

            if (
                _solapamiento(
                    candidato["sitios"],
                    existente["sitios"],
                )
                >= SOLAPAMIENTO_MAXIMO_PROPUESTAS
            ):

                repetida = True

                break

        if repetida:
            continue

        seleccionadas.append(
            candidato
        )

        if (
            len(seleccionadas)
            >= cantidad
        ):
            break

    if (
        not seleccionadas
        and candidatos
    ):

        seleccionadas.append(
            candidatos[0]
        )

    return seleccionadas


# ============================================================
# COMPATIBILIDAD TEMPORAL
# ============================================================


def obtener_candidatos_reserva_zona(
    *,
    principales,
    universo,
):
    if not principales:
        return []

    ids_principales = {
        _id_sitio(sitio)
        for sitio in principales
    }

    metricas = (
        calcular_metricas_zona(
            principales
        )
    )

    centro_latitud = metricas[
        "centro_latitud"
    ]

    centro_longitud = metricas[
        "centro_longitud"
    ]

    radio_base = metricas[
        "radio_km"
    ]

    candidatos = []

    for candidato in universo:

        if (
            _id_sitio(candidato)
            in ids_principales
        ):
            continue

        if not _sitio_tiene_coordenadas(
            candidato
        ):
            continue

        distancia_centro = (
            _distancia_coordenadas(
                centro_latitud,
                centro_longitud,
                candidato.latitud,
                candidato.longitud,
            )
        )

        if distancia_centro is None:
            continue

        if (
            distancia_centro
            > radio_base + 5.0
        ):
            continue

        candidatos.append(
            candidato
        )

    return candidatos

from statistics import mean

from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km
from planificacion.services.motor_batch_semanal.tipos import ClusterMotor

# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

# Radio máximo de contacto entre clusters.
#
# Antes era 30 km. Eso era demasiado permisivo para Santiago
# y permitía que agrupaciones territorialmente diferentes
# terminaran siendo candidatas a fusionarse.
RADIO_VECINDAD_KM = 14.0


# ============================================================
# TAMAÑO DE LOS CLUSTERS
# ============================================================

# Un cluster ya NO representa una macrozona completa semanal.
#
# Representa una concentración operacional de sitios.
#
# El orquestador podrá utilizar varios clusters durante la
# misma semana.
MAX_SITIOS_CLUSTER = 9


# ============================================================
# AFINIDAD
# ============================================================

# Vecinos utilizados para medir afinidad de frontera.
VECINOS_AFINIDAD = 3

# Para mover un sitio de un cluster a otro,
# el destino debe ser realmente mejor.
MEJORA_MINIMA_AFINIDAD_KM = 1.0

# Número máximo de pasadas de refinamiento.
MAX_ITERACIONES_REFINAMIENTO = 12


# ============================================================
# LÍMITES POR TIPO DE TERRITORIO
# ============================================================

# IMPORTANTE:
#
# Estos límites están orientados a formar concentraciones
# operacionales reales.
#
# No intentamos representar toda una comuna o macrozona.

LIMITES_URBANOS = {
    "radio_max_km": 6.5,
    "diametro_max_km": 14.0,
    "media_max_km": 6.0,
    "p75_max_km": 8.0,
    "enlace_max_km": 6.0,
}

LIMITES_MIXTOS = {
    "radio_max_km": 9.0,
    "diametro_max_km": 20.0,
    "media_max_km": 8.0,
    "p75_max_km": 11.0,
    "enlace_max_km": 8.0,
}

LIMITES_RURALES = {
    "radio_max_km": 14.0,
    "diametro_max_km": 30.0,
    "media_max_km": 11.0,
    "p75_max_km": 16.0,
    "enlace_max_km": 11.0,
}


# ============================================================
# UTILIDADES ESTADÍSTICAS
# ============================================================


def _percentil(
    valores,
    porcentaje,
):
    """
    Percentil simple con interpolación lineal.

    No necesitamos numpy para esta operación.
    """

    valores = sorted(valor for valor in valores if valor is not None)

    if not valores:
        return 0.0

    if len(valores) == 1:
        return float(valores[0])

    posicion = (len(valores) - 1) * porcentaje

    inferior = int(posicion)

    superior = min(
        inferior + 1,
        len(valores) - 1,
    )

    fraccion = posicion - inferior

    return valores[inferior] + (valores[superior] - valores[inferior]) * fraccion


# ============================================================
# DISTANCIAS
# ============================================================


def _clave_distancia(
    sitio_a,
    sitio_b,
):
    id_a = sitio_a.sitio_planificado_id
    id_b = sitio_b.sitio_planificado_id

    if id_a <= id_b:
        return (
            id_a,
            id_b,
        )

    return (
        id_b,
        id_a,
    )


def _distancia_entre_sitios(
    sitio_a,
    sitio_b,
    cache=None,
):
    if sitio_a.sitio_planificado_id == sitio_b.sitio_planificado_id:
        return 0.0

    clave = _clave_distancia(
        sitio_a,
        sitio_b,
    )

    if cache is not None and clave in cache:
        return cache[clave]

    distancia = distancia_haversine_km(
        sitio_a.latitud,
        sitio_a.longitud,
        sitio_b.latitud,
        sitio_b.longitud,
    )

    if cache is not None:
        cache[clave] = distancia

    return distancia


def _construir_cache_distancias(
    sitios,
):
    cache = {}

    for indice, sitio_a in enumerate(sitios):

        for sitio_b in sitios[indice + 1 :]:

            _distancia_entre_sitios(
                sitio_a,
                sitio_b,
                cache,
            )

    return cache


# ============================================================
# CLASIFICACIÓN DEL TERRITORIO
# ============================================================


def _limites_para_sitios(
    sitios,
):
    """
    Selecciona límites según la composición del grupo.

    Urbano:
        máxima compactación.

    Mixto:
        tolerancia intermedia.

    Rural:
        mayor extensión geográfica permitida.
    """

    if not sitios:
        return LIMITES_URBANOS

    rurales = sum(1 for sitio in sitios if sitio.rural)

    urbanos = sum(1 for sitio in sitios if sitio.urbano)

    total = len(sitios)

    proporcion_rural = rurales / total if total else 0

    if proporcion_rural >= 0.60:
        return LIMITES_RURALES

    if rurales > 0 and urbanos > 0:
        return LIMITES_MIXTOS

    if rurales > 0:
        return LIMITES_RURALES

    return LIMITES_URBANOS


# ============================================================
# MÉTRICAS DE UN GRUPO
# ============================================================


def _metricas_grupo(
    sitios,
    cache=None,
):
    if not sitios:

        return {
            "centro_latitud": None,
            "centro_longitud": None,
            "radio_km": 0.0,
            "distancia_media_km": 0.0,
            "distancia_maxima_km": 0.0,
            "distancia_p75_km": 0.0,
        }

    latitudes = [sitio.latitud for sitio in sitios if sitio.latitud is not None]

    longitudes = [sitio.longitud for sitio in sitios if sitio.longitud is not None]

    centro_latitud = mean(latitudes) if latitudes else None

    centro_longitud = mean(longitudes) if longitudes else None

    distancias_centro = []

    for sitio in sitios:

        distancia = distancia_haversine_km(
            centro_latitud,
            centro_longitud,
            sitio.latitud,
            sitio.longitud,
        )

        if distancia is not None:
            distancias_centro.append(distancia)

    distancias_internas = []

    for indice, sitio_a in enumerate(sitios):

        for sitio_b in sitios[indice + 1 :]:

            distancia = _distancia_entre_sitios(
                sitio_a,
                sitio_b,
                cache,
            )

            if distancia is not None:

                distancias_internas.append(distancia)

    return {
        "centro_latitud": (centro_latitud),
        "centro_longitud": (centro_longitud),
        "radio_km": (max(distancias_centro) if distancias_centro else 0.0),
        "distancia_media_km": (
            mean(distancias_internas) if distancias_internas else 0.0
        ),
        "distancia_maxima_km": (
            max(distancias_internas) if distancias_internas else 0.0
        ),
        "distancia_p75_km": (
            _percentil(
                distancias_internas,
                0.75,
            )
            if distancias_internas
            else 0.0
        ),
    }


# ============================================================
# DISTANCIA ENTRE DOS GRUPOS
# ============================================================


def _distancia_minima_entre_grupos(
    grupo_a,
    grupo_b,
    cache,
):
    distancias = []

    for sitio_a in grupo_a:

        for sitio_b in grupo_b:

            distancia = _distancia_entre_sitios(
                sitio_a,
                sitio_b,
                cache,
            )

            if distancia is not None:
                distancias.append(distancia)

    if not distancias:
        return None

    return min(distancias)


def _distancia_media_enlace(
    grupo_a,
    grupo_b,
    cache,
    cantidad=3,
):
    """
    Mira varios enlaces entre los dos grupos.

    Evita decidir una fusión solamente porque existe
    una pareja fronteriza muy cercana.
    """

    distancias = []

    for sitio_a in grupo_a:

        for sitio_b in grupo_b:

            distancia = _distancia_entre_sitios(
                sitio_a,
                sitio_b,
                cache,
            )

            if distancia is not None:

                distancias.append(distancia)

    if not distancias:
        return None

    distancias.sort()

    seleccionadas = distancias[
        : min(
            cantidad,
            len(distancias),
        )
    ]

    return mean(seleccionadas)


# ============================================================
# COHESIÓN LOCAL
# ============================================================


def _grupo_tiene_cohesion_local(
    sitios,
    cache,
):
    """
    Impide clusters unidos por una cadena de sitios.

    Para cada sitio verificamos que exista al menos otro
    integrante razonablemente cercano.

    Para grupos medianos/grandes también pedimos que una
    proporción importante tenga al menos dos vecinos locales.
    """

    if len(sitios) <= 2:
        return True

    limites = _limites_para_sitios(sitios)

    distancia_vecino = limites["enlace_max_km"]

    miembros_con_un_vecino = 0
    miembros_con_dos_vecinos = 0

    for sitio in sitios:

        distancias = []

        for otro in sitios:

            if sitio.sitio_planificado_id == otro.sitio_planificado_id:
                continue

            distancia = _distancia_entre_sitios(
                sitio,
                otro,
                cache,
            )

            if distancia is not None:

                distancias.append(distancia)

        distancias.sort()

        vecinos_cercanos = [
            distancia for distancia in distancias if distancia <= distancia_vecino
        ]

        if len(vecinos_cercanos) >= 1:
            miembros_con_un_vecino += 1

        if len(vecinos_cercanos) >= 2:
            miembros_con_dos_vecinos += 1

    total = len(sitios)

    # Todos deben tener al menos una conexión local.
    if miembros_con_un_vecino < total:
        return False

    # En clusters de 5 o más sitios exigimos además que
    # por lo menos 60 % tenga dos vecinos cercanos.
    if total >= 5:

        proporcion_doble = miembros_con_dos_vecinos / total

        if proporcion_doble < 0.60:
            return False

    return True


# ============================================================
# VALIDACIÓN DE CLUSTER
# ============================================================


def _grupo_es_valido(
    sitios,
    cache,
):
    if not sitios:
        return False

    if len(sitios) > MAX_SITIOS_CLUSTER:
        return False

    if len(sitios) == 1:
        return True

    limites = _limites_para_sitios(sitios)

    metricas = _metricas_grupo(
        sitios,
        cache,
    )

    if metricas["radio_km"] > limites["radio_max_km"]:
        return False

    if metricas["distancia_maxima_km"] > limites["diametro_max_km"]:
        return False

    if metricas["distancia_media_km"] > limites["media_max_km"]:
        return False

    if metricas["distancia_p75_km"] > limites["p75_max_km"]:
        return False

    if not _grupo_tiene_cohesion_local(
        sitios,
        cache,
    ):
        return False

    return True


# ============================================================
# FUSIÓN DE CLUSTERS
# ============================================================


def _fusion_es_valida(
    grupo_a,
    grupo_b,
    cache,
    radio_km,
):
    combinado = list(grupo_a) + list(grupo_b)

    if len(combinado) > MAX_SITIOS_CLUSTER:
        return False

    limites = _limites_para_sitios(combinado)

    distancia_minima = _distancia_minima_entre_grupos(
        grupo_a,
        grupo_b,
        cache,
    )

    if distancia_minima is None:
        return False

    enlace_maximo = min(
        limites["enlace_max_km"],
        radio_km,
    )

    if distancia_minima > enlace_maximo:
        return False

    if not _grupo_es_valido(
        combinado,
        cache,
    ):
        return False

    return True


def _score_fusion(
    grupo_a,
    grupo_b,
    cache,
):
    """
    Menor score = mejor fusión.

    Damos mayor importancia a la estructura interna final
    que al simple punto de contacto.
    """

    distancia_minima = _distancia_minima_entre_grupos(
        grupo_a,
        grupo_b,
        cache,
    )

    distancia_enlace = _distancia_media_enlace(
        grupo_a,
        grupo_b,
        cache,
    )

    combinado = list(grupo_a) + list(grupo_b)

    metricas = _metricas_grupo(
        combinado,
        cache,
    )

    return (
        (distancia_minima or 0) * 0.20
        + (distancia_enlace or 0) * 0.25
        + metricas["radio_km"] * 0.15
        + metricas["distancia_media_km"] * 0.15
        + metricas["distancia_p75_km"] * 0.25
    )


# ============================================================
# AFINIDAD SITIO / CLUSTER
# ============================================================


def _afinidad_sitio_cluster(
    sitio,
    cluster,
    cache,
):
    """
    Afinidad por los vecinos reales más cercanos.

    Menor valor = mejor pertenencia.
    """

    otros = [
        otro
        for otro in cluster
        if (otro.sitio_planificado_id != sitio.sitio_planificado_id)
    ]

    if not otros:
        return float("inf")

    distancias = []

    for otro in otros:

        distancia = _distancia_entre_sitios(
            sitio,
            otro,
            cache,
        )

        if distancia is not None:

            distancias.append(distancia)

    if not distancias:
        return float("inf")

    distancias.sort()

    vecinos = distancias[
        : min(
            VECINOS_AFINIDAD,
            len(distancias),
        )
    ]

    return mean(vecinos)


# ============================================================
# REFINAMIENTO DE FRONTERAS
# ============================================================


def _refinar_fronteras(
    grupos,
    cache,
):
    """
    Mueve puntos fronterizos solamente cuando existe
    una mejora territorial clara y ambos grupos continúan
    siendo válidos.
    """

    grupos = [list(grupo) for grupo in grupos if grupo]

    for _ in range(MAX_ITERACIONES_REFINAMIENTO):

        hubo_cambio = False

        for indice_actual in range(len(grupos)):

            grupo_actual = grupos[indice_actual]

            if len(grupo_actual) <= 1:
                continue

            for sitio in list(grupo_actual):

                afinidad_actual = _afinidad_sitio_cluster(
                    sitio,
                    grupo_actual,
                    cache,
                )

                mejor_indice = None

                mejor_afinidad = afinidad_actual

                for (
                    indice_otro,
                    grupo_otro,
                ) in enumerate(grupos):

                    if indice_otro == indice_actual:
                        continue

                    afinidad_otro = _afinidad_sitio_cluster(
                        sitio,
                        grupo_otro,
                        cache,
                    )

                    if afinidad_otro + MEJORA_MINIMA_AFINIDAD_KM >= mejor_afinidad:
                        continue

                    grupo_actual_nuevo = [
                        miembro
                        for miembro in grupo_actual
                        if (miembro.sitio_planificado_id != sitio.sitio_planificado_id)
                    ]

                    grupo_otro_nuevo = list(grupo_otro) + [sitio]

                    if grupo_actual_nuevo and not _grupo_es_valido(
                        grupo_actual_nuevo,
                        cache,
                    ):
                        continue

                    if not _grupo_es_valido(
                        grupo_otro_nuevo,
                        cache,
                    ):
                        continue

                    mejor_indice = indice_otro

                    mejor_afinidad = afinidad_otro

                if mejor_indice is None:
                    continue

                grupos[indice_actual].remove(sitio)

                grupos[mejor_indice].append(sitio)

                grupo_actual = grupos[indice_actual]

                hubo_cambio = True

        grupos = [grupo for grupo in grupos if grupo]

        if not hubo_cambio:
            break

    return grupos


# ============================================================
# FUSIÓN DE SINGLETONS
# ============================================================


def _fusionar_grupos_pequenos(
    grupos,
    cache,
    radio_km,
):
    """
    Intenta absorber solamente clusters de un sitio.

    No fuerza agrupaciones artificiales.
    """

    grupos = [list(grupo) for grupo in grupos if grupo]

    cambio = True

    while cambio:

        cambio = False

        grupos.sort(key=len)

        for indice, grupo in enumerate(list(grupos)):

            if len(grupo) > 1:
                continue

            candidatos = []

            for (
                indice_otro,
                otro,
            ) in enumerate(grupos):

                if indice_otro == indice:
                    continue

                if not _fusion_es_valida(
                    grupo,
                    otro,
                    cache,
                    radio_km,
                ):
                    continue

                candidatos.append(
                    (
                        _score_fusion(
                            grupo,
                            otro,
                            cache,
                        ),
                        indice_otro,
                    )
                )

            if not candidatos:
                continue

            candidatos.sort(key=lambda item: item[0])

            indice_destino = candidatos[0][1]

            grupos[indice_destino].extend(grupo)

            grupos.pop(indice)

            cambio = True

            break

    return grupos


# ============================================================
# ORDEN GEOGRÁFICO ESTABLE
# ============================================================


def _ordenar_grupos_geograficamente(
    grupos,
    cache,
):
    resultado = []

    for grupo in grupos:

        metricas = _metricas_grupo(
            grupo,
            cache,
        )

        resultado.append(
            (
                metricas["centro_latitud"],
                metricas["centro_longitud"],
                grupo,
            )
        )

    resultado.sort(
        key=lambda item: (
            -(item[0] if item[0] is not None else -999),
            (item[1] if item[1] is not None else 999),
        )
    )

    return [item[2] for item in resultado]


# ============================================================
# DETECCIÓN PRINCIPAL
# ============================================================


def detectar_clusters(
    sitios,
    radio_km=RADIO_VECINDAD_KM,
):
    """
    Detecta concentraciones territoriales operacionales.

    Filosofía:

    NO queremos:
        macrozonas gigantes.

    QUEREMOS:
        grupos compactos que posteriormente puedan ser
        utilizados por una o varias salidas diarias.

    Cada fusión debe respetar:

    - máximo de sitios;
    - radio;
    - diámetro;
    - distancia media;
    - percentil 75;
    - cohesión local;
    - continuidad territorial.

    Después refinamos fronteras para corregir puntos que
    pertenezcan claramente a otro grupo.
    """

    sitios_validos = [
        sitio
        for sitio in sitios
        if (sitio.latitud is not None and sitio.longitud is not None)
    ]

    if not sitios_validos:
        return []

    cache = _construir_cache_distancias(sitios_validos)

    # ========================================================
    # UN CLUSTER POR SITIO
    # ========================================================

    grupos = [[sitio] for sitio in sitios_validos]

    # ========================================================
    # CLUSTERING AGLOMERATIVO RESTRINGIDO
    # ========================================================

    while True:

        mejor = None

        for indice_a in range(len(grupos)):

            for indice_b in range(
                indice_a + 1,
                len(grupos),
            ):

                grupo_a = grupos[indice_a]

                grupo_b = grupos[indice_b]

                if not _fusion_es_valida(
                    grupo_a,
                    grupo_b,
                    cache,
                    radio_km,
                ):
                    continue

                score = _score_fusion(
                    grupo_a,
                    grupo_b,
                    cache,
                )

                if mejor is None or score < mejor[0]:

                    mejor = (
                        score,
                        indice_a,
                        indice_b,
                    )

        if mejor is None:
            break

        (
            _,
            indice_a,
            indice_b,
        ) = mejor

        grupos[indice_a] = grupos[indice_a] + grupos[indice_b]

        grupos.pop(indice_b)

    # ========================================================
    # REFINAMIENTO DE FRONTERAS
    # ========================================================

    grupos = _refinar_fronteras(
        grupos,
        cache,
    )

    # ========================================================
    # SINGLETONS
    # ========================================================

    grupos = _fusionar_grupos_pequenos(
        grupos,
        cache,
        radio_km,
    )

    # ========================================================
    # SEGUNDO REFINAMIENTO
    # ========================================================

    grupos = _refinar_fronteras(
        grupos,
        cache,
    )

    # ========================================================
    # ORDEN ESTABLE
    # ========================================================

    grupos_geograficos = _ordenar_grupos_geograficamente(
        grupos,
        cache,
    )

    clusters = []

    for numero, grupo in enumerate(
        grupos_geograficos,
        start=1,
    ):

        clusters.append(
            construir_cluster(
                grupo,
                numero,
            )
        )

    # Concentraciones grandes y compactas primero.
    return sorted(
        clusters,
        key=lambda cluster: (
            len(cluster.sitios),
            cluster.score_compactacion,
        ),
        reverse=True,
    )


# ============================================================
# CONSTRUCCIÓN DEL CLUSTER
# ============================================================


def construir_cluster(
    sitios,
    numero_cluster,
):
    metricas = _metricas_grupo(sitios)

    urbanos = sum(1 for sitio in sitios if sitio.urbano)

    rurales = sum(1 for sitio in sitios if sitio.rural)

    score_compactacion = calcular_score_compactacion(
        metricas["radio_km"],
        metricas["distancia_media_km"],
        metricas["distancia_p75_km"],
    )

    return ClusterMotor(
        id_cluster=(f"cluster_{numero_cluster}"),
        sitios=sitios,
        centro_latitud=(metricas["centro_latitud"]),
        centro_longitud=(metricas["centro_longitud"]),
        radio_km=round(
            metricas["radio_km"],
            2,
        ),
        urbanos=urbanos,
        rurales=rurales,
        distancia_media_km=round(
            metricas["distancia_media_km"],
            2,
        ),
        distancia_maxima_km=round(
            metricas["distancia_maxima_km"],
            2,
        ),
        score_compactacion=(score_compactacion),
    )


# ============================================================
# SCORE DE COMPACTACIÓN
# ============================================================


def calcular_score_compactacion(
    radio_km,
    distancia_media_km,
    distancia_p75_km=0.0,
):
    """
    El score ahora penaliza también la cola larga de
    distancias internas.

    Esto evita considerar excelente un cluster cuyo promedio
    sea bueno pero tenga varios puntos alejados.
    """

    score = 100.0

    score -= min(
        radio_km * 2.2,
        40,
    )

    score -= min(
        distancia_media_km * 1.8,
        30,
    )

    score -= min(
        distancia_p75_km * 1.4,
        30,
    )

    return round(
        max(
            score,
            0,
        ),
        2,
    )

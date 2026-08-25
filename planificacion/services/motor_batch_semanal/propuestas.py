from statistics import mean

from planificacion.services.motor_batch_semanal.accesos import \
    score_acceso_conjunto
from planificacion.services.motor_batch_semanal.balance_mensual import \
    score_balance_restante
from planificacion.services.motor_batch_semanal.cierre_mensual import \
    construir_seleccion_cierre_mensual
from planificacion.services.motor_batch_semanal.clustering import (
    construir_cluster, detectar_clusters)
from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km
from planificacion.services.motor_batch_semanal.orquestador import (
    ESTRATEGIA_BALANCEADA, ESTRATEGIA_COMPACTA, ESTRATEGIA_OPERATIVA,
    construir_plan_operativo_semana)
from planificacion.services.motor_batch_semanal.scoring import \
    score_total_propuesta
from planificacion.services.motor_batch_semanal.tipos import \
    PropuestaBatchMotor
from planificacion.services.motor_batch_semanal.zonas_semanales import (
    analizar_accesibilidad_bases_zona, calcular_metricas_zona,
    generar_zonas_semanales, score_zona_semanal)

# ============================================================
# SCORE DE VECINDAD
# ============================================================


def _score_vecindad(
    sitios,
):
    if len(sitios) <= 1:
        return 100.0

    resultados = []

    for sitio in sitios:

        distancias = []

        for otro in sitios:

            if sitio.sitio_planificado_id == otro.sitio_planificado_id:
                continue

            distancia = distancia_haversine_km(
                sitio.latitud,
                sitio.longitud,
                otro.latitud,
                otro.longitud,
            )

            if distancia is not None:
                distancias.append(distancia)

        if not distancias:
            continue

        distancias.sort()

        cercanos = distancias[
            : min(
                3,
                len(distancias),
            )
        ]

        resultados.append(mean(cercanos))

    if not resultados:
        return 30.0

    promedio = mean(resultados)

    return round(
        max(
            100 - promedio * 3.0,
            0,
        ),
        2,
    )


# ============================================================
# SCORE GEOGRÁFICO
# ============================================================


def _score_geografico(
    *,
    sitios,
    objetivo,
    modo_cierre_mensual=False,
):
    if not sitios:
        return 0.0

    score_vecindad = _score_vecindad(sitios)

    if modo_cierre_mensual:

        metricas = calcular_metricas_zona(sitios)

        score = (
            score_vecindad * 0.60
            + max(
                100 - metricas["distancia_media_km"] * 1.5,
                0,
            )
            * 0.40
        )

    else:

        score_zona = score_zona_semanal(
            sitios=sitios,
            objetivo=objetivo,
        )

        score = score_zona * 0.70 + score_vecindad * 0.30

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
# ESTRATEGIA
# ============================================================


def _estrategia_por_posicion(
    posicion,
):
    estrategias = [
        ESTRATEGIA_OPERATIVA,
        ESTRATEGIA_COMPACTA,
        ESTRATEGIA_BALANCEADA,
    ]

    indice = min(
        posicion,
        len(estrategias) - 1,
    )

    return estrategias[indice]


# ============================================================
# CLUSTER VISUAL
# ============================================================


def _construir_cluster_zona_semanal(
    sitios,
    *,
    objetivo,
    modo_cierre_mensual=False,
):
    if not sitios:
        return []

    if modo_cierre_mensual:

        return detectar_clusters(sitios)

    cluster = construir_cluster(
        sitios,
        1,
    )

    cluster.id_cluster = "zona_semanal"

    cluster.score_compactacion = score_zona_semanal(
        sitios=sitios,
        objetivo=objetivo,
    )

    return [cluster]


# ============================================================
# TEXTO DE ACCESO DESDE BASES
# ============================================================


def _texto_acceso_bases(
    accesibilidad,
):
    if not accesibilidad:
        return ""

    minutos = accesibilidad.get("minutos_minimos")

    distancia = accesibilidad.get("distancia_minima_km")

    if minutos is None or distancia is None:
        return ""

    return (
        f"Desde la base operacional más favorable, "
        f"el punto medio de la zona queda aproximadamente "
        f"a {distancia:.0f} km y "
        f"{minutos} minutos de traslado estimado. "
    )


# ============================================================
# RESUMEN OPERACIONAL POR CUADRILLA
# ============================================================


def _construir_resumen_cuadrillas(
    *,
    plan_operativo,
    disponibilidades,
):
    resumen = {}

    # ========================================================
    # CUADRILLAS DISPONIBLES
    # ========================================================

    for disponibilidad in disponibilidades:

        if not disponibilidad.activa:
            continue

        codigo = disponibilidad.codigo_cuadrilla

        resumen[codigo] = {
            "codigo": codigo,
            "nombre": (disponibilidad.nombre_cuadrilla),
            "tipo_vehiculo": (disponibilidad.tipo_vehiculo),
            "permite_urbano": (disponibilidad.permite_urbano),
            "permite_rural": (disponibilidad.permite_rural),
            "base_nombre": (disponibilidad.base_nombre_efectiva),
            "dias_disponibles": (disponibilidad.dias_disponibles),
            "trabaja_sabado": (disponibilidad.trabaja_sabado),
            "salidas": 0,
            "sitios": 0,
            "minutos_viaje": 0,
            "minutos_trabajo": 0,
            "minutos_total": 0,
            "utilizada": False,
        }

    # ========================================================
    # ACUMULAR SALIDAS
    # ========================================================

    for salida in plan_operativo.get(
        "salidas",
        [],
    ):

        codigo = salida.get("cuadrilla")

        if not codigo:
            continue

        if codigo not in resumen:

            resumen[codigo] = {
                "codigo": codigo,
                "nombre": salida.get(
                    "cuadrilla_display",
                    codigo,
                ),
                "tipo_vehiculo": "",
                "permite_urbano": False,
                "permite_rural": False,
                "base_nombre": "",
                "dias_disponibles": 0,
                "trabaja_sabado": False,
                "salidas": 0,
                "sitios": 0,
                "minutos_viaje": 0,
                "minutos_trabajo": 0,
                "minutos_total": 0,
                "utilizada": False,
            }

        item = resumen[codigo]

        item["salidas"] += 1

        item["sitios"] += int(
            salida.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )

        item["minutos_viaje"] += int(
            salida.get(
                "minutos_viaje",
                0,
            )
            or 0
        )

        item["minutos_trabajo"] += int(
            salida.get(
                "minutos_trabajo",
                0,
            )
            or 0
        )

        item["minutos_total"] += int(
            salida.get(
                "minutos_total",
                0,
            )
            or 0
        )

        item["utilizada"] = True

    # ========================================================
    # DERIVADOS
    # ========================================================

    resultado = []

    for item in resumen.values():

        dias_disponibles = max(
            int(
                item.get(
                    "dias_disponibles",
                    0,
                )
                or 0
            ),
            0,
        )

        salidas = max(
            int(
                item.get(
                    "salidas",
                    0,
                )
                or 0
            ),
            0,
        )

        sitios = max(
            int(
                item.get(
                    "sitios",
                    0,
                )
                or 0
            ),
            0,
        )

        if salidas:

            promedio_sitios_salida = sitios / salidas

        else:

            promedio_sitios_salida = 0.0

        if dias_disponibles:

            utilizacion_dias = min(
                salidas / dias_disponibles * 100,
                100,
            )

        else:

            utilizacion_dias = 0.0

        item["promedio_sitios_salida"] = round(
            promedio_sitios_salida,
            2,
        )

        item["utilizacion_dias_porcentaje"] = round(
            utilizacion_dias,
            1,
        )

        resultado.append(item)

    return resultado


# ============================================================
# MOTIVO
# ============================================================


def _construir_motivo(
    *,
    principales,
    metricas_zona,
    plan_operativo,
    score_geografico,
    score_balance,
    accesibilidad_bases,
    modo_cierre_mensual=False,
):
    urbanos = sum(1 for sitio in principales if sitio.urbano)

    rurales = sum(1 for sitio in principales if sitio.rural)

    propuestos = len(principales)

    simulados = plan_operativo["cantidad_sitios"]

    texto_bases = _texto_acceso_bases(accesibilidad_bases)

    if modo_cierre_mensual:

        return (
            f"Selección de cierre mensual con "
            f"{propuestos} sitios principales. "
            "El remanente ya no forma una única zona "
            "territorial suficientemente compacta, por lo que "
            "el motor conserva las agrupaciones naturales. "
            f"{texto_bases}"
            f"Distribución: {urbanos} urbanos y "
            f"{rurales} rurales. "
            f"La simulación operacional puede distribuir "
            f"{simulados} sitios en "
            f"{plan_operativo['total_salidas']} salidas: "
            f"{plan_operativo['salidas_3_sitios']} de 3, "
            f"{plan_operativo['salidas_2_sitios']} de 2 y "
            f"{plan_operativo['salidas_1_sitio']} de 1. "
            f"Eficiencia operacional "
            f"{plan_operativo['score_operativo']:.0f}/100."
        )

    texto_operacional = (
        f"La simulación operacional actual puede distribuir "
        f"{simulados} de estos {propuestos} sitios en "
        f"{plan_operativo['total_salidas']} salidas: "
        f"{plan_operativo['salidas_3_sitios']} de 3 sitios, "
        f"{plan_operativo['salidas_2_sitios']} de 2 y "
        f"{plan_operativo['salidas_1_sitio']} de 1."
    )

    return (
        f"Zona semanal concentrada con "
        f"{propuestos} sitios principales. "
        f"Radio aproximado de la zona: "
        f"{metricas_zona['radio_km']:.1f} km. "
        f"Distancia interna media: "
        f"{metricas_zona['distancia_media_km']:.1f} km. "
        f"{texto_bases}"
        f"Distribución principal: "
        f"{urbanos} urbanos y "
        f"{rurales} rurales. "
        f"{texto_operacional} "
        f"Calidad geográfica "
        f"{score_geografico:.0f}/100. "
        f"Eficiencia operacional "
        f"{plan_operativo['score_operativo']:.0f}/100. "
        f"Balance restante "
        f"{score_balance:.0f}/100."
    )


# ============================================================
# CONSTRUIR ZONA DE CIERRE
# ============================================================


def _construir_zona_cierre(
    *,
    universo,
    objetivo,
):
    seleccion = construir_seleccion_cierre_mensual(
        universo=universo,
        objetivo=objetivo,
    )

    if not seleccion:
        return None

    return {
        "sitios": (seleccion),
        "score": 0.0,
        "metricas": (calcular_metricas_zona(seleccion)),
        "semilla_id": None,
    }


# ============================================================
# GENERAR PROPUESTAS
# ============================================================


def generar_propuestas(
    *,
    universo,
    objetivo,
    cantidad_reserva=0,
    disponibilidades=None,
    capacidades=None,
    cantidad_propuestas=3,
):
    disponibilidades = list(disponibilidades or [])

    universo = list(universo or [])

    if not universo:
        return []

    if objetivo <= 0:
        return []

    objetivo = min(
        int(
            objetivo,
        ),
        len(
            universo,
        ),
    )

    # ========================================================
    # BASES
    # ========================================================

    bases_configuradas = [
        disponibilidad
        for disponibilidad in disponibilidades
        if (disponibilidad.activa and disponibilidad.tiene_base_operacional)
    ]

    if not bases_configuradas:
        return []

    # ========================================================
    # MOTOR TERRITORIAL
    # ========================================================

    zonas = generar_zonas_semanales(
        universo=universo,
        objetivo=objetivo,
        cantidad=max(
            cantidad_propuestas,
            3,
        ),
        disponibilidades=(disponibilidades),
    )

    modo_cierre_mensual = False

    # ========================================================
    # CIERRE
    # ========================================================

    mejor_cantidad = 0

    if zonas:

        mejor_cantidad = max(len(zona["sitios"]) for zona in zonas)

    if (
        objetivo
        >= len(
            universo,
        )
        and mejor_cantidad < objetivo
    ):

        zona_cierre = _construir_zona_cierre(
            universo=universo,
            objetivo=objetivo,
        )

        if zona_cierre:

            zonas = [
                zona_cierre,
            ]

            modo_cierre_mensual = True

    if not zonas:
        return []

    propuestas = []

    # ========================================================
    # EVALUAR
    # ========================================================

    for (
        posicion_zona,
        zona,
    ) in enumerate(
        zonas,
    ):

        if (
            len(
                propuestas,
            )
            >= cantidad_propuestas
        ):
            break

        principales = list(zona["sitios"])

        if not principales:
            continue

        metricas_zona = calcular_metricas_zona(principales)

        # ====================================================
        # ACCESIBILIDAD
        # ====================================================

        accesibilidad_bases = analizar_accesibilidad_bases_zona(
            sitios=principales,
            disponibilidades=(disponibilidades),
        )

        # ====================================================
        # SUBCLUSTERS OPERACIONALES
        # ====================================================

        clusters_operacionales = detectar_clusters(principales)

        if not clusters_operacionales:
            continue

        estrategia = _estrategia_por_posicion(posicion_zona)

        # ====================================================
        # PLAN OPERACIONAL
        # ====================================================

        plan_operativo = construir_plan_operativo_semana(
            clusters=(clusters_operacionales),
            disponibilidades=(disponibilidades),
            objetivo=len(principales),
            estrategia=(estrategia),
        )

        # ====================================================
        # DATOS OPERACIONALES REALES
        # ====================================================

        sitios_planificados = int(
            plan_operativo.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )

        faltantes_operacionales = max(
            len(principales) - sitios_planificados,
            0,
        )

        sitios_planificados_ids = list(
            plan_operativo.get(
                "sitio_ids",
                [],
            )
            or []
        )

        recompactacion_global_aplicada = bool(
            plan_operativo.get(
                "recompactacion_residual_global_aplicada",
                False,
            )
        )

        cantidad_sitios_plan_base = int(
            plan_operativo.get(
                "cantidad_sitios_plan_base",
                sitios_planificados,
            )
            or 0
        )

        cantidad_sitios_mejora = int(
            plan_operativo.get(
                "cantidad_sitios_mejora",
                0,
            )
            or 0
        )

        # ====================================================
        # RESUMEN CUADRILLAS
        # ====================================================

        resumen_cuadrillas = _construir_resumen_cuadrillas(
            plan_operativo=(plan_operativo),
            disponibilidades=(disponibilidades),
        )

        # ====================================================
        # SCORES
        # ====================================================

        score_geografico = _score_geografico(
            sitios=principales,
            objetivo=objetivo,
            modo_cierre_mensual=(modo_cierre_mensual),
        )

        score_capacidad = plan_operativo["score_operativo"]

        score_acceso = score_acceso_conjunto(principales)

        score_balance = score_balance_restante(
            universo=universo,
            seleccionados=principales,
            bases_operacionales=(disponibilidades),
        )

        score_total = score_total_propuesta(
            geografico=(score_geografico),
            capacidad=(score_capacidad),
            acceso=(score_acceso),
            balance_mensual=(score_balance),
            respaldo=0.0,
        )

        # ====================================================
        # CLUSTER VISUAL
        # ====================================================

        clusters_visuales = _construir_cluster_zona_semanal(
            principales,
            objetivo=objetivo,
            modo_cierre_mensual=(modo_cierre_mensual),
        )

        prioridad_exterior = zona.get(
            "prioridad_exterior",
            {},
        )

        impacto_restante = zona.get(
            "impacto_restante",
            {},
        )

        # ====================================================
        # PROPUESTA
        # ====================================================

        propuesta = PropuestaBatchMotor(
            codigo=(f"PROP-" f"{len(propuestas) + 1}"),
            principales=(principales),
            reservas=[],
            clusters=(clusters_visuales),
            score_geografico=(score_geografico),
            score_capacidad=(score_capacidad),
            score_acceso=(score_acceso),
            score_balance_mensual=(score_balance),
            score_respaldo=0.0,
            score_total=(score_total),
            motivo=(
                _construir_motivo(
                    principales=(principales),
                    metricas_zona=(metricas_zona),
                    plan_operativo=(plan_operativo),
                    score_geografico=(score_geografico),
                    score_balance=(score_balance),
                    accesibilidad_bases=(accesibilidad_bases),
                    modo_cierre_mensual=(modo_cierre_mensual),
                )
            ),
            metricas={
                # ============================================
                # TIPO
                # ============================================
                "modo_cierre_mensual": (modo_cierre_mensual),
                "zona_semanal": (not modo_cierre_mensual),
                # ============================================
                # COMPOSICIÓN
                # ============================================
                "estrategia": (estrategia),
                "urbanos": (metricas_zona["urbanos"]),
                "rurales": (metricas_zona["rurales"]),
                "reservas": 0,
                "objetivo": (objetivo),
                "sitios_propuestos": len(principales),
                # ============================================
                # GEOGRAFÍA
                # ============================================
                "radio_zona_km": round(
                    metricas_zona["radio_km"],
                    2,
                ),
                "distancia_media_zona_km": round(
                    metricas_zona["distancia_media_km"],
                    2,
                ),
                "distancia_p75_zona_km": round(
                    metricas_zona["distancia_p75_km"],
                    2,
                ),
                "distancia_maxima_zona_km": round(
                    metricas_zona["distancia_maxima_km"],
                    2,
                ),
                "centro_latitud": (metricas_zona["centro_latitud"]),
                "centro_longitud": (metricas_zona["centro_longitud"]),
                # ============================================
                # SCORE TERRITORIAL
                # ============================================
                "score_zona_semanal": (
                    score_zona_semanal(
                        sitios=principales,
                        objetivo=objetivo,
                    )
                ),
                # ============================================
                # PRIORIDAD EXTERIOR
                # ============================================
                "score_prioridad_exterior": (zona.get("score_exterior")),
                "distancia_exterior_media_km": (
                    prioridad_exterior.get("distancia_media_km")
                ),
                "distancia_exterior_p75_km": (
                    prioridad_exterior.get("distancia_p75_km")
                ),
                "distancia_exterior_maxima_km": (
                    prioridad_exterior.get("distancia_maxima_km")
                ),
                # ============================================
                # RESTO DEL MES
                # ============================================
                "restante_cantidad": (impacto_restante.get("cantidad_restante")),
                "restante_distancia_media_base_km": (
                    impacto_restante.get("distancia_media_base_restante_km")
                ),
                "restante_distancia_p75_base_km": (
                    impacto_restante.get("distancia_p75_base_restante_km")
                ),
                "restante_distancia_maxima_base_km": (
                    impacto_restante.get("distancia_maxima_base_restante_km")
                ),
                "restante_aislados": (impacto_restante.get("aislados_total")),
                "restante_aislados_lejanos": (impacto_restante.get("aislados_lejanos")),
                # ============================================
                # ACCESO DESDE BASES
                # ============================================
                "accesibilidad_bases": (accesibilidad_bases),
                "distancia_base_minima_km": (
                    accesibilidad_bases.get("distancia_minima_km")
                ),
                "distancia_base_media_km": (
                    accesibilidad_bases.get("distancia_media_km")
                ),
                "minutos_base_minimos": (accesibilidad_bases.get("minutos_minimos")),
                "minutos_base_promedio": (accesibilidad_bases.get("minutos_promedio")),
                "score_accesibilidad_bases": (accesibilidad_bases.get("score")),
                # ============================================
                # CUADRILLAS
                # ============================================
                "resumen_cuadrillas": (resumen_cuadrillas),
                # ============================================
                # SIMULACIÓN
                # ============================================
                "sitios_planificados": (sitios_planificados),
                "sitio_ids_planificados": (sitios_planificados_ids),
                "faltantes_operacionales": (faltantes_operacionales),
                "faltantes_objetivo": max(
                    objetivo - len(principales),
                    0,
                ),
                "score_operativo": (plan_operativo["score_operativo"]),
                "total_salidas": (plan_operativo["total_salidas"]),
                "salidas_3_sitios": (plan_operativo["salidas_3_sitios"]),
                "salidas_2_sitios": (plan_operativo["salidas_2_sitios"]),
                "salidas_1_sitio": (plan_operativo["salidas_1_sitio"]),
                "promedio_sitios_salida": (plan_operativo["promedio_sitios_salida"]),
                "minutos_viaje_estimados": (plan_operativo["minutos_viaje"]),
                "minutos_totales_estimados": (plan_operativo["minutos_total"]),
                "salidas_por_cuadrilla": (plan_operativo["salidas_por_cuadrilla"]),
                "cupos_por_cuadrilla": (plan_operativo["cupos_por_cuadrilla"]),
                # ============================================
                # OPTIMIZACIÓN RESIDUAL GLOBAL
                # ============================================
                "recompactacion_residual_global_aplicada": (
                    recompactacion_global_aplicada
                ),
                "cantidad_sitios_plan_base": (cantidad_sitios_plan_base),
                "cantidad_sitios_mejora": (cantidad_sitios_mejora),
                # ============================================
                # SUBCLUSTERS
                # ============================================
                "clusters_operacionales": [
                    {
                        "id_cluster": (cluster.id_cluster),
                        "cantidad": len(cluster.sitios),
                        "radio_km": (cluster.radio_km),
                        "score_compactacion": (cluster.score_compactacion),
                    }
                    for cluster in clusters_operacionales
                ],
                # ============================================
                # SALIDAS
                # ============================================
                "salidas": [
                    {
                        "cluster_id": (salida["cluster_id"]),
                        "cuadrilla": (salida["cuadrilla"]),
                        "cuadrilla_display": (salida["cuadrilla_display"]),
                        "sitio_ids": (salida["sitio_ids"]),
                        "orden": (salida["orden"]),
                        "cantidad_sitios": (salida["cantidad_sitios"]),
                        "minutos_viaje": (salida["minutos_viaje"]),
                        "minutos_trabajo": (salida["minutos_trabajo"]),
                        "minutos_total": (salida["minutos_total"]),
                        "margen_minutos": (salida["margen_minutos"]),
                        "jornada_extendida": (
                            salida.get(
                                "jornada_extendida",
                                False,
                            )
                        ),
                        "exceso_jornada_minutos": (
                            salida.get(
                                "exceso_jornada_minutos",
                                0,
                            )
                        ),
                        "recompactacion_residual": (
                            salida.get(
                                "recompactacion_residual",
                                False,
                            )
                        ),
                        "recompactacion_global": (
                            salida.get(
                                "recompactacion_global",
                                False,
                            )
                        ),
                    }
                    for salida in plan_operativo["salidas"]
                ],
            },
        )

        propuestas.append(propuesta)

    # ========================================================
    # ORDEN FINAL
    # ========================================================
    #
    # ESTA ES LA CORRECCIÓN IMPORTANTE.
    #
    # La propuesta ya NO gana solamente por score_total.
    #
    # PRIORIDAD ABSOLUTA:
    #
    # 1. más sitios realmente planificados;
    # 2. menos faltantes operacionales;
    # 3. más jornadas completas de 3;
    # 4. menos salidas unitarias;
    # 5. menos jornadas extendidas;
    # 6. menos salidas totales;
    # 7. finalmente score_total.
    #
    # Por ejemplo:
    #
    #     propuesta A:
    #         25 planificados
    #         score 88
    #
    # gana siempre a:
    #
    #     propuesta B:
    #         23 planificados
    #         score 94
    #
    # ========================================================

    propuestas.sort(
        key=lambda propuesta: (
            int(
                propuesta.metricas.get(
                    "sitios_planificados",
                    0,
                )
                or 0
            ),
            -int(
                propuesta.metricas.get(
                    "faltantes_operacionales",
                    0,
                )
                or 0
            ),
            int(
                propuesta.metricas.get(
                    "salidas_3_sitios",
                    0,
                )
                or 0
            ),
            -int(
                propuesta.metricas.get(
                    "salidas_1_sitio",
                    0,
                )
                or 0
            ),
            -int(
                sum(
                    1
                    for salida in propuesta.metricas.get(
                        "salidas",
                        [],
                    )
                    if salida.get(
                        "jornada_extendida",
                        False,
                    )
                )
            ),
            -int(
                propuesta.metricas.get(
                    "total_salidas",
                    0,
                )
                or 0
            ),
            float(propuesta.score_total or 0),
        ),
        reverse=True,
    )

    # ========================================================
    # RENOMBRAR SEGÚN ORDEN REAL
    # ========================================================

    for (
        indice,
        propuesta,
    ) in enumerate(
        propuestas,
        start=1,
    ):

        propuesta.codigo = f"PROP-{indice}"

    return propuestas[:cantidad_propuestas]

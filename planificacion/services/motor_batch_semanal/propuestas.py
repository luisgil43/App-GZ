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
    _analizar_bloque_semanal_candidato, _completar_zona_hasta_objetivo,
    _construir_semillas_estrategicas, _construir_zona_desde_semilla, _id_sitio,
    _prioridad_exterior_sitio, _recentrar_zona, _score_densidad_sitio,
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
# CLAVE DE CALIDAD DEL PLAN OPERATIVO
# ============================================================


def _clave_plan_operativo_propuesta(
    plan_operativo,
):
    """
    Compara dos planes operativos para decidir cuál debe
    conservarse durante la reparación de una propuesta.

    PRIORIDAD ABSOLUTA
    ==========================================================

    1. más sitios realmente planificados;
    2. menos faltantes;
    3. más salidas de 3;
    4. menos unitarios;
    5. menos jornadas extendidas;
    6. menos salidas totales;
    7. mejor score operativo;
    8. menor viaje;
    9. menor tiempo total.

    La cobertura domina completamente cualquier otra métrica.
    """

    cantidad_sitios = int(
        plan_operativo.get(
            "cantidad_sitios",
            0,
        )
        or 0
    )

    faltantes = int(
        plan_operativo.get(
            "faltantes_objetivo",
            0,
        )
        or 0
    )

    salidas_3 = int(
        plan_operativo.get(
            "salidas_3_sitios",
            0,
        )
        or 0
    )

    salidas_1 = int(
        plan_operativo.get(
            "salidas_1_sitio",
            0,
        )
        or 0
    )

    extendidas = int(
        plan_operativo.get(
            "salidas_jornada_extendida",
            0,
        )
        or 0
    )

    total_salidas = int(
        plan_operativo.get(
            "total_salidas",
            0,
        )
        or 0
    )

    score_operativo = float(
        plan_operativo.get(
            "score_operativo",
            0,
        )
        or 0
    )

    minutos_viaje = int(
        plan_operativo.get(
            "minutos_viaje",
            0,
        )
        or 0
    )

    minutos_total = int(
        plan_operativo.get(
            "minutos_total",
            0,
        )
        or 0
    )

    return (
        cantidad_sitios,
        -faltantes,
        salidas_3,
        -salidas_1,
        -extendidas,
        -total_salidas,
        score_operativo,
        -minutos_viaje,
        -minutos_total,
    )


# ============================================================
# CONSTRUIR PLAN PARA UNA SELECCIÓN CONCRETA
# ============================================================


def _construir_plan_para_seleccion(
    *,
    sitios,
    disponibilidades,
    estrategia,
):
    """
    Ejecuta clustering + orquestador para una selección
    concreta de sitios.

    Devuelve:

        {
            "sitios": [...],
            "clusters": [...],
            "plan": {...},
        }

    No modifica base de datos.
    """

    sitios = list(sitios or [])

    if not sitios:

        return {
            "sitios": [],
            "clusters": [],
            "plan": {
                "cantidad_sitios": 0,
                "faltantes_objetivo": 0,
                "salidas": [],
                "total_salidas": 0,
                "salidas_3_sitios": 0,
                "salidas_2_sitios": 0,
                "salidas_1_sitio": 0,
                "salidas_jornada_extendida": 0,
                "score_operativo": 0.0,
                "minutos_viaje": 0,
                "minutos_total": 0,
                "sitio_ids": [],
            },
        }

    clusters = detectar_clusters(sitios)

    if not clusters:

        return {
            "sitios": sitios,
            "clusters": [],
            "plan": {
                "cantidad_sitios": 0,
                "faltantes_objetivo": len(sitios),
                "salidas": [],
                "total_salidas": 0,
                "salidas_3_sitios": 0,
                "salidas_2_sitios": 0,
                "salidas_1_sitio": 0,
                "salidas_jornada_extendida": 0,
                "score_operativo": 0.0,
                "minutos_viaje": 0,
                "minutos_total": 0,
                "sitio_ids": [],
            },
        }

    plan = construir_plan_operativo_semana(
        clusters=clusters,
        disponibilidades=disponibilidades,
        objetivo=len(sitios),
        estrategia=estrategia,
    )

    return {
        "sitios": sitios,
        "clusters": clusters,
        "plan": plan,
    }


# ============================================================
# DISTANCIA DE UN CANDIDATO A LA SELECCIÓN
# ============================================================


def _distancia_candidato_a_seleccion(
    *,
    candidato,
    seleccion,
):
    """
    Calcula la distancia mínima entre un candidato exterior
    y cualquiera de los sitios de la selección actual.

    Se utiliza solamente para ordenar las pruebas de
    sustitución.

    No representa una restricción absoluta.
    """

    distancias = []

    for sitio in seleccion:

        if sitio.sitio_planificado_id == candidato.sitio_planificado_id:
            continue

        distancia = distancia_haversine_km(
            candidato.latitud,
            candidato.longitud,
            sitio.latitud,
            sitio.longitud,
        )

        if distancia is not None:

            distancias.append(distancia)

    if not distancias:
        return float("inf")

    return min(distancias)


# ============================================================
# ORDENAR CANDIDATOS EXTERNOS
# ============================================================


def _ordenar_candidatos_reparacion(
    *,
    candidatos,
    seleccion,
):
    """
    Ordena los sitios que actualmente están fuera de la
    propuesta.

    Primero probamos los que poseen mejor continuidad con
    algún territorio ya seleccionado.

    IMPORTANTE:

    Esta función solamente define el ORDEN de evaluación.

    No descarta candidatos lejanos.

    Todos pueden llegar a probarse.
    """

    candidatos = list(candidatos or [])

    candidatos.sort(
        key=lambda candidato: (
            _distancia_candidato_a_seleccion(
                candidato=candidato,
                seleccion=seleccion,
            ),
            candidato.sitio_planificado_id,
        )
    )

    return candidatos


# ============================================================
# REPARAR SELECCIÓN OPERACIONAL
# ============================================================


def _reparar_seleccion_operacional(
    *,
    seleccion_inicial,
    universo,
    objetivo,
    disponibilidades,
    estrategia,
):
    """
    Corrige una selección territorial que no consigue cubrir
    operacionalmente el objetivo.

    PROBLEMA QUE RESUELVE
    ==========================================================

    Ejemplo:

        universo:
            90 sitios

        objetivo:
            40

        selección territorial:
            40

        simulación operacional:
            39

    No aceptamos inmediatamente los 39.

    Primero identificamos cuáles de los 40 seleccionados
    quedaron sin planificar y los sustituimos por otros sitios
    del universo.

    La selección inicial NO es sagrada.

    PRIORIDAD
    ==========================================================

    El motor busca:

        40 / 40

    antes que:

        39 / 40

    aunque la segunda selección posea mejor score geográfico.

    ALGORITMO
    ==========================================================

    1. evaluar la selección original;
    2. obtener los sitios seleccionados que el plan no pudo
       utilizar;
    3. sustituir cada sitio problemático por candidatos
       exteriores;
    4. ejecutar nuevamente clustering + orquestador;
    5. conservar exclusivamente cambios que mejoren el plan;
    6. repetir hasta alcanzar el objetivo o hasta que ninguna
       sustitución mejore el resultado.

    No modifica base de datos.
    """

    universo = list(universo or [])

    objetivo = min(
        int(objetivo),
        len(universo),
    )

    # ========================================================
    # NORMALIZAR SELECCIÓN INICIAL
    # ========================================================

    seleccion = []

    ids_seleccionados = set()

    for sitio in seleccion_inicial or []:

        sitio_id = sitio.sitio_planificado_id

        if sitio_id in ids_seleccionados:
            continue

        seleccion.append(sitio)

        ids_seleccionados.add(sitio_id)

        if len(seleccion) >= objetivo:
            break

    # ========================================================
    # SI LA SELECCIÓN VINO CORTA, COMPLETAR HASTA OBJETIVO
    # ========================================================

    if len(seleccion) < objetivo:

        faltantes = [
            sitio
            for sitio in universo
            if (sitio.sitio_planificado_id not in ids_seleccionados)
        ]

        faltantes = _ordenar_candidatos_reparacion(
            candidatos=faltantes,
            seleccion=seleccion,
        )

        for sitio in faltantes:

            seleccion.append(sitio)

            ids_seleccionados.add(sitio.sitio_planificado_id)

            if len(seleccion) >= objetivo:
                break

    if not seleccion:

        return {
            "sitios": [],
            "clusters": [],
            "plan": None,
            "reparacion_aplicada": False,
            "cantidad_reemplazos": 0,
            "sitios_reemplazados": [],
        }

    # ========================================================
    # PLAN ORIGINAL
    # ========================================================

    resultado_actual = _construir_plan_para_seleccion(
        sitios=seleccion,
        disponibilidades=disponibilidades,
        estrategia=estrategia,
    )

    plan_actual = resultado_actual["plan"]

    clave_actual = _clave_plan_operativo_propuesta(plan_actual)

    cantidad_reemplazos = 0

    sitios_reemplazados = []

    reparacion_aplicada = False

    # ========================================================
    # CACHE
    # ========================================================

    cache_planes = {}

    firma_actual = frozenset(sitio.sitio_planificado_id for sitio in seleccion)

    cache_planes[firma_actual] = resultado_actual

    # ========================================================
    # ITERACIONES DE REPARACIÓN
    # ========================================================
    #
    # Nunca necesitamos infinitas sustituciones.
    #
    # Con objetivo 40 permitimos hasta 40 mejoras consecutivas.
    # ========================================================

    max_iteraciones = max(
        objetivo,
        1,
    )

    for _ in range(max_iteraciones):

        cantidad_planificada = int(
            plan_actual.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )

        # ====================================================
        # OBJETIVO COMPLETO
        # ====================================================

        if cantidad_planificada >= objetivo:

            break

        ids_planificados = set(
            plan_actual.get(
                "sitio_ids",
                [],
            )
            or []
        )

        # ====================================================
        # SITIOS SELECCIONADOS QUE NO ENTRARON AL PLAN
        # ====================================================

        sitios_problematicos = [
            sitio
            for sitio in seleccion
            if (sitio.sitio_planificado_id not in ids_planificados)
        ]

        # ====================================================
        # SEGURIDAD
        # ====================================================
        #
        # Si por alguna razón el plan reporta menos cobertura
        # pero no podemos identificar el sitio pendiente,
        # evaluamos primero los unitarios porque son los que
        # normalmente consumen capacidad con menor eficiencia.
        # ====================================================

        if not sitios_problematicos:

            ids_unitarios = set()

            for salida in plan_actual.get(
                "salidas",
                [],
            ):

                if (
                    int(
                        salida.get(
                            "cantidad_sitios",
                            0,
                        )
                        or 0
                    )
                    != 1
                ):
                    continue

                ids_unitarios.update(
                    salida.get(
                        "sitio_ids",
                        [],
                    )
                    or []
                )

            sitios_problematicos = [
                sitio
                for sitio in seleccion
                if (sitio.sitio_planificado_id in ids_unitarios)
            ]

        if not sitios_problematicos:
            break

        ids_seleccionados = {sitio.sitio_planificado_id for sitio in seleccion}

        candidatos_externos = [
            sitio
            for sitio in universo
            if (sitio.sitio_planificado_id not in ids_seleccionados)
        ]

        if not candidatos_externos:
            break

        candidatos_externos = _ordenar_candidatos_reparacion(
            candidatos=candidatos_externos,
            seleccion=seleccion,
        )

        # ====================================================
        # BUSCAR LA MEJOR SUSTITUCIÓN 1 POR 1
        # ====================================================

        mejor_resultado = None

        mejor_clave = clave_actual

        mejor_sale = None

        mejor_entra = None

        for sitio_sale in sitios_problematicos:

            for sitio_entra in candidatos_externos:

                nueva_seleccion = [
                    sitio
                    for sitio in seleccion
                    if (sitio.sitio_planificado_id != sitio_sale.sitio_planificado_id)
                ]

                nueva_seleccion.append(sitio_entra)

                firma = frozenset(
                    sitio.sitio_planificado_id for sitio in nueva_seleccion
                )

                if firma in cache_planes:

                    resultado_prueba = cache_planes[firma]

                else:

                    resultado_prueba = _construir_plan_para_seleccion(
                        sitios=nueva_seleccion,
                        disponibilidades=disponibilidades,
                        estrategia=estrategia,
                    )

                    cache_planes[firma] = resultado_prueba

                plan_prueba = resultado_prueba["plan"]

                clave_prueba = _clave_plan_operativo_propuesta(plan_prueba)

                # =============================================
                # SOLO ACEPTAMOS MEJORA REAL
                # =============================================

                if clave_prueba <= mejor_clave:
                    continue

                mejor_clave = clave_prueba

                mejor_resultado = resultado_prueba

                mejor_sale = sitio_sale

                mejor_entra = sitio_entra

                # =============================================
                # SI YA CONSEGUIMOS COBERTURA COMPLETA
                # =============================================
                #
                # No necesitamos seguir buscando una mejora de
                # cobertura.
                #
                # El ranking final de propuestas continuará
                # comparando las alternativas.
                # =============================================

                if (
                    int(
                        plan_prueba.get(
                            "cantidad_sitios",
                            0,
                        )
                        or 0
                    )
                    >= objetivo
                ):

                    break

            if (
                mejor_resultado is not None
                and int(
                    mejor_resultado["plan"].get(
                        "cantidad_sitios",
                        0,
                    )
                    or 0
                )
                >= objetivo
            ):

                break

        # ====================================================
        # NO EXISTE SUSTITUCIÓN MEJOR
        # ====================================================

        if mejor_resultado is None:
            break

        # ====================================================
        # APLICAR MEJORA EN MEMORIA
        # ====================================================

        seleccion = list(mejor_resultado["sitios"])

        resultado_actual = mejor_resultado

        plan_actual = mejor_resultado["plan"]

        clave_actual = mejor_clave

        reparacion_aplicada = True

        cantidad_reemplazos += 1

        sitios_reemplazados.append(
            {
                "sale_id": (mejor_sale.sitio_planificado_id if mejor_sale else None),
                "sale_id_claro": (mejor_sale.id_claro if mejor_sale else ""),
                "entra_id": (mejor_entra.sitio_planificado_id if mejor_entra else None),
                "entra_id_claro": (mejor_entra.id_claro if mejor_entra else ""),
                "cantidad_planificada_resultante": int(
                    plan_actual.get(
                        "cantidad_sitios",
                        0,
                    )
                    or 0
                ),
            }
        )

    # ========================================================
    # RESULTADO DEFINITIVO
    # ========================================================

    return {
        "sitios": list(resultado_actual["sitios"]),
        "clusters": list(resultado_actual["clusters"]),
        "plan": (resultado_actual["plan"]),
        "reparacion_aplicada": (reparacion_aplicada),
        "cantidad_reemplazos": (cantidad_reemplazos),
        "sitios_reemplazados": (sitios_reemplazados),
    }

# ============================================================
# CONSTRUIR MACROZONA SEMANAL MULTIBLOQUE
# ============================================================


def _construir_zona_multibloque(
    *,
    universo,
    objetivo,
    disponibilidades,
):
    """
    Construye una propuesta semanal utilizando varios bloques
    territoriales cuando una única macrozona continua no logra
    alcanzar el objetivo solicitado.

    EJEMPLO
    ==========================================================

        objetivo = 40

        bloque principal:
            26 sitios

        segundo bloque:
            8 sitios

        tercer bloque:
            6 sitios

        resultado:
            40 sitios

    IMPORTANTE
    ==========================================================

    Esta función opera a nivel SEMANAL.

    No significa que esos 40 sitios formen una única salida,
    ni un único cluster operacional.

    Posteriormente:

        detectar_clusters()

    dividirá nuevamente la selección en agrupaciones pequeñas
    y el orquestador construirá las jornadas reales.

    La prioridad de incorporación de nuevos bloques es:

        1. mayor cantidad de sitios;
        2. mayor exterioridad respecto de las bases;
        3. menor radio interno;
        4. mayor densidad territorial.

    Si ya no existe un bloque de al menos 3 sitios, se utiliza
    el mejor grupo natural restante para continuar acercándonos
    al objetivo.

    Esta función NO modifica base de datos.
    """

    universo = list(universo or [])

    disponibilidades = list(disponibilidades or [])

    if not universo:
        return None

    try:

        objetivo = int(objetivo)

    except (
        TypeError,
        ValueError,
    ):

        return None

    if objetivo <= 0:
        return None

    objetivo = min(
        objetivo,
        len(universo),
    )

    # ========================================================
    # 1. BUSCAR EL MEJOR BLOQUE INICIAL
    # ========================================================

    semillas = _construir_semillas_estrategicas(
        universo=universo,
        cantidad=3,
        disponibilidades=disponibilidades,
    )

    mejor_zona = []

    mejor_clave = None

    for semilla in semillas:

        zona = _construir_zona_desde_semilla(
            semilla=semilla,
            universo=universo,
            objetivo=objetivo,
        )

        zona = _recentrar_zona(
            sitios=zona,
            universo=universo,
            objetivo=objetivo,
        )

        if zona and len(zona) < objetivo:

            zona = _completar_zona_hasta_objetivo(
                zona=zona,
                universo=universo,
                objetivo=objetivo,
                disponibilidades=disponibilidades,
            )

        if not zona:
            continue

        metricas = calcular_metricas_zona(zona)

        exterioridades = []

        for sitio in zona:

            exterioridad = _prioridad_exterior_sitio(
                sitio=sitio,
                disponibilidades=disponibilidades,
            )

            if exterioridad is not None:
                exterioridades.append(float(exterioridad))

        exterioridad_media = mean(exterioridades) if exterioridades else 0.0

        clave = (
            len(zona),
            exterioridad_media,
            -float(
                metricas.get(
                    "radio_km",
                    0,
                )
                or 0
            ),
        )

        if mejor_clave is None or clave > mejor_clave:

            mejor_clave = clave

            mejor_zona = list(zona)

    if not mejor_zona:
        return None

    # ========================================================
    # 2. INICIAR SELECCIÓN
    # ========================================================

    seleccion = list(mejor_zona[:objetivo])

    ids_seleccionados = {_id_sitio(sitio) for sitio in seleccion}

    bloques_utilizados = [
        {
            "numero": 1,
            "cantidad": len(seleccion),
            "sitio_ids": [_id_sitio(sitio) for sitio in seleccion],
            "id_claros": [sitio.id_claro for sitio in seleccion],
        }
    ]

    # ========================================================
    # 3. COMPLETAR CON BLOQUES TERRITORIALES ADICIONALES
    # ========================================================

    while len(seleccion) < objetivo:

        restantes = [
            sitio for sitio in universo if (_id_sitio(sitio) not in ids_seleccionados)
        ]

        if not restantes:
            break

        candidatos_bloque = []

        firmas = set()

        # ====================================================
        # BLOQUES REALES DE AL MENOS 3
        # ====================================================

        for candidato in restantes:

            info = _analizar_bloque_semanal_candidato(
                candidato=candidato,
                universo=restantes,
            )

            bloque = list(
                info.get(
                    "sitios",
                    [],
                )
                or []
            )

            bloque = [
                sitio for sitio in bloque if (_id_sitio(sitio) not in ids_seleccionados)
            ]

            if len(bloque) < 3:

                continue

            firma = frozenset(_id_sitio(sitio) for sitio in bloque)

            if firma in firmas:
                continue

            firmas.add(firma)

            # =================================================
            # EXTERIORIDAD
            # =================================================

            exterioridades = []

            for sitio in bloque:

                exterioridad = _prioridad_exterior_sitio(
                    sitio=sitio,
                    disponibilidades=disponibilidades,
                )

                if exterioridad is not None:

                    exterioridades.append(float(exterioridad))

            exterioridad_media = mean(exterioridades) if exterioridades else 0.0

            # =================================================
            # DENSIDAD
            # =================================================

            densidades = []

            for sitio in bloque:

                densidad = _score_densidad_sitio(
                    sitio,
                    restantes,
                )

                if densidad != float("inf"):

                    densidades.append(float(densidad))

            densidad_media = mean(densidades) if densidades else 999999.0

            metricas_bloque = calcular_metricas_zona(bloque)

            candidatos_bloque.append(
                {
                    "bloque": bloque,
                    "cantidad": len(bloque),
                    "exterioridad": exterioridad_media,
                    "densidad": densidad_media,
                    "radio": float(
                        metricas_bloque.get(
                            "radio_km",
                            0,
                        )
                        or 0
                    ),
                }
            )

        # ====================================================
        # 4. FALLBACK:
        #    NO EXISTE BLOQUE >= 3
        # ====================================================

        if not candidatos_bloque:

            semillas_restantes = sorted(
                restantes,
                key=lambda sitio: (
                    _score_densidad_sitio(
                        sitio,
                        restantes,
                    )
                ),
            )

            if not semillas_restantes:
                break

            semilla = semillas_restantes[0]

            faltantes = objetivo - len(seleccion)

            bloque = _construir_zona_desde_semilla(
                semilla=semilla,
                universo=restantes,
                objetivo=faltantes,
            )

            if not bloque:

                bloque = [semilla]

            metricas_bloque = calcular_metricas_zona(bloque)

            candidatos_bloque.append(
                {
                    "bloque": list(bloque),
                    "cantidad": len(bloque),
                    "exterioridad": 0.0,
                    "densidad": (
                        _score_densidad_sitio(
                            semilla,
                            restantes,
                        )
                    ),
                    "radio": float(
                        metricas_bloque.get(
                            "radio_km",
                            0,
                        )
                        or 0
                    ),
                }
            )

        # ====================================================
        # 5. ELEGIR MEJOR BLOQUE
        # ====================================================

        candidatos_bloque.sort(
            key=lambda item: (
                int(
                    item.get(
                        "cantidad",
                        0,
                    )
                    or 0
                ),
                float(
                    item.get(
                        "exterioridad",
                        0,
                    )
                    or 0
                ),
                -float(
                    item.get(
                        "radio",
                        0,
                    )
                    or 0
                ),
                -float(
                    item.get(
                        "densidad",
                        999999,
                    )
                    or 999999
                ),
            ),
            reverse=True,
        )

        elegido = candidatos_bloque[0]

        faltantes = objetivo - len(seleccion)

        bloque_elegido = list(
            elegido.get(
                "bloque",
                [],
            )
            or []
        )[:faltantes]

        if not bloque_elegido:
            break

        incorporados = []

        for sitio in bloque_elegido:

            sitio_id = _id_sitio(sitio)

            if sitio_id in ids_seleccionados:
                continue

            seleccion.append(sitio)

            ids_seleccionados.add(sitio_id)

            incorporados.append(sitio)

        if not incorporados:
            break

        bloques_utilizados.append(
            {
                "numero": len(bloques_utilizados) + 1,
                "cantidad": len(incorporados),
                "sitio_ids": [_id_sitio(sitio) for sitio in incorporados],
                "id_claros": [sitio.id_claro for sitio in incorporados],
                "exterioridad_media_km": round(
                    float(
                        elegido.get(
                            "exterioridad",
                            0,
                        )
                        or 0
                    ),
                    2,
                ),
                "radio_km": round(
                    float(
                        elegido.get(
                            "radio",
                            0,
                        )
                        or 0
                    ),
                    2,
                ),
            }
        )

    # ========================================================
    # 6. RESULTADO
    # ========================================================

    if not seleccion:
        return None

    seleccion = seleccion[:objetivo]

    metricas = calcular_metricas_zona(seleccion)

    return {
        "sitios": seleccion,
        "score": score_zona_semanal(
            sitios=seleccion,
            objetivo=objetivo,
        ),
        "score_concentracion": score_zona_semanal(
            sitios=seleccion,
            objetivo=objetivo,
        ),
        "score_restante": 0.0,
        "score_bases": 0.0,
        "score_exterior": 0.0,
        "prioridad_exterior": {},
        "metricas": metricas,
        "semilla_id": None,
        "objetivo": objetivo,
        "cantidad_propuesta": len(seleccion),
        "cobertura_objetivo": round(
            (
                len(seleccion)
                / max(
                    objetivo,
                    1,
                )
            )
            * 100,
            2,
        ),
        "accesibilidad_bases": {},
        "impacto_restante": {},
        "fallback_multibloque": True,
        "bloques_semanales": bloques_utilizados,
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

    # ========================================================
    # VALIDACIONES BÁSICAS
    # ========================================================

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

    # ========================================================
    # BASES OPERACIONALES
    # ========================================================

    bases_configuradas = [
        disponibilidad
        for disponibilidad in disponibilidades
        if (disponibilidad.activa and disponibilidad.tiene_base_operacional)
    ]

    if not bases_configuradas:
        return []

    # ========================================================
    # MOTOR TERRITORIAL PRINCIPAL
    # ========================================================

    zonas = generar_zonas_semanales(
        universo=universo,
        objetivo=objetivo,
        cantidad=max(
            cantidad_propuestas,
            3,
        ),
        disponibilidades=disponibilidades,
    )

    modo_cierre_mensual = False

    # ========================================================
    # CIERRE MENSUAL REAL
    # ========================================================
    #
    # Solamente utilizamos el cierre mensual cuando el
    # objetivo corresponde realmente a consumir todo el
    # universo restante.
    #
    # Ejemplo:
    #
    #     universo = 18
    #     objetivo = 18
    #
    # En ese caso no tiene sentido exigir que todos formen
    # una única macrozona territorial.
    # ========================================================

    mejor_cantidad = 0

    if zonas:

        mejor_cantidad = max(
            len(
                zona.get(
                    "sitios",
                    [],
                )
                or []
            )
            for zona in zonas
        )

    if objetivo >= len(universo) and mejor_cantidad < objetivo:

        zona_cierre = _construir_zona_cierre(
            universo=universo,
            objetivo=objetivo,
        )

        if zona_cierre:

            zona_cierre["fallback_multibloque"] = False

            zona_cierre["fallback_global"] = False

            zona_cierre["bloques_semanales"] = []

            zonas = [zona_cierre]

            modo_cierre_mensual = True

    # ========================================================
    # FALLBACK TERRITORIAL MULTIBLOQUE
    # ========================================================
    #
    # Si el motor territorial normal no consiguió una zona,
    # NO saltamos inmediatamente al cierre global.
    #
    # Primero intentamos construir una semana compuesta por
    # varios bloques territoriales naturales.
    #
    # Ejemplo real:
    #
    #     objetivo = 40
    #
    #     bloque 1 = 26
    #     bloque 2 = 8
    #     bloque 3 = 6
    #
    #     total = 40
    #
    # Esto sigue siendo una selección SEMANAL.
    #
    # Posteriormente:
    #
    #     detectar_clusters()
    #
    # dividirá nuevamente los 40 sitios en clusters
    # operacionales pequeños.
    # ========================================================

    if not zonas:

        zona_multibloque = _construir_zona_multibloque(
            universo=universo,
            objetivo=objetivo,
            disponibilidades=(disponibilidades),
        )

        if zona_multibloque:

            zona_multibloque["fallback_multibloque"] = True

            zona_multibloque["fallback_global"] = False

            zonas = [zona_multibloque]

    # ========================================================
    # FALLBACK GLOBAL ABSOLUTO
    # ========================================================
    #
    # Solamente llegamos aquí si:
    #
    # 1. el motor territorial normal falló;
    # 2. tampoco pudimos construir una macrozona multibloque.
    #
    # Este es el último respaldo.
    #
    # Su objetivo es evitar devolver cero propuestas cuando
    # existen sitios suficientes en el universo.
    # ========================================================

    if not zonas:

        seleccion_fallback = construir_seleccion_cierre_mensual(
            universo=universo,
            objetivo=objetivo,
        )

        if seleccion_fallback:

            zonas = [
                {
                    "sitios": list(seleccion_fallback),
                    "score": 0.0,
                    "score_concentracion": 0.0,
                    "score_restante": 0.0,
                    "score_bases": 0.0,
                    "score_exterior": None,
                    "prioridad_exterior": {},
                    "metricas": (calcular_metricas_zona(seleccion_fallback)),
                    "semilla_id": None,
                    "objetivo": objetivo,
                    "cantidad_propuesta": len(seleccion_fallback),
                    "cobertura_objetivo": round(
                        (
                            len(seleccion_fallback)
                            / max(
                                objetivo,
                                1,
                            )
                        )
                        * 100,
                        2,
                    ),
                    "accesibilidad_bases": {},
                    "impacto_restante": {},
                    "fallback_multibloque": False,
                    "fallback_global": True,
                    "bloques_semanales": [],
                }
            ]

    # ========================================================
    # NINGUNA SELECCIÓN POSIBLE
    # ========================================================

    if not zonas:
        return []

    # ========================================================
    # PROPUESTAS
    # ========================================================

    propuestas = []

    # ========================================================
    # EVALUAR CADA ZONA
    # ========================================================

    for (
        posicion_zona,
        zona,
    ) in enumerate(zonas):

        if len(propuestas) >= cantidad_propuestas:
            break

        principales_iniciales = list(
            zona.get(
                "sitios",
                [],
            )
            or []
        )

        if not principales_iniciales:
            continue

        estrategia = _estrategia_por_posicion(posicion_zona)

        # ====================================================
        # REPARACIÓN OPERACIONAL
        # ====================================================
        #
        # La selección territorial inicial NO es sagrada.
        #
        # Ejemplo:
        #
        #     objetivo:
        #         40
        #
        #     selección territorial:
        #         40
        #
        #     motor operativo:
        #         39
        #
        # Antes de aceptar 39:
        #
        #     buscamos cuál de los 40 genera el problema
        #
        # y probamos:
        #
        #     sitio problemático
        #         ↓
        #     fuera
        #
        #     candidato del universo
        #         ↓
        #     dentro
        #
        # hasta intentar conseguir:
        #
        #     40 / 40.
        # ====================================================

        reparacion = _reparar_seleccion_operacional(
            seleccion_inicial=(principales_iniciales),
            universo=universo,
            objetivo=objetivo,
            disponibilidades=(disponibilidades),
            estrategia=estrategia,
        )

        principales = list(
            reparacion.get(
                "sitios",
                [],
            )
            or []
        )

        clusters_operacionales = list(
            reparacion.get(
                "clusters",
                [],
            )
            or []
        )

        plan_operativo = reparacion.get("plan")

        if not principales:
            continue

        if not clusters_operacionales:
            continue

        if not plan_operativo:
            continue

        # ====================================================
        # MÉTRICAS GEOGRÁFICAS REALES
        # ========================================================
        #
        # Se recalculan DESPUÉS de la reparación porque la
        # composición final pudo cambiar.
        # ====================================================

        metricas_zona = calcular_metricas_zona(principales)

        # ====================================================
        # ACCESIBILIDAD DESDE BASES
        # ====================================================

        accesibilidad_bases = analizar_accesibilidad_bases_zona(
            sitios=principales,
            disponibilidades=(disponibilidades),
        )

        # ====================================================
        # DATOS OPERACIONALES
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

        # ====================================================
        # OPTIMIZACIÓN RESIDUAL
        # ====================================================

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
        # RESUMEN POR CUADRILLA
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

        score_capacidad = float(
            plan_operativo.get(
                "score_operativo",
                0,
            )
            or 0
        )

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

        # ====================================================
        # DATOS TERRITORIALES DE LA ZONA ORIGINAL
        # ====================================================

        prioridad_exterior = (
            zona.get(
                "prioridad_exterior",
                {},
            )
            or {}
        )

        impacto_restante = (
            zona.get(
                "impacto_restante",
                {},
            )
            or {}
        )

        # ====================================================
        # REPARACIÓN OPERACIONAL
        # ====================================================

        reparacion_aplicada = bool(
            reparacion.get(
                "reparacion_aplicada",
                False,
            )
        )

        cantidad_reemplazos = int(
            reparacion.get(
                "cantidad_reemplazos",
                0,
            )
            or 0
        )

        sitios_reemplazados = list(
            reparacion.get(
                "sitios_reemplazados",
                [],
            )
            or []
        )

        # ====================================================
        # FLAGS DE FALLBACK
        # ====================================================

        fallback_multibloque = bool(
            zona.get(
                "fallback_multibloque",
                False,
            )
        )

        fallback_global = bool(
            zona.get(
                "fallback_global",
                False,
            )
        )

        bloques_semanales = list(
            zona.get(
                "bloques_semanales",
                [],
            )
            or []
        )

        # ====================================================
        # PROPUESTA
        # ====================================================

        propuesta = PropuestaBatchMotor(
            codigo=(f"PROP-" f"{len(propuestas) + 1}"),
            principales=principales,
            reservas=[],
            clusters=clusters_visuales,
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
                "fallback_multibloque": (fallback_multibloque),
                "fallback_global": (fallback_global),
                "bloques_semanales": (bloques_semanales),
                # ============================================
                # REPARACIÓN OPERACIONAL
                # ============================================
                "reparacion_operacional_aplicada": (reparacion_aplicada),
                "cantidad_reemplazos_operacionales": (cantidad_reemplazos),
                "sitios_reemplazados_operacionalmente": (sitios_reemplazados),
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
                # SIMULACIÓN OPERACIONAL
                # ============================================
                "sitios_planificados": (sitios_planificados),
                "sitio_ids_planificados": (sitios_planificados_ids),
                "faltantes_operacionales": (faltantes_operacionales),
                "faltantes_objetivo": max(
                    objetivo - sitios_planificados,
                    0,
                ),
                "score_operativo": (plan_operativo["score_operativo"]),
                "total_salidas": (plan_operativo["total_salidas"]),
                "salidas_3_sitios": (plan_operativo["salidas_3_sitios"]),
                "salidas_2_sitios": (plan_operativo["salidas_2_sitios"]),
                "salidas_1_sitio": (plan_operativo["salidas_1_sitio"]),
                "salidas_jornada_extendida": (
                    plan_operativo.get(
                        "salidas_jornada_extendida",
                        0,
                    )
                ),
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
                "reasignacion_ternas_protegidas_aplicada": (
                    bool(
                        plan_operativo.get(
                            "reasignacion_ternas_protegidas_aplicada",
                            False,
                        )
                    )
                ),
                # ============================================
                # SUBCLUSTERS OPERACIONALES
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
                        "terna_protegida": (
                            salida.get(
                                "terna_protegida",
                                False,
                            )
                        ),
                        "cuadrilla_reasignada_terna": (
                            salida.get(
                                "cuadrilla_reasignada_terna",
                                False,
                            )
                        ),
                    }
                    for salida in plan_operativo.get(
                        "salidas",
                        [],
                    )
                ],
            },
        )

        propuestas.append(propuesta)

    # ========================================================
    # ORDEN FINAL DE PROPUESTAS
    # ========================================================
    #
    # PRIORIDAD ABSOLUTA
    # ========================================================
    #
    # 1. MÁS SITIOS REALMENTE PLANIFICADOS.
    #
    # 2. MENOS FALTANTES.
    #
    # 3. MÁS JORNADAS COMPLETAS DE 3.
    #
    # 4. MENOS UNITARIOS.
    #
    # 5. MENOS JORNADAS EXTENDIDAS.
    #
    # 6. MENOS SALIDAS TOTALES.
    #
    # 7. MEJOR SCORE TOTAL.
    #
    # Una propuesta:
    #
    #     40 sitios
    #     score 82
    #
    # siempre gana frente a:
    #
    #     39 sitios
    #     score 96.
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
                propuesta.metricas.get(
                    "salidas_jornada_extendida",
                    0,
                )
                or 0
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
    # RENOMBRAR SEGÚN EL ORDEN REAL
    # ========================================================

    for (
        indice,
        propuesta,
    ) in enumerate(
        propuestas,
        start=1,
    ):

        propuesta.codigo = f"PROP-{indice}"

    # ========================================================
    # RESULTADO
    # ========================================================

    return propuestas[:cantidad_propuestas]

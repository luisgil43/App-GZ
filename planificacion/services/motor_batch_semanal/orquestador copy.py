from functools import lru_cache
from itertools import combinations

from planificacion.services.motor_batch_semanal.cuadrillas import (
    construir_configuracion_cuadrilla, cuadrilla_puede_ejecutar_sitio)
from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km
from planificacion.services.motor_batch_semanal.salidas import (
    MAX_SITIOS_POR_SALIDA, encontrar_mejor_salida)

# ============================================================
# ESTRATEGIAS
# ============================================================

ESTRATEGIA_OPERATIVA = "operativa"
ESTRATEGIA_COMPACTA = "compacta"
ESTRATEGIA_BALANCEADA = "balanceada"


# ============================================================
# CONFIGURACIÓN DE SELECCIÓN
# ============================================================

PESO_SALIDA_OPERATIVA = 0.68
PESO_REMANENTE_OPERATIVA = 0.32

PESO_SALIDA_COMPACTA = 0.78
PESO_REMANENTE_COMPACTA = 0.22

PESO_SALIDA_BALANCEADA = 0.72
PESO_REMANENTE_BALANCEADA = 0.28


# ============================================================
# PENALIZACIÓN DE JORNADA EXTENDIDA
# ============================================================

PENALIZACION_EXTENSION_POR_MINUTO = 0.15
PENALIZACION_EXTENSION_MAXIMA = 18.0


# ============================================================
# BALANCE OPERACIONAL ENTRE CUADRILLAS
# ============================================================
#
# REGLA PRINCIPAL
# ============================================================
#
# Una cuadrilla activa NO debe quedarse sin trabajo mientras:
#
# - tenga días disponibles;
# - existan sitios compatibles;
# - exista al menos una salida viable para ella.
#
# Después de garantizar actividad a todas las cuadrillas,
# distribuimos progresivamente el trabajo según el porcentaje
# de utilización semanal.
#
# Ejemplo con tres cuadrillas de 5 días:
#
#     C1 -> 1/5
#     C2 -> 0/5
#     C3 -> 1/5
#
# Si C2 tiene una salida viable, C2 tiene prioridad.
#
# Luego:
#
#     C1 -> 1/5
#     C2 -> 1/5
#     C3 -> 1/5
#
# Y continuamos equilibrando.
#
# No buscamos igualdad matemática absoluta de sitios.
#
# Buscamos equilibrio de DÍAS / SALIDAS porque:
#
# - una cuadrilla puede hacer 2 sitios;
# - otra puede hacer 3;
# - existen diferencias urbano/rural;
# - existen diferencias de capacidad diaria;
# - algunas zonas necesitan jornada extendida.
# ============================================================

TOLERANCIA_UTILIZACION_BALANCE = 0.0001


# ============================================================
# ESPECIALIZACIÓN TERRITORIAL
# ============================================================
#
# Una cuadrilla que solamente puede hacer urbano posee menos
# flexibilidad que una cuadrilla urbano+rural.
#
# Por eso:
#
# salida completamente urbana:
#
#     C2 urbano-only
#         tiene prioridad
#     sobre
#     C1/C3 urbano+rural
#
# siempre que C2:
#
# - tenga cupo;
# - pueda ejecutar la salida;
# - no rompa el balance semanal.
#
# Lo mismo aplica simétricamente a una futura cuadrilla
# rural-only.
# ============================================================

BONIFICACION_ESPECIALIZACION_LOGICA = True

# ============================================================
# OPTIMIZACIÓN RESIDUAL GLOBAL
# ============================================================
#
# IMPORTANTE:
#
# Esta optimización NO reemplaza el plan que ya funciona.
#
# Primero se construye exactamente el plan actual.
#
# Después:
#
# - las salidas de 3 quedan protegidas;
# - reunimos salidas de 1 y 2 + sitios pendientes;
# - volvemos a empaquetar ese universo globalmente;
# - ignoramos las fronteras entre clusters;
# - solamente aceptamos el nuevo resultado si mantiene
#   o mejora la cobertura del plan original.
#
# ============================================================

MAX_SITIOS_OPTIMIZACION_EXACTA = 18

RADIO_MAXIMO_TERNA_RESIDUAL_GLOBAL_KM = 14.0

RADIO_MAXIMO_PAREJA_URBANA_KM = 14.0
RADIO_MAXIMO_PAREJA_MIXTA_KM = 22.0
RADIO_MAXIMO_PAREJA_RURAL_KM = 30.0

# Una terna creada durante la recomposición residual global
# no podrá convertirse en una jornada absurda.
#
# Esto NO modifica las salidas originales del motor.
# Solamente protege las nuevas combinaciones residuales.
MINUTOS_MAXIMOS_TERNA_RESIDUAL_GLOBAL = 12 * 60
# ============================================================
# UTILIDADES
# ============================================================


def _id_sitio(
    sitio,
):
    return sitio.sitio_planificado_id


def _normalizar_disponibilidades(
    disponibilidades,
):
    """
    Convierte DisponibilidadCuadrillaSemana en estructuras
    simples para el motor.

    Solo conserva cuadrillas activas.
    """

    configuraciones = []

    for disponibilidad in disponibilidades:

        configuracion = construir_configuracion_cuadrilla(disponibilidad)

        if not configuracion["activa"]:
            continue

        configuraciones.append(configuracion)

    return configuraciones


def _configuraciones_por_cuadrilla(
    configuraciones,
):
    """
    Permite consultar rápidamente la configuración efectiva
    de una cuadrilla por su código.
    """

    return {
        configuracion["cuadrilla"]: configuracion for configuracion in configuraciones
    }


def _sitios_compatibles(
    *,
    sitios,
    configuracion,
):
    compatibles = []

    incompatibles = []

    for sitio in sitios:

        if cuadrilla_puede_ejecutar_sitio(
            configuracion,
            sitio,
        ):
            compatibles.append(sitio)

        else:
            incompatibles.append(sitio)

    return {
        "compatibles": compatibles,
        "incompatibles": incompatibles,
    }


def _maximo_sitios_configuracion(
    configuracion,
):
    """
    Devuelve el máximo de sitios que vale la pena intentar
    para esta cuadrilla.

    Nunca supera MAX_SITIOS_POR_SALIDA.
    """

    try:
        capacidad = int(
            configuracion.get(
                "capacidad_diaria",
                MAX_SITIOS_POR_SALIDA,
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        capacidad = MAX_SITIOS_POR_SALIDA

    capacidad = max(
        capacidad,
        1,
    )

    return min(
        capacidad,
        MAX_SITIOS_POR_SALIDA,
    )


# ============================================================
# UTILIZACIÓN RELATIVA DE CUADRILLA
# ============================================================


def _utilizacion_relativa_cuadrilla(
    *,
    cuadrilla,
    salidas_usadas,
    cupos,
):
    """
    Devuelve utilización entre 0 y 1.

    Ejemplos:

        0 / 5 -> 0.00
        1 / 5 -> 0.20
        3 / 5 -> 0.60
        5 / 5 -> 1.00

    Usamos porcentaje y no número absoluto porque en el futuro
    puede existir una cuadrilla con 5 días y otra con 6.
    """

    usados = max(
        int(
            salidas_usadas.get(
                cuadrilla,
                0,
            )
            or 0
        ),
        0,
    )

    cupo = max(
        int(
            cupos.get(
                cuadrilla,
                0,
            )
            or 0
        ),
        0,
    )

    if cupo <= 0:
        return 1.0

    return min(
        usados / cupo,
        1.0,
    )


# ============================================================
# FLEXIBILIDAD TERRITORIAL
# ============================================================


def _flexibilidad_cuadrilla(
    configuracion,
):
    """
    Menor número = cuadrilla más especializada.

    urbano-only:
        1

    rural-only:
        1

    urbano+rural:
        2
    """

    if not configuracion:
        return 99

    flexibilidad = 0

    if configuracion.get(
        "permite_urbano",
        False,
    ):
        flexibilidad += 1

    if configuracion.get(
        "permite_rural",
        False,
    ):
        flexibilidad += 1

    if flexibilidad <= 0:
        return 99

    return flexibilidad


# ============================================================
# TIPO TERRITORIAL DE UNA SALIDA
# ============================================================


def _tipo_territorial_salida(
    salida,
):
    sitios = list(
        salida.get(
            "sitios",
            [],
        )
        or []
    )

    if not sitios:
        return "desconocido"

    urbanos = sum(1 for sitio in sitios if sitio.urbano)

    rurales = sum(1 for sitio in sitios if sitio.rural)

    total = len(sitios)

    if urbanos == total:
        return "urbano"

    if rurales == total:
        return "rural"

    if urbanos > 0 and rurales > 0:
        return "mixto"

    return "desconocido"


# ============================================================
# PRIORIDAD POR ESPECIALIZACIÓN
# ============================================================


def _prioridad_especializacion_salida(
    *,
    salida,
    configuraciones_por_cuadrilla,
):
    """
    Premia lógicamente que una salida sea entregada primero
    a una cuadrilla especializada compatible.

    Ejemplo:

        C2:
            urbano sí
            rural no

        salida:
            3 urbanos

    C2 recibe prioridad sobre C1/C3 urbano+rural.

    Una salida mixta NO recibe esta prioridad.
    """

    if not BONIFICACION_ESPECIALIZACION_LOGICA:
        return 0

    cuadrilla = salida.get("cuadrilla")

    configuracion = configuraciones_por_cuadrilla.get(
        cuadrilla,
        {},
    )

    tipo_salida = _tipo_territorial_salida(salida)

    permite_urbano = bool(
        configuracion.get(
            "permite_urbano",
            False,
        )
    )

    permite_rural = bool(
        configuracion.get(
            "permite_rural",
            False,
        )
    )

    if tipo_salida == "urbano" and permite_urbano and not permite_rural:
        return 2

    if tipo_salida == "rural" and permite_rural and not permite_urbano:
        return 2

    if (
        tipo_salida
        in [
            "urbano",
            "rural",
        ]
        and _flexibilidad_cuadrilla(configuracion) == 1
    ):
        return 1

    return 0


# ============================================================
# PENALIZACIÓN DE EXTENSIÓN
# ============================================================


def _penalizacion_jornada_extendida(
    salida,
):
    if not salida.get(
        "jornada_extendida",
        False,
    ):
        return 0.0

    exceso = max(
        salida.get(
            "exceso_jornada_minutos",
            0,
        )
        or 0,
        0,
    )

    return min(
        exceso * PENALIZACION_EXTENSION_POR_MINUTO,
        PENALIZACION_EXTENSION_MAXIMA,
    )


# ============================================================
# SCORE DE UNA SALIDA
# ============================================================


def _score_salida(
    *,
    salida,
    cluster,
    estrategia,
):
    """
    Score individual de una salida.

    Premia:

    - 3 sitios;
    - luego 2 sitios;
    - menor proporción de viaje;
    - margen de jornada;
    - compactación territorial.

    Las jornadas extendidas siguen siendo válidas para
    salidas de 2 o 3 sitios.
    """

    cantidad = salida["cantidad_sitios"]

    if cantidad == 3:
        score_cantidad = 100.0

    elif cantidad == 2:
        score_cantidad = 82.0

    else:
        score_cantidad = 30.0

    minutos_total = max(
        salida["minutos_total"],
        1,
    )

    minutos_viaje = salida["minutos_viaje"]

    proporcion_viaje = minutos_viaje / minutos_total

    score_viaje = max(
        100 - proporcion_viaje * 100,
        0,
    )

    minutos_jornada = max(
        salida["minutos_jornada"],
        1,
    )

    margen = max(
        salida["margen_minutos"],
        0,
    )

    score_margen = min(
        margen / minutos_jornada * 100,
        100,
    )

    score_cluster = cluster.score_compactacion

    # ========================================================
    # COMPACTA
    # ========================================================

    if estrategia == ESTRATEGIA_COMPACTA:

        score = (
            score_cantidad * 0.30
            + score_viaje * 0.20
            + score_margen * 0.10
            + score_cluster * 0.40
        )

    # ========================================================
    # BALANCEADA
    # ========================================================

    elif estrategia == ESTRATEGIA_BALANCEADA:

        score = (
            score_cantidad * 0.38
            + score_viaje * 0.24
            + score_margen * 0.18
            + score_cluster * 0.20
        )

    # ========================================================
    # OPERATIVA
    # ========================================================

    else:

        score = (
            score_cantidad * 0.45
            + score_viaje * 0.28
            + score_margen * 0.17
            + score_cluster * 0.10
        )

    score -= _penalizacion_jornada_extendida(salida)

    if cantidad == 1:
        score -= 18.0

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
# GENERAR SALIDAS DE UN CLUSTER
# ============================================================


def generar_salidas_cluster(
    *,
    cluster,
    configuraciones,
    estrategia=ESTRATEGIA_OPERATIVA,
):
    candidatos = []

    sitios = list(cluster.sitios)

    for configuracion in configuraciones:

        if not configuracion["tiene_base"]:
            continue

        compatibilidad = _sitios_compatibles(
            sitios=sitios,
            configuracion=(configuracion),
        )

        compatibles = compatibilidad["compatibles"]

        if not compatibles:
            continue

        maximo_cuadrilla = _maximo_sitios_configuracion(configuracion)

        tamanos = range(
            min(
                maximo_cuadrilla,
                len(compatibles),
            ),
            0,
            -1,
        )

        for cantidad in tamanos:

            for grupo in combinations(
                compatibles,
                cantidad,
            ):

                salida = encontrar_mejor_salida(
                    sitios=grupo,
                    configuracion_cuadrilla=(configuracion),
                )

                if not salida:
                    continue

                if not salida["viable"]:
                    continue

                salida = dict(salida)

                salida["cluster_id"] = cluster.id_cluster

                salida["cluster_score"] = cluster.score_compactacion

                salida["sitio_ids_set"] = frozenset(salida["sitio_ids"])

                salida["score_salida"] = _score_salida(
                    salida=salida,
                    cluster=cluster,
                    estrategia=estrategia,
                )

                candidatos.append(salida)

    candidatos.sort(
        key=lambda salida: (
            -salida["score_salida"],
            -salida["cantidad_sitios"],
            salida.get(
                "jornada_extendida",
                False,
            ),
            salida["minutos_total"],
        )
    )

    return candidatos


# ============================================================
# TODAS LAS SALIDAS DE LA SEMANA
# ============================================================


def generar_salidas_semana(
    *,
    clusters,
    disponibilidades,
    estrategia=ESTRATEGIA_OPERATIVA,
):
    configuraciones = _normalizar_disponibilidades(disponibilidades)

    candidatos = []

    for cluster in clusters:

        candidatos.extend(
            generar_salidas_cluster(
                cluster=cluster,
                configuraciones=(configuraciones),
                estrategia=estrategia,
            )
        )

    candidatos.sort(
        key=lambda salida: (
            -salida["score_salida"],
            -salida["cantidad_sitios"],
            salida.get(
                "jornada_extendida",
                False,
            ),
            salida["minutos_total"],
        )
    )

    return {
        "configuraciones": configuraciones,
        "salidas": candidatos,
    }


# ============================================================
# DISPONIBILIDAD DE DÍAS
# ============================================================


def _cupos_dias_por_cuadrilla(
    configuraciones,
):
    return {
        configuracion["cuadrilla"]: configuracion["dias_disponibles"]
        for configuracion in configuraciones
    }


# ============================================================
# MAPAS DE CLUSTERS
# ============================================================


def _sitios_por_cluster(
    clusters,
):
    resultado = {}

    for cluster in clusters:

        resultado[cluster.id_cluster] = {_id_sitio(sitio) for sitio in cluster.sitios}

    return resultado


def _candidatos_por_cluster(
    candidatos,
):
    resultado = {}

    for salida in candidatos:

        cluster_id = salida["cluster_id"]

        resultado.setdefault(
            cluster_id,
            [],
        ).append(salida)

    return resultado


# ============================================================
# CUPO DE SALIDA
# ============================================================


def _salida_tiene_cupo(
    *,
    salida,
    salidas_usadas,
    cupos,
):
    cuadrilla = salida["cuadrilla"]

    return salidas_usadas.get(
        cuadrilla,
        0,
    ) < cupos.get(
        cuadrilla,
        0,
    )


# ============================================================
# CANDIDATO TODAVÍA DISPONIBLE
# ============================================================


def _salida_disponible_actualmente(
    *,
    salida,
    sitios_usados,
    salidas_usadas,
    cupos,
    restantes_objetivo,
    permitir_unitarias,
):
    cantidad = salida["cantidad_sitios"]

    if permitir_unitarias:

        if cantidad != 1:
            return False

    else:

        if cantidad <= 1:
            return False

    if cantidad > restantes_objetivo:
        return False

    if not _salida_tiene_cupo(
        salida=salida,
        salidas_usadas=(salidas_usadas),
        cupos=cupos,
    ):
        return False

    ids = salida["sitio_ids_set"]

    if sitios_usados.intersection(ids):
        return False

    return True


# ============================================================
# CUADRILLAS CON OPCIONES REALES
# ============================================================


def _cuadrillas_con_opciones_actuales(
    *,
    candidatos,
    sitios_usados,
    salidas_usadas,
    cupos,
    restantes_objetivo,
    permitir_unitarias,
):
    """
    Devuelve las cuadrillas que REALMENTE pueden recibir
    una salida en este momento.

    Esto es importante porque no debemos castigar C1/C3 por
    utilizarse si C2 no posee ninguna salida compatible.
    """

    resultado = set()

    for salida in candidatos:

        if not _salida_disponible_actualmente(
            salida=salida,
            sitios_usados=(sitios_usados),
            salidas_usadas=(salidas_usadas),
            cupos=cupos,
            restantes_objetivo=(restantes_objetivo),
            permitir_unitarias=(permitir_unitarias),
        ):
            continue

        cuadrilla = salida["cuadrilla"]

        resultado.add(cuadrilla)

    return resultado


# ============================================================
# PRIORIDAD DE BALANCE
# ============================================================


def _prioridad_balance_cuadrilla(
    *,
    cuadrilla,
    cuadrillas_con_opciones,
    salidas_usadas,
    cupos,
):
    """
    Devuelve:

    {
        "sin_trabajo": ...,
        "utilizacion": ...,
        "es_menor_utilizacion": ...,
    }

    Una cuadrilla sin trabajo tiene máxima prioridad si posee
    una opción viable.

    Después favorecemos la utilización relativa más baja.
    """

    if cuadrilla not in cuadrillas_con_opciones:

        return {
            "sin_trabajo": False,
            "utilizacion": 1.0,
            "es_menor_utilizacion": False,
        }

    utilizaciones = {
        codigo: (
            _utilizacion_relativa_cuadrilla(
                cuadrilla=codigo,
                salidas_usadas=(salidas_usadas),
                cupos=cupos,
            )
        )
        for codigo in cuadrillas_con_opciones
    }

    utilizacion = utilizaciones[cuadrilla]

    menor_utilizacion = min(utilizaciones.values())

    es_menor_utilizacion = (
        utilizacion <= menor_utilizacion + TOLERANCIA_UTILIZACION_BALANCE
    )

    sin_trabajo = (
        salidas_usadas.get(
            cuadrilla,
            0,
        )
        == 0
    )

    return {
        "sin_trabajo": (sin_trabajo),
        "utilizacion": (utilizacion),
        "es_menor_utilizacion": (es_menor_utilizacion),
    }


# ============================================================
# ORDEN DEL REMANENTE
# ============================================================


def _orden_tamanos_remanente(
    cantidad,
):
    """
    Orden operacional utilizado al simular el remanente.

    REGLA
    ==========================================================

    Siempre preferimos:

        3 sitios
        2 sitios
        1 sitio

    Incluso si quedan 4 sitios:

        preferimos 3 + 1

    antes que:

        2 + 2

    porque el objetivo operacional es llenar jornadas de
    3 sitios siempre que exista una combinación posible.

    La salida individual solamente será utilizada al final
    si realmente no existe otra forma de absorber el sitio.
    """

    if cantidad <= 1:
        return [
            1,
        ]

    if cantidad == 2:
        return [
            2,
        ]

    return [
        3,
        2,
    ]


# ============================================================
# SIMULAR REMANENTE
# ============================================================


def _simular_empaquetamiento_remanente(
    *,
    ids_restantes,
    candidatos_cluster,
    salidas_usadas,
    cupos,
    salida_actual,
):
    """
    Simula cómo podría empaquetarse el remanente después
    de elegir una salida determinada.

    JERARQUÍA
    ==========================================================

        1. grupos de 3;
        2. grupos de 2;
        3. dejamos los unitarios para la fase final real.

    IMPORTANTE
    ==========================================================

    El balance entre cuadrillas NO debe provocar que
    sacrifiquemos una salida completa de 3.

    En esta simulación interesa principalmente saber si
    los sitios restantes pueden seguir agrupándose de forma
    eficiente utilizando la menor cantidad de jornadas.
    """

    ids_restantes = set(ids_restantes)

    if not ids_restantes:

        return {
            "total": 0,
            "empaquetados": 0,
            "sin_empaquetar": 0,
            "salidas_simuladas": 0,
        }

    uso_simulado = dict(salidas_usadas)

    cuadrilla_actual = salida_actual["cuadrilla"]

    uso_simulado[cuadrilla_actual] = (
        uso_simulado.get(
            cuadrilla_actual,
            0,
        )
        + 1
    )

    pendientes = set(ids_restantes)

    empaquetados = set()

    salidas_simuladas = 0

    # ========================================================
    # MIENTRAS PODAMOS FORMAR AL MENOS UNA SALIDA DE 2
    # ========================================================

    while len(pendientes) >= 2:

        mejor = None

        # ====================================================
        # SIEMPRE BUSCAR 3 ANTES QUE 2
        # ====================================================

        tamanos = _orden_tamanos_remanente(
            len(pendientes),
        )

        for tamano in tamanos:

            # La simulación no crea salidas unitarias.
            if tamano <= 1:
                continue

            opciones = []

            for salida in candidatos_cluster:

                if salida["cantidad_sitios"] != tamano:
                    continue

                ids = set(salida["sitio_ids_set"])

                if not ids.issubset(pendientes):
                    continue

                cuadrilla = salida["cuadrilla"]

                if uso_simulado.get(
                    cuadrilla,
                    0,
                ) >= cupos.get(
                    cuadrilla,
                    0,
                ):
                    continue

                opciones.append(salida)

            if not opciones:
                continue

            # =================================================
            # DENTRO DEL MISMO TAMAÑO:
            #
            # 1. mejor score;
            # 2. mejor remanente implícito;
            # 3. menor viaje;
            # 4. menor duración.
            #
            # Una jornada extendida NO pierde automáticamente.
            # =================================================

            opciones.sort(
                key=lambda salida: (
                    -salida["score_salida"],
                    salida["minutos_viaje"],
                    salida["minutos_total"],
                )
            )

            mejor = opciones[0]

            # Encontramos una salida del mayor tamaño posible.
            break

        if mejor is None:
            break

        ids_mejor = set(mejor["sitio_ids_set"])

        pendientes.difference_update(
            ids_mejor,
        )

        empaquetados.update(
            ids_mejor,
        )

        cuadrilla = mejor["cuadrilla"]

        uso_simulado[cuadrilla] = (
            uso_simulado.get(
                cuadrilla,
                0,
            )
            + 1
        )

        salidas_simuladas += 1

    return {
        "total": len(ids_restantes),
        "empaquetados": len(empaquetados),
        "sin_empaquetar": len(pendientes),
        "salidas_simuladas": salidas_simuladas,
    }


# ============================================================
# SCORE REMANENTE
# ============================================================


def _score_remanente_salida(
    *,
    salida,
    sitios_usados,
    cluster_ids,
    candidatos_cluster,
    salidas_usadas,
    cupos,
):
    usados_actuales = set(sitios_usados)

    ids_salida = set(salida["sitio_ids_set"])

    disponibles_cluster = set(cluster_ids) - usados_actuales

    restantes = disponibles_cluster - ids_salida

    cantidad_antes = len(disponibles_cluster)

    cantidad_despues = len(restantes)

    if cantidad_despues == 0:

        return {
            "score": 100.0,
            "antes": cantidad_antes,
            "despues": 0,
            "empaquetados": 0,
            "sin_empaquetar": 0,
        }

    if cantidad_despues == 1:

        return {
            "score": 12.0,
            "antes": cantidad_antes,
            "despues": 1,
            "empaquetados": 0,
            "sin_empaquetar": 1,
        }

    simulacion = _simular_empaquetamiento_remanente(
        ids_restantes=(restantes),
        candidatos_cluster=(candidatos_cluster),
        salidas_usadas=(salidas_usadas),
        cupos=cupos,
        salida_actual=salida,
    )

    total = max(
        simulacion["total"],
        1,
    )

    empaquetados = simulacion["empaquetados"]

    sin_empaquetar = simulacion["sin_empaquetar"]

    proporcion_empaquetada = empaquetados / total

    score = proporcion_empaquetada * 100

    if sin_empaquetar == 0:

        score = 100.0

    elif sin_empaquetar == 1:

        score = min(
            score,
            45.0,
        )

    elif sin_empaquetar == 2:

        score = min(
            score,
            55.0,
        )

    else:

        score = min(
            score,
            40.0,
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
        "antes": (cantidad_antes),
        "despues": (cantidad_despues),
        "empaquetados": (empaquetados),
        "sin_empaquetar": (sin_empaquetar),
    }


# ============================================================
# SCORE FINAL
# ============================================================


def _score_seleccion(
    *,
    score_salida,
    score_remanente,
    estrategia,
):
    if estrategia == ESTRATEGIA_COMPACTA:

        peso_salida = PESO_SALIDA_COMPACTA

        peso_remanente = PESO_REMANENTE_COMPACTA

    elif estrategia == ESTRATEGIA_BALANCEADA:

        peso_salida = PESO_SALIDA_BALANCEADA

        peso_remanente = PESO_REMANENTE_BALANCEADA

    else:

        peso_salida = PESO_SALIDA_OPERATIVA

        peso_remanente = PESO_REMANENTE_OPERATIVA

    return round(
        (score_salida * peso_salida + score_remanente * peso_remanente),
        2,
    )


# ============================================================
# BUSCAR SIGUIENTE SALIDA
# ============================================================


def _buscar_mejor_siguiente_salida(
    *,
    candidatos,
    candidatos_por_cluster,
    sitios_cluster,
    sitios_usados,
    salidas_usadas,
    cupos,
    restantes_objetivo,
    estrategia,
    permitir_unitarias,
    configuraciones_por_cuadrilla,
):
    """
    Selecciona dinámicamente la siguiente salida.

    NUEVA JERARQUÍA OPERACIONAL
    ==========================================================

    La prioridad es:

        1. cantidad de sitios;
        2. calidad territorial / operacional;
        3. salud del remanente;
        4. especialización urbano/rural;
        5. menor viaje;
        6. menor duración;
        7. balance de cuadrillas solamente como desempate.

    IMPORTANTE
    ==========================================================

    El balance entre cuadrillas NO puede provocar situaciones
    como:

        C1 -> 1 sitio
        C2 -> 1 sitio

    cuando existe una alternativa:

        C1 -> 2 sitios
        C2 -> sin salida

    Tampoco debe impedir una salida completa de 3 únicamente
    porque otra cuadrilla tenga menor utilización semanal.

    El objetivo empresarial es utilizar mejor cada vehículo
    que sale a terreno.

    La equidad entre cuadrillas continúa calculándose como
    métrica y puede utilizarse como desempate, pero deja de
    dominar la decisión operacional.
    """

    cuadrillas_con_opciones = _cuadrillas_con_opciones_actuales(
        candidatos=candidatos,
        sitios_usados=sitios_usados,
        salidas_usadas=salidas_usadas,
        cupos=cupos,
        restantes_objetivo=restantes_objetivo,
        permitir_unitarias=permitir_unitarias,
    )

    mejor = None

    mejor_clave = None

    for salida in candidatos:

        if not _salida_disponible_actualmente(
            salida=salida,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
            cupos=cupos,
            restantes_objetivo=restantes_objetivo,
            permitir_unitarias=permitir_unitarias,
        ):
            continue

        cantidad = salida["cantidad_sitios"]

        cuadrilla = salida["cuadrilla"]

        cluster_id = salida["cluster_id"]

        ids_cluster = sitios_cluster.get(
            cluster_id,
            set(),
        )

        # ====================================================
        # REMANENTE
        # ====================================================

        resultado_remanente = _score_remanente_salida(
            salida=salida,
            sitios_usados=sitios_usados,
            cluster_ids=ids_cluster,
            candidatos_cluster=(
                candidatos_por_cluster.get(
                    cluster_id,
                    [],
                )
            ),
            salidas_usadas=salidas_usadas,
            cupos=cupos,
        )

        # ====================================================
        # SCORE ESTRATÉGICO
        # ====================================================

        score_seleccion = _score_seleccion(
            score_salida=salida["score_salida"],
            score_remanente=resultado_remanente["score"],
            estrategia=estrategia,
        )

        # ====================================================
        # ESPECIALIZACIÓN
        # ====================================================

        prioridad_especializacion = _prioridad_especializacion_salida(
            salida=salida,
            configuraciones_por_cuadrilla=(configuraciones_por_cuadrilla),
        )

        # ====================================================
        # BALANCE
        # ====================================================
        #
        # Se conserva para trazabilidad y desempate.
        #
        # YA NO domina la decisión.
        # ====================================================

        balance = _prioridad_balance_cuadrilla(
            cuadrilla=cuadrilla,
            cuadrillas_con_opciones=cuadrillas_con_opciones,
            salidas_usadas=salidas_usadas,
            cupos=cupos,
        )

        # ====================================================
        # CLAVE DE SELECCIÓN
        # ====================================================
        #
        # Esta función puede recibir únicamente candidatos
        # de 3, únicamente de 2 o únicamente de 1 según la
        # pasada del orquestador.
        #
        # Aun así dejamos cantidad en primer lugar para que
        # la regla quede protegida si posteriormente se usa
        # con candidatos mezclados.
        #
        # MUY IMPORTANTE:
        #
        # jornada_extendida NO está delante del score.
        #
        # Una salida:
        #
        #   3 sitios
        #   +181 min
        #
        # sigue siendo una salida válida.
        #
        # El exceso se mostrará al usuario.
        # ====================================================

        clave = (
            # 1. LLENAR VEHÍCULO / JORNADA.
            cantidad,
            # 2. CALIDAD OPERACIONAL GENERAL.
            score_seleccion,
            # 3. EVITAR DEJAR RESIDUOS MAL EMPAQUETADOS.
            resultado_remanente["score"],
            # 4. UTILIZAR LA CUADRILLA MÁS ADECUADA
            #    TERRITORIALMENTE.
            prioridad_especializacion,
            # 5. MENOR VIAJE.
            -salida["minutos_viaje"],
            # 6. MENOR JORNADA TOTAL ENTRE OPCIONES
            #    DE IGUAL CALIDAD.
            -salida["minutos_total"],
            # 7. BALANCE SOLO COMO DESEMPATE.
            balance["sin_trabajo"],
            balance["es_menor_utilizacion"],
            -balance["utilizacion"],
        )

        if mejor is None or clave > mejor_clave:

            mejor = dict(salida)

            # =================================================
            # TRAZABILIDAD DEL REMANENTE
            # =================================================

            mejor["score_remanente"] = resultado_remanente["score"]

            mejor["score_seleccion"] = score_seleccion

            mejor["remanente_cluster_antes"] = resultado_remanente["antes"]

            mejor["remanente_cluster_despues"] = resultado_remanente["despues"]

            mejor["remanente_empaquetados"] = resultado_remanente["empaquetados"]

            mejor["remanente_sin_empaquetar"] = resultado_remanente["sin_empaquetar"]

            # =================================================
            # TRAZABILIDAD DEL BALANCE
            # =================================================

            mejor["utilizacion_cuadrilla_antes"] = round(
                balance["utilizacion"] * 100,
                2,
            )

            mejor["prioridad_cuadrilla_sin_trabajo"] = balance["sin_trabajo"]

            mejor["prioridad_cuadrilla_menor_utilizacion"] = balance[
                "es_menor_utilizacion"
            ]

            mejor["prioridad_especializacion"] = prioridad_especializacion

            mejor_clave = clave

    return mejor


# ============================================================
# AGREGAR SALIDA
# ============================================================


def _agregar_salida(
    *,
    salida,
    seleccionadas,
    sitios_usados,
    salidas_usadas,
):
    seleccionadas.append(salida)

    sitios_usados.update(salida["sitio_ids_set"])

    cuadrilla = salida["cuadrilla"]

    salidas_usadas[cuadrilla] = (
        salidas_usadas.get(
            cuadrilla,
            0,
        )
        + 1
    )


# ============================================================
# SCORE DE EQUIDAD
# ============================================================


def _calcular_score_equidad(
    *,
    salidas_usadas,
    cupos,
):
    """
    Mide qué tan parecida quedó la utilización porcentual
    entre cuadrillas.

    100:
        utilización perfectamente equilibrada.

    No afecta la selección.
    Se devuelve como métrica de diagnóstico.
    """

    utilizaciones = []

    for cuadrilla, cupo in cupos.items():

        if not cupo:
            continue

        utilizacion = (
            salidas_usadas.get(
                cuadrilla,
                0,
            )
            / cupo
        )

        utilizaciones.append(utilizacion)

    if not utilizaciones:
        return 0.0

    diferencia = max(utilizaciones) - min(utilizaciones)

    return round(
        max(
            100 - diferencia * 100,
            0,
        ),
        2,
    )


# ============================================================
# UTILIZACIÓN POR CUADRILLA
# ============================================================


def _utilizacion_por_cuadrilla(
    *,
    salidas_usadas,
    cupos,
):
    resultado = {}

    for cuadrilla, cupo in cupos.items():

        usadas = salidas_usadas.get(
            cuadrilla,
            0,
        )

        porcentaje = usadas / cupo * 100 if cupo else 0

        resultado[cuadrilla] = round(
            porcentaje,
            2,
        )

    return resultado


# ============================================================
# PLAN OPERATIVO SEMANAL
# ============================================================


def _construir_plan_operativo_semana_base(
    *,
    clusters,
    disponibilidades,
    objetivo,
    estrategia=ESTRATEGIA_OPERATIVA,
):
    """
    Construye la propuesta operacional.

    JERARQUÍA ABSOLUTA
    ==========================================================

        1. formar salidas de 3 sitios dentro de los clusters;
        2. antes de aceptar pares, intentar recomponer ternas
           con todos los sitios residuales, incluso si cruzan
           fronteras de clusters;
        3. cuando ya no exista ninguna terna disponible,
           formar salidas de 2;
        4. solamente al final permitir salidas de 1.

    OBJETIVO EMPRESARIAL
    ==========================================================

    Buscamos reducir la cantidad de vehículos/jornadas
    necesarias.

    Preferimos:

        3 sitios

    antes que:

        2 + 1

    siempre que exista una terna territorialmente razonable
    y compatible con alguna cuadrilla disponible.

    RECOMPACTACIÓN RESIDUAL
    ==========================================================

    Los clusters siguen siendo la primera estructura de
    búsqueda y NO se modifican.

    Sin embargo, sus fronteras no se consideran paredes
    absolutas.

    Una vez agotadas las ternas internas, se revisan todos
    los sitios todavía libres y se permite formar una terna
    cruzando clusters cuando:

        - los tres sitios continúan sin usar;
        - la cuadrilla puede ejecutar los tres;
        - la cuadrilla tiene cupo;
        - la distancia máxima entre cualquier par de sitios
          no supera 14 km;
        - encontrar_mejor_salida() puede calcular la jornada.

    El tiempo de jornada NO invalida una terna de 3.

    Si resulta extendida:

        jornada_extendida = True

    y el exceso se conserva para mostrarlo al usuario.

    TERRITORIO
    ==========================================================

    Continúan respetándose:

        urbano/rural;
        capacidad de la cuadrilla;
        disponibilidad semanal;
        base operacional;
        compatibilidad territorial.

    BALANCE
    ==========================================================

    La equidad entre C1/C2/C3 se conserva como métrica y
    desempate.

    No sacrifica una salida completa de 3.
    """

    # ========================================================
    # CONFIGURACIÓN GENERAL DE RECOMPACTACIÓN RESIDUAL
    # ========================================================

    RADIO_MAXIMO_TERNA_RESIDUAL_KM = 14.0

    # ========================================================
    # GENERAR CANDIDATOS NORMALES POR CLUSTER
    # ========================================================

    generado = generar_salidas_semana(
        clusters=clusters,
        disponibilidades=disponibilidades,
        estrategia=estrategia,
    )

    configuraciones = generado["configuraciones"]

    candidatos = generado["salidas"]

    configuraciones_mapa = _configuraciones_por_cuadrilla(configuraciones)

    # ========================================================
    # SEPARAR POR TAMAÑO
    # ========================================================

    candidatos_3 = [salida for salida in candidatos if salida["cantidad_sitios"] == 3]

    candidatos_2 = [salida for salida in candidatos if salida["cantidad_sitios"] == 2]

    candidatos_1 = [salida for salida in candidatos if salida["cantidad_sitios"] == 1]

    # ========================================================
    # CUPOS SEMANALES
    # ========================================================

    cupos = _cupos_dias_por_cuadrilla(configuraciones)

    salidas_usadas = {cuadrilla: 0 for cuadrilla in cupos}

    sitios_usados = set()

    seleccionadas = []

    # ========================================================
    # MAPAS DE APOYO
    # ========================================================

    sitios_cluster = _sitios_por_cluster(clusters)

    candidatos_cluster = _candidatos_por_cluster(candidatos)

    # ========================================================
    # UNIVERSO COMPLETO DE SITIOS
    # ========================================================
    #
    # Este mapa es fundamental para la PASADA 1B.
    #
    # Nos permite recuperar todos los SitioMotor originales
    # independientemente del cluster al que pertenecen.
    # ========================================================

    sitios_por_id = {}

    cluster_por_sitio_id = {}

    for cluster in clusters:

        for sitio in cluster.sitios:

            sitio_id = _id_sitio(sitio)

            sitios_por_id[sitio_id] = sitio

            cluster_por_sitio_id[sitio_id] = cluster.id_cluster

    # ========================================================
    # HELPER LOCAL: DISTANCIA ENTRE DOS SITIOS
    # ========================================================

    def distancia_entre_sitios(
        sitio_a,
        sitio_b,
    ):
        distancia = distancia_haversine_km(
            sitio_a.latitud,
            sitio_a.longitud,
            sitio_b.latitud,
            sitio_b.longitud,
        )

        if distancia is None:
            return None

        return float(distancia)

    # ========================================================
    # HELPER LOCAL: TERNA TERRITORIALMENTE RAZONABLE
    # ========================================================

    def terna_residual_es_razonable(
        grupo,
    ):
        """
        Valida únicamente la cercanía entre los tres sitios.

        NO utiliza la distancia a la base porque eso ya se
        incorpora posteriormente al cálculo de ruta.

        Queremos impedir casos como:

            Santiago + Valparaíso + Rancagua

        pero permitir situaciones de frontera como:

            05_067 + 05_366 + 05_098

        aunque hayan terminado en clusters distintos.
        """

        grupo = list(grupo)

        if len(grupo) != 3:
            return False

        distancias = []

        for indice, sitio_a in enumerate(grupo):

            for sitio_b in grupo[indice + 1 :]:

                distancia = distancia_entre_sitios(
                    sitio_a,
                    sitio_b,
                )

                if distancia is None:
                    return False

                distancias.append(distancia)

        if not distancias:
            return False

        distancia_maxima = max(distancias)

        return distancia_maxima <= RADIO_MAXIMO_TERNA_RESIDUAL_KM

    # ========================================================
    # HELPER LOCAL: SCORE DE TERNA RESIDUAL
    # ========================================================

    def score_terna_residual(
        *,
        salida,
        cantidad_clusters,
        prioridad_especializacion,
        balance,
    ):
        """
        Selección de una terna residual.

        Todas las opciones ya poseen 3 sitios.

        Por eso ordenamos por:

            1. menor fragmentación de clusters;
            2. mejor especialización;
            3. menor viaje;
            4. menor jornada total;
            5. menor exceso;
            6. balance solamente como desempate.

        IMPORTANTE:

        jornada_extendida NO invalida ni domina la decisión.
        """

        return (
            -cantidad_clusters,
            prioridad_especializacion,
            -int(
                salida.get(
                    "minutos_viaje",
                    0,
                )
                or 0
            ),
            -int(
                salida.get(
                    "minutos_total",
                    0,
                )
                or 0
            ),
            -int(
                salida.get(
                    "exceso_jornada_minutos",
                    0,
                )
                or 0
            ),
            balance["sin_trabajo"],
            balance["es_menor_utilizacion"],
            -balance["utilizacion"],
        )

    # ========================================================
    # PASADA 1
    #
    # TERNAS NORMALES DENTRO DE LOS CLUSTERS
    # ========================================================

    while len(sitios_usados) < objetivo:

        restantes_objetivo = objetivo - len(sitios_usados)

        if restantes_objetivo < 3:
            break

        mejor = _buscar_mejor_siguiente_salida(
            candidatos=candidatos_3,
            candidatos_por_cluster=(candidatos_cluster),
            sitios_cluster=sitios_cluster,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
            cupos=cupos,
            restantes_objetivo=(restantes_objetivo),
            estrategia=estrategia,
            permitir_unitarias=False,
            configuraciones_por_cuadrilla=(configuraciones_mapa),
        )

        if mejor is None:
            break

        mejor["nivel_empaquetamiento"] = 3

        mejor["objetivo_diario_completo"] = True

        mejor["salida_incompleta_recalculable"] = False

        mejor["recompactacion_residual"] = False

        _agregar_salida(
            salida=mejor,
            seleccionadas=seleccionadas,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
        )

    # ========================================================
    # PASADA 1B
    #
    # RECOMPACTACIÓN GLOBAL DE TERNAS RESIDUALES
    # ========================================================
    #
    # MUY IMPORTANTE:
    #
    # Antes de aceptar una pareja, revisamos nuevamente TODO
    # el remanente.
    #
    # Aquí sí permitimos cruzar fronteras entre clusters.
    #
    # Ejemplo real:
    #
    # cluster_3:
    #     05_067
    #     05_366
    #
    # cluster_4:
    #     05_098
    #
    # Si los tres son cercanos y compatibles:
    #
    #     05_067 + 05_366 + 05_098
    #
    # debe ganar frente a:
    #
    #     05_067 + 05_366
    #     05_098
    #
    # ========================================================

    while len(sitios_usados) < objetivo:

        restantes_objetivo = objetivo - len(sitios_usados)

        if restantes_objetivo < 3:
            break

        # ====================================================
        # SITIOS TODAVÍA LIBRES
        # ====================================================

        sitios_restantes = [
            sitio
            for sitio_id, sitio in sitios_por_id.items()
            if sitio_id not in sitios_usados
        ]

        if len(sitios_restantes) < 3:
            break

        mejor_residual = None

        mejor_clave_residual = None

        # ====================================================
        # CUADRILLAS QUE TODAVÍA POSEEN CUPO
        # ====================================================

        cuadrillas_con_cupo = {
            configuracion["cuadrilla"]
            for configuracion in configuraciones
            if (
                salidas_usadas.get(
                    configuracion["cuadrilla"],
                    0,
                )
                < cupos.get(
                    configuracion["cuadrilla"],
                    0,
                )
            )
        }

        if not cuadrillas_con_cupo:
            break

        # ====================================================
        # PROBAR TODAS LAS TERNAS RESIDUALES
        # ====================================================

        for grupo in combinations(
            sitios_restantes,
            3,
        ):

            ids_grupo = frozenset(_id_sitio(sitio) for sitio in grupo)

            # ================================================
            # PROTECCIÓN ADICIONAL
            # ================================================

            if sitios_usados.intersection(ids_grupo):
                continue

            # ================================================
            # TERRITORIO
            # ================================================

            if not terna_residual_es_razonable(grupo):
                continue

            # ================================================
            # ¿CUÁNTOS CLUSTERS ESTAMOS CRUZANDO?
            # ================================================

            clusters_grupo = {
                cluster_por_sitio_id.get(sitio_id) for sitio_id in ids_grupo
            }

            clusters_grupo.discard(None)

            cantidad_clusters = max(
                len(clusters_grupo),
                1,
            )

            # ================================================
            # PROBAR LA TERNA CON CADA CUADRILLA
            # ================================================

            for configuracion in configuraciones:

                cuadrilla = configuracion["cuadrilla"]

                if cuadrilla not in cuadrillas_con_cupo:
                    continue

                if not configuracion.get(
                    "tiene_base",
                    False,
                ):
                    continue

                # ============================================
                # COMPATIBILIDAD INDIVIDUAL
                # ============================================

                compatibles = all(
                    cuadrilla_puede_ejecutar_sitio(
                        configuracion,
                        sitio,
                    )
                    for sitio in grupo
                )

                if not compatibles:
                    continue

                # ============================================
                # CALCULAR RUTA REAL
                # ============================================

                salida = encontrar_mejor_salida(
                    sitios=grupo,
                    configuracion_cuadrilla=(configuracion),
                )

                if not salida:
                    continue

                if not salida.get(
                    "viable",
                    False,
                ):
                    continue

                salida = dict(salida)

                salida["cluster_id"] = "residual_global_" + "_".join(
                    sorted(str(valor) for valor in clusters_grupo)
                )

                salida["cluster_score"] = 0.0

                salida["sitio_ids_set"] = ids_grupo

                # ============================================
                # SCORE DE SALIDA
                # ============================================
                #
                # No tenemos un único ClusterMotor porque
                # precisamente estamos cruzando fronteras.
                #
                # Todas las candidatas aquí poseen 3 sitios,
                # por lo que no necesitamos comparar cantidad.
                #
                # Conservamos un score diagnóstico basado
                # principalmente en viaje.
                # ============================================

                minutos_total = max(
                    int(
                        salida.get(
                            "minutos_total",
                            0,
                        )
                        or 0
                    ),
                    1,
                )

                minutos_viaje = int(
                    salida.get(
                        "minutos_viaje",
                        0,
                    )
                    or 0
                )

                proporcion_viaje = minutos_viaje / minutos_total

                score_viaje = max(
                    100.0 - proporcion_viaje * 100.0,
                    0.0,
                )

                penalizacion_extension = _penalizacion_jornada_extendida(salida)

                salida["score_salida"] = round(
                    max(
                        min(
                            70.0 + score_viaje * 0.30 - penalizacion_extension,
                            100.0,
                        ),
                        0.0,
                    ),
                    2,
                )

                # ============================================
                # ESPECIALIZACIÓN
                # ============================================

                prioridad_especializacion = _prioridad_especializacion_salida(
                    salida=salida,
                    configuraciones_por_cuadrilla=(configuraciones_mapa),
                )

                # ============================================
                # BALANCE
                # ============================================

                balance = _prioridad_balance_cuadrilla(
                    cuadrilla=cuadrilla,
                    cuadrillas_con_opciones=(cuadrillas_con_cupo),
                    salidas_usadas=(salidas_usadas),
                    cupos=cupos,
                )

                # ============================================
                # CLAVE FINAL
                # ============================================

                clave = score_terna_residual(
                    salida=salida,
                    cantidad_clusters=(cantidad_clusters),
                    prioridad_especializacion=(prioridad_especializacion),
                    balance=balance,
                )

                if mejor_residual is None or clave > mejor_clave_residual:

                    mejor_residual = salida

                    mejor_clave_residual = clave

                    # ========================================
                    # TRAZABILIDAD
                    # ========================================

                    mejor_residual["score_remanente"] = 100.0

                    mejor_residual["score_seleccion"] = salida["score_salida"]

                    mejor_residual["remanente_cluster_antes"] = len(sitios_restantes)

                    mejor_residual["remanente_cluster_despues"] = max(
                        len(sitios_restantes) - 3,
                        0,
                    )

                    mejor_residual["remanente_empaquetados"] = 3

                    mejor_residual["remanente_sin_empaquetar"] = max(
                        len(sitios_restantes) - 3,
                        0,
                    )

                    mejor_residual["utilizacion_cuadrilla_antes"] = round(
                        balance["utilizacion"] * 100,
                        2,
                    )

                    mejor_residual["prioridad_cuadrilla_sin_trabajo"] = balance[
                        "sin_trabajo"
                    ]

                    mejor_residual["prioridad_cuadrilla_menor_utilizacion"] = balance[
                        "es_menor_utilizacion"
                    ]

                    mejor_residual["prioridad_especializacion"] = (
                        prioridad_especializacion
                    )

                    mejor_residual["clusters_origen"] = sorted(
                        str(valor) for valor in clusters_grupo
                    )

        # ====================================================
        # YA NO EXISTE NINGUNA TERNA RESIDUAL POSIBLE
        # ====================================================

        if mejor_residual is None:
            break

        # ====================================================
        # MARCAR RESULTADO
        # ====================================================

        mejor_residual["nivel_empaquetamiento"] = 3

        mejor_residual["objetivo_diario_completo"] = True

        mejor_residual["salida_incompleta_recalculable"] = False

        mejor_residual["recompactacion_residual"] = True

        # ====================================================
        # AGREGAR
        # ====================================================

        _agregar_salida(
            salida=mejor_residual,
            seleccionadas=seleccionadas,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
        )

    # ========================================================
    # PASADA 2
    #
    # PARES
    #
    # Solamente después de agotar:
    #
    #   - ternas normales;
    #   - ternas residuales entre clusters.
    # ========================================================

    while len(sitios_usados) < objetivo:

        restantes_objetivo = objetivo - len(sitios_usados)

        if restantes_objetivo < 2:
            break

        mejor = _buscar_mejor_siguiente_salida(
            candidatos=candidatos_2,
            candidatos_por_cluster=(candidatos_cluster),
            sitios_cluster=sitios_cluster,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
            cupos=cupos,
            restantes_objetivo=(restantes_objetivo),
            estrategia=estrategia,
            permitir_unitarias=False,
            configuraciones_por_cuadrilla=(configuraciones_mapa),
        )

        if mejor is None:
            break

        mejor["nivel_empaquetamiento"] = 2

        mejor["objetivo_diario_completo"] = False

        mejor["salida_incompleta_recalculable"] = True

        mejor["recompactacion_residual"] = False

        _agregar_salida(
            salida=mejor,
            seleccionadas=seleccionadas,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
        )

    # ========================================================
    # PASADA 3
    #
    # UNITARIAS
    # ========================================================

    while len(sitios_usados) < objetivo:

        restantes_objetivo = objetivo - len(sitios_usados)

        mejor = _buscar_mejor_siguiente_salida(
            candidatos=candidatos_1,
            candidatos_por_cluster=(candidatos_cluster),
            sitios_cluster=sitios_cluster,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
            cupos=cupos,
            restantes_objetivo=(restantes_objetivo),
            estrategia=estrategia,
            permitir_unitarias=True,
            configuraciones_por_cuadrilla=(configuraciones_mapa),
        )

        if mejor is None:
            break

        mejor["salida_individual_excepcional"] = True

        mejor["nivel_empaquetamiento"] = 1

        mejor["objetivo_diario_completo"] = False

        mejor["salida_incompleta_recalculable"] = True

        mejor["recompactacion_residual"] = False

        _agregar_salida(
            salida=mejor,
            seleccionadas=seleccionadas,
            sitios_usados=sitios_usados,
            salidas_usadas=salidas_usadas,
        )

    # ========================================================
    # SITIOS SELECCIONADOS
    # ========================================================

    sitios_seleccionados = []

    ids_ya_agregados = set()

    for salida in seleccionadas:

        for sitio in salida["sitios"]:

            sitio_id = sitio.sitio_planificado_id

            if sitio_id in ids_ya_agregados:
                continue

            sitios_seleccionados.append(sitio)

            ids_ya_agregados.add(sitio_id)

    # ========================================================
    # MÉTRICAS
    # ========================================================

    total_salidas = len(seleccionadas)

    salidas_3 = sum(1 for salida in seleccionadas if salida["cantidad_sitios"] == 3)

    salidas_2 = sum(1 for salida in seleccionadas if salida["cantidad_sitios"] == 2)

    salidas_1 = sum(1 for salida in seleccionadas if salida["cantidad_sitios"] == 1)

    salidas_extendidas = sum(
        1
        for salida in seleccionadas
        if salida.get(
            "jornada_extendida",
            False,
        )
    )

    minutos_exceso_total = sum(
        (
            salida.get(
                "exceso_jornada_minutos",
                0,
            )
            or 0
        )
        for salida in seleccionadas
    )

    minutos_viaje = sum(salida["minutos_viaje"] for salida in seleccionadas)

    minutos_total = sum(salida["minutos_total"] for salida in seleccionadas)

    distancia_directa = sum(
        (
            salida.get(
                "distancia_directa_km",
                0,
            )
            or 0
        )
        for salida in seleccionadas
    )

    distancia_vial = sum(
        (
            salida.get(
                "distancia_vial_estimada_km",
                0,
            )
            or 0
        )
        for salida in seleccionadas
    )

    if total_salidas:

        promedio_sitios_salida = len(sitios_seleccionados) / total_salidas

    else:

        promedio_sitios_salida = 0.0

    # ========================================================
    # COBERTURA
    # ========================================================

    if objetivo:

        score_cobertura = min(
            (len(sitios_seleccionados) / objetivo * 100),
            100,
        )

    else:

        score_cobertura = 0.0

    # ========================================================
    # APROVECHAMIENTO
    # ========================================================

    score_aprovechamiento = min(
        (promedio_sitios_salida / MAX_SITIOS_POR_SALIDA * 100),
        100,
    )

    # ========================================================
    # RESIDUALES
    # ========================================================

    if total_salidas:

        score_residuales = 100 - (salidas_1 / total_salidas * 100)

    else:

        score_residuales = 0.0

    # ========================================================
    # REMANENTE
    # ========================================================

    scores_remanente = [
        salida.get(
            "score_remanente",
            100,
        )
        for salida in seleccionadas
    ]

    if scores_remanente:

        score_remanente_global = sum(scores_remanente) / len(scores_remanente)

    else:

        score_remanente_global = 0.0

    # ========================================================
    # EQUIDAD
    # ========================================================

    score_equidad = _calcular_score_equidad(
        salidas_usadas=salidas_usadas,
        cupos=cupos,
    )

    utilizacion_por_cuadrilla = _utilizacion_por_cuadrilla(
        salidas_usadas=salidas_usadas,
        cupos=cupos,
    )

    cuadrillas_sin_trabajo = [
        cuadrilla
        for cuadrilla, usadas in salidas_usadas.items()
        if (
            cupos.get(
                cuadrilla,
                0,
            )
            > 0
            and usadas == 0
        )
    ]

    # ========================================================
    # SCORE OPERATIVO
    # ========================================================

    score_operativo = (
        score_cobertura * 0.35
        + score_aprovechamiento * 0.25
        + score_residuales * 0.16
        + score_remanente_global * 0.14
        + score_equidad * 0.10
    )

    clusters_utilizados = {salida["cluster_id"] for salida in seleccionadas}

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "estrategia": estrategia,
        "objetivo": objetivo,
        "sitios": sitios_seleccionados,
        "sitio_ids": [sitio.sitio_planificado_id for sitio in sitios_seleccionados],
        "cantidad_sitios": len(sitios_seleccionados),
        "faltantes_objetivo": max(
            objetivo - len(sitios_seleccionados),
            0,
        ),
        "salidas": seleccionadas,
        "total_salidas": total_salidas,
        "salidas_3_sitios": salidas_3,
        "salidas_2_sitios": salidas_2,
        "salidas_1_sitio": salidas_1,
        "salidas_jornada_extendida": (salidas_extendidas),
        "minutos_extension_total": (minutos_exceso_total),
        "promedio_sitios_salida": round(
            promedio_sitios_salida,
            2,
        ),
        "minutos_viaje": minutos_viaje,
        "minutos_total": minutos_total,
        "distancia_directa_km": round(
            distancia_directa,
            2,
        ),
        "distancia_vial_estimada_km": round(
            distancia_vial,
            2,
        ),
        "score_cobertura": round(
            score_cobertura,
            2,
        ),
        "score_aprovechamiento": round(
            score_aprovechamiento,
            2,
        ),
        "score_residuales": round(
            score_residuales,
            2,
        ),
        "score_remanente": round(
            score_remanente_global,
            2,
        ),
        "score_equidad_cuadrillas": (score_equidad),
        "score_operativo": round(
            max(
                min(
                    score_operativo,
                    100,
                ),
                0,
            ),
            2,
        ),
        "clusters_utilizados": len(clusters_utilizados),
        "salidas_por_cuadrilla": (salidas_usadas),
        "cupos_por_cuadrilla": cupos,
        "utilizacion_por_cuadrilla": (utilizacion_por_cuadrilla),
        "cuadrillas_sin_trabajo": (cuadrillas_sin_trabajo),
    }


# ============================================================
# TIPO TERRITORIAL DE GRUPO RESIDUAL
# ============================================================


def _tipo_territorial_grupo_residual(
    sitios,
):
    """
    Clasifica un grupo residual como:

        urbano
        rural
        mixto
        desconocido

    Se utiliza únicamente para decidir qué distancia máxima
    permitimos al recomponer parejas globales.
    """

    sitios = list(
        sitios,
    )

    if not sitios:
        return "desconocido"

    todos_urbanos = all(
        bool(
            sitio.urbano,
        )
        and not bool(
            sitio.rural,
        )
        for sitio in sitios
    )

    todos_rurales = all(
        bool(
            sitio.rural,
        )
        and not bool(
            sitio.urbano,
        )
        for sitio in sitios
    )

    contiene_urbano = any(
        bool(
            sitio.urbano,
        )
        for sitio in sitios
    )

    contiene_rural = any(
        bool(
            sitio.rural,
        )
        for sitio in sitios
    )

    if todos_urbanos:
        return "urbano"

    if todos_rurales:
        return "rural"

    if contiene_urbano and contiene_rural:
        return "mixto"

    return "desconocido"


# ============================================================
# DISTANCIA RESIDUAL ENTRE SITIOS
# ============================================================


def _distancia_residual_entre_sitios(
    sitio_a,
    sitio_b,
):
    distancia = distancia_haversine_km(
        sitio_a.latitud,
        sitio_a.longitud,
        sitio_b.latitud,
        sitio_b.longitud,
    )

    if distancia is None:
        return None

    try:
        return float(
            distancia,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# GRUPO RESIDUAL GLOBAL RAZONABLE
# ============================================================


def _grupo_residual_global_es_razonable(
    grupo,
):
    """
    Decide si vale la pena evaluar territorialmente un grupo
    residual.

    REGLAS
    ==========================================================

    1 sitio:
        siempre puede evaluarse.

    2 sitios:
        usamos distancia dependiendo del tipo territorial:

            urbano  -> 14 km
            mixto   -> 22 km
            rural   -> 30 km

    3 sitios:
        exigimos que la distancia máxima entre cualquier
        pareja no supere 14 km.

    IMPORTANTE
    ==========================================================

    Los clusters NO participan en esta validación.

    Si dos sitios están territorialmente relacionados,
    pueden juntarse aunque originalmente hayan terminado
    en clusters diferentes.
    """

    grupo = list(
        grupo,
    )

    cantidad = len(
        grupo,
    )

    if cantidad == 1:
        return True

    if cantidad == 2:

        distancia = _distancia_residual_entre_sitios(
            grupo[0],
            grupo[1],
        )

        if distancia is None:
            return False

        tipo = _tipo_territorial_grupo_residual(
            grupo,
        )

        if tipo == "urbano":

            limite = RADIO_MAXIMO_PAREJA_URBANA_KM

        elif tipo == "rural":

            limite = RADIO_MAXIMO_PAREJA_RURAL_KM

        else:

            limite = RADIO_MAXIMO_PAREJA_MIXTA_KM

        return distancia <= limite

    if cantidad == 3:

        distancias = []

        for indice, sitio_a in enumerate(
            grupo,
        ):

            for sitio_b in grupo[indice + 1 :]:

                distancia = _distancia_residual_entre_sitios(
                    sitio_a,
                    sitio_b,
                )

                if distancia is None:
                    return False

                distancias.append(
                    distancia,
                )

        if not distancias:
            return False

        return (
            max(
                distancias,
            )
            <= RADIO_MAXIMO_TERNA_RESIDUAL_GLOBAL_KM
        )

    return False


# ============================================================
# GENERAR CANDIDATOS RESIDUALES GLOBALES
# ============================================================


def _generar_candidatos_residuales_globales(
    *,
    sitios,
    configuraciones,
    cupos_residuales,
    cluster_por_sitio_id,
):
    """
    Genera todas las combinaciones razonables de:

        3 sitios
        2 sitios
        1 sitio

    utilizando TODO el universo residual.

    Aquí desaparece la frontera entre clusters.

    Una combinación solamente existe si una misma cuadrilla:

        - tiene cupo;
        - posee base;
        - soporta la cantidad;
        - puede ejecutar todos los sitios;
        - puede calcular una ruta válida.

    Para nuevas ternas residuales agregamos además una
    protección de 12 horas máximas.

    Esto evita crear ternas absurdas solamente por alcanzar
    el número 3.
    """

    sitios = list(
        sitios,
    )

    candidatos = []

    if not sitios:
        return candidatos

    maximo_global = min(
        MAX_SITIOS_POR_SALIDA,
        len(
            sitios,
        ),
    )

    for cantidad in range(
        maximo_global,
        0,
        -1,
    ):

        for grupo in combinations(
            sitios,
            cantidad,
        ):

            if not _grupo_residual_global_es_razonable(
                grupo,
            ):
                continue

            ids_grupo = frozenset(
                _id_sitio(
                    sitio,
                )
                for sitio in grupo
            )

            clusters_origen = {
                cluster_por_sitio_id.get(
                    sitio_id,
                )
                for sitio_id in ids_grupo
            }

            clusters_origen.discard(
                None,
            )

            for configuracion in configuraciones:

                cuadrilla = configuracion["cuadrilla"]

                if (
                    cupos_residuales.get(
                        cuadrilla,
                        0,
                    )
                    <= 0
                ):
                    continue

                if not configuracion.get(
                    "tiene_base",
                    False,
                ):
                    continue

                if cantidad > _maximo_sitios_configuracion(
                    configuracion,
                ):
                    continue

                compatibles = all(
                    cuadrilla_puede_ejecutar_sitio(
                        configuracion,
                        sitio,
                    )
                    for sitio in grupo
                )

                if not compatibles:
                    continue

                salida = encontrar_mejor_salida(
                    sitios=grupo,
                    configuracion_cuadrilla=(configuracion),
                )

                if not salida:
                    continue

                if not salida.get(
                    "viable",
                    False,
                ):
                    continue

                # ============================================
                # PROTECCIÓN DE TERNAS RESIDUALES
                # ============================================
                #
                # En salidas.py las ternas actualmente pueden
                # ser declaradas viables aun con una duración
                # muy elevada.
                #
                # No utilizaremos esa excepción para crear
                # nuevas recomposiciones absurdas.
                # ============================================

                if cantidad == 3:

                    minutos_total = int(
                        salida.get(
                            "minutos_total",
                            0,
                        )
                        or 0
                    )

                    minutos_jornada = int(
                        salida.get(
                            "minutos_jornada",
                            0,
                        )
                        or 0
                    )

                    limite_terna = max(
                        minutos_jornada,
                        MINUTOS_MAXIMOS_TERNA_RESIDUAL_GLOBAL,
                    )

                    if minutos_total > limite_terna:
                        continue

                salida = dict(
                    salida,
                )

                salida["cluster_id"] = "recompactacion_residual_global_" + "_".join(
                    sorted(
                        str(
                            cluster_id,
                        )
                        for cluster_id in clusters_origen
                    )
                )

                salida["cluster_score"] = 0.0

                salida["sitio_ids_set"] = ids_grupo

                # ============================================
                # SCORE DIAGNÓSTICO
                # ============================================

                if cantidad == 3:

                    score_cantidad = 100.0

                elif cantidad == 2:

                    score_cantidad = 82.0

                else:

                    score_cantidad = 30.0

                minutos_total = max(
                    int(
                        salida.get(
                            "minutos_total",
                            0,
                        )
                        or 0
                    ),
                    1,
                )

                minutos_viaje = int(
                    salida.get(
                        "minutos_viaje",
                        0,
                    )
                    or 0
                )

                proporcion_viaje = minutos_viaje / minutos_total

                score_viaje = max(
                    100.0 - proporcion_viaje * 100.0,
                    0.0,
                )

                score_salida = score_cantidad * 0.70 + score_viaje * 0.30

                score_salida -= _penalizacion_jornada_extendida(
                    salida,
                )

                salida["score_salida"] = round(
                    max(
                        min(
                            score_salida,
                            100.0,
                        ),
                        0.0,
                    ),
                    2,
                )

                salida["score_remanente"] = 100.0

                salida["score_seleccion"] = salida["score_salida"]

                salida["nivel_empaquetamiento"] = cantidad

                salida["objetivo_diario_completo"] = cantidad == 3

                salida["salida_incompleta_recalculable"] = cantidad < 3

                salida["salida_individual_excepcional"] = cantidad == 1

                salida["recompactacion_residual"] = True

                salida["recompactacion_global"] = True

                salida["clusters_origen"] = sorted(
                    str(
                        cluster_id,
                    )
                    for cluster_id in clusters_origen
                )

                salida["prioridad_especializacion"] = _prioridad_especializacion_salida(
                    salida=salida,
                    configuraciones_por_cuadrilla=(
                        _configuraciones_por_cuadrilla(
                            configuraciones,
                        )
                    ),
                )

                candidatos.append(
                    salida,
                )

    candidatos.sort(
        key=lambda salida: (
            -salida["cantidad_sitios"],
            salida.get(
                "jornada_extendida",
                False,
            ),
            -salida.get(
                "prioridad_especializacion",
                0,
            ),
            salida.get(
                "minutos_viaje",
                0,
            ),
            salida.get(
                "minutos_total",
                0,
            ),
        )
    )

    return candidatos


# ============================================================
# VALOR DE UNA SALIDA PARA OPTIMIZACIÓN EXACTA
# ============================================================


def _valor_salida_residual(
    salida,
):
    """
    Devuelve una tupla ADITIVA para comparar planes.

    JERARQUÍA ABSOLUTA
    ==========================================================

    1. cantidad total de sitios;
    2. cantidad de ternas;
    3. evitar unitarios;
    4. evitar jornadas extendidas;
    5. menor viaje;
    6. menor duración.

    Ejemplo:

        4 sitios como 3 + 1

    gana frente a:

        4 sitios como 2 + 2

    porque mantenemos la filosofía operacional de priorizar
    jornadas completas de 3.
    """

    cantidad = int(
        salida.get(
            "cantidad_sitios",
            0,
        )
        or 0
    )

    es_terna = 1 if cantidad == 3 else 0

    es_unitaria = 1 if cantidad == 1 else 0

    extendida = (
        1
        if salida.get(
            "jornada_extendida",
            False,
        )
        else 0
    )

    minutos_viaje = int(
        salida.get(
            "minutos_viaje",
            0,
        )
        or 0
    )

    minutos_total = int(
        salida.get(
            "minutos_total",
            0,
        )
        or 0
    )

    return (
        cantidad,
        es_terna,
        -es_unitaria,
        -extendida,
        -minutos_viaje,
        -minutos_total,
    )


# ============================================================
# OPTIMIZACIÓN EXACTA DEL RESIDUAL
# ============================================================


def _optimizar_residuales_exacto(
    *,
    sitios,
    candidatos,
    cupos_residuales,
):
    """
    Busca la mejor combinación COMPLETA del universo residual.

    Esta es la pieza que evita decisiones greedy como:

        C2 -> San José urbano solo

    dejando:

        San José rural pendiente

    cuando realmente existe:

        C1/C3 -> urbano + rural

    y con eso liberamos otro día/cupo.

    El objetivo número 1 es SIEMPRE maximizar sitios.

    Con hasta MAX_SITIOS_OPTIMIZACION_EXACTA utilizamos una
    búsqueda exacta con memoización.
    """

    sitios = list(
        sitios,
    )

    if not sitios:
        return []

    sitios_ids = [
        _id_sitio(
            sitio,
        )
        for sitio in sitios
    ]

    indice_por_id = {
        sitio_id: indice
        for indice, sitio_id in enumerate(
            sitios_ids,
        )
    }

    cuadrillas = sorted(cupos_residuales.keys())

    indice_cuadrilla = {
        cuadrilla: indice
        for indice, cuadrilla in enumerate(
            cuadrillas,
        )
    }

    candidatos_preparados = []

    candidatos_por_indice_sitio = {
        indice: []
        for indice in range(
            len(
                sitios,
            )
        )
    }

    for salida in candidatos:

        cuadrilla = salida["cuadrilla"]

        if cuadrilla not in indice_cuadrilla:
            continue

        mascara = 0

        valido = True

        for sitio_id in salida["sitio_ids_set"]:

            indice = indice_por_id.get(
                sitio_id,
            )

            if indice is None:

                valido = False

                break

            mascara |= 1 << indice

        if not valido:
            continue

        indice_candidato = len(
            candidatos_preparados,
        )

        candidatos_preparados.append(
            {
                "salida": salida,
                "mascara": mascara,
                "indice_cuadrilla": (indice_cuadrilla[cuadrilla]),
                "valor": (
                    _valor_salida_residual(
                        salida,
                    )
                ),
            }
        )

        for indice in range(
            len(
                sitios,
            )
        ):

            if mascara & (1 << indice):

                candidatos_por_indice_sitio[indice].append(
                    indice_candidato,
                )

    mascara_inicial = (
        1
        << len(
            sitios,
        )
    ) - 1

    cupos_iniciales = tuple(
        max(
            int(
                cupos_residuales.get(
                    cuadrilla,
                    0,
                )
                or 0
            ),
            0,
        )
        for cuadrilla in cuadrillas
    )

    valor_cero = (
        0,
        0,
        0,
        0,
        0,
        0,
    )

    @lru_cache(
        maxsize=None,
    )
    def resolver(
        mascara_disponible,
        cupos_estado,
    ):
        if (
            mascara_disponible == 0
            or sum(
                cupos_estado,
            )
            <= 0
        ):
            return (
                valor_cero,
                (),
            )

        # ====================================================
        # TOMAMOS EL PRIMER SITIO TODAVÍA DISPONIBLE
        # ====================================================

        bit_menor = mascara_disponible & -mascara_disponible

        indice_sitio = bit_menor.bit_length() - 1

        # ====================================================
        # OPCIÓN 1:
        #
        # dejar este sitio pendiente.
        # ====================================================

        valor_mejor, seleccion_mejor = resolver(
            mascara_disponible & ~bit_menor,
            cupos_estado,
        )

        # ====================================================
        # OPCIÓN 2:
        #
        # utilizar cualquiera de las salidas que contiene
        # este sitio.
        # ====================================================

        for indice_candidato in candidatos_por_indice_sitio.get(
            indice_sitio,
            [],
        ):

            candidato = candidatos_preparados[indice_candidato]

            mascara_candidato = candidato["mascara"]

            if (mascara_candidato & mascara_disponible) != mascara_candidato:
                continue

            indice_cuadrilla_candidato = candidato["indice_cuadrilla"]

            if cupos_estado[indice_cuadrilla_candidato] <= 0:
                continue

            nuevos_cupos = list(
                cupos_estado,
            )

            nuevos_cupos[indice_cuadrilla_candidato] -= 1

            valor_restante, seleccion_restante = resolver(
                mascara_disponible & ~mascara_candidato,
                tuple(
                    nuevos_cupos,
                ),
            )

            valor_candidato = tuple(
                valor_restante[posicion] + candidato["valor"][posicion]
                for posicion in range(
                    len(
                        valor_cero,
                    )
                )
            )

            if valor_candidato > valor_mejor:

                valor_mejor = valor_candidato

                seleccion_mejor = (indice_candidato,) + seleccion_restante

        return (
            valor_mejor,
            seleccion_mejor,
        )

    _, seleccion_indices = resolver(
        mascara_inicial,
        cupos_iniciales,
    )

    return [
        candidatos_preparados[indice_candidato]["salida"]
        for indice_candidato in seleccion_indices
    ]


# ============================================================
# OPTIMIZACIÓN HEURÍSTICA DEL RESIDUAL
# ============================================================


def _optimizar_residuales_heuristico(
    *,
    sitios,
    candidatos,
    cupos_residuales,
):
    """
    Fallback para universos residuales demasiado grandes.

    La optimización exacta es preferida siempre que el número
    de sitios lo permita.

    IMPORTANTE:

    El resultado heurístico todavía será comparado contra el
    plan original.

    Por lo tanto nunca puede empeorar la cobertura publicada.
    """

    sitios_ids = {
        _id_sitio(
            sitio,
        )
        for sitio in sitios
    }

    sitios_usados = set()

    cupos_usados = {cuadrilla: 0 for cuadrilla in cupos_residuales}

    seleccionadas = []

    candidatos_ordenados = sorted(
        candidatos,
        key=lambda salida: (
            -salida["cantidad_sitios"],
            salida.get(
                "jornada_extendida",
                False,
            ),
            -salida.get(
                "prioridad_especializacion",
                0,
            ),
            salida.get(
                "minutos_viaje",
                0,
            ),
            salida.get(
                "minutos_total",
                0,
            ),
        ),
    )

    for salida in candidatos_ordenados:

        cuadrilla = salida["cuadrilla"]

        if cupos_usados.get(
            cuadrilla,
            0,
        ) >= cupos_residuales.get(
            cuadrilla,
            0,
        ):
            continue

        ids = set(salida["sitio_ids_set"])

        if not ids.issubset(
            sitios_ids,
        ):
            continue

        if sitios_usados.intersection(
            ids,
        ):
            continue

        seleccionadas.append(
            salida,
        )

        sitios_usados.update(
            ids,
        )

        cupos_usados[cuadrilla] = (
            cupos_usados.get(
                cuadrilla,
                0,
            )
            + 1
        )

    return seleccionadas


# ============================================================
# OPTIMIZAR RESIDUALES GLOBALES
# ============================================================


def _optimizar_residuales_globales(
    *,
    sitios,
    candidatos,
    cupos_residuales,
):
    sitios = list(
        sitios,
    )

    if (
        len(
            sitios,
        )
        <= MAX_SITIOS_OPTIMIZACION_EXACTA
    ):

        return _optimizar_residuales_exacto(
            sitios=sitios,
            candidatos=candidatos,
            cupos_residuales=(cupos_residuales),
        )

    return _optimizar_residuales_heuristico(
        sitios=sitios,
        candidatos=candidatos,
        cupos_residuales=(cupos_residuales),
    )


# ============================================================
# RECONSTRUIR MÉTRICAS DE PLAN
# ============================================================


def _reconstruir_metricas_plan(
    *,
    plan_base,
    salidas,
    cupos,
):
    """
    Reconstruye exactamente las métricas principales después
    de una recomposición residual.

    No modifica los modelos ni persiste nada.

    Solamente reconstruye el diccionario que ya devuelve el
    orquestador.
    """

    plan = dict(
        plan_base,
    )

    salidas = list(
        salidas,
    )

    sitios_seleccionados = []

    ids_agregados = set()

    salidas_usadas = {cuadrilla: 0 for cuadrilla in cupos}

    for salida in salidas:

        cuadrilla = salida["cuadrilla"]

        salidas_usadas[cuadrilla] = (
            salidas_usadas.get(
                cuadrilla,
                0,
            )
            + 1
        )

        for sitio in salida["sitios"]:

            sitio_id = sitio.sitio_planificado_id

            if sitio_id in ids_agregados:
                continue

            sitios_seleccionados.append(
                sitio,
            )

            ids_agregados.add(
                sitio_id,
            )

    total_salidas = len(
        salidas,
    )

    salidas_3 = sum(1 for salida in salidas if salida["cantidad_sitios"] == 3)

    salidas_2 = sum(1 for salida in salidas if salida["cantidad_sitios"] == 2)

    salidas_1 = sum(1 for salida in salidas if salida["cantidad_sitios"] == 1)

    salidas_extendidas = sum(
        1
        for salida in salidas
        if salida.get(
            "jornada_extendida",
            False,
        )
    )

    minutos_exceso_total = sum(
        int(
            salida.get(
                "exceso_jornada_minutos",
                0,
            )
            or 0
        )
        for salida in salidas
    )

    minutos_viaje = sum(
        int(
            salida.get(
                "minutos_viaje",
                0,
            )
            or 0
        )
        for salida in salidas
    )

    minutos_total = sum(
        int(
            salida.get(
                "minutos_total",
                0,
            )
            or 0
        )
        for salida in salidas
    )

    distancia_directa = sum(
        float(
            salida.get(
                "distancia_directa_km",
                0,
            )
            or 0
        )
        for salida in salidas
    )

    distancia_vial = sum(
        float(
            salida.get(
                "distancia_vial_estimada_km",
                0,
            )
            or 0
        )
        for salida in salidas
    )

    if total_salidas:

        promedio_sitios_salida = (
            len(
                sitios_seleccionados,
            )
            / total_salidas
        )

    else:

        promedio_sitios_salida = 0.0

    objetivo = int(
        plan.get(
            "objetivo",
            0,
        )
        or 0
    )

    if objetivo:

        score_cobertura = min(
            (
                len(
                    sitios_seleccionados,
                )
                / objetivo
                * 100
            ),
            100,
        )

    else:

        score_cobertura = 0.0

    score_aprovechamiento = min(
        (promedio_sitios_salida / MAX_SITIOS_POR_SALIDA * 100),
        100,
    )

    if total_salidas:

        score_residuales = 100 - (salidas_1 / total_salidas * 100)

    else:

        score_residuales = 0.0

    scores_remanente = [
        float(
            salida.get(
                "score_remanente",
                100,
            )
            or 0
        )
        for salida in salidas
    ]

    if scores_remanente:

        score_remanente_global = sum(
            scores_remanente,
        ) / len(
            scores_remanente,
        )

    else:

        score_remanente_global = 0.0

    score_equidad = _calcular_score_equidad(
        salidas_usadas=(salidas_usadas),
        cupos=cupos,
    )

    utilizacion_por_cuadrilla = _utilizacion_por_cuadrilla(
        salidas_usadas=(salidas_usadas),
        cupos=cupos,
    )

    cuadrillas_sin_trabajo = [
        cuadrilla
        for cuadrilla, usadas in salidas_usadas.items()
        if (
            cupos.get(
                cuadrilla,
                0,
            )
            > 0
            and usadas == 0
        )
    ]

    score_operativo = (
        score_cobertura * 0.35
        + score_aprovechamiento * 0.25
        + score_residuales * 0.16
        + score_remanente_global * 0.14
        + score_equidad * 0.10
    )

    clusters_utilizados = {
        salida.get(
            "cluster_id",
        )
        for salida in salidas
        if salida.get(
            "cluster_id",
        )
    }

    plan.update(
        {
            "sitios": (sitios_seleccionados),
            "sitio_ids": [sitio.sitio_planificado_id for sitio in sitios_seleccionados],
            "cantidad_sitios": len(
                sitios_seleccionados,
            ),
            "faltantes_objetivo": max(
                objetivo
                - len(
                    sitios_seleccionados,
                ),
                0,
            ),
            "salidas": salidas,
            "total_salidas": (total_salidas),
            "salidas_3_sitios": (salidas_3),
            "salidas_2_sitios": (salidas_2),
            "salidas_1_sitio": (salidas_1),
            "salidas_jornada_extendida": (salidas_extendidas),
            "minutos_extension_total": (minutos_exceso_total),
            "promedio_sitios_salida": round(
                promedio_sitios_salida,
                2,
            ),
            "minutos_viaje": (minutos_viaje),
            "minutos_total": (minutos_total),
            "distancia_directa_km": round(
                distancia_directa,
                2,
            ),
            "distancia_vial_estimada_km": round(
                distancia_vial,
                2,
            ),
            "score_cobertura": round(
                score_cobertura,
                2,
            ),
            "score_aprovechamiento": round(
                score_aprovechamiento,
                2,
            ),
            "score_residuales": round(
                score_residuales,
                2,
            ),
            "score_remanente": round(
                score_remanente_global,
                2,
            ),
            "score_equidad_cuadrillas": (score_equidad),
            "score_operativo": round(
                max(
                    min(
                        score_operativo,
                        100,
                    ),
                    0,
                ),
                2,
            ),
            "clusters_utilizados": len(
                clusters_utilizados,
            ),
            "salidas_por_cuadrilla": (salidas_usadas),
            "cupos_por_cuadrilla": (cupos),
            "utilizacion_por_cuadrilla": (utilizacion_por_cuadrilla),
            "cuadrillas_sin_trabajo": (cuadrillas_sin_trabajo),
        }
    )

    return plan


# ============================================================
# CLAVE DE CALIDAD DEL PLAN
# ============================================================


def _clave_calidad_plan(
    plan,
):
    """
    Regla definitiva para comparar el plan original contra
    la recomposición inteligente.

    PRIORIDAD
    ==========================================================

    1. MÁS SITIOS PLANIFICADOS.
    2. MÁS SALIDAS DE 3.
    3. MENOS SALIDAS DE 1.
    4. MENOS JORNADAS EXTENDIDAS.
    5. MENOS SALIDAS TOTALES.
    6. MENOS VIAJE.
    7. MENOR TIEMPO TOTAL.

    Por lo tanto:

        34 sitios

    SIEMPRE gana a:

        32 sitios

    sin importar que el plan de 32 tenga rutas aparentemente
    más bonitas.
    """

    return (
        int(
            plan.get(
                "cantidad_sitios",
                0,
            )
            or 0
        ),
        int(
            plan.get(
                "salidas_3_sitios",
                0,
            )
            or 0
        ),
        -int(
            plan.get(
                "salidas_1_sitio",
                0,
            )
            or 0
        ),
        -int(
            plan.get(
                "salidas_jornada_extendida",
                0,
            )
            or 0
        ),
        -int(
            plan.get(
                "total_salidas",
                0,
            )
            or 0
        ),
        -int(
            plan.get(
                "minutos_viaje",
                0,
            )
            or 0
        ),
        -int(
            plan.get(
                "minutos_total",
                0,
            )
            or 0
        ),
    )


# ============================================================
# PLAN OPERATIVO SEMANAL
# ============================================================


def construir_plan_operativo_semana(
    *,
    clusters,
    disponibilidades,
    objetivo,
    estrategia=ESTRATEGIA_OPERATIVA,
):
    """
    Construye el plan semanal definitivo.

    ETAPA 1
    ==========================================================

    Ejecuta SIN MODIFICAR el motor que ya venía funcionando
    correctamente.

    Ese resultado constituye el PLAN BASE.

    ETAPA 2
    ==========================================================

    Las salidas de 3 sitios del plan base quedan protegidas.

    No las destruimos.

    ETAPA 3
    ==========================================================

    Reunimos:

        - todas las salidas de 2;
        - todas las salidas de 1;
        - todos los sitios que quedaron pendientes.

    Esos sitios vuelven a convertirse en un único universo
    semanal.

    Los clusters dejan de actuar como barreras.

    ETAPA 4
    ==========================================================

    Buscamos la combinación global que:

        1. planifique más sitios;
        2. consiga más ternas;
        3. deje menos unitarios;
        4. respete urbano/rural;
        5. respete cupos;
        6. respete compatibilidad;
        7. reduzca jornadas extendidas.

    ETAPA 5
    ==========================================================

    Comparamos el resultado contra el PLAN BASE.

    Si la nueva solución empeora aunque sea un solo sitio:

        SE DESCARTA.

    Esto impide repetir el problema:

        antes -> 34
        después -> 32

    Si encuentra:

        35
        36
        37
        38
        39
        40

    entonces sí reemplaza al plan base.
    """

    # ========================================================
    # 1. PLAN BASE
    # ========================================================

    plan_base = _construir_plan_operativo_semana_base(
        clusters=clusters,
        disponibilidades=(disponibilidades),
        objetivo=objetivo,
        estrategia=estrategia,
    )

    salidas_base = list(
        plan_base.get(
            "salidas",
            [],
        )
        or []
    )

    if not salidas_base:
        return plan_base

    # ========================================================
    # 2. CONFIGURACIONES
    # ========================================================

    configuraciones = _normalizar_disponibilidades(
        disponibilidades,
    )

    if not configuraciones:
        return plan_base

    cupos = _cupos_dias_por_cuadrilla(
        configuraciones,
    )

    # ========================================================
    # 3. PROTEGER SALIDAS COMPLETAS DE 3
    # ========================================================

    salidas_fijas = [
        salida
        for salida in salidas_base
        if int(
            salida.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )
        == 3
    ]

    ids_fijos = set()

    salidas_fijas_por_cuadrilla = {cuadrilla: 0 for cuadrilla in cupos}

    for salida in salidas_fijas:

        ids_fijos.update(salida["sitio_ids_set"])

        cuadrilla = salida["cuadrilla"]

        salidas_fijas_por_cuadrilla[cuadrilla] = (
            salidas_fijas_por_cuadrilla.get(
                cuadrilla,
                0,
            )
            + 1
        )

    # ========================================================
    # 4. CUPOS QUE QUEDAN DESPUÉS DE LAS TERNAS FIJAS
    # ========================================================

    cupos_residuales = {}

    for cuadrilla, cupo_total in cupos.items():

        cupos_residuales[cuadrilla] = max(
            int(cupo_total or 0)
            - salidas_fijas_por_cuadrilla.get(
                cuadrilla,
                0,
            ),
            0,
        )

    if sum(cupos_residuales.values()) <= 0:
        return plan_base

    # ========================================================
    # 5. UNIVERSO COMPLETO DE SITIOS
    # ========================================================

    sitios_por_id = {}

    cluster_por_sitio_id = {}

    for cluster in clusters:

        for sitio in cluster.sitios:

            sitio_id = _id_sitio(
                sitio,
            )

            sitios_por_id[sitio_id] = sitio

            cluster_por_sitio_id[sitio_id] = cluster.id_cluster

    # ========================================================
    # 6. UNIVERSO RESIDUAL
    # ========================================================
    #
    # Incluye TODO lo que no quedó protegido dentro de una
    # salida de 3.
    #
    # Por tanto reúne automáticamente:
    #
    # - parejas existentes;
    # - unitarios existentes;
    # - sitios pendientes.
    #
    # Ejemplo:
    #
    #   53_086 San José urbano
    #   13_809 San José rural
    #
    # vuelven a estar juntos en el mismo universo.
    # ========================================================

    sitios_residuales = [
        sitio for sitio_id, sitio in sitios_por_id.items() if sitio_id not in ids_fijos
    ]

    if not sitios_residuales:
        return plan_base

    # ========================================================
    # 7. GENERAR TODAS LAS OPCIONES RESIDUALES GLOBALES
    # ========================================================

    candidatos_residuales = _generar_candidatos_residuales_globales(
        sitios=sitios_residuales,
        configuraciones=(configuraciones),
        cupos_residuales=(cupos_residuales),
        cluster_por_sitio_id=(cluster_por_sitio_id),
    )

    if not candidatos_residuales:
        return plan_base

    # ========================================================
    # 8. OPTIMIZAR RESIDUAL COMPLETO
    # ========================================================

    salidas_residuales_nuevas = _optimizar_residuales_globales(
        sitios=sitios_residuales,
        candidatos=(candidatos_residuales),
        cupos_residuales=(cupos_residuales),
    )

    if not salidas_residuales_nuevas:
        return plan_base

    # ========================================================
    # 9. PLAN ALTERNATIVO
    # ========================================================

    salidas_alternativas = list(
        salidas_fijas,
    ) + list(
        salidas_residuales_nuevas,
    )

    plan_alternativo = _reconstruir_metricas_plan(
        plan_base=plan_base,
        salidas=salidas_alternativas,
        cupos=cupos,
    )

    # ========================================================
    # 10. COMPARACIÓN ABSOLUTA
    # ========================================================
    #
    # Esto es lo que protege el funcionamiento que ya teníamos.
    #
    # Si el original tiene:
    #
    #     34
    #
    # y el nuevo:
    #
    #     32
    #
    # gana automáticamente el original.
    #
    # Si ambos tienen 34:
    #
    # revisamos:
    #
    #     más ternas
    #     menos unitarios
    #     menos extendidas
    #     menos jornadas
    #     menos viaje
    #
    # ========================================================

    if _clave_calidad_plan(
        plan_alternativo,
    ) > _clave_calidad_plan(
        plan_base,
    ):

        plan_alternativo["recompactacion_residual_global_aplicada"] = True

        plan_alternativo["cantidad_sitios_plan_base"] = plan_base.get(
            "cantidad_sitios",
            0,
        )

        plan_alternativo["cantidad_sitios_mejora"] = plan_alternativo.get(
            "cantidad_sitios",
            0,
        ) - plan_base.get(
            "cantidad_sitios",
            0,
        )

        return plan_alternativo

    # ========================================================
    # EL PLAN NUEVO NO FUE MEJOR
    # ========================================================

    plan_base["recompactacion_residual_global_aplicada"] = False

    plan_base["cantidad_sitios_plan_base"] = plan_base.get(
        "cantidad_sitios",
        0,
    )

    plan_base["cantidad_sitios_mejora"] = 0

    return plan_base

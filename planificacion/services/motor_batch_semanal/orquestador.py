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
# PAREJAS RESIDUALES EXCEPCIONALES POR TIEMPO
# ============================================================

MINUTOS_MAXIMOS_PAREJA_RESIDUAL_EXCEPCIONAL = 12 * 60
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
# REASIGNAR CUADRILLAS DE SALIDAS FIJAS
# ============================================================


def _reasignar_cuadrillas_salidas_fijas(
    *,
    salidas_fijas,
    configuraciones,
    cupos,
):
    """
    Redistribuye las salidas protegidas de 3 sitios entre
    las cuadrillas compatibles.

    REGLA FUNDAMENTAL
    ==========================================================

    Protegemos:

        los SITIOS que forman la terna.

    NO protegemos:

        la cuadrilla con la que originalmente fue encontrada.

    Esto es consistente con la arquitectura de planificación:

        orquestador.py
            decide agrupaciones;

        planificacion_diaria.py
            decide fecha y cuadrilla real.

    OBJETIVO
    ==========================================================

    Preservar la capacidad de las cuadrillas más flexibles.

    Ejemplo:

        C1 -> urbano + rural
        C2 -> urbano solamente
        C3 -> urbano + rural

    Una terna completamente urbana debe tender a utilizar C2
    antes que consumir innecesariamente un día de C1/C3.

    Las salidas rurales o mixtas continúan utilizando
    exclusivamente cuadrillas que puedan ejecutarlas.

    IMPORTANTE
    ==========================================================

    Esta función NO:

        rompe ternas;
        cambia sitios;
        crea parejas;
        modifica clusters;
        persiste datos.

    Solamente calcula una mejor asignación lógica de capacidad
    semanal para las ternas ya protegidas.
    """

    salidas_fijas = list(
        salidas_fijas,
    )

    configuraciones = list(
        configuraciones,
    )

    if not salidas_fijas:
        return []

    configuraciones_mapa = _configuraciones_por_cuadrilla(
        configuraciones,
    )

    # ========================================================
    # OPCIONES POR SALIDA
    # ========================================================

    opciones_por_indice = {}

    for indice, salida_original in enumerate(
        salidas_fijas,
    ):

        sitios = list(
            salida_original.get(
                "sitios",
                [],
            )
            or []
        )

        opciones = []

        if not sitios:
            opciones_por_indice[indice] = opciones
            continue

        tipo_salida = _tipo_territorial_salida(
            salida_original,
        )

        for configuracion in configuraciones:

            cuadrilla = configuracion["cuadrilla"]

            if (
                cupos.get(
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

            if len(
                sitios,
            ) > _maximo_sitios_configuracion(
                configuracion,
            ):
                continue

            compatibles = all(
                cuadrilla_puede_ejecutar_sitio(
                    configuracion,
                    sitio,
                )
                for sitio in sitios
            )

            if not compatibles:
                continue

            calculo = encontrar_mejor_salida(
                sitios=sitios,
                configuracion_cuadrilla=(
                    configuracion
                ),
            )

            if not calculo:
                continue

            if not calculo.get(
                "viable",
                False,
            ):
                continue

            salida = dict(
                calculo,
            )

            # =================================================
            # CONSERVAR METADATOS DE LA AGRUPACIÓN ORIGINAL
            # =================================================

            salida["cluster_id"] = salida_original.get(
                "cluster_id",
                "",
            )

            salida["cluster_score"] = salida_original.get(
                "cluster_score",
                0.0,
            )

            salida["sitio_ids_set"] = frozenset(
                salida_original.get(
                    "sitio_ids_set",
                    {
                        _id_sitio(
                            sitio,
                        )
                        for sitio in sitios
                    },
                )
            )

            salida["score_salida"] = salida_original.get(
                "score_salida",
                0.0,
            )

            salida["score_remanente"] = salida_original.get(
                "score_remanente",
                100.0,
            )

            salida["score_seleccion"] = salida_original.get(
                "score_seleccion",
                salida["score_salida"],
            )

            salida["nivel_empaquetamiento"] = 3

            salida["objetivo_diario_completo"] = True

            salida["salida_incompleta_recalculable"] = False

            salida["salida_individual_excepcional"] = False

            salida["recompactacion_residual"] = salida_original.get(
                "recompactacion_residual",
                False,
            )

            salida["recompactacion_global"] = salida_original.get(
                "recompactacion_global",
                False,
            )

            salida["clusters_origen"] = salida_original.get(
                "clusters_origen",
                [],
            )

            # =================================================
            # ESPECIALIZACIÓN
            # =================================================

            prioridad_especializacion = (
                _prioridad_especializacion_salida(
                    salida=salida,
                    configuraciones_por_cuadrilla=(
                        configuraciones_mapa
                    ),
                )
            )

            # =================================================
            # FLEXIBILIDAD
            # =================================================
            #
            # Menor flexibilidad significa cuadrilla más
            # especializada.
            #
            # urbano-only:
            #     1
            #
            # urbano+rural:
            #     2
            #
            # Para una salida urbana queremos preservar las
            # cuadrillas flexibles.
            # =================================================

            flexibilidad = _flexibilidad_cuadrilla(
                configuracion,
            )

            if tipo_salida == "urbano":

                prioridad_preservacion = (
                    flexibilidad
                )

            else:

                # Para rural/mixto solamente importan las
                # cuadrillas realmente compatibles.
                prioridad_preservacion = 0

            opciones.append(
                {
                    "salida": salida,
                    "cuadrilla": cuadrilla,
                    "tipo_salida": tipo_salida,
                    "prioridad_especializacion": (
                        prioridad_especializacion
                    ),
                    "prioridad_preservacion": (
                        prioridad_preservacion
                    ),
                }
            )

        opciones_por_indice[indice] = opciones

    # ========================================================
    # BÚSQUEDA EXACTA DE ASIGNACIÓN
    # ========================================================
    #
    # La cantidad de ternas semanales es pequeña.
    #
    # En W35 tenemos 8.
    #
    # Por tanto podemos evaluar la asignación de cuadrillas
    # exactamente en vez de utilizar un greedy.
    # ========================================================

    mejor_seleccion = None

    mejor_clave = None

    usos_iniciales = {
        cuadrilla: 0
        for cuadrilla in cupos
    }

    def evaluar_asignacion(
        seleccion,
        usos,
    ):
        """
        Devuelve una clave donde MAYOR es mejor.

        PRIORIDAD
        ======================================================

        1. asignar todas las ternas;
        2. utilizar especialización;
        3. preservar capacidad urbano+rural;
        4. dejar más capacidad rural disponible;
        5. menor jornada extendida;
        6. menor viaje;
        7. menor duración.
        """

        cantidad_asignada = len(
            seleccion,
        )

        especializacion_total = sum(
            item.get(
                "prioridad_especializacion",
                0,
            )
            for item in seleccion
        )

        preservacion_total = sum(
            item.get(
                "prioridad_preservacion",
                0,
            )
            for item in seleccion
        )

        # ====================================================
        # CAPACIDAD RURAL RESTANTE
        # ====================================================

        capacidad_rural_restante = 0

        for configuracion in configuraciones:

            cuadrilla = configuracion["cuadrilla"]

            if not configuracion.get(
                "permite_rural",
                False,
            ):
                continue

            restante = max(
                int(
                    cupos.get(
                        cuadrilla,
                        0,
                    )
                    or 0
                )
                - usos.get(
                    cuadrilla,
                    0,
                ),
                0,
            )

            capacidad_rural_restante += restante

        extendidas = sum(
            1
            for item in seleccion
            if item["salida"].get(
                "jornada_extendida",
                False,
            )
        )

        minutos_viaje = sum(
            int(
                item["salida"].get(
                    "minutos_viaje",
                    0,
                )
                or 0
            )
            for item in seleccion
        )

        minutos_total = sum(
            int(
                item["salida"].get(
                    "minutos_total",
                    0,
                )
                or 0
            )
            for item in seleccion
        )

        return (
            cantidad_asignada,
            especializacion_total,
            -preservacion_total,
            capacidad_rural_restante,
            -extendidas,
            -minutos_viaje,
            -minutos_total,
        )

    def resolver(
        indice,
        seleccion,
        usos,
    ):
        nonlocal mejor_seleccion
        nonlocal mejor_clave

        if indice >= len(
            salidas_fijas,
        ):

            clave = evaluar_asignacion(
                seleccion,
                usos,
            )

            if (
                mejor_seleccion is None
                or clave > mejor_clave
            ):

                mejor_seleccion = list(
                    seleccion,
                )

                mejor_clave = clave

            return

        opciones = opciones_por_indice.get(
            indice,
            [],
        )

        # ====================================================
        # FALLBACK
        # ========================================================
        #
        # Una terna base debería poseer al menos la cuadrilla
        # con la que originalmente fue calculada.
        #
        # Si por alguna inconsistencia no existe opción,
        # dejamos que la rama continúe sin ella.
        # Posteriormente verificaremos que todas hayan sido
        # asignadas.
        # ====================================================

        if not opciones:

            resolver(
                indice + 1,
                seleccion,
                usos,
            )

            return

        # ====================================================
        # PROBAR CADA CUADRILLA
        # ====================================================

        for opcion in opciones:

            cuadrilla = opcion[
                "cuadrilla"
            ]

            usadas = usos.get(
                cuadrilla,
                0,
            )

            cupo = int(
                cupos.get(
                    cuadrilla,
                    0,
                )
                or 0
            )

            if usadas >= cupo:
                continue

            nuevos_usos = dict(
                usos,
            )

            nuevos_usos[cuadrilla] = (
                usadas + 1
            )

            resolver(
                indice + 1,
                seleccion + [
                    opcion,
                ],
                nuevos_usos,
            )

    resolver(
        0,
        [],
        usos_iniciales,
    )

    # ========================================================
    # SEGURIDAD
    # ========================================================

    if (
        mejor_seleccion is None
        or len(
            mejor_seleccion,
        )
        != len(
            salidas_fijas,
        )
    ):

        # Si no conseguimos reasignar absolutamente todas las
        # ternas, conservamos exactamente el plan original.
        return salidas_fijas

    return [
        item["salida"]
        for item in mejor_seleccion
    ]

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
    Decide si un grupo residual puede pasar a evaluación
    operacional.

    REGLAS
    ==========================================================

    1 sitio:
        siempre puede evaluarse.

    2 sitios:
        NO descartamos aquí por distancia.

        La distancia continúa siendo la condición territorial
        PREFERIDA:

            urbano  -> 14 km
            mixto   -> 22 km
            rural   -> 30 km

        pero una pareja que supere esos límites todavía puede
        ser aceptada posteriormente si su jornada real queda
        dentro del máximo excepcional de 12 horas.

        Por eso toda pareja con coordenadas válidas pasa a
        encontrar_mejor_salida().

    3 sitios:
        conservamos la protección territorial fuerte.

        La distancia máxima entre cualquier pareja debe ser
        <= RADIO_MAXIMO_TERNA_RESIDUAL_GLOBAL_KM.

    IMPORTANTE
    ==========================================================

    Las ternas siguen siendo estrictamente territoriales.

    La excepción por tiempo aplica solamente a parejas.
    """

    grupo = list(
        grupo,
    )

    cantidad = len(
        grupo,
    )

    # ========================================================
    # UNITARIA
    # ========================================================

    if cantidad == 1:
        return True

    # ========================================================
    # PAREJA
    # ========================================================
    #
    # No filtramos todavía por distancia.
    #
    # Solamente verificamos que exista una distancia válida.
    #
    # La decisión:
    #
    #   dentro del radio normal
    #
    # o:
    #
    #   fuera del radio pero <= 12 horas
    #
    # se tomará después de calcular la ruta real.
    # ========================================================

    if cantidad == 2:

        distancia = _distancia_residual_entre_sitios(
            grupo[0],
            grupo[1],
        )

        return distancia is not None

    # ========================================================
    # TERNA
    # ========================================================

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

    REGLA DE TERNAS
    ==========================================================

    Las ternas mantienen una protección territorial fuerte:

        distancia máxima entre sitios <=
        RADIO_MAXIMO_TERNA_RESIDUAL_GLOBAL_KM

    y además:

        duración máxima residual protegida <= 12 horas.

    REGLA DE PAREJAS
    ==========================================================

    Las parejas poseen dos niveles de aceptación:

    NIVEL 1 - NORMAL
    ----------------------------------------------------------

    Si la distancia entre sitios cumple:

        urbano  <= 14 km
        mixto   <= 22 km
        rural   <= 30 km

    la pareja se evalúa normalmente.

    NIVEL 2 - EXCEPCIONAL POR TIEMPO
    ----------------------------------------------------------

    Si supera el límite territorial normal, todavía puede
    utilizarse siempre que:

        encontrar_mejor_salida()
            produzca una salida viable

    y:

        minutos_total <= 12 horas.

    De esta forma:

        distancia preferida
            sigue siendo la primera protección;

    pero:

        una pareja operacionalmente razonable
            no se pierde únicamente por superar algunos km.

    IMPORTANTE
    ==========================================================

    La excepción por tiempo aplica SOLAMENTE a parejas.

    No flexibilizamos las ternas con esta regla.
    """

    sitios = list(
        sitios,
    )

    candidatos = []

    if not sitios:
        return candidatos

    configuraciones_mapa = _configuraciones_por_cuadrilla(
        configuraciones,
    )

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

            # =================================================
            # FILTRO TERRITORIAL PRELIMINAR
            # =================================================
            #
            # Para parejas ya NO descarta por superar 14/22/30.
            #
            # Para ternas continúa siendo estricto.
            # =================================================

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

            # =================================================
            # INFORMACIÓN TERRITORIAL DE PAREJA
            # =================================================

            pareja_supera_limite_normal = False

            distancia_pareja_km = None

            limite_pareja_km = None

            tipo_pareja = None

            if cantidad == 2:

                distancia_pareja_km = _distancia_residual_entre_sitios(
                    grupo[0],
                    grupo[1],
                )

                if distancia_pareja_km is None:
                    continue

                tipo_pareja = _tipo_territorial_grupo_residual(
                    grupo,
                )

                if tipo_pareja == "urbano":

                    limite_pareja_km = RADIO_MAXIMO_PAREJA_URBANA_KM

                elif tipo_pareja == "rural":

                    limite_pareja_km = RADIO_MAXIMO_PAREJA_RURAL_KM

                else:

                    limite_pareja_km = RADIO_MAXIMO_PAREJA_MIXTA_KM

                pareja_supera_limite_normal = distancia_pareja_km > limite_pareja_km

            # =================================================
            # PROBAR CUADRILLAS
            # =================================================

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

                # =============================================
                # COMPATIBILIDAD
                # =============================================

                compatibles = all(
                    cuadrilla_puede_ejecutar_sitio(
                        configuracion,
                        sitio,
                    )
                    for sitio in grupo
                )

                if not compatibles:
                    continue

                # =============================================
                # RUTA REAL
                # =============================================

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

                minutos_total_original = int(
                    salida.get(
                        "minutos_total",
                        0,
                    )
                    or 0
                )

                # =============================================
                # PAREJA EXCEPCIONAL
                # =============================================
                #
                # Si está fuera del radio normal:
                #
                #     solamente entra si <= 12 horas.
                #
                # Si está dentro del radio:
                #
                #     continúa con la lógica normal.
                # =============================================

                pareja_excepcional_por_tiempo = False

                if cantidad == 2 and pareja_supera_limite_normal:

                    if (
                        minutos_total_original
                        > MINUTOS_MAXIMOS_PAREJA_RESIDUAL_EXCEPCIONAL
                    ):
                        continue

                    pareja_excepcional_por_tiempo = True

                # =============================================
                # PROTECCIÓN DE TERNAS RESIDUALES
                # =============================================

                if cantidad == 3:

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

                    if minutos_total_original > limite_terna:
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

                # =============================================
                # TRAZABILIDAD PAREJA
                # =============================================

                salida["pareja_excepcional_por_tiempo"] = bool(
                    pareja_excepcional_por_tiempo
                )

                salida["distancia_limite_superada"] = bool(pareja_supera_limite_normal)

                salida["distancia_pareja_km"] = (
                    round(
                        distancia_pareja_km,
                        2,
                    )
                    if distancia_pareja_km is not None
                    else None
                )

                salida["distancia_limite_pareja_km"] = limite_pareja_km

                salida["tipo_territorial_pareja"] = tipo_pareja

                # =============================================
                # SCORE DIAGNÓSTICO
                # =============================================

                if cantidad == 3:

                    score_cantidad = 100.0

                elif cantidad == 2:

                    score_cantidad = 82.0

                else:

                    score_cantidad = 30.0

                minutos_total = max(
                    minutos_total_original,
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

                # =============================================
                # PEQUEÑA PENALIZACIÓN DIAGNÓSTICA
                # PARA PAREJA EXCEPCIONAL
                # =============================================
                #
                # No la invalida.
                #
                # Simplemente evita que una pareja a 70 km
                # gane a una pareja equivalente a 8 km si
                # ambas cubren la misma cantidad de sitios.
                # =============================================

                if pareja_excepcional_por_tiempo:

                    score_salida -= 5.0

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
                    configuraciones_por_cuadrilla=(configuraciones_mapa),
                )

                candidatos.append(
                    salida,
                )

    # ========================================================
    # ORDEN
    # ========================================================
    #
    # Entre dos parejas equivalentes:
    #
    # 1. preferimos la que cumple distancia normal;
    # 2. luego especialización;
    # 3. menor viaje;
    # 4. menor duración.
    #
    # Pero cantidad continúa siendo absoluta:
    #
    # 3 > 2 > 1.
    # ========================================================

    candidatos.sort(
        key=lambda salida: (
            -salida["cantidad_sitios"],
            salida.get(
                "pareja_excepcional_por_tiempo",
                False,
            ),
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

    ARQUITECTURA
    ==========================================================

    ETAPA 1
    ----------------------------------------------------------

    Construimos exactamente el PLAN BASE utilizando el motor
    que ya funciona.

    ETAPA 2
    ----------------------------------------------------------

    Las ternas del PLAN BASE quedan protegidas como GRUPOS.

    Ejemplo:

        A + B + C

    continuará siendo:

        A + B + C

    No desarmamos una terna buena para fabricar artificialmente
    nuevas parejas o unitarios.

    CAMBIO FUNDAMENTAL
    ----------------------------------------------------------

    Lo que YA NO queda protegido es la cuadrilla original.

    Antes:

        A + B + C -> C3

    quedaba congelado para siempre.

    Ahora:

        A + B + C

    se vuelve a probar con:

        C1
        C2
        C3

    según compatibilidad real.

    ETAPA 3
    ----------------------------------------------------------

    Reunimos nuevamente todos los sitios que NO pertenecen a
    una terna protegida:

        - parejas;
        - unitarios;
        - pendientes.

    Los clusters dejan de ser barreras absolutas para este
    universo residual.

    ETAPA 4
    ----------------------------------------------------------

    Evaluamos conjuntamente:

        asignación de cuadrillas a ternas protegidas

        +

        optimización residual global.

    Esto evita el defecto anterior:

        C3 -> 5 ternas
        C1 -> 2 ternas
        C2 -> 1 terna

    cuando esa distribución destruye la capacidad restante
    para sitios rurales.

    ETAPA 5
    ----------------------------------------------------------

    El nuevo candidato solamente gana si:

        _clave_calidad_plan(nuevo)
            >
        _clave_calidad_plan(base)

    PRIORIDAD ABSOLUTA
    ==========================================================

        1. más sitios;
        2. más ternas;
        3. menos unitarios;
        4. menos extendidas;
        5. menos jornadas;
        6. menos viaje;
        7. menor tiempo total.

    SEGURIDAD
    ==========================================================

    Si la nueva optimización falla o produce algo peor:

        se conserva PLAN BASE.

    Por tanto nunca debemos volver a degradar:

        34 -> 32

    por una optimización secundaria.
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

        plan_base["recompactacion_residual_global_aplicada"] = False

        plan_base["reasignacion_ternas_protegidas_aplicada"] = False

        plan_base["cantidad_sitios_plan_base"] = plan_base.get(
            "cantidad_sitios",
            0,
        )

        plan_base["cantidad_sitios_mejora"] = 0

        return plan_base

    # ========================================================
    # 2. CONFIGURACIONES REALES
    # ========================================================

    configuraciones = _normalizar_disponibilidades(
        disponibilidades,
    )

    if not configuraciones:

        plan_base["recompactacion_residual_global_aplicada"] = False

        plan_base["reasignacion_ternas_protegidas_aplicada"] = False

        plan_base["cantidad_sitios_plan_base"] = plan_base.get(
            "cantidad_sitios",
            0,
        )

        plan_base["cantidad_sitios_mejora"] = 0

        return plan_base

    cupos = _cupos_dias_por_cuadrilla(
        configuraciones,
    )

    if sum(int(valor or 0) for valor in cupos.values()) <= 0:

        plan_base["recompactacion_residual_global_aplicada"] = False

        plan_base["reasignacion_ternas_protegidas_aplicada"] = False

        plan_base["cantidad_sitios_plan_base"] = plan_base.get(
            "cantidad_sitios",
            0,
        )

        plan_base["cantidad_sitios_mejora"] = 0

        return plan_base

    # ========================================================
    # 3. PROTEGER SOLAMENTE LA COMPOSICIÓN DE LAS TERNAS
    # ========================================================

    salidas_ternas_base = [
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

    ids_ternas_protegidas = set()

    for salida in salidas_ternas_base:

        ids_ternas_protegidas.update(
            salida.get(
                "sitio_ids_set",
                set(),
            )
            or set()
        )

    # ========================================================
    # 4. UNIVERSO COMPLETO DE SITIOS
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
    # 5. UNIVERSO RESIDUAL
    # ========================================================
    #
    # Solamente excluimos los sitios que pertenecen a una
    # terna protegida.
    #
    # Todo lo demás vuelve a competir globalmente:
    #
    #     parejas originales
    #     unitarios originales
    #     sitios pendientes
    #
    # ========================================================

    sitios_residuales = [
        sitio
        for sitio_id, sitio in sitios_por_id.items()
        if (sitio_id not in ids_ternas_protegidas)
    ]

    # ========================================================
    # 6. OPTIMIZACIÓN CONJUNTA
    # ========================================================

    plan_alternativo = _optimizar_ternas_protegidas_y_residual(
        plan_base=plan_base,
        salidas_ternas_base=(salidas_ternas_base),
        sitios_residuales=(sitios_residuales),
        configuraciones=(configuraciones),
        cupos=cupos,
        cluster_por_sitio_id=(cluster_por_sitio_id),
    )

    # ========================================================
    # NO FUE POSIBLE CONSTRUIR ALTERNATIVA
    # ========================================================

    if plan_alternativo is None:

        plan_base["recompactacion_residual_global_aplicada"] = False

        plan_base["reasignacion_ternas_protegidas_aplicada"] = False

        plan_base["cantidad_sitios_plan_base"] = plan_base.get(
            "cantidad_sitios",
            0,
        )

        plan_base["cantidad_sitios_mejora"] = 0

        return plan_base

    # ========================================================
    # 7. COMPARACIÓN ABSOLUTA CONTRA PLAN BASE
    # ========================================================

    clave_base = _clave_calidad_plan(
        plan_base,
    )

    clave_alternativa = _clave_calidad_plan(
        plan_alternativo,
    )

    # ========================================================
    # ALTERNATIVA MEJOR
    # ========================================================

    if clave_alternativa > clave_base:

        cantidad_base = int(
            plan_base.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )

        cantidad_nueva = int(
            plan_alternativo.get(
                "cantidad_sitios",
                0,
            )
            or 0
        )

        plan_alternativo["recompactacion_residual_global_aplicada"] = True

        plan_alternativo["cantidad_sitios_plan_base"] = cantidad_base

        plan_alternativo["cantidad_sitios_mejora"] = cantidad_nueva - cantidad_base

        return plan_alternativo

    # ========================================================
    # 8. PLAN BASE CONTINÚA SIENDO MEJOR
    # ========================================================

    plan_base["recompactacion_residual_global_aplicada"] = False

    plan_base["reasignacion_ternas_protegidas_aplicada"] = False

    plan_base["cantidad_sitios_plan_base"] = plan_base.get(
        "cantidad_sitios",
        0,
    )

    plan_base["cantidad_sitios_mejora"] = 0

    return plan_base


# ============================================================
# GENERAR VARIANTES DE CUADRILLA PARA TERNAS PROTEGIDAS
# ============================================================


def _generar_variantes_ternas_protegidas(
    *,
    salidas_ternas,
    configuraciones,
):
    """
    Convierte cada terna protegida del PLAN BASE en un grupo
    fijo de tres sitios que puede ser ejecutado por cualquiera
    de las cuadrillas realmente compatibles.

    REGLA FUNDAMENTAL
    ==========================================================

    Protegemos:

        LOS TRES SITIOS JUNTOS.

    NO protegemos necesariamente:

        LA CUADRILLA ELEGIDA ORIGINALMENTE.

    Ejemplo:

        PLAN BASE:

            05_A + 05_B + 05_C -> C3

        Si C1 también puede ejecutar los tres:

            variante 1 -> C1
            variante 2 -> C3

        El optimizador semanal decidirá posteriormente cuál
        conviene utilizar considerando TODOS los cupos del
        resto de la semana.

    Esto permite liberar capacidad rural de C1/C3 cuando una
    terna urbana puede ser absorbida por C2, o redistribuir
    correctamente las ternas entre las cuadrillas mixtas.

    No cambia:

        - los tres sitios de la terna;
        - las reglas urbano/rural;
        - capacidad diaria;
        - base operacional;
        - cálculo real de ruta;
        - viabilidad.

    Sí recalcula:

        - viaje;
        - duración;
        - jornada extendida;
        - distancia;
        - cuadrilla.

    Devuelve:

        [
            {
                "grupo_id": ...,
                "salida_base": ...,
                "variantes": [...]
            },
            ...
        ]
    """

    resultado = []

    configuraciones_mapa = _configuraciones_por_cuadrilla(
        configuraciones,
    )

    for indice, salida_base in enumerate(
        salidas_ternas,
        start=1,
    ):

        sitios = list(
            salida_base.get(
                "sitios",
                [],
            )
            or []
        )

        # ====================================================
        # PROTECCIÓN
        # ====================================================

        if len(sitios) != 3:
            continue

        ids_grupo = frozenset(
            _id_sitio(
                sitio,
            )
            for sitio in sitios
        )

        variantes = []

        # ====================================================
        # PROBAR TODAS LAS CUADRILLAS ACTIVAS
        # ====================================================

        for configuracion in configuraciones:

            if not configuracion.get(
                "activa",
                False,
            ):
                continue

            if not configuracion.get(
                "tiene_base",
                False,
            ):
                continue

            if (
                _maximo_sitios_configuracion(
                    configuracion,
                )
                < 3
            ):
                continue

            # =================================================
            # COMPATIBILIDAD DE LOS TRES SITIOS
            # =================================================

            compatibles = all(
                cuadrilla_puede_ejecutar_sitio(
                    configuracion,
                    sitio,
                )
                for sitio in sitios
            )

            if not compatibles:
                continue

            # =================================================
            # RECALCULAR RUTA REAL CON ESTA CUADRILLA
            # =================================================

            calculo = encontrar_mejor_salida(
                sitios=sitios,
                configuracion_cuadrilla=(configuracion),
            )

            if not calculo:
                continue

            if not calculo.get(
                "viable",
                False,
            ):
                continue

            salida = dict(
                calculo,
            )

            cuadrilla = configuracion["cuadrilla"]

            salida["cluster_id"] = salida_base.get(
                "cluster_id",
                f"terna_protegida_{indice}",
            )

            salida["cluster_score"] = salida_base.get(
                "cluster_score",
                0.0,
            )

            salida["sitio_ids_set"] = ids_grupo

            # =================================================
            # SCORE DIAGNÓSTICO
            # =================================================
            #
            # Aquí todos tienen exactamente 3 sitios.
            #
            # Lo importante para la optimización definitiva
            # será la calidad DEL PLAN COMPLETO.
            #
            # Este score se conserva además para el posterior
            # ordenamiento del calendario.
            # =================================================

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

            score = 70.0 + score_viaje * 0.30

            score -= _penalizacion_jornada_extendida(
                salida,
            )

            salida["score_salida"] = round(
                max(
                    min(
                        score,
                        100.0,
                    ),
                    0.0,
                ),
                2,
            )

            # =================================================
            # METADATOS OPERACIONALES
            # =================================================

            salida["score_remanente"] = float(
                salida_base.get(
                    "score_remanente",
                    100.0,
                )
                or 100.0
            )

            salida["score_seleccion"] = float(
                salida_base.get(
                    "score_seleccion",
                    salida["score_salida"],
                )
                or salida["score_salida"]
            )

            salida["nivel_empaquetamiento"] = 3

            salida["objetivo_diario_completo"] = True

            salida["salida_incompleta_recalculable"] = False

            salida["salida_individual_excepcional"] = False

            salida["terna_protegida"] = True

            salida["grupo_terna_protegida"] = indice

            salida["cuadrilla_original_terna"] = salida_base.get(
                "cuadrilla",
            )

            salida["cuadrilla_reasignada_terna"] = cuadrilla != salida_base.get(
                "cuadrilla",
            )

            salida["prioridad_especializacion"] = _prioridad_especializacion_salida(
                salida=salida,
                configuraciones_por_cuadrilla=(configuraciones_mapa),
            )

            variantes.append(
                salida,
            )

        # ====================================================
        # FALLBACK ABSOLUTO
        # ====================================================
        #
        # En teoría la cuadrilla original debería aparecer
        # siempre porque ya ejecutó correctamente el cálculo
        # del plan base.
        #
        # Si por cualquier motivo no encontramos variantes,
        # conservamos la salida original para no destruir el
        # comportamiento existente.
        # ====================================================

        if not variantes:

            fallback = dict(
                salida_base,
            )

            fallback["sitio_ids_set"] = ids_grupo

            fallback["terna_protegida"] = True

            fallback["grupo_terna_protegida"] = indice

            fallback["cuadrilla_original_terna"] = fallback.get(
                "cuadrilla",
            )

            fallback["cuadrilla_reasignada_terna"] = False

            variantes = [
                fallback,
            ]

        # ====================================================
        # ORDEN ESTABLE
        # ====================================================

        variantes.sort(
            key=lambda salida: (
                salida.get(
                    "jornada_extendida",
                    False,
                ),
                int(
                    salida.get(
                        "minutos_total",
                        0,
                    )
                    or 0
                ),
                int(
                    salida.get(
                        "minutos_viaje",
                        0,
                    )
                    or 0
                ),
                -int(
                    salida.get(
                        "prioridad_especializacion",
                        0,
                    )
                    or 0
                ),
                str(
                    salida.get(
                        "cuadrilla",
                        "",
                    )
                ),
            )
        )

        resultado.append(
            {
                "grupo_id": indice,
                "salida_base": salida_base,
                "variantes": variantes,
            }
        )

    return resultado


# ============================================================
# OPTIMIZAR TERNAS PROTEGIDAS + RESIDUAL SEMANAL
# ============================================================


def _optimizar_ternas_protegidas_y_residual(
    *,
    plan_base,
    salidas_ternas_base,
    sitios_residuales,
    configuraciones,
    cupos,
    cluster_por_sitio_id,
):
    """
    Optimiza conjuntamente:

        A) qué cuadrilla ejecuta cada terna protegida;

        B) cómo empaquetamos todos los sitios residuales.

    ARQUITECTURA
    ==========================================================

    Las ternas del plan base continúan siendo grupos
    inseparables.

    Lo que eliminamos es la asignación rígida:

        terna X -> C3 para siempre.

    Para cada terna construimos todas sus variantes compatibles.

    Ejemplo:

        terna A:
            C1
            C3

        terna B:
            C1
            C2
            C3

        terna C:
            C1
            C3

    Luego recorremos las combinaciones posibles respetando
    los cupos semanales.

    Para cada distribución calculamos:

        cupos restantes por cuadrilla

    y ejecutamos sobre esos cupos el optimizador residual
    global existente.

    IMPORTANTE
    ==========================================================

    El resultado final se evalúa utilizando:

        _clave_calidad_plan()

    Por tanto la prioridad absoluta sigue siendo:

        1. MÁS SITIOS;
        2. más ternas;
        3. menos unitarios;
        4. menos extendidas;
        5. menos salidas;
        6. menos viaje;
        7. menor duración.

    CACHE
    ==========================================================

    Muchas asignaciones distintas de ternas producen los
    mismos cupos residuales.

    Ejemplo:

        C1=3, C2=2, C3=3

    No resolvemos el residual repetidamente.

    Guardamos la solución por estado de cupos.

    Esto mantiene el proceso razonable incluso con varias
    ternas protegidas.
    """

    # ========================================================
    # VARIANTES DE LAS TERNAS
    # ========================================================

    grupos_ternas = _generar_variantes_ternas_protegidas(
        salidas_ternas=(salidas_ternas_base),
        configuraciones=(configuraciones),
    )

    if salidas_ternas_base and not grupos_ternas:
        return None

    cuadrillas = sorted(
        cupos.keys(),
    )

    # ========================================================
    # GENERAR CANDIDATOS RESIDUALES UNA SOLA VEZ
    # ========================================================
    #
    # Utilizamos los cupos TOTALES únicamente para permitir
    # que aparezca cualquier cuadrilla potencial.
    #
    # La cantidad realmente disponible se validará después
    # dentro del optimizador residual.
    # ========================================================

    candidatos_residuales = []

    if sitios_residuales:

        candidatos_residuales = _generar_candidatos_residuales_globales(
            sitios=sitios_residuales,
            configuraciones=(configuraciones),
            cupos_residuales=cupos,
            cluster_por_sitio_id=(cluster_por_sitio_id),
        )

    # ========================================================
    # CACHE DE OPTIMIZACIÓN RESIDUAL
    # ========================================================

    cache_residual = {}

    def resolver_residual(
        cupos_restantes,
    ):
        clave_cupos = tuple(
            int(
                cupos_restantes.get(
                    cuadrilla,
                    0,
                )
                or 0
            )
            for cuadrilla in cuadrillas
        )

        if clave_cupos in cache_residual:

            return cache_residual[clave_cupos]

        if (
            not sitios_residuales
            or not candidatos_residuales
            or sum(
                clave_cupos,
            )
            <= 0
        ):

            solucion = []

        else:

            solucion = _optimizar_residuales_globales(
                sitios=(sitios_residuales),
                candidatos=(candidatos_residuales),
                cupos_residuales={
                    cuadrilla: (
                        cupos_restantes.get(
                            cuadrilla,
                            0,
                        )
                    )
                    for cuadrilla in cuadrillas
                },
            )

        cache_residual[clave_cupos] = solucion

        return solucion

    # ========================================================
    # MEJOR RESULTADO
    # ========================================================

    mejor_plan = None

    mejor_clave = None

    mejor_salidas_ternas = None

    mejor_cupos_restantes = None

    # ========================================================
    # BÚSQUEDA RECURSIVA DE ASIGNACIÓN DE TERNAS
    # ========================================================

    def explorar(
        indice_grupo,
        salidas_elegidas,
        uso_cuadrillas,
    ):
        nonlocal mejor_plan
        nonlocal mejor_clave
        nonlocal mejor_salidas_ternas
        nonlocal mejor_cupos_restantes

        # ====================================================
        # YA ASIGNAMOS TODAS LAS TERNAS
        # ====================================================

        if indice_grupo >= len(
            grupos_ternas,
        ):

            cupos_restantes = {}

            for cuadrilla in cuadrillas:

                cupos_restantes[cuadrilla] = max(
                    int(
                        cupos.get(
                            cuadrilla,
                            0,
                        )
                        or 0
                    )
                    - int(
                        uso_cuadrillas.get(
                            cuadrilla,
                            0,
                        )
                        or 0
                    ),
                    0,
                )

            salidas_residuales = resolver_residual(
                cupos_restantes,
            )

            salidas_finales = list(salidas_elegidas) + list(salidas_residuales)

            plan_candidato = _reconstruir_metricas_plan(
                plan_base=plan_base,
                salidas=salidas_finales,
                cupos=cupos,
            )

            clave = _clave_calidad_plan(
                plan_candidato,
            )

            if mejor_plan is None or clave > mejor_clave:

                mejor_plan = plan_candidato

                mejor_clave = clave

                mejor_salidas_ternas = list(salidas_elegidas)

                mejor_cupos_restantes = dict(cupos_restantes)

            return

        # ====================================================
        # GRUPO ACTUAL
        # ====================================================

        grupo = grupos_ternas[indice_grupo]

        variantes = grupo["variantes"]

        # ====================================================
        # PROBAR CADA CUADRILLA COMPATIBLE
        # ====================================================

        for variante in variantes:

            cuadrilla = variante.get(
                "cuadrilla",
            )

            if not cuadrilla:
                continue

            usadas = int(
                uso_cuadrillas.get(
                    cuadrilla,
                    0,
                )
                or 0
            )

            cupo = int(
                cupos.get(
                    cuadrilla,
                    0,
                )
                or 0
            )

            if usadas >= cupo:
                continue

            nuevo_uso = dict(
                uso_cuadrillas,
            )

            nuevo_uso[cuadrilla] = usadas + 1

            explorar(
                indice_grupo + 1,
                (
                    salidas_elegidas
                    + [
                        variante,
                    ]
                ),
                nuevo_uso,
            )

    # ========================================================
    # EJECUTAR BÚSQUEDA
    # ========================================================

    explorar(
        0,
        [],
        {cuadrilla: 0 for cuadrilla in cuadrillas},
    )

    if mejor_plan is None:
        return None

    # ========================================================
    # DIAGNÓSTICO DE ASIGNACIONES
    # ========================================================

    asignacion_original = []

    for salida in salidas_ternas_base:

        asignacion_original.append(
            {
                "sitio_ids": sorted(
                    str(sitio_id)
                    for sitio_id in salida.get(
                        "sitio_ids_set",
                        [],
                    )
                ),
                "cuadrilla": (salida.get("cuadrilla")),
            }
        )

    asignacion_final = []

    for salida in mejor_salidas_ternas or []:

        asignacion_final.append(
            {
                "sitio_ids": sorted(
                    str(sitio_id)
                    for sitio_id in salida.get(
                        "sitio_ids_set",
                        [],
                    )
                ),
                "cuadrilla": (salida.get("cuadrilla")),
                "cuadrilla_original": (salida.get("cuadrilla_original_terna")),
                "reasignada": bool(
                    salida.get(
                        "cuadrilla_reasignada_terna",
                        False,
                    )
                ),
            }
        )

    hubo_reasignacion = any(
        item.get(
            "reasignada",
            False,
        )
        for item in asignacion_final
    )

    mejor_plan["reasignacion_ternas_protegidas_aplicada"] = hubo_reasignacion

    mejor_plan["asignacion_ternas_protegidas_base"] = asignacion_original

    mejor_plan["asignacion_ternas_protegidas_final"] = asignacion_final

    mejor_plan["cupos_residuales_por_cuadrilla"] = mejor_cupos_restantes or {}

    mejor_plan["estados_residuales_evaluados"] = len(
        cache_residual,
    )

    return mejor_plan

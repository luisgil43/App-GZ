from django.db import transaction

from planificacion.models import SitioBatchSemanal, SitioPlanificado
from planificacion.services.motor_batch_semanal import (
    construir_universo_batch, generar_propuestas)
from planificacion.services.motor_batch_semanal.perimetros import convex_hull

# ============================================================
# VERSIÓN DEL ANÁLISIS
# ============================================================

ANALISIS_BATCH_VERSION = 2


# ============================================================
# CLUSTERS POR SITIO
# ============================================================


def _mapa_clusters_por_sitio(
    propuesta_serializada,
):
    """
    Construye:

        sitio_planificado_id -> cluster_codigo

    utilizando la propuesta ya serializada.

    Se conserva porque SitioBatchSemanal todavía almacena
    cluster_codigo y lo utilizamos para trazabilidad y mapas.
    """

    resultado = {}

    for cluster in propuesta_serializada.get(
        "clusters",
        [],
    ):
        codigo = cluster.get(
            "id_cluster",
            "",
        )

        for sitio in cluster.get(
            "sitios",
            [],
        ):
            sitio_id = sitio.get(
                "sitio_planificado_id",
            )

            if sitio_id:
                resultado[sitio_id] = codigo

    return resultado


# ============================================================
# ANÁLISIS PRINCIPAL
# ============================================================


def analizar_batch_semanal(
    *,
    batch,
    cantidad_reserva=None,
):
    """
    Ejecuta el análisis semanal completo.

    IMPORTANTE
    ==========================================================

    Ya NO generamos reservas automáticas.

    El objetivo indicado por planificación corresponde
    directamente a la cantidad de sitios PRINCIPALES que
    queremos presentar para esa semana.

    Ejemplo:

        objetivo = 40

    significa:

        intentar generar 40 sitios principales

    y NO:

        40 principales + 5 reservas.

    Si planificación quiere entregar 45 sitios al cliente,
    debe definir objetivo = 45.

    ==========================================================
    EL MOTOR CONSIDERA
    ==========================================================

    - concentración territorial;
    - agrupaciones naturales;
    - distancia entre sitios;
    - ubicación de las bases;
    - capacidad urbano/rural;
    - jornada;
    - tiempo de trabajo;
    - viajes de ida;
    - traslados;
    - regreso a base;
    - días disponibles;
    - accesos;
    - composición restante del mes;
    - riesgo territorial del remanente.

    La lógica geográfica del motor debe intentar evitar que
    después de seleccionar las semanas iniciales queden sitios
    lejanos completamente aislados.

    Si inevitablemente debe quedar un sitio suelto para una
    semana posterior, debe favorecerse que sea uno más cercano
    a Santiago antes que un sitio lejano que implique una
    jornada exclusiva de alto costo.

    Esta función NO modifica base de datos.
    """

    # ========================================================
    # UNIVERSO
    # ========================================================

    universo = construir_universo_batch(
        batch,
    )

    universo_total = len(
        universo,
    )

    # ========================================================
    # SITIOS FIJOS DEL BATCH ACTUAL
    # ========================================================
    #
    # Una decisión manual existente debe ser respetada por
    # los análisis posteriores de ESTA misma semana.
    #
    # Son fijos:
    #
    # - sitios confirmados;
    # - sitios agregados manualmente que continúan activos.
    #
    # Los excluidos/reemplazados no pertenecen al universo
    # de este batch, pero continúan disponibles para otras
    # semanas si no están comprometidos en ellas.
    # ========================================================

    ids_fijos = set(
        SitioBatchSemanal.objects.filter(
            batch=batch,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .filter(
            agregado_manualmente=True,
        )
        .values_list(
            "sitio_planificado_id",
            flat=True,
        )
    )

    ids_fijos.update(
        SitioBatchSemanal.objects.filter(
            batch=batch,
            estado="confirmado",
        ).values_list(
            "sitio_planificado_id",
            flat=True,
        )
    )

    ids_universo = {
        sitio.sitio_planificado_id
        for sitio in universo
    }

    ids_fijos.intersection_update(
        ids_universo,
    )

    if not universo:
        return {
            "version": ANALISIS_BATCH_VERSION,
            "universo": [],
            "propuestas": [],
            "cantidad_reserva": 0,
            "advertencias": [
                "No existen sitios disponibles para " "analizar dentro de este batch."
            ],
        }

    # ========================================================
    # RESERVAS
    # ========================================================
    #
    # Se conserva la clave cantidad_reserva únicamente por
    # compatibilidad con templates/session antiguos.
    #
    # Funcionalmente siempre será cero.
    # ========================================================

    cantidad_reserva = 0

    # ========================================================
    # DISPONIBILIDADES REALES DE LA SEMANA
    # ========================================================

    disponibilidades = []

    advertencias = []

    if batch.configuracion_semana_id:

        disponibilidades = list(
            batch.configuracion_semana.disponibilidades_cuadrillas.select_related(
                "cuadrilla_operativa",
            )
            .filter(
                activa=True,
            )
            .order_by(
                "cuadrilla_operativa__orden",
                "cuadrilla_operativa__nombre",
                "cuadrilla",
                "id",
            )
        )

    # ========================================================
    # VALIDACIÓN DE CUADRILLAS
    # ========================================================

    if not disponibilidades:

        advertencias.append(
            ("No existen cuadrillas activas " "configuradas para esta semana.")
        )

    else:

        sin_base = [
            disponibilidad
            for disponibilidad in disponibilidades
            if not disponibilidad.tiene_base_operacional
        ]

        for disponibilidad in sin_base:

            advertencias.append(
                (
                    f"{disponibilidad.nombre_cuadrilla} "
                    "no posee base operacional configurada."
                )
            )

    # ========================================================
    # OBJETIVO REAL
    # ========================================================

    try:
        objetivo = int(
            batch.objetivo_sitios,
        )

    except (
        TypeError,
        ValueError,
    ):
        objetivo = 0

    if objetivo <= 0:

        advertencias.append(("El objetivo semanal debe ser " "mayor que cero."))

        return {
            "version": ANALISIS_BATCH_VERSION,
            "universo": universo,
            "propuestas": [],
            "cantidad_reserva": 0,
            "advertencias": advertencias,
        }

    # Protección adicional.
    #
    # Normalmente la vista de creación ya impide solicitar más
    # sitios que los disponibles, pero el servicio también se
    # protege por si existen batches históricos.

    # Los sitios fijados manualmente/confirmados son decisiones
    # ya tomadas dentro del batch y nunca deben ser descartados
    # porque el objetivo haya sido reducido posteriormente.
    objetivo_motor = min(
        max(
            objetivo,
            len(ids_fijos),
        ),
        universo_total,
    )

    if objetivo > universo_total:

        advertencias.append(
            (
                f"El batch tiene objetivo {objetivo}, "
                f"pero actualmente solamente existen "
                f"{universo_total} sitio(s) disponibles. "
                f"El análisis utilizará {objetivo_motor}."
            )
        )

    # ========================================================
    # PROPUESTAS
    # ========================================================

    propuestas = generar_propuestas(
        universo=universo,
        objetivo=objetivo_motor,
        cantidad_reserva=0,
        disponibilidades=disponibilidades,
        cantidad_propuestas=3,
        ids_fijos=ids_fijos,
    )

    if disponibilidades and not propuestas:

        advertencias.append(
            (
                "No fue posible construir una propuesta "
                "con la configuración territorial y "
                "operacional actual."
            )
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "version": ANALISIS_BATCH_VERSION,
        "universo": universo,
        "propuestas": propuestas,
        "cantidad_reserva": 0,
        "advertencias": advertencias,
    }


# ============================================================
# SERIALIZAR SITIO
# ============================================================


def serializar_sitio_motor(
    sitio,
):
    return {
        "sitio_planificado_id": (sitio.sitio_planificado_id),
        "sitio_id": (sitio.sitio_id),
        "id_claro": (sitio.id_claro),
        "nombre": (sitio.nombre),
        "comuna": (sitio.comuna),
        "tipo_zona": (sitio.tipo_zona),
        "latitud": (sitio.latitud),
        "longitud": (sitio.longitud),
        "condicion_acceso": (sitio.condicion_acceso),
        "estado_permiso": (sitio.estado_permiso),
        "prioridad": (sitio.prioridad),
        "urbano": (sitio.urbano),
        "rural": (sitio.rural),
    }


# ============================================================
# CLUSTERS RELEVANTES
# ============================================================


def _clusters_relevantes_propuesta(
    propuesta,
):
    """
    Serializa los clusters que realmente contienen sitios
    de la propuesta.

    Aunque ya no existen reservas automáticas, conservamos
    compatibilidad con la estructura PropuestaBatchMotor.
    """

    ids_propuesta = {sitio.sitio_planificado_id for sitio in propuesta.principales}

    resultado = []

    for cluster in propuesta.clusters:

        sitios_cluster = [
            sitio
            for sitio in cluster.sitios
            if (sitio.sitio_planificado_id in ids_propuesta)
        ]

        if not sitios_cluster:
            continue

        resultado.append(
            {
                "id_cluster": (cluster.id_cluster),
                "cantidad": (len(sitios_cluster)),
                "centro_latitud": (cluster.centro_latitud),
                "centro_longitud": (cluster.centro_longitud),
                "radio_km": (cluster.radio_km),
                "distancia_media_km": (cluster.distancia_media_km),
                "distancia_maxima_km": (cluster.distancia_maxima_km),
                "urbanos": sum(1 for sitio in sitios_cluster if sitio.urbano),
                "rurales": sum(1 for sitio in sitios_cluster if sitio.rural),
                "score_compactacion": (cluster.score_compactacion),
                "perimetro": (
                    convex_hull(
                        sitios_cluster,
                    )
                ),
                "sitios": [
                    serializar_sitio_motor(
                        sitio,
                    )
                    for sitio in sitios_cluster
                ],
            }
        )

    return resultado


# ============================================================
# SERIALIZAR PROPUESTA
# ============================================================


def serializar_propuesta(
    propuesta,
    posicion,
):
    principales = [
        serializar_sitio_motor(
            sitio,
        )
        for sitio in propuesta.principales
    ]

    # --------------------------------------------------------
    # RESERVAS
    # --------------------------------------------------------
    #
    # Conservamos la estructura para no romper templates
    # antiguos, pero siempre queda vacía.
    # --------------------------------------------------------

    reservas = []

    return {
        "posicion": posicion,
        "codigo": (propuesta.codigo),
        "recomendada": (posicion == 1),
        "principales": (principales),
        "reservas": (reservas),
        "principal_ids": [sitio["sitio_planificado_id"] for sitio in principales],
        "reserva_ids": [],
        "clusters": (
            _clusters_relevantes_propuesta(
                propuesta,
            )
        ),
        "score_geografico": (propuesta.score_geografico),
        "score_capacidad": (propuesta.score_capacidad),
        "score_acceso": (propuesta.score_acceso),
        "score_balance_mensual": (propuesta.score_balance_mensual),
        "score_respaldo": 0.0,
        "score_total": (propuesta.score_total),
        "motivo": (propuesta.motivo),
        "metricas": (propuesta.metricas),
    }


# ============================================================
# RESULTADO SERIALIZABLE
# ============================================================


def construir_resultado_serializable(
    *,
    batch,
    cantidad_reserva=None,
):
    """
    Construye una estructura apta para guardar en session.

    cantidad_reserva se mantiene en la firma por compatibilidad
    con llamadas existentes, pero el nuevo motor no genera
    reservas automáticas.
    """

    resultado = analizar_batch_semanal(
        batch=batch,
        cantidad_reserva=0,
    )

    propuestas = []

    for posicion, propuesta in enumerate(
        resultado["propuestas"],
        start=1,
    ):

        propuestas.append(
            serializar_propuesta(
                propuesta,
                posicion,
            )
        )

    return {
        "version": ANALISIS_BATCH_VERSION,
        "batch_id": (batch.pk),
        "objetivo": (batch.objetivo_sitios),
        "universo_total": (len(resultado["universo"])),
        "cantidad_reserva": 0,
        "advertencias": (
            resultado.get(
                "advertencias",
                [],
            )
        ),
        "propuestas": (propuestas),
    }


# ============================================================
# APLICAR PROPUESTA
# ============================================================


@transaction.atomic
def aplicar_propuesta_batch(
    *,
    batch,
    propuesta_serializada,
    usuario,
):
    """
    Aplica una propuesta automática a una semana operacional
    global y multimes.

    Los sitios pueden pertenecer a cualquiera de las
    PlanificacionMensual vinculadas mediante
    planificaciones_origen.

    La validación contra otros batches se realiza además por
    SitioMovil físico para impedir que una instancia mensual
    diferente duplique el mismo sitio.
    """

    # ========================================================
    # BLOQUEAR BATCH
    # ========================================================

    batch = (
        batch.__class__.objects.select_for_update()
        .prefetch_related(
            "planificaciones_origen",
        )
        .get(
            pk=batch.pk,
        )
    )

    # ========================================================
    # SOLO BORRADOR
    # ========================================================

    if batch.estado != "borrador":

        raise ValueError(
            "Solo se puede aplicar una propuesta automática "
            "mientras el batch se encuentre en borrador."
        )

    # ========================================================
    # PRINCIPALES
    # ========================================================

    principales = propuesta_serializada.get(
        "principal_ids",
        [],
    )

    if not principales:

        raise ValueError("La propuesta seleccionada no contiene " "sitios principales.")

    principales = list(dict.fromkeys(principales))

    todos_ids = set(principales)

    # ========================================================
    # PLANIFICACIONES VÁLIDAS
    # ========================================================

    planificacion_ids = set(
        batch.planificaciones_origen.values_list(
            "id",
            flat=True,
        )
    )

    if batch.planificacion_id:
        planificacion_ids.add(batch.planificacion_id)

    if not planificacion_ids:

        raise ValueError(
            "El batch no posee ninguna planificación mensual " "de origen vinculada."
        )

    # ========================================================
    # VALIDAR SITIOS
    # ========================================================

    sitios = {
        sitio.id: sitio
        for sitio in (
            SitioPlanificado.objects.filter(
                id__in=todos_ids,
                planificacion_id__in=planificacion_ids,
                activo_en_mes=True,
            ).select_related(
                "sitio",
                "planificacion",
            )
        )
    }

    if len(sitios) != len(todos_ids):

        raise ValueError(
            "Uno o más sitios de la propuesta ya no se "
            "encuentran disponibles dentro de los meses "
            "vinculados a esta semana."
        )

    # ========================================================
    # EVITAR DUPLICADOS FÍSICOS DENTRO DE LA PROPUESTA
    # ========================================================

    sitios_fisicos = {sitio.sitio_id for sitio in sitios.values()}

    if len(sitios_fisicos) != len(sitios):

        raise ValueError(
            "La propuesta contiene más de una representación "
            "mensual del mismo sitio físico. "
            "Recalcula la propuesta."
        )

    # ========================================================
    # VALIDAR OTROS BATCHES POR SITIO FÍSICO
    # ========================================================

    estados_comprometidos = [
        "candidato",
        "seleccionado",
        "gestion_permiso",
        "disponible",
        "confirmado",
    ]

    comprometidos_otros = set(
        SitioBatchSemanal.objects.filter(
            sitio_planificado__sitio_id__in=(sitios_fisicos),
            estado__in=estados_comprometidos,
        )
        .exclude(
            batch=batch,
        )
        .values_list(
            "sitio_planificado__sitio_id",
            flat=True,
        )
    )

    if comprometidos_otros:

        raise ValueError(
            "Uno o más sitios físicos fueron utilizados por "
            "otro batch después de ejecutar el análisis. "
            "Recalcula la propuesta antes de continuar."
        )

    # ========================================================
    # CLUSTERS
    # ========================================================

    clusters_por_sitio = _mapa_clusters_por_sitio(
        propuesta_serializada,
    )

    # ========================================================
    # SINCRONIZAR BORRADOR ACTUAL
    # ========================================================
    #
    # Una nueva propuesta NO debe borrar indiscriminadamente
    # la historia del batch.
    #
    # Conservamos:
    #
    # - excluidos/reemplazados, como decisión de ESTA semana;
    # - sitios agregados manualmente;
    # - sitios confirmados.
    #
    # Los elementos automáticos anteriores que ya no formen
    # parte de la nueva propuesta sí pueden desaparecer.
    # ========================================================

    ids_principales = set(principales)

    items_actuales = {
        item.sitio_planificado_id: item
        for item in (
            SitioBatchSemanal.objects.select_for_update()
            .filter(
                batch=batch,
            )
        )
    }

    # ========================================================
    # RETIRAR ÚNICAMENTE AUTOMÁTICOS OBSOLETOS
    # ========================================================

    ids_automaticos_obsoletos = [
        item.pk
        for item in items_actuales.values()
        if (
            item.sitio_planificado_id not in ids_principales
            and item.estado not in {
                "excluido",
                "reemplazado",
            }
            and not item.agregado_manualmente
            and item.estado != "confirmado"
        )
    ]

    if ids_automaticos_obsoletos:

        SitioBatchSemanal.objects.filter(
            pk__in=ids_automaticos_obsoletos,
        ).delete()

        items_actuales = {
            sitio_id: item
            for sitio_id, item in items_actuales.items()
            if item.pk not in ids_automaticos_obsoletos
        }

    score_total = propuesta_serializada.get(
        "score_total",
        0,
    )

    codigo = propuesta_serializada.get(
        "codigo",
        "PROP",
    )

    motivo_general = propuesta_serializada.get(
        "motivo",
        "",
    )

    motivo_motor = (
        f"{codigo}. {motivo_general}"
    ).strip()

    creados_principales = 0

    # ========================================================
    # SINCRONIZAR PRINCIPALES
    # ========================================================

    for sitio_id in principales:

        sitio_planificado = sitios[sitio_id]

        item_existente = items_actuales.get(
            sitio_id,
        )

        # ====================================================
        # YA EXISTE EN EL BATCH
        # ====================================================

        if item_existente is not None:

            # Un excluido/reemplazado nunca debería aparecer
            # nuevamente en la propuesta de ESTA semana.
            #
            # Si una propuesta antigua guardada en sesión lo
            # contiene, rechazamos su aplicación en lugar de
            # reactivarlo silenciosamente.

            if item_existente.estado in {
                "excluido",
                "reemplazado",
            }:

                raise ValueError(
                    "La propuesta contiene un sitio que fue "
                    "excluido o reemplazado manualmente en "
                    "esta semana. Recalcula el análisis antes "
                    "de aplicar la propuesta."
                )

            # Los confirmados conservan su estado.
            #
            # Los agregados manualmente conservan además su
            # origen y trazabilidad manual.

            if item_existente.estado != "confirmado":
                item_existente.estado = "seleccionado"

            if not item_existente.agregado_manualmente:
                item_existente.origen = "motor"

            item_existente.puntaje_motor = score_total

            item_existente.motivo_recomendacion = motivo_motor

            item_existente.es_reserva = False

            item_existente.cluster_codigo = (
                clusters_por_sitio.get(
                    sitio_id,
                    "",
                )
            )

            if item_existente.agregado_por_id is None:
                item_existente.agregado_por = usuario

            item_existente.save(
                update_fields=[
                    "estado",
                    "origen",
                    "puntaje_motor",
                    "motivo_recomendacion",
                    "es_reserva",
                    "cluster_codigo",
                    "agregado_por",
                    "actualizado_en",
                ]
            )

            creados_principales += 1

            continue

        # ====================================================
        # NUEVO SITIO DEL MOTOR
        # ====================================================

        item_nuevo = SitioBatchSemanal.objects.create(
            batch=batch,
            sitio_planificado=sitio_planificado,
            estado="seleccionado",
            origen="motor",
            puntaje_motor=score_total,
            motivo_recomendacion=motivo_motor,
            agregado_manualmente=False,
            bloqueado_en_batch=False,
            es_reserva=False,
            cluster_codigo=(
                clusters_por_sitio.get(
                    sitio_id,
                    "",
                )
            ),
            agregado_por=usuario,
        )

        items_actuales[sitio_id] = item_nuevo

        creados_principales += 1

    # ========================================================
    # BATCH
    # ========================================================

    batch.generado_por_motor = True

    batch.actualizado_por = usuario

    batch.save(
        update_fields=[
            "generado_por_motor",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return {
        "principales": creados_principales,
        "reservas": 0,
    }

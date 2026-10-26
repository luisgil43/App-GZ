"""
Traslado manual de sitios entre planificaciones mensuales.

No modifica el servicio existente de traslado al mes siguiente.
No elimina participaciones semanales, servicios ni evidencias.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from operaciones.models import ServicioCotizado
from planificacion.models import (
    PlanificacionMensual,
    SitioBatchSemanal,
    SitioPlanificado,
)


ESTADOS_SITIO_PROTEGIDOS = {
    "planificado",
    "en_ruta",
    "en_ejecucion",
    "completado",
    "cancelado",
}

ESTADOS_SEMANA_INACTIVOS = {
    "excluido",
    "reemplazado",
}



MESES_PRODUCCION = (
    "",
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)


def _validar_servicio_pendiente(servicio):
    """
    Permite mover servicios pendientes sin compromiso operacional.

    Las referencias administrativas y las notificaciones no
    constituyen, por sí solas, ejecución del servicio.
    """
    if servicio.estado != "aprobado_pendiente":
        raise ValidationError(
            f"El servicio {servicio.pk} tiene estado "
            f"{servicio.estado} y no puede trasladarse."
        )

    campos_ejecucion = (
        "tecnico_aceptado_id",
        "tecnico_finalizo_id",
        "supervisor_aprobo_id",
        "supervisor_rechazo_id",
        "fecha_aprobacion_supervisor",
    )

    if any(
        getattr(servicio, campo, None) is not None
        for campo in campos_ejecucion
    ):
        raise ValidationError(
            f"El servicio {servicio.pk} tiene intervención "
            "operacional registrada."
        )

    if servicio.trabajadores_asignados.exists():
        raise ValidationError(
            f"El servicio {servicio.pk} tiene trabajadores asignados."
        )

    campos_documentos = (
        "reporte_fotografico",
        "acta_aceptacion_pdf",
        "documento_compra",
    )

    if any(
        bool(getattr(servicio, campo, None))
        for campo in campos_documentos
    ):
        raise ValidationError(
            f"El servicio {servicio.pk} tiene documentos "
            "o evidencias asociados."
        )

    # Una sesión fotográfica vacía puede existir desde la
    # preparación administrativa del servicio. No constituye
    # por sí sola evidencia de ejecución.
    try:
        sesion = servicio.sesion_fotos
    except ServicioCotizado.sesion_fotos.RelatedObjectDoesNotExist:
        sesion = None

    if sesion is not None:
        if sesion.asignaciones.exists():
            raise ValidationError(
                f"El servicio {servicio.pk} tiene asignaciones "
                "en su sesión fotográfica."
            )

    relaciones_administrativas = {
        "notificaciones",
        "alertas_clima_bot",
        "sesion_fotos",
    }

    relaciones_protegidas = (
        relacion.get_accessor_name()
        for relacion in servicio._meta.related_objects
        if relacion.get_accessor_name()
        not in relaciones_administrativas
    )

    for nombre in relaciones_protegidas:
        try:
            relacionados = getattr(servicio, nombre)
        except AttributeError:
            continue

        if hasattr(relacionados, "exists"):
            if relacionados.exists():
                raise ValidationError(
                    f"El servicio {servicio.pk} tiene registros "
                    f"operacionales en {nombre}."
                )
        elif relacionados is not None:
            raise ValidationError(
                f"El servicio {servicio.pk} tiene una "
                f"relación operacional en {nombre}."
            )


def _validar_origen(sitio):
    if not sitio.activo_en_mes:
        raise ValidationError(
            "El sitio ya no está activo en el mes de origen."
        )

    if sitio.estado in ESTADOS_SITIO_PROTEGIDOS:
        raise ValidationError(
            "El sitio tiene un estado de planificación protegido."
        )

    if sitio.fecha_planificada is not None:
        raise ValidationError(
            "El sitio tiene una fecha planificada. "
            "Debe resolverse antes del traslado."
        )

    from planificacion.modelos import (
        SitioSalidaPlanificacionDiaria,
        PrioridadPlanificacionDiaria,
    )

    participaciones = list(
        SitioBatchSemanal.objects.select_for_update()
        .filter(sitio_planificado=sitio)
        .order_by("pk")
    )

    ids_participaciones = [
        participacion.pk
        for participacion in participaciones
    ]

    estados_diarios_historicos = {
        "retirado",
        "cancelado",
        "reprogramado",
    }

    salidas_activas = list(
        SitioSalidaPlanificacionDiaria.objects
        .select_for_update()
        .filter(sitio_batch_id__in=ids_participaciones)
        .exclude(estado__in=estados_diarios_historicos)
        .order_by("pk")
    )

    if salidas_activas:
        raise ValidationError(
            "El sitio tiene una participación diaria vigente. "
            "Debe resolverse antes del traslado."
        )

    prioridades_activas = list(
        PrioridadPlanificacionDiaria.objects
        .select_for_update()
        .filter(
            sitio_batch_id__in=ids_participaciones,
            estado="activa",
        )
        .order_by("pk")
    )

    if prioridades_activas:
        raise ValidationError(
            "El sitio tiene una prioridad diaria activa."
        )

    servicios = list(
        ServicioCotizado.objects.select_for_update()
        .filter(sitio_planificado=sitio)
        .order_by("pk")
    )

    for servicio in servicios:
        _validar_servicio_pendiente(servicio)

    return servicios, participaciones



def _estado_destino(permiso):
    if permiso in {"aprobado", "no_requiere"}:
        return "listo_planificar"

    if permiso == "sin_gestion":
        return "pendiente"

    return "gestionando_permiso"


@transaction.atomic
def trasladar_sitio_mes_manual(
    *,
    sitio_planificado_id,
    planificacion_origen_id,
    anio_destino,
    mes_destino,
    mantener_permiso,
    usuario,
):
    try:
        anio_destino = int(anio_destino)
        mes_destino = int(mes_destino)
    except (TypeError, ValueError):
        raise ValidationError(
            "Debes indicar un mes y año válidos."
        )

    if not 1 <= mes_destino <= 12:
        raise ValidationError(
            "El mes de destino debe estar entre 1 y 12."
        )

    if not 2000 <= anio_destino <= 2100:
        raise ValidationError(
            "El año de destino no es válido."
        )

    origen = (
        SitioPlanificado.objects
        .select_for_update()
        .select_related("sitio", "planificacion")
        .get(
            pk=sitio_planificado_id,
            planificacion_id=planificacion_origen_id,
        )
    )

    if (
        origen.planificacion.anio == anio_destino
        and origen.planificacion.mes == mes_destino
    ):
        raise ValidationError(
            "El mes de destino debe ser diferente al de origen."
        )

    if origen.planificacion.estado == "cerrada":
        raise ValidationError(
            "No se pueden trasladar sitios desde "
            "una planificación mensual cerrada."
        )

    servicios_pendientes, participaciones_semanales = _validar_origen(origen)

    destino_planificacion = (
        PlanificacionMensual.objects
        .select_for_update()
        .filter(
            anio=anio_destino,
            mes=mes_destino,
        )
        .first()
    )

    if destino_planificacion is None:
        raise ValidationError(
            "Primero debes crear la planificación mensual "
            f"{mes_destino:02d}/{anio_destino}."
        )

    if destino_planificacion.estado == "cerrada":
        raise ValidationError(
            "La planificación mensual de destino está cerrada."
        )

    existente = (
        SitioPlanificado.objects
        .select_for_update()
        .filter(
            planificacion=destino_planificacion,
            sitio=origen.sitio,
        )
        .first()
    )

    if existente is not None:
        raise ValidationError(
            "El sitio ya tiene un registro en el mes de destino. "
            "No se sobrescribirá para evitar duplicados "
            "o pérdida de información."
        )

    permiso_destino = (
        origen.estado_permiso
        if mantener_permiso
        else "sin_gestion"
    )

    nuevo = SitioPlanificado.objects.create(
        planificacion=destino_planificacion,
        sitio=origen.sitio,
        importacion_origen=None,
        activo_en_mes=True,
        fecha_planificada=None,
        orden_dia=0,
        estado=_estado_destino(permiso_destino),
        estado_permiso=permiso_destino,
        tipo_ruta="",
        prioridad="normal",
        fecha_minima_ejecucion=None,
        bloqueado_motor=False,
        planificado_manualmente=False,
        motivo_bloqueo="",
        observacion_planificacion="",
        alerta_motor="",
        requiere_contacto=origen.requiere_contacto,
        contacto_confirmado=origen.contacto_confirmado,
        fecha_contacto_confirmado=(
            origen.fecha_contacto_confirmado
        ),
        creado_por=usuario,
        actualizado_por=usuario,
    )

    mes_produccion_destino = (
        f"{MESES_PRODUCCION[mes_destino]} {anio_destino}"
    )

    for servicio in servicios_pendientes:
        servicio.sitio_planificado = nuevo
        servicio.mes_produccion = mes_produccion_destino
        servicio.save(
            update_fields=[
                "sitio_planificado",
                "mes_produccion",
            ]
        )

    for participacion in participaciones_semanales:
        if participacion.estado in ESTADOS_SEMANA_INACTIVOS:
            continue

        participacion.estado = "excluido"
        participacion.motivo_exclusion = (
            "Sitio trasladado manualmente a "
            f"{mes_destino:02d}/{anio_destino}. "
            "Se conserva el historial semanal."
        )
        participacion.save(
            update_fields=[
                "estado",
                "motivo_exclusion",
                "actualizado_en",
            ]
        )

    nota = (
        "Trasladado manualmente a "
        f"{mes_destino:02d}/{anio_destino}. "
        f"Nuevo registro: {nuevo.pk}."
    )

    anterior = (
        origen.observacion_planificacion or ""
    ).strip()

    origen.observacion_planificacion = (
        f"{anterior}\\n\\n{nota}"
        if anterior
        else nota
    )

    origen.activo_en_mes = False
    origen.estado = "reprogramado"
    origen.fecha_planificada = None
    origen.orden_dia = 0
    origen.bloqueado_motor = True
    origen.motivo_bloqueo = (
        f"Trasladado manualmente a "
        f"{mes_destino:02d}/{anio_destino}."
    )
    origen.actualizado_por = usuario

    origen.save(
        update_fields=[
            "activo_en_mes",
            "estado",
            "fecha_planificada",
            "orden_dia",
            "bloqueado_motor",
            "motivo_bloqueo",
            "observacion_planificacion",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return {
        "origen": origen,
        "destino": nuevo,
        "planificacion_destino": destino_planificacion,
    }

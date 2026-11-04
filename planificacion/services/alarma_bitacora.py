from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.apps import apps
from django.utils import timezone


ZONA_CHILE = ZoneInfo("America/Santiago")

ESTADOS_SITIO_INACTIVOS = {
    "retirado",
    "cancelado",
    "reprogramado",
    "no_ejecutado",
}

ESTADOS_SALIDA_INACTIVOS = {
    "cancelada",
    "reprogramada",
}

ESTADOS_BATCH_INACTIVOS = {
    "excluido",
    "reemplazado",
}


def vencimiento_alarma(fecha):
    """
    La alarma vence a las 00:01 de Chile
    del día siguiente a la salida programada.
    """
    if fecha is None:
        return None

    return datetime.combine(
        fecha + timedelta(days=1),
        time(0, 1),
        tzinfo=ZONA_CHILE,
    )


def _participacion_vigente(sitio_id):
    """
    Busca la participación diaria vigente del sitio.

    Excluye participaciones retiradas, canceladas,
    reprogramadas y batches reemplazados o excluidos.
    """
    Participacion = apps.get_model(
        "planificacion",
        "SitioSalidaPlanificacionDiaria",
    )

    return (
        Participacion.objects.filter(
            sitio_batch__sitio_planificado__sitio_id=sitio_id,
            sitio_batch__sitio_planificado__activo_en_mes=True,
        )
        .exclude(estado__in=ESTADOS_SITIO_INACTIVOS)
        .exclude(salida__estado__in=ESTADOS_SALIDA_INACTIVOS)
        .exclude(sitio_batch__estado__in=ESTADOS_BATCH_INACTIVOS)
        .select_related("salida")
        .order_by(
            "-actualizado_en",
            "-creado_en",
            "-pk",
        )
        .first()
    )


def fecha_salida_vigente(sitio_id):
    participacion = _participacion_vigente(sitio_id)

    if participacion is None:
        return None

    return participacion.salida.fecha


def _extension_valida(
    fecha_anterior,
    fecha_nueva,
    fecha_cambio,
):
    """
    Solo se permite extender el vencimiento cuando
    el cambio ocurrió antes de vencer la fecha anterior.

    Una modificación posterior no reactiva la alarma.
    """
    if (
        fecha_anterior is None
        or fecha_nueva is None
        or fecha_cambio is None
        or timezone.is_naive(fecha_cambio)
    ):
        return False

    if fecha_nueva <= fecha_anterior:
        return False

    limite = vencimiento_alarma(fecha_anterior)

    return (
        fecha_cambio.astimezone(ZONA_CHILE) < limite
    )


def estado_alarma_sitio(sitio, *, sincronizar=True):
    """
    Calcula el estado efectivo de la alarma.

    No activa alarmas apagadas.
    No altera programación, servicios ni permisos.
    """
    if not sitio.bitacora_alarma_activa:
        return {
            "activa": False,
            "fecha_salida": None,
            "vence_en": None,
        }

    ahora = timezone.now().astimezone(ZONA_CHILE)

    fecha_anterior = sitio.bitacora_alarma_fecha_salida

    participacion = _participacion_vigente(sitio.pk)

    fecha_actual = (
        participacion.salida.fecha
        if participacion is not None
        else None
    )

    fecha_cambio = None

    if participacion is not None:
        # Una nueva participación tiene fecha de creación
        # propia. En una participación existente utilizamos
        # la fecha de su última actualización.
        fecha_cambio = (
            participacion.creado_en
            if participacion.reprogramado_desde_id
            else participacion.actualizado_en
        )

    vencimiento_anterior = vencimiento_alarma(
        fecha_anterior
    )

    # No permitir que una asignación posterior
    # vuelva a encender una alarma vencida.
    if (
        vencimiento_anterior is not None
        and ahora >= vencimiento_anterior
    ):
        extension = _extension_valida(
            fecha_anterior,
            fecha_actual,
            fecha_cambio,
        )

        if not extension:
            if sincronizar:
                sitio.bitacora_alarma_activa = False
                sitio.save(
                    update_fields=["bitacora_alarma_activa"]
                )

            return {
                "activa": False,
                "fecha_salida": fecha_anterior.isoformat(),
                "vence_en": vencimiento_anterior.isoformat(),
            }

    vencimiento_actual = vencimiento_alarma(
        fecha_actual
    )

    if (
        vencimiento_actual is not None
        and ahora >= vencimiento_actual
    ):
        if sincronizar:
            sitio.bitacora_alarma_activa = False
            sitio.save(
                update_fields=["bitacora_alarma_activa"]
            )

        return {
            "activa": False,
            "fecha_salida": fecha_actual.isoformat(),
            "vence_en": vencimiento_actual.isoformat(),
        }

    if fecha_actual != fecha_anterior and sincronizar:
        sitio.bitacora_alarma_fecha_salida = fecha_actual
        sitio.save(
            update_fields=["bitacora_alarma_fecha_salida"]
        )

    return {
        "activa": True,
        "fecha_salida": (
            fecha_actual.isoformat()
            if fecha_actual is not None
            else None
        ),
        "vence_en": (
            vencimiento_actual.isoformat()
            if vencimiento_actual is not None
            else None
        ),
    }

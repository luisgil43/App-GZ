# planificacion/views/mover_semana.py

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal
from planificacion.services.mover_semana import mover_sitio_a_semana
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================

ROLES_MOVER_SEMANA = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# PREVIEW / SELECCIÓN DE SEMANA DESTINO
# ============================================================


@require_GET
@rol_requerido(*ROLES_MOVER_SEMANA)
def seleccionar_semana_destino(
    request,
    sitio_batch_id,
):
    """
    Permite seleccionar la semana destino de un sitio.

    El sitio puede provenir tanto de:

    - una salida diaria;
    - la lista de pendientes sin salida.

    La validación operacional definitiva se ejecuta
    nuevamente dentro de mover_sitio_a_semana().
    """

    sitio_batch = get_object_or_404(
        SitioBatchSemanal.objects.select_related(
            "batch",
            "sitio_planificado",
            "sitio_planificado__planificacion",
            "sitio_planificado__sitio",
        ),
        pk=sitio_batch_id,
    )

    batch_origen = sitio_batch.batch

    sitio_planificado = sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    # ========================================================
    # SEMANAS DESTINO
    # ========================================================

    batches_destino = list(
        BatchPlanificacionSemanal.objects.exclude(
            pk=batch_origen.pk,
        )
        .exclude(
            estado="cancelado",
        )
        .select_related(
            "configuracion_semana",
        )
        .order_by(
            "-fecha_inicio",
            "-id",
        )
    )

    # ========================================================
    # RESPUESTA
    # ========================================================

    return render(
        request,
        "planificacion/diaria/mover_semana.html",
        {
            "sitio_batch": sitio_batch,
            "sitio_planificado": sitio_planificado,
            "sitio": sitio,
            "batch_origen": batch_origen,
            "batches_destino": batches_destino,
        },
    )


# ============================================================
# CONFIRMAR MOVIMIENTO
# ============================================================


@require_POST
@rol_requerido(*ROLES_MOVER_SEMANA)
def confirmar_mover_semana(
    request,
    sitio_batch_id,
):
    """
    Ejecuta el movimiento definitivo hacia otra semana.

    Toda la lógica destructiva y las protecciones de
    Operaciones viven en mover_sitio_a_semana().
    """

    sitio_batch = get_object_or_404(
        SitioBatchSemanal.objects.select_related(
            "batch",
            "sitio_planificado",
            "sitio_planificado__sitio",
        ),
        pk=sitio_batch_id,
    )

    batch_origen_id = sitio_batch.batch_id

    # ========================================================
    # DESTINO
    # ========================================================

    batch_destino_id = request.POST.get(
        "batch_destino_id",
    )

    try:

        batch_destino_id = int(
            batch_destino_id,
        )

    except (
        TypeError,
        ValueError,
    ):

        messages.error(
            request,
            "Debes seleccionar una semana destino válida.",
        )

        return redirect(
            "planificacion:seleccionar_semana_destino",
            sitio_batch_id=sitio_batch.pk,
        )

    # ========================================================
    # EJECUTAR
    # ========================================================

    try:

        resultado = mover_sitio_a_semana(
            sitio_batch_id=sitio_batch.pk,
            batch_destino_id=batch_destino_id,
            usuario=request.user,
        )

    except BatchPlanificacionSemanal.DoesNotExist:

        messages.error(
            request,
            "La semana destino seleccionada ya no existe.",
        )

        return redirect(
            "planificacion:seleccionar_semana_destino",
            sitio_batch_id=sitio_batch.pk,
        )

    except ValidationError as exc:

        for mensaje in exc.messages:

            messages.error(
                request,
                mensaje,
            )

        return redirect(
            "planificacion:seleccionar_semana_destino",
            sitio_batch_id=sitio_batch.pk,
        )

    except Exception as exc:

        messages.error(
            request,
            ("No fue posible mover el sitio de semana. " f"Detalle: {exc}"),
        )

        return redirect(
            "planificacion:seleccionar_semana_destino",
            sitio_batch_id=sitio_batch.pk,
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    identificador = resultado["identificador"]

    batch_origen = resultado["batch_origen"]

    batch_destino = resultado["batch_destino"]

    messages.success(
        request,
        (
            f"{identificador} fue movido desde "
            f"{batch_origen.codigo_semana} hacia "
            f"{batch_destino.codigo_semana}. "
            "La programación anterior fue eliminada y el sitio "
            "quedó disponible para ser planificado nuevamente "
            "desde cero en la semana destino."
        ),
    )

    # ========================================================
    # VOLVER A LA SEMANA DE ORIGEN
    # ========================================================
    #
    # Así el usuario continúa exactamente en la pantalla
    # desde la cual realizó el movimiento.
    # ========================================================

    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch_origen_id,
    )

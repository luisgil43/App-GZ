# planificacion/views/prioridades_diarias.py

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from planificacion.forms import PrioridadPlanificacionDiariaForm
from planificacion.modelos import PrioridadPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================

ROLES_PRIORIDADES_DIARIAS = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# UTILIDADES
# ============================================================


def _url_planificacion_diaria(batch):
    return reverse(
        "planificacion:detalle_planificacion_diaria",
        kwargs={
            "batch_id": batch.pk,
        },
    )


# ============================================================
# CREAR PRIORIDAD
# ============================================================


@rol_requerido(*ROLES_PRIORIDADES_DIARIAS)
@transaction.atomic
def crear_prioridad_diaria(
    request,
    batch_id,
):
    """
    Crea una prioridad exclusivamente para planificación diaria.

    No modifica:

    - la selección semanal;
    - el motor semanal;
    - Operaciones;
    - trabajadores asignados.

    La prioridad será consumida posteriormente por el motor
    diario antes de construir las salidas normales.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
        ),
        pk=batch_id,
    )

    # ========================================================
    # SITIO PRESELECCIONADO
    # ========================================================

    sitio_batch_id = (
        request.GET.get("sitio_batch") or request.POST.get("sitio_batch") or ""
    )

    initial = {}

    if sitio_batch_id:

        sitio_batch = get_object_or_404(
            SitioBatchSemanal.objects.select_related(
                "sitio_planificado",
                "sitio_planificado__sitio",
            ),
            pk=sitio_batch_id,
            batch=batch,
        )

        # No permitimos crear una segunda prioridad para
        # el mismo sitio.
        if hasattr(
            sitio_batch,
            "prioridad_diaria",
        ):

            prioridad = sitio_batch.prioridad_diaria

            messages.info(
                request,
                (
                    "Este sitio ya posee una prioridad diaria. "
                    "Puedes modificarla desde esta pantalla."
                ),
            )

            return redirect(
                "planificacion:editar_prioridad_diaria",
                prioridad_id=prioridad.pk,
            )

        initial["sitio_batch"] = sitio_batch.pk

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        form = PrioridadPlanificacionDiariaForm(
            request.POST,
            batch=batch,
        )

        if form.is_valid():

            prioridad = form.save(
                commit=False,
            )

            prioridad.estado = "activa"

            prioridad.creado_por = request.user

            prioridad.actualizado_por = request.user

            prioridad.save()

            messages.success(
                request,
                (f"Prioridad diaria creada para " f"{prioridad.id_claro}."),
            )

            return redirect(_url_planificacion_diaria(batch))

    # ========================================================
    # GET
    # ========================================================

    else:

        form = PrioridadPlanificacionDiariaForm(
            batch=batch,
            initial=initial,
        )

    return render(
        request,
        "planificacion/diaria/prioridad_form.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "form": form,
            "modo": "crear",
            "prioridad": None,
        },
    )


# ============================================================
# EDITAR PRIORIDAD
# ============================================================


@rol_requerido(*ROLES_PRIORIDADES_DIARIAS)
@transaction.atomic
def editar_prioridad_diaria(
    request,
    prioridad_id,
):
    prioridad = get_object_or_404(
        PrioridadPlanificacionDiaria.objects.select_related(
            "sitio_batch",
            "sitio_batch__batch",
            "sitio_batch__batch__planificacion",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
            "cuadrilla_obligatoria",
        ),
        pk=prioridad_id,
    )

    batch = prioridad.sitio_batch.batch

    if request.method == "POST":

        form = PrioridadPlanificacionDiariaForm(
            request.POST,
            instance=prioridad,
            batch=batch,
        )

        if form.is_valid():

            prioridad = form.save(
                commit=False,
            )

            prioridad.actualizado_por = request.user

            prioridad.save()

            messages.success(
                request,
                (f"Prioridad diaria de " f"{prioridad.id_claro} actualizada."),
            )

            return redirect(_url_planificacion_diaria(batch))

    else:

        form = PrioridadPlanificacionDiariaForm(
            instance=prioridad,
            batch=batch,
        )

    return render(
        request,
        "planificacion/diaria/prioridad_form.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "form": form,
            "modo": "editar",
            "prioridad": prioridad,
        },
    )


# ============================================================
# CANCELAR PRIORIDAD
# ============================================================


@require_POST
@rol_requerido(*ROLES_PRIORIDADES_DIARIAS)
@transaction.atomic
def cancelar_prioridad_diaria(
    request,
    prioridad_id,
):
    """
    Conservamos el registro para auditoría, pero deja
    inmediatamente de afectar al motor diario.
    """

    prioridad = get_object_or_404(
        PrioridadPlanificacionDiaria.objects.select_related(
            "sitio_batch",
            "sitio_batch__batch",
            "sitio_batch__sitio_planificado__sitio",
        ),
        pk=prioridad_id,
    )

    batch = prioridad.sitio_batch.batch

    prioridad.estado = "cancelada"

    prioridad.actualizado_por = request.user

    prioridad.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    messages.success(
        request,
        (f"La prioridad diaria de " f"{prioridad.id_claro} fue cancelada."),
    )

    return redirect(_url_planificacion_diaria(batch))

# ============================================================
# QUITAR PRIORIDAD DESDE PLANIFICACIÓN DIARIA
# ============================================================


@require_POST
@rol_requerido(*ROLES_PRIORIDADES_DIARIAS)
@transaction.atomic
def quitar_prioridad_planificacion_diaria(
    request,
    prioridad_id,
):
    """
    Quita la condición de prioridad de un sitio.

    REGLA
    ==========================================================

    Esta acción:

    - NO elimina el registro;
    - NO elimina el sitio de su jornada actual;
    - NO modifica la salida donde ya está programado;
    - NO modifica Operaciones;
    - NO modifica técnicos;
    - NO recalcula automáticamente la planificación.

    Únicamente cambia:

        prioridad.estado
            activa
                ↓
            cancelada

    De esta manera:

    - desaparece la estrella amarilla;
    - el sitio deja de estar protegido como prioridad;
    - en un próximo recálculo el sitio se comportará como
      cualquier otro sitio normal, siempre que su salida
      siga siendo editable.

    El registro se conserva para auditoría y podrá volver
    a reactivarse posteriormente si fuese necesario.
    """

    prioridad = get_object_or_404(
        PrioridadPlanificacionDiaria.objects.select_for_update().select_related(
            "sitio_batch",
            "sitio_batch__batch",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
        ),
        pk=prioridad_id,
    )

    batch = prioridad.sitio_batch.batch

    # ========================================================
    # IDENTIFICADOR
    # ========================================================

    sitio = prioridad.sitio_batch.sitio_planificado.sitio

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    # ========================================================
    # YA NO ESTÁ ACTIVA
    # ========================================================

    if prioridad.estado != "activa":

        messages.info(
            request,
            (f"{identificador} ya no posee una " "prioridad diaria activa."),
        )

        return redirect(
            _url_planificacion_diaria(
                batch,
            )
        )

    # ========================================================
    # CANCELAR SOLAMENTE LA PRIORIDAD
    # ========================================================

    prioridad.estado = "cancelada"

    prioridad.actualizado_por = request.user

    prioridad.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    # ========================================================
    # MENSAJE
    # ========================================================

    messages.success(
        request,
        (
            f"{identificador} dejó de ser prioritario. "
            "Su programación actual no fue modificada."
        ),
    )

    return redirect(
        _url_planificacion_diaria(
            batch,
        )
    )


# ============================================================
# REACTIVAR PRIORIDAD
# ============================================================


@require_POST
@rol_requerido(*ROLES_PRIORIDADES_DIARIAS)
@transaction.atomic
def reactivar_prioridad_diaria(
    request,
    prioridad_id,
):
    prioridad = get_object_or_404(
        PrioridadPlanificacionDiaria.objects.select_related(
            "sitio_batch",
            "sitio_batch__batch",
            "sitio_batch__sitio_planificado__sitio",
        ),
        pk=prioridad_id,
    )

    batch = prioridad.sitio_batch.batch

    prioridad.estado = "activa"

    prioridad.actualizado_por = request.user

    prioridad.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    messages.success(
        request,
        (f"La prioridad diaria de " f"{prioridad.id_claro} fue reactivada."),
    )

    return redirect(_url_planificacion_diaria(batch))

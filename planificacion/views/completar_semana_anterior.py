import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.models import (BatchPlanificacionSemanal,
                                  PlanificacionMensual)
from planificacion.services.completar_semana_anterior import (
    confirmar_sitios_para_completar_semana,
    generar_recomendacion_completar_semana, obtener_batches_completables)
from usuarios.decoradores import rol_requerido

ROLES_PLANIFICACION = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# ELEGIR SEMANA
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
def completar_semana_anterior(
    request,
    mensual_id,
):
    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    opciones = obtener_batches_completables(
        planificacion_nueva=mensual,
    )

    return render(
        request,
        ("planificacion/" "completar_semana_anterior/" "seleccionar_semana.html"),
        {
            "mensual": mensual,
            "opciones": opciones,
        },
    )


# ============================================================
# ANALIZAR
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def analizar_completar_semana(
    request,
    mensual_id,
):
    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    batch_id = request.POST.get("batch_id")

    batch = get_object_or_404(
        BatchPlanificacionSemanal,
        pk=batch_id,
    )

    resultado = generar_recomendacion_completar_semana(
        planificacion_nueva=mensual,
        batch_destino=batch,
    )

    return render(
        request,
        ("planificacion/" "completar_semana_anterior/" "recomendacion.html"),
        {
            "mensual": mensual,
            "batch": batch,
            "resultado": resultado,
            "diagnostico": (resultado["diagnostico"]),
            "recomendados": (resultado["recomendados"]),
        },
    )


# ============================================================
# CONFIRMAR
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def confirmar_completar_semana(
    request,
    mensual_id,
    batch_id,
):
    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    batch = get_object_or_404(
        BatchPlanificacionSemanal,
        pk=batch_id,
    )

    ids = request.POST.getlist("sitio_ids")

    try:

        resultado = confirmar_sitios_para_completar_semana(
            planificacion_nueva=mensual,
            batch_destino=batch,
            sitio_planificado_ids=ids,
            usuario=request.user,
        )

    except ValueError as exc:

        messages.error(
            request,
            str(exc),
        )

        return redirect(
            "planificacion:" "completar_semana_anterior",
            mensual_id=mensual.pk,
        )

    messages.success(
        request,
        (
            f"{resultado['cantidad_creados']} "
            "sitio(s) fueron incorporados "
            f"operacionalmente a "
            f"{batch.codigo_semana}. "
            "Los sitios continúan perteneciendo "
            f"a {mensual}."
        ),
    )

    return redirect(
        "planificacion:" "detalle_planificacion_semanal",
        batch_id=batch.pk,
    )


"""API meteorológica interna de Planificación Diaria."""

from datetime import date

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from usuarios.decoradores import rol_requerido

from planificacion.models import (
    BatchPlanificacionSemanal,
    SitioBatchSemanal,
)

from planificacion.services.clima_sitios import (
    consultar_clima_sitio,
)


@require_GET
@rol_requerido("admin", "pm", "supervisor")
def clima_sitio_planificacion_diaria(
    request,
    batch_id,
    sitio_batch_id,
):
    batch = get_object_or_404(
        BatchPlanificacionSemanal,
        pk=batch_id,
    )

    item = get_object_or_404(
        SitioBatchSemanal.objects.select_related(
            "sitio_planificado__sitio",
        ),
        pk=sitio_batch_id,
        batch=batch,
    )

    sitio = item.sitio_planificado.sitio

    fecha_texto = request.GET.get(
        "fecha",
        "",
    ).strip()

    if fecha_texto:
        try:
            fecha = date.fromisoformat(
                fecha_texto
            )
        except ValueError:
            return JsonResponse(
                {
                    "disponible": False,
                    "mensaje": "Fecha inválida.",
                },
                status=400,
            )
    else:
        fecha = timezone.localdate()

    resultado = consultar_clima_sitio(
        latitud=sitio.latitud,
        longitud=sitio.longitud,
        fecha=fecha,
    )

    resultado["sitio"] = (
        sitio.id_claro
        or sitio.id_sites
    )

    return JsonResponse(resultado)

from django.conf import settings
from django.shortcuts import get_object_or_404, render

from planificacion.models import BatchPlanificacionSemanal
from planificacion.services.mapas_planificacion import \
    construir_mapa_batch_semanal
from usuarios.decoradores import rol_requerido

ROLES_PLANIFICACION = [
    "admin",
    "pm",
    "supervisor",
]


@rol_requerido(*ROLES_PLANIFICACION)
def mapa_batch_semanal(
    request,
    batch_id,
):
    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    mapa = construir_mapa_batch_semanal(batch)

    return render(
        request,
        "planificacion/semanal/mapa.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "mapa": mapa,
            "google_maps_api_key": getattr(
                settings,
                "GOOGLE_MAPS_API_KEY",
                "",
            ),
        },
    )

from django.db import transaction
from django.utils import timezone

from planificacion.models import SitioPlanificado

ESTADOS_PERMISO_VALIDOS = {
    "sin_gestion",
    "por_solicitar",
    "solicitado",
    "en_espera",
    "aprobado",
    "rechazado",
    "no_requiere",
}


@transaction.atomic
def actualizar_permiso_sitio(
    *,
    sitio_planificado,
    nuevo_estado,
    user,
):
    """
    Actualiza únicamente el estado de permiso de un SitioPlanificado.

    IMPORTANTE:

    - No modifica SitioMovil.
    - No modifica la asignación mensual.
    - No cambia fecha_planificada.
    - No mueve el sitio de semana.
    - No ejecuta todavía el motor de rutas.

    Esta función únicamente mantiene el estado operativo
    necesario para que posteriormente el motor pueda decidir
    qué sitios están disponibles para planificación.
    """

    nuevo_estado = str(nuevo_estado or "").strip()

    if nuevo_estado not in ESTADOS_PERMISO_VALIDOS:
        raise ValueError("El estado de permiso seleccionado no es válido.")

    sitio_planificado = SitioPlanificado.objects.select_for_update().get(
        pk=sitio_planificado.pk,
    )

    estado_anterior = sitio_planificado.estado_permiso

    if estado_anterior == nuevo_estado:
        return {
            "sitio_planificado": sitio_planificado,
            "cambio": False,
            "estado_anterior": estado_anterior,
            "estado_nuevo": nuevo_estado,
        }

    sitio_planificado.estado_permiso = nuevo_estado

    sitio_planificado.actualizado_por = user

    # ========================================================
    # ESTADO OPERATIVO DERIVADO
    # ========================================================

    if nuevo_estado in {
        "aprobado",
        "no_requiere",
    }:
        if sitio_planificado.estado in {
            "pendiente",
            "por_contactar",
            "gestionando_permiso",
        }:
            sitio_planificado.estado = "listo_planificar"

    elif nuevo_estado in {
        "por_solicitar",
        "solicitado",
        "en_espera",
    }:
        if sitio_planificado.estado in {
            "pendiente",
            "por_contactar",
            "listo_planificar",
        }:
            sitio_planificado.estado = "gestionando_permiso"

    elif nuevo_estado == "rechazado":
        if sitio_planificado.estado not in {
            "completado",
            "cancelado",
        }:
            sitio_planificado.estado = "bloqueado"

    elif nuevo_estado == "sin_gestion":
        if sitio_planificado.estado in {
            "gestionando_permiso",
            "listo_planificar",
            "bloqueado",
        }:
            sitio_planificado.estado = "pendiente"

    sitio_planificado.save(
        update_fields=[
            "estado_permiso",
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return {
        "sitio_planificado": sitio_planificado,
        "cambio": True,
        "estado_anterior": estado_anterior,
        "estado_nuevo": nuevo_estado,
        "actualizado_en": timezone.now(),
    }

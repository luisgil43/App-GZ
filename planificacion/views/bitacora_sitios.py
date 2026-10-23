# ============================================================
# GZ SERVICES — BITÁCORA OPERACIONAL POR SITIO
# ============================================================
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import SitioMovil
from planificacion.models import BitacoraSitio
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES AUTORIZADOS
# ============================================================

ROLES_BITACORA_SITIOS = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# CONSULTAR BITÁCORA
# ============================================================


@require_GET
@rol_requerido(*ROLES_BITACORA_SITIOS)
def consultar_bitacora_sitio(request, sitio_id):
    """
    Devuelve el historial de anotaciones de un sitio.

    La consulta está vinculada directamente a SitioMovil
    y es independiente de los batches y las jornadas.
    """

    sitio = get_object_or_404(
        SitioMovil,
        pk=sitio_id,
    )

    anotaciones = (
        BitacoraSitio.objects.filter(
            sitio=sitio,
        )
        .select_related("creado_por")
        .order_by("-creado_en", "-id")
    )

    historial = []

    for anotacion in anotaciones:

        usuario = anotacion.creado_por

        nombre_usuario = usuario.get_full_name() or usuario.get_username()

        historial.append(
            {
                "id": anotacion.pk,
                "contenido": anotacion.contenido,
                "usuario": nombre_usuario,
                "fecha": anotacion.creado_en.strftime("%d/%m/%Y %H:%M"),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "sitio": {
                "id": sitio.pk,
                "identificador": (sitio.id_claro or sitio.id_sites or str(sitio.pk)),
                "nombre": sitio.nombre or "",
            },
            "total": len(historial),
            "anotaciones": historial,
        }
    )


# ============================================================
# REGISTRAR NUEVA ANOTACIÓN
# ============================================================


@require_POST
@rol_requerido(*ROLES_BITACORA_SITIOS)
@transaction.atomic
def registrar_bitacora_sitio(request, sitio_id):
    """
    Registra una anotación nueva.

    Las anotaciones son independientes y no modifican
    el estado de permisos, planificación u operaciones.
    """

    sitio = get_object_or_404(
        SitioMovil,
        pk=sitio_id,
    )

    contenido = request.POST.get("contenido", "").strip()

    if not contenido:

        return JsonResponse(
            {
                "ok": False,
                "error": "La anotación no puede estar vacía.",
            },
            status=400,
        )

    if len(contenido) > 5000:

        return JsonResponse(
            {
                "ok": False,
                "error": ("La anotación no puede superar " "los 5000 caracteres."),
            },
            status=400,
        )

    anotacion = BitacoraSitio.objects.create(
        sitio=sitio,
        contenido=contenido,
        creado_por=request.user,
    )

    usuario = anotacion.creado_por

    nombre_usuario = usuario.get_full_name() or usuario.get_username()

    return JsonResponse(
        {
            "ok": True,
            "mensaje": "Anotación registrada correctamente.",
            "anotacion": {
                "id": anotacion.pk,
                "contenido": anotacion.contenido,
                "usuario": nombre_usuario,
                "fecha": anotacion.creado_en.strftime("%d/%m/%Y %H:%M"),
            },
        },
        status=201,
    )

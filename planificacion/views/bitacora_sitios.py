from zoneinfo import ZoneInfo
# ============================================================
# GZ SERVICES — BITÁCORA OPERACIONAL POR SITIO
# ============================================================
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import SitioMovil, ServicioCotizado
from django.http import HttpResponseForbidden
from planificacion.models import BitacoraSitio
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES AUTORIZADOS
# ============================================================

ROLES_BITACORA_SITIOS = [
    "admin",
    "pm",
    "supervisor",
    "usuario",
]


# ============================================================

# GZ_BITACORA_TECNICOS_V1
def _tecnico_autorizado_para_bitacora(usuario, sitio):
    """
    Autoriza solamente sitios de servicios asignados al técnico.
    """
    if not getattr(usuario, "is_authenticated", False):
        return False

    if "usuario" not in _roles_bitacora(usuario):
        return False

    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
    )

    if servicios.filter(
        sitio_planificado__sitio_id=sitio.pk,
    ).exists():
        return True

    identificador = str(sitio.id_claro or "").strip()

    if not identificador:
        return False

    if SitioMovil.objects.filter(
        id_claro=identificador,
    ).count() != 1:
        return False

    return servicios.filter(
        sitio_planificado__isnull=True,
        id_claro=identificador,
    ).exists()


def _roles_bitacora(usuario):
    if not getattr(usuario, "is_authenticated", False):
        return set()

    if getattr(usuario, "is_superuser", False):
        return {"admin"}

    if not hasattr(usuario, "roles"):
        return set()

    return set(
        usuario.roles.values_list("nombre", flat=True)
    )


def _puede_acceder_bitacora(usuario, sitio):
    roles = _roles_bitacora(usuario)

    if roles.intersection({"admin", "pm", "supervisor"}):
        return True

    if "usuario" in roles:
        return _tecnico_autorizado_para_bitacora(
            usuario,
            sitio,
        )

    return False


def _puede_escribir_bitacora(usuario):
    return bool(
        _roles_bitacora(usuario).intersection(
            {"admin", "pm", "supervisor"}
        )
    )



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

    if not _puede_acceder_bitacora(request.user, sitio):
        return HttpResponseForbidden(
            "No tienes autorización para acceder "
            "a la bitácora de este sitio."
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
                "fecha": timezone.localtime(anotacion.creado_en, ZoneInfo("America/Santiago")).strftime("%d/%m/%Y %H:%M"),
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

    # GZ_BITACORA_ESCRITURA_ADMIN_V3
    if not _puede_escribir_bitacora(request.user):
        return JsonResponse(
            {
                "ok": False,
                "error": "Tu cuenta solo puede consultar la bitácora.",
            },
            status=403,
        )

    sitio = get_object_or_404(
        SitioMovil,
        pk=sitio_id,
    )

    if not _puede_acceder_bitacora(request.user, sitio):
        return HttpResponseForbidden(
            "No tienes autorización para acceder "
            "a la bitácora de este sitio."
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
                "fecha": timezone.localtime(anotacion.creado_en, ZoneInfo("America/Santiago")).strftime("%d/%m/%Y %H:%M"),
            },
        },
        status=201,
    )

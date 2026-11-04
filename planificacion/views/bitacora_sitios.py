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
from planificacion.services.alarma_bitacora import (
    estado_alarma_sitio,
    fecha_salida_vigente,
)
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
            "alarma": estado_alarma_sitio(sitio),
            "puede_modificar_alarma": _puede_escribir_bitacora(
                request.user
            ),
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



# ============================================================
# ACTIVAR / DESACTIVAR ALARMA DE BITÁCORA
# ============================================================

@require_POST
@rol_requerido(*ROLES_BITACORA_SITIOS)
@transaction.atomic
def actualizar_alarma_bitacora_sitio(request, sitio_id):

    if not _puede_escribir_bitacora(request.user):
        return JsonResponse(
            {
                "ok": False,
                "error": "No tienes permiso para modificar la alarma.",
            },
            status=403,
        )

    sitio = get_object_or_404(
        SitioMovil.objects.select_for_update(),
        pk=sitio_id,
    )

    if not _puede_acceder_bitacora(request.user, sitio):
        return JsonResponse(
            {
                "ok": False,
                "error": "No tienes acceso a este sitio.",
            },
            status=403,
        )

    valor = request.POST.get("activa")

    if valor not in {"true", "false"}:
        return JsonResponse(
            {
                "ok": False,
                "error": "Estado de alarma no válido.",
            },
            status=400,
        )

    activar = valor == "true"

    if activar:
        sitio.bitacora_alarma_activa = True
        sitio.bitacora_alarma_activada_en = timezone.now()
        sitio.bitacora_alarma_fecha_salida = fecha_salida_vigente(
            sitio.pk
        )
    else:
        sitio.bitacora_alarma_activa = False
        sitio.bitacora_alarma_fecha_salida = None

    sitio.save(
        update_fields=[
            "bitacora_alarma_activa",
            "bitacora_alarma_activada_en",
            "bitacora_alarma_fecha_salida",
        ]
    )

    alarma = estado_alarma_sitio(sitio)

    return JsonResponse(
        {
            "ok": True,
            "alarma": alarma,
            "mensaje": (
                "Alarma activada."
                if alarma["activa"]
                else "Alarma desactivada."
            ),
        }
    )



# ============================================================
# CONSULTA MASIVA DE ALARMAS VISUALES
# ============================================================

@require_GET
@rol_requerido(*ROLES_BITACORA_SITIOS)
def consultar_alarmas_bitacora_sitios(request):
    """
    Consulta las alarmas de los sitios autorizados.

    No modifica anotaciones, planificación ni operaciones.
    El estado de alarma se evalúa con su vencimiento real.
    """

    from planificacion.services.alarma_bitacora import (
        estado_alarma_sitio,
    )

    valores = request.GET.get("ids", "").strip()

    if not valores:
        return JsonResponse({
            "ok": True,
            "alarmas": {},
        })

    partes = valores.split(",")

    if len(partes) > 200:
        return JsonResponse(
            {
                "ok": False,
                "error": "Máximo 200 sitios por consulta.",
            },
            status=400,
        )

    ids = set()

    for parte in partes:
        valor = parte.strip()

        if not valor.isdecimal():
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Identificador de sitio no válido.",
                },
                status=400,
            )

        identificador = int(valor)

        if identificador <= 0:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Identificador de sitio no válido.",
                },
                status=400,
            )

        ids.add(identificador)

    sitios = SitioMovil.objects.filter(
        pk__in=ids,
    )

    alarmas = {}

    for sitio in sitios:

        if not _puede_acceder_bitacora(
            request.user,
            sitio,
        ):
            continue

        estado = estado_alarma_sitio(sitio)

        alarmas[str(sitio.pk)] = {
            "activa": bool(estado["activa"]),
            "vence_en": estado.get("vence_en"),
        }

    return JsonResponse({
        "ok": True,
        "alarmas": alarmas,
    })

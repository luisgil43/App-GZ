import math

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from usuarios.decoradores import rol_requerido
from .models import EvidenciaFoto, RequisitoFoto, ServicioCotizado, SesionFotoTecnico
from .views_fotos import _norm_title, upload_evidencias_ajax
from .permisos_fotos import _ORIGEN_CAMARA


def _pendientes(asignacion):
    completados = {
        _norm_title(titulo)
        for titulo in EvidenciaFoto.objects.filter(
            tecnico_sesion__sesion_id=asignacion.sesion_id,
            requisito__isnull=False,
        ).values_list("requisito__titulo", flat=True)
    }
    pendientes = []
    vistos = set()
    for requisito in RequisitoFoto.objects.filter(
        tecnico_sesion=asignacion, activo=True
    ).order_by("orden", "pk"):
        titulo = _norm_title(requisito.titulo)
        if titulo not in completados and titulo not in vistos:
            pendientes.append({
                "id": requisito.pk,
                "titulo": requisito.titulo,
                "etapa": requisito.get_etapa_display(),
            })
            vistos.add(titulo)
    return pendientes


@login_required
@rol_requerido("usuario")
@require_http_methods(["GET", "POST"])
def camera_take(request, pk):
    asignacion = get_object_or_404(
        SesionFotoTecnico.objects.select_related("sesion__servicio"),
        pk=pk, tecnico=request.user,
    )
    servicio = get_object_or_404(
        ServicioCotizado,
        pk=asignacion.sesion.servicio_id,
        trabajadores_asignados=request.user,
    )

    if request.method == "POST":
        try:
            lat = float(request.POST.get("lat", ""))
            lng = float(request.POST.get("lng", ""))
            acc = float(request.POST.get("acc", ""))
            fecha = parse_datetime(request.POST.get("client_taken_at", ""))
            if (
                not all(math.isfinite(valor) for valor in (lat, lng, acc))
                or not -90 <= lat <= 90 or not -180 <= lng <= 180
                or acc < 0 or fecha is None or timezone.is_naive(fecha)
            ):
                raise ValueError
            requisito_id = int(request.POST["req_id"]) if request.POST.get("req_id") else None
        except (ValueError, TypeError, OverflowError):
            return JsonResponse(
                {"ok": False, "error": "La captura no tiene fecha o ubicación válida."},
                status=400,
            )

        # Serializar confirmaciones del equipo y permitir reintentar la misma captura.
        with transaction.atomic():
            type(asignacion.sesion).objects.select_for_update().get(
                pk=asignacion.sesion_id
            )
            asignacion.refresh_from_db()
            if not servicio.trabajadores_asignados.filter(pk=request.user.pk).exists():
                return JsonResponse(
                    {"ok": False, "error": "Ya no estás asignado a este servicio."},
                    status=403,
                )

            existente = EvidenciaFoto.objects.filter(
                tecnico_sesion=asignacion,
                requisito_id=requisito_id,
                client_taken_at=fecha,
                lat=lat, lng=lng,
            ).first()
            if existente:
                return JsonResponse({
                    "ok": True,
                    "evidencia": {"id": existente.pk, "url": existente.imagen.url},
                    "requisitos": _pendientes(asignacion),
                })

            abierta = asignacion.estado == "en_proceso" or (
                asignacion.estado == "rechazado_supervisor"
                and asignacion.reintento_habilitado
            )
            if not abierta:
                return JsonResponse(
                    {"ok": False, "error": "La asignación no está abierta para subir fotos."},
                    status=403,
                )
            if requisito_id and requisito_id not in {
                requisito["id"] for requisito in _pendientes(asignacion)
            }:
                return JsonResponse(
                    {"ok": False, "error": "El requerimiento ya está cubierto o no está disponible."},
                    status=409,
                )

            respuesta = upload_evidencias_ajax(
                request,
                pk=asignacion.pk,
                _origen_camara=_ORIGEN_CAMARA,
            )
            return respuesta

    abierta = asignacion.estado == "en_proceso" or (
        asignacion.estado == "rechazado_supervisor"
        and asignacion.reintento_habilitado
    )
    if not abierta:
        return JsonResponse(
            {"ok": False, "error": "La asignación no está abierta para tomar fotos."},
            status=403,
        )

    pendientes = _pendientes(asignacion)
    if request.GET.get("formato") == "json":
        return JsonResponse({"ok": True, "requisitos": pendientes})

    seleccionado = request.GET.get("req_id", "")
    validos = {str(requisito["id"]) for requisito in pendientes}
    if seleccionado and seleccionado not in validos:
        return JsonResponse(
            {"ok": False, "error": "El requerimiento ya está cubierto o no está disponible."},
            status=409,
        )

    return render(request, "operaciones/fotos_camera_take.html", {
        "a": asignacion,
        "servicio": servicio,
        "requisitos_camera": pendientes,
        "requisito_seleccionado": seleccionado,
        "proyecto_especial": asignacion.sesion.proyecto_especial,
    })

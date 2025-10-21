from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_current_timezone, make_aware, now
from django.views.decorators.http import require_GET, require_POST

from .models import GeoPhoto


@login_required
@require_GET
def capture(request):
    """
    Cámara standalone.
    """
    ctx = {
        "next_url": request.GET.get("next", ""),
        "titulo_required": request.GET.get("titulo_required") == "1",
        "titulo_default": request.GET.get("titulo_default", "Extra"),
        "mapbox_token": getattr(settings, "MAPBOX_TOKEN", ""),
    }
    return render(request, "geo_cam/capture.html", ctx)


@login_required
@require_POST
def upload(request):
    """
    Recibe imagen + metadatos, guarda archivo con `upload_to` y crea GeoPhoto.
    """
    img = request.FILES.get("imagen")
    if not img:
        return HttpResponseBadRequest("Falta 'imagen'")

    titulo_manual = (request.POST.get("titulo_manual") or "").strip() or "Extra"

    # Metadatos (pueden venir vacíos)
    lat = request.POST.get("lat")
    lng = request.POST.get("lng")
    acc = request.POST.get("acc")
    client_taken_at = request.POST.get("client_taken_at")  # ISO-8601 (opcional)

    # Parseo seguro
    lat_dec = GeoPhoto._to_decimal_or_none(lat)
    lng_dec = GeoPhoto._to_decimal_or_none(lng)
    try:
        acc_float = float(acc) if acc not in (None, "") else None
    except Exception:
        acc_float = None

    dt_client = None
    if client_taken_at:
        dt_client = parse_datetime(client_taken_at)
        if dt_client and dt_client.tzinfo is None:
            dt_client = make_aware(dt_client, get_current_timezone())

    # Crear y guardar archivo a través del ImageField (respeta upload_to)
    photo = GeoPhoto(
        user=request.user,
        titulo_manual=titulo_manual,
        lat=lat_dec,
        lng=lng_dec,
        acc=acc_float,
        client_taken_at=dt_client,
    )
    filename = f"foto_{int(now().timestamp())}.jpg"
    photo.image.save(filename, img, save=True)

    return JsonResponse(
        {
            "ok": True,
            "id": photo.id,
            "url": photo.image.url,
            "created_at": photo.created_at.isoformat(),
        }
    )


@login_required
@require_GET
def gallery(request):
    """
    Galería simple del usuario autenticado.
    """
    qs = GeoPhoto.objects.filter(user=request.user).order_by("-created_at")[:200]
    return render(request, "geo_cam/gallery.html", {"photos": qs})
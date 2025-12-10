import logging
from datetime import datetime
from decimal import Decimal

import requests  # proxy geocoding / static map
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_current_timezone, make_aware, now
from django.views.decorators.http import require_GET, require_POST

from .models import GeoPhoto

logger = logging.getLogger(__name__)


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

    # Metadatos
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

    # Crear y guardar
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


@login_required
@require_GET
def geocode_google(request):
    """
    Proxy seguro para Google Geocoding (usa GOOGLE_MAPS_SERVER_KEY).
    """
    lat = request.GET.get("lat")
    lng = request.GET.get("lng")
    if not lat or not lng:
        return JsonResponse(
            {"status": "INVALID_REQUEST", "error_message": "lat/lng requeridos"},
            status=400,
        )

    key = getattr(settings, "GOOGLE_MAPS_SERVER_KEY", "")
    if not key:
        return JsonResponse(
            {
                "status": "REQUEST_DENIED",
                "error_message": "Falta GOOGLE_MAPS_SERVER_KEY en el servidor.",
            },
            status=200,
        )

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lng}", "language": "es", "key": key},
            timeout=10,
        )
        try:
            data = r.json()
        except ValueError:
            data = {
                "status": "ERROR",
                "error_message": f"Respuesta no JSON ({r.status_code}).",
            }

        if r.status_code != 200 or data.get("status") != "OK":
            logger.warning(
                "Geocoding fallo: code=%s status=%s msg=%s",
                r.status_code,
                data.get("status"),
                data.get("error_message"),
            )
        return JsonResponse(data, status=200)

    except requests.Timeout:
        return JsonResponse(
            {"status": "ERROR", "error_message": "Timeout hacia Google Geocoding."},
            status=200,
        )
    except Exception as e:
        logger.exception("Excepcion en geocode_google")
        return JsonResponse({"status": "ERROR", "error_message": str(e)}, status=200)


@login_required
@require_GET
def static_map(request):
    """
    Proxy seguro para Google Static Maps (usa GOOGLE_MAPS_SERVER_KEY).

    Retorna una imagen desde nuestro propio dominio, así el frontend
    puede dibujarla dentro del canvas sin problemas de CORS.
    """
    lat = request.GET.get("lat")
    lng = request.GET.get("lng")
    if not lat or not lng:
        return HttpResponseBadRequest("lat/lng requeridos")

    key = getattr(settings, "GOOGLE_MAPS_SERVER_KEY", "")
    if not key:
        return HttpResponseBadRequest("Falta GOOGLE_MAPS_SERVER_KEY en settings.")

    params = {
        "center": f"{lat},{lng}",
        "zoom": request.GET.get("zoom", "18"),
        "size": request.GET.get("size", "240x240"),
        "scale": request.GET.get("scale", "2"),
        "maptype": request.GET.get("maptype", "satellite"),
        "markers": f"{lat},{lng}",
        "key": key,
    }

    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/staticmap",
            params=params,
            timeout=10,
        )
    except requests.Timeout:
        return HttpResponse(b"", content_type="image/png", status=504)
    except Exception:
        logger.exception("Excepcion en static_map")
        return HttpResponse(b"", content_type="image/png", status=500)

    content_type = r.headers.get("Content-Type", "image/png")
    resp = HttpResponse(r.content, content_type=content_type)
    # Puedes cachear un poco si quieres, yo lo dejo casi sin cache
    resp["Cache-Control"] = "private, max-age=60"
    return resp
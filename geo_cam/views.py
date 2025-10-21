import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST

from .models import GeoPhoto


@login_required
@require_GET
def capture(request):
    """
    Cámara standalone.
    Query params:
      - next (opcional): si viene, el botón back va allí; no redirigimos tras subir.
      - titulo_required=1
      - titulo_default=Texto
    """
    ctx = {
        "next_url": request.GET.get("next", ""),
        "titulo_required": request.GET.get("titulo_required") == "1",
        "titulo_default": request.GET.get("titulo_default", "Extra"),
        # Si quieres pasar el token del mapa desde settings:
        "mapbox_token": getattr(settings, "MAPBOX_TOKEN", ""),
    }
    return render(request, "geo_cam/capture.html", ctx)


@login_required
@require_POST
def upload(request):
    """
    Recibe imagen + metadatos y guarda archivo físicamente y registro en DB.
    Responde JSON.
    """
    img = request.FILES.get("imagen")
    if not img:
        return HttpResponseBadRequest("Falta 'imagen'")

    # Metadatos
    lat = request.POST.get("lat") or ""
    lng = request.POST.get("lng") or ""
    acc = request.POST.get("acc") or ""
    client_taken_at = request.POST.get("client_taken_at") or now().isoformat()
    titulo_manual = request.POST.get("titulo_manual", "")

    # Guarda archivo
    subdir = "geo_cam"
    fs = FileSystemStorage(
        location=os.path.join(settings.MEDIA_ROOT, subdir),
        base_url=os.path.join(settings.MEDIA_URL, subdir + "/"),
    )
    fname = f"foto_{int(now().timestamp())}.jpg"
    saved_name = fs.save(fname, img)
    file_url = fs.url(saved_name)

    # Registro en DB
    photo = GeoPhoto.objects.create(
        user=request.user,
        image=os.path.join(subdir, os.path.basename(saved_name)),
        titulo_manual=titulo_manual,
        lat=lat,
        lng=lng,
        acc=acc,
        client_taken_at=client_taken_at,
    )

    return JsonResponse({
        "ok": True,
        "id": photo.id,
        "url": file_url,
        "created_at": photo.created_at.isoformat(),
    })


@login_required
@require_GET
def gallery(request):
    """
    Galería simple del usuario autenticado.
    """
    qs = GeoPhoto.objects.filter(user=request.user).order_by("-created_at")[:200]
    return render(request, "geo_cam/gallery.html", {"photos": qs})
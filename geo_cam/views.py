import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import (HttpResponseBadRequest, HttpResponseForbidden,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST

from .models import GeoPhoto


@login_required
@require_GET
def capture(request):
    """
    Cámara standalone.
    ?titulo_required=1 (obliga título)
    ?titulo_default=Texto (si no es obligatorio)
    """
    ctx = {
        "next_url": request.GET.get("next", ""),  # se ignora al subir (nos quedamos en cámara)
        "titulo_required": request.GET.get("titulo_required") == "1",
        "titulo_default": request.GET.get("titulo_default", "Extra"),
    }
    return render(request, "geo_cam/capture.html", ctx)

@login_required
@require_POST
def upload(request):
    """
    Recibe imagen ya estampada + metadatos. Guarda y registra en DB para Galería.
    Responde JSON.
    """
    img = request.FILES.get("imagen")
    if not img:
        return HttpResponseBadRequest("Falta 'imagen'")

    # Metadatos (opcionales)
    lat   = request.POST.get("lat", "")
    lng   = request.POST.get("lng", "")
    acc   = request.POST.get("acc", "")
    cta   = request.POST.get("client_taken_at") or now().isoformat()
    title = request.POST.get("titulo_manual", "")

    # Guarda archivo
    subdir = "geo_cam"
    fs = FileSystemStorage(
        location=os.path.join(settings.MEDIA_ROOT, subdir),
        base_url=os.path.join(settings.MEDIA_URL, subdir + "/"),
    )
    filename = f"foto_{int(now().timestamp())}.jpg"
    saved_name = fs.save(filename, img)
    url = fs.url(saved_name)

    # Registro para la galería
    photo = GeoPhoto.objects.create(
        user=request.user,
        image=os.path.join(subdir, os.path.basename(saved_name)),
        titulo_manual=title,
        lat=lat, lng=lng, acc=acc, client_taken_at=cta,
    )

    return JsonResponse({
        "ok": True,
        "id": photo.pk,
        "url": url,
        "title": title,
    })

@login_required
@require_GET
def gallery(request):
    photos = GeoPhoto.objects.filter(user=request.user)[:200]
    return render(request, "geo_cam/gallery.html", {"photos": photos})

@login_required
@require_POST
def delete_photo(request, pk: int):
    photo = get_object_or_404(GeoPhoto, pk=pk, user=request.user)
    # elimina archivo físico
    try:
        storage = photo.image.storage
        if storage.exists(photo.image.name):
            storage.delete(photo.image.name)
    except Exception:
        pass
    photo.delete()
    if request.headers.get("HX-Request"):  # si luego usas htmx
        return JsonResponse({"ok": True})
    return redirect("geo_cam:gallery")
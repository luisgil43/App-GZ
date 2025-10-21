import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST


@require_GET
def capture(request):
    """
    Pantalla de cámara standalone.
    Query params opcionales:
      - next: URL a la que volver (botón Volver)
      - titulo_required=1 -> obliga a ingresar título
      - titulo_default=Texto (valor por defecto)
    """
    ctx = {
        "next_url": request.GET.get("next", ""),
        "titulo_required": request.GET.get("titulo_required") == "1",
        "titulo_default": request.GET.get("titulo_default", "Extra"),
    }
    return render(request, "geo_cam/capture.html", ctx)

@require_POST
def upload(request):
    """
    Endpoint propio de geo_cam para recibir la imagen + metadatos.
    Guarda en MEDIA_ROOT/geo_cam/ y responde JSON.
    """
    img = request.FILES.get("imagen")
    if not img:
        return HttpResponseBadRequest("Falta 'imagen'")

    # metadatos opcionales
    lat = request.POST.get("lat")
    lng = request.POST.get("lng")
    acc = request.POST.get("acc")
    client_taken_at = request.POST.get("client_taken_at") or now().isoformat()
    titulo_manual = request.POST.get("titulo_manual", "")

    # folder de la app
    subdir = "geo_cam"
    fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, subdir),
                           base_url=os.path.join(settings.MEDIA_URL, subdir + "/"))

    # nombre de archivo
    fname = f"foto_{int(now().timestamp())}.jpg"
    saved_name = fs.save(fname, img)
    url = fs.url(saved_name)

    return JsonResponse({
        "ok": True,
        "file": saved_name,
        "url": url,
        "meta": {
            "lat": lat, "lng": lng, "acc": acc,
            "client_taken_at": client_taken_at,
            "titulo_manual": titulo_manual,
        },
    })
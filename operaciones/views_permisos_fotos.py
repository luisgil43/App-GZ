from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from usuarios.decoradores import rol_requerido
from .models import ServicioCotizado


@login_required
@rol_requerido("admin")
@require_POST
def configurar_archivos_evidencias(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)
    valor = request.POST.get("habilitar")
    if valor not in {"0", "1"}:
        return HttpResponseBadRequest("Permiso inválido.")

    habilitar = valor == "1"
    ServicioCotizado.objects.filter(pk=servicio.pk).update(
        permitir_archivos_evidencias=habilitar,
    )
    messages.success(
        request,
        "Carga de archivos "
        + ("habilitada" if habilitar else "deshabilitada")
        + f" para DU{servicio.du}.",
    )
    destino = request.POST.get("next", "")
    if destino and url_has_allowed_host_and_scheme(
        destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(destino)
    return redirect("operaciones:listar_servicios_supervisor")

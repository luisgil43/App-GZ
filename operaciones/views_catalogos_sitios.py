
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_http_methods

from usuarios.decoradores import rol_requerido
from .forms_catalogos_sitios import (
    IngenieroCampoSitioForm,
    OficinaRetiroLlavesForm,
)
from .models import (
    SitioMovil,
    IngenieroCampoSitio,
    OficinaRetiroLlaves,
)

ROLES = ("pm", "admin", "facturacion", "supervisor")


@login_required
@rol_requerido(*ROLES)
@require_POST
def crear_catalogo_sitio(request, tipo):
    formulario = {
        "ic": IngenieroCampoSitioForm,
        "oficina": OficinaRetiroLlavesForm,
    }.get(tipo)

    if formulario is None:
        return JsonResponse(
            {"ok": False, "error": "Catálogo inválido."},
            status=400,
        )

    form = formulario(request.POST)
    if not form.is_valid():
        errores = [
            f"{campo}: {mensaje}"
            for campo, mensajes in form.errors.items()
            for mensaje in mensajes
        ]
        return JsonResponse(
            {"ok": False, "error": " ".join(errores)},
            status=400,
        )

    obj = form.save()
    return JsonResponse({
        "ok": True,
        "id": obj.pk,
        "texto": str(obj),
    })


@login_required
@rol_requerido(*ROLES)
@require_POST
@transaction.atomic
def asignar_catalogo_sitio(request, pk):
    from .views import _registrar_actualizacion_tabla_sitios

    sitio = get_object_or_404(SitioMovil, pk=pk)
    tipo = request.POST.get("tipo")
    configuracion = {
        "ic": (IngenieroCampoSitio, "ic_id"),
        "oficina": (OficinaRetiroLlaves, "oficina_retiro_llaves_id"),
    }.get(tipo)

    if configuracion is None:
        return JsonResponse(
            {"ok": False, "error": "Campo inválido."},
            status=400,
        )

    modelo, campo = configuracion
    valor = request.POST.get("valor", "").strip()
    obj = None

    if valor:
        if not valor.isdecimal() or len(valor) > 18:
            return JsonResponse(
                {"ok": False, "error": "Selección inválida."},
                status=400,
            )
        obj = get_object_or_404(modelo, pk=int(valor))

    SitioMovil.objects.filter(pk=sitio.pk).update(
        **{campo: obj.pk if obj else None}
    )
    _registrar_actualizacion_tabla_sitios(request.user)

    return JsonResponse({
        "ok": True,
        "valor": str(obj.pk) if obj else "",
    })


@login_required
@rol_requerido(*ROLES)
@require_http_methods(["GET", "POST"])
def editar_catalogo_sitio(request, tipo, pk):
    configuracion = {
        "ic": (IngenieroCampoSitio, IngenieroCampoSitioForm, "telefono"),
        "oficina": (OficinaRetiroLlaves, OficinaRetiroLlavesForm, "direccion"),
    }.get(tipo)

    if configuracion is None:
        return JsonResponse(
            {"ok": False, "error": "Catálogo inválido."}, status=400
        )

    modelo, formulario, campo = configuracion
    obj = get_object_or_404(modelo, pk=pk)

    if request.method == "GET":
        return JsonResponse({
            "ok": True,
            "id": obj.pk,
            "nombre": obj.nombre,
            "dato": getattr(obj, campo),
        })

    form = formulario(request.POST, instance=obj)
    if not form.is_valid():
        errores = [
            f"{campo_error}: {mensaje}"
            for campo_error, mensajes in form.errors.items()
            for mensaje in mensajes
        ]
        return JsonResponse(
            {"ok": False, "error": " ".join(errores)}, status=400
        )

    from .views import _registrar_actualizacion_tabla_sitios

    with transaction.atomic():
        obj = form.save()
        _registrar_actualizacion_tabla_sitios(request.user)

    return JsonResponse({
        "ok": True,
        "id": obj.pk,
        "texto": str(obj),
    })

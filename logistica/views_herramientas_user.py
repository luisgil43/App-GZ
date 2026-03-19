from __future__ import annotations

from typing import List

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from usuarios.decoradores import rol_requerido

from .forms_herramientas import InventarioUploadForm, RejectAssignmentForm
from .models import HerramientaAsignacion, HerramientaInventario


def _must_user(request):
    return bool(getattr(request.user, "es_usuario", False))


@login_required
@rol_requerido("usuario")
def mis_herramientas(request):
    """
    Usuario ve SOLO sus asignaciones ACTIVAS.
    Solo muestra asignaciones con cantidad_entregada > 0.
    Inventario: estado real según último inventario de su asignación.
    """
    if not _must_user(request):
        return HttpResponseForbidden("No tienes permiso.")

    q = (request.GET.get("q") or "").strip()
    estado_asig = (request.GET.get("estado") or "").strip()
    cantidad = (request.GET.get("cantidad") or "20").strip()
    page_number = (request.GET.get("page") or "1").strip()

    try:
        per_page = int(cantidad)
    except Exception:
        per_page = 20
    if per_page not in (5, 10, 20, 50, 100):
        per_page = 20

    qs = (
        HerramientaAsignacion.objects
        .select_related("herramienta", "asignado_por")
        .filter(asignado_a=request.user, active=True)
        .filter(cantidad_entregada__gt=0)   # ✅ regla: si no tiene cantidad, no aparece
        .order_by("-asignado_at", "-id")
    )

    if q:
        qs = qs.filter(
            Q(herramienta__nombre__icontains=q) |
            Q(herramienta__serial__icontains=q) |
            Q(herramienta__descripcion__icontains=q)
        )

    if estado_asig:
        qs = qs.filter(estado=estado_asig)

    paginator = Paginator(qs, per_page)
    pagina = paginator.get_page(page_number)

    # último inventario SOLO para asignaciones que están en esta página
    asig_ids = [a.id for a in pagina.object_list]
    latest_inv_by_asig = {}
    if asig_ids:
        invs = (
            HerramientaInventario.objects
            .filter(asignacion_id__in=asig_ids)
            .select_related("revisado_por")
            .order_by("-created_at", "-id")
        )
        for inv in invs:
            if inv.asignacion_id not in latest_inv_by_asig:
                latest_inv_by_asig[inv.asignacion_id] = inv

    for a in pagina.object_list:
        a.last_inv = latest_inv_by_asig.get(a.id)

    pendientes = [a for a in pagina.object_list if a.estado == "pendiente"]

    return render(request, "logistica/user_mis_herramientas.html", {
        "pagina": pagina,
        "pendientes": pendientes,
        "q": q,
        "estado": estado_asig,
        "cantidad": str(per_page),
        "ESTADO_ASIG_CHOICES": HerramientaAsignacion.ESTADO_CHOICES,
    })

@login_required
@rol_requerido("usuario")
def aceptar_herramientas(request):
    """
    Aceptación masiva o individual:
    - Llega POST con asignaciones[] (ids)
    - Si no tiene firma_digital: redirige a registrar firma
    - No se puede dejar aceptada sin firma
    """
    if request.method != "POST":
        raise Http404()

    if not _must_user(request):
        return HttpResponseForbidden("No tienes permiso.")

    if not getattr(request.user, "firma_digital", None):
        messages.error(request, "Debes registrar tu firma digital para aceptar herramientas.")
        return redirect(f"{reverse('dashboard:registrar_firma_usuario')}?next={reverse('logistica:mis_herramientas')}")

    ids = request.POST.getlist("asignaciones")
    if not ids:
        messages.warning(request, "Selecciona al menos una herramienta.")
        return redirect("logistica:mis_herramientas")

    qs = (
        HerramientaAsignacion.objects
        .select_related("herramienta")
        .filter(pk__in=ids, asignado_a=request.user, active=True)
    )

    updated = 0
    now = timezone.now()
    for a in qs:
        if a.estado != "pendiente":
            continue
        a.estado = "aceptada"
        a.aceptado_at = now
        a.comentario_rechazo = None
        a.rechazado_at = None
        a.save(update_fields=["estado", "aceptado_at", "comentario_rechazo", "rechazado_at"])

        # reflejar estado herramienta
        h = a.herramienta
        if h.status != "asignada":
            h.status = "asignada"
            h.save(update_fields=["status", "updated_at"])

        updated += 1

    if updated:
        messages.success(request, f"✅ Aceptaste {updated} herramienta(s). Queda registro con tu firma digital.")
    else:
        messages.info(request, "No había herramientas pendientes para aceptar.")

    return redirect("logistica:mis_herramientas")


@login_required
@rol_requerido("usuario")
def rechazar_herramienta(request, asignacion_id: int):
    """
    Rechazo individual:
    - Modal en template manda POST con comentario
    - Queda como rechazada, con comentario visible al usuario y admin.
    """
    if not _must_user(request):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )

    if request.method != "POST":
        raise Http404()

    form = RejectAssignmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Debes escribir un comentario para rechazar.")
        return redirect("logistica:mis_herramientas")

    if a.estado == "aceptada":
        messages.error(request, "Esta herramienta ya fue aceptada. Si hay un problema, contacta a logística.")
        return redirect("logistica:mis_herramientas")

    now = timezone.now()
    a.estado = "rechazada"
    a.comentario_rechazo = form.cleaned_data["comentario"]
    a.rechazado_at = now
    a.save(update_fields=["estado", "comentario_rechazo", "rechazado_at"])

    messages.warning(request, "❌ Herramienta rechazada. Se notificará a logística (si aplica).")
    return redirect("logistica:mis_herramientas")


@login_required
@rol_requerido("usuario")
def subir_inventario(request, asignacion_id: int):
    """
    Usuario sube foto de inventario.
    - Solo si inventory_required=True
    - Tras subir, queda "pendiente revisión" y se deshabilita el botón (inventory_required=False)
    - Si admin rechaza, se vuelve a habilitar (reject() deja inventory_required=True)
    """
    if not _must_user(request):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )
    h = a.herramienta

    if not h.inventory_required:
        messages.info(request, "Esta herramienta no tiene inventario solicitado.")
        return redirect("logistica:mis_herramientas")

    # 🚫 si ya hay un inventario pendiente para esta asignación, no dejar subir otro
    already_pending = HerramientaInventario.objects.filter(asignacion=a, estado="pendiente").exists()
    if already_pending:
        messages.info(request, "Ya enviaste un inventario y está pendiente de revisión.")
        return redirect("logistica:mis_herramientas")

    if request.method == "POST":
        form = InventarioUploadForm(request.POST, request.FILES)
        if form.is_valid():
            inv = form.save(commit=False)
            inv.herramienta = h
            inv.asignacion = a
            inv.estado = "pendiente"
            inv.save()

            # ✅ bloquear botón hasta revisión
            h.inventory_required = False
            h.save(update_fields=["inventory_required", "updated_at"])

            messages.success(request, "📸 Inventario enviado. Queda pendiente de revisión.")
            return redirect("logistica:mis_herramientas")

        messages.error(request, "No se pudo subir la foto. Revisa el archivo.")
    else:
        form = InventarioUploadForm()

    return render(request, "logistica/user_inventario_subir.html", {
        "asignacion": a,
        "herramienta": h,
        "form": form,
    })


@login_required
@rol_requerido("usuario")
def historial_inventario(request, asignacion_id: int):
    """
    Usuario ve historial de inventarios SOLO de su asignación.
    """
    if not _must_user(request):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )

    invs = (
        HerramientaInventario.objects
        .filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at")
    )

    return render(request, "logistica/user_inventario_historial.html", {
        "asignacion": a,
        "herramienta": a.herramienta,
        "inventarios": list(invs),
    })
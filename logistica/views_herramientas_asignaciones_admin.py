from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

from .forms_herramientas_asignaciones import (
    HerramientaAsignacionCantidadForm, HerramientaAsignacionCerrarForm)
from .models import (Bodega, Herramienta, HerramientaAsignacion,
                     HerramientaAsignacionLog, HerramientaInventario)


def _can_admin_logistica(user) -> bool:
    if getattr(user, "es_admin_general", False):
        return True
    if getattr(user, "es_supervisor", False) or getattr(user, "es_pm", False) or getattr(user, "es_logistica", False):
        return True
    return False


def _is_admin_general(user) -> bool:
    return bool(getattr(user, "es_admin_general", False))


def _is_logistica_or_admin(user) -> bool:
    return bool(getattr(user, "es_logistica", False) or getattr(user, "es_admin_general", False))


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def asignaciones_panel(request):
    """
    Panel de asignaciones:
    - Agrupa por trabajador
    - Inventario mostrado es el de ESA asignación (last_inv)
    - Historial de cambios NO se muestra expandido: se abre modal desde Acciones
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    q = (request.GET.get("q") or "").strip().lower()

    asignaciones_qs = (
        HerramientaAsignacion.objects
        .select_related("herramienta", "asignado_a", "asignado_por", "herramienta__bodega")
        .order_by("-active", "-asignado_at", "-id")
    )

    if q:
        asignaciones_qs = asignaciones_qs.filter(
            models.Q(asignado_a__first_name__icontains=q) |
            models.Q(asignado_a__last_name__icontains=q) |
            models.Q(asignado_a__username__icontains=q) |
            models.Q(herramienta__nombre__icontains=q) |
            models.Q(herramienta__serial__icontains=q)
        )

    asignaciones = list(asignaciones_qs)
    asig_ids = [a.id for a in asignaciones]

    # ===== último inventario por asignación =====
    last_inv_by_asig = {}
    if asig_ids:
        invs = (
            HerramientaInventario.objects
            .filter(asignacion_id__in=asig_ids)
            .select_related("revisado_por")
            .order_by("-created_at", "-id")
        )
        for inv in invs:
            if inv.asignacion_id not in last_inv_by_asig:
                last_inv_by_asig[inv.asignacion_id] = inv

    # ===== logs por asignación (últimos N para modal) =====
    logs_by_asig = defaultdict(list)
    if asig_ids:
        logs = (
            HerramientaAsignacionLog.objects
            .filter(asignacion_id__in=asig_ids)
            .select_related("by_user")
            .order_by("-created_at", "-id")
        )
        for lg in logs:
            if len(logs_by_asig[lg.asignacion_id]) < 20:
                logs_by_asig[lg.asignacion_id].append(lg)

    grouped = defaultdict(list)
    users = {}

    for a in asignaciones:
        users[a.asignado_a_id] = a.asignado_a

        a.last_inv = last_inv_by_asig.get(a.id)

        a.logs_list = logs_by_asig.get(a.id, [])
        a.last_edit = a.logs_list[0] if a.logs_list else None

        grouped[a.asignado_a_id].append(a)

    user_ids_sorted = sorted(
        users.keys(),
        key=lambda uid: (users[uid].first_name or "", users[uid].last_name or "", users[uid].username or "")
    )

    users_qs = list(CustomUser.objects.filter(is_active=True).order_by("first_name", "last_name", "username"))

    return render(request, "logistica/admin_herramientas_asignaciones_panel.html", {
        "q": request.GET.get("q", ""),
        "user_ids_sorted": user_ids_sorted,
        "users": users,
        "grouped": dict(grouped),
        "users_qs": users_qs,
        "can_edit": _is_logistica_or_admin(request.user),
        "can_delete": _is_admin_general(request.user),
    })

@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def asignar_cantidad(request, herramienta_id: int):
    """
    Crear asignación por cantidad:
    - Resta stock en Herramienta.cantidad
    - Permite múltiples asignaciones activas de la misma herramienta a distintas personas
    - Registra log (create)
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    herramienta = get_object_or_404(Herramienta, pk=herramienta_id)

    if request.method == "POST":
        form = HerramientaAsignacionCantidadForm(request.POST, herramienta=herramienta)
        if form.is_valid():
            to_user = form.cleaned_data["asignado_a"]
            cantidad_entregada = int(form.cleaned_data["cantidad_entregada"])
            asignado_at = form.cleaned_data["asignado_at"]
            solicitar_inv = bool(form.cleaned_data.get("solicitar_inventario"))

            if cantidad_entregada <= 0:
                messages.error(request, "Cantidad inválida.")
                return redirect("logistica:herramientas_asignaciones_panel")

            with transaction.atomic():
                herramienta = Herramienta.objects.select_for_update().get(pk=herramienta.pk)

                if cantidad_entregada > int(herramienta.cantidad):
                    messages.error(request, f"No hay stock suficiente. Disponible: {herramienta.cantidad}.")
                    return redirect("logistica:herramientas_asignaciones_panel")

                # restar stock
                before_stock = int(herramienta.cantidad)
                herramienta.cantidad = before_stock - cantidad_entregada

                # marcar estado general (opcional)
                if herramienta.cantidad <= 0:
                    herramienta.status = "asignada"
                herramienta.updated_at = timezone.now()
                herramienta.save(update_fields=["cantidad", "status", "updated_at"])

                # crear asignación
                a = HerramientaAsignacion.objects.create(
                    herramienta=herramienta,
                    asignado_a=to_user,
                    asignado_por=request.user,
                    asignado_at=asignado_at,
                    cantidad_entregada=cantidad_entregada,
                    active=True,
                    estado="pendiente",
                )

                # solicitar inventario si corresponde
                if solicitar_inv:
                    herramienta.inventory_required = True
                    if not herramienta.next_inventory_due:
                        herramienta.mark_inventory_due_default()
                    herramienta.save(update_fields=["inventory_required", "next_inventory_due", "updated_at"])

                # ✅ log create
                HerramientaAsignacionLog.objects.create(
                    asignacion=a,
                    accion="create",
                    by_user=request.user,
                    cambios={
                        "herramienta": str(herramienta),
                        "asignado_a": to_user.get_full_name() or to_user.username,
                        "cantidad_entregada": cantidad_entregada,
                        "stock": {"from": before_stock, "to": int(herramienta.cantidad)},
                        "solicitar_inventario": bool(solicitar_inv),
                    },
                    nota=None,
                )

            messages.success(
                request,
                f"✅ Asignación creada: {to_user.get_full_name() or to_user.username} • Cantidad: {cantidad_entregada}."
            )
            return redirect("logistica:herramientas_asignaciones_panel")

        messages.error(request, "❌ Revisa los campos.")
    else:
        form = HerramientaAsignacionCantidadForm(herramienta=herramienta)

    return render(request, "logistica/admin_herramientas_asignar_cantidad.html", {
        "herramienta": herramienta,
        "form": form,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def cerrar_asignacion(request, asignacion_id: int):
    """
    Cerrar una asignación:
    - solicita cantidad devuelta
    - suma devuelta al stock
    - deja registro de cierre/justificación
    - Registra log (close)
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(HerramientaAsignacion, pk=asignacion_id)

    if request.method == "POST":
        form = HerramientaAsignacionCerrarForm(request.POST, asignacion=a)
        if form.is_valid():
            dev = int(form.cleaned_data["cantidad_devuelta"])
            comentario = (form.cleaned_data.get("comentario_cierre") or "").strip() or None
            just = (form.cleaned_data.get("justificacion_diferencia") or "").strip() or None

            with transaction.atomic():
                a = HerramientaAsignacion.objects.select_for_update().select_related("herramienta").get(pk=a.pk)
                h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

                before_stock = int(h.cantidad)

                # set campos cierre
                a.cantidad_devuelta = dev
                a.comentario_cierre = comentario
                a.justificacion_diferencia = just
                a.closed_at = timezone.now()
                a.closed_by = request.user
                a.active = False
                a.estado = "terminada"

                # validación modelo
                a.full_clean()
                a.save()

                # devolver al stock lo devuelto
                if dev > 0:
                    h.cantidad = int(h.cantidad) + dev
                    h.updated_at = timezone.now()

                    if h.status == "asignada" and h.cantidad > 0:
                        h.status = "bodega"

                    h.save(update_fields=["cantidad", "status", "updated_at"])

                # ✅ log close
                HerramientaAsignacionLog.objects.create(
                    asignacion=a,
                    accion="close",
                    by_user=request.user,
                    cambios={
                        "cantidad_devuelta": dev,
                        "comentario_cierre": comentario or "",
                        "justificacion_diferencia": just or "",
                        "stock": {"from": before_stock, "to": int(h.cantidad)},
                    },
                    nota=None,
                )

            messages.success(request, "✅ Asignación terminada y stock actualizado.")
            return redirect("logistica:herramientas_asignaciones_panel")

        messages.error(request, "❌ Revisa los campos del cierre.")
    else:
        form = HerramientaAsignacionCerrarForm(asignacion=a)

    return render(request, "logistica/admin_herramientas_asignacion_cerrar.html", {
        "asignacion": a,
        "form": form,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def reiniciar_estado_asignacion(request, asignacion_id: int):
    """
    Reinicia a pendiente solo si sigue activa.
    Registra log (reset)
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")
    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(HerramientaAsignacion, pk=asignacion_id)
    if not a.active:
        messages.error(request, "No puedes reiniciar una asignación terminada.")
        return redirect("logistica:herramientas_asignaciones_panel")

    prev_estado = a.estado

    a.estado = "pendiente"
    a.comentario_rechazo = None
    a.rechazado_at = None
    a.aceptado_at = None
    a.save(update_fields=["estado", "comentario_rechazo", "rechazado_at", "aceptado_at"])

    HerramientaAsignacionLog.objects.create(
        asignacion=a,
        accion="reset",
        by_user=request.user,
        cambios={"estado": {"from": prev_estado, "to": "pendiente"}},
        nota=None,
    )

    messages.success(request, "✅ Estado reiniciado a Pendiente.")
    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def editar_asignacion(request, asignacion_id: int):
    """
    Editar asignación (solo logística/admin):
    - Permite editar: asignado_a, asignado_at, cantidad_entregada (si activa), nota
    - Ajusta stock si cambia cantidad_entregada y está activa
    - Registra log (update)
    """
    if not _is_logistica_or_admin(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_a"),
        pk=asignacion_id,
    )

    if request.method != "POST":
        raise Http404()

    to_user_id = (request.POST.get("asignado_a") or "").strip()
    asignado_at_raw = (request.POST.get("asignado_at") or "").strip()
    cantidad_raw = (request.POST.get("cantidad_entregada") or "").strip()
    nota = (request.POST.get("nota") or "").strip() or None

    cambios = {}

    # usuario
    to_user = None
    if to_user_id:
        to_user = CustomUser.objects.filter(pk=to_user_id, is_active=True).first()

    # fecha (datetime-local)
    asignado_at = None
    if asignado_at_raw:
        try:
            asignado_at = timezone.make_aware(
                timezone.datetime.strptime(asignado_at_raw, "%Y-%m-%dT%H:%M"),
                timezone.get_current_timezone(),
            )
        except Exception:
            messages.error(request, "❌ Fecha inválida.")
            return redirect("logistica:herramientas_asignaciones_panel")

    # cantidad
    nueva_cantidad = None
    if cantidad_raw:
        try:
            nueva_cantidad = int(cantidad_raw)
        except Exception:
            messages.error(request, "❌ Cantidad inválida.")
            return redirect("logistica:herramientas_asignaciones_panel")

    with transaction.atomic():
        a = HerramientaAsignacion.objects.select_for_update().select_related("herramienta", "asignado_a").get(pk=a.pk)
        h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

        # asignado_a
        if to_user and to_user.pk != a.asignado_a_id:
            cambios["asignado_a"] = {
                "from": a.asignado_a.get_full_name() or a.asignado_a.username,
                "to": to_user.get_full_name() or to_user.username,
            }
            a.asignado_a = to_user

        # asignado_at
        if asignado_at and (not a.asignado_at or asignado_at != a.asignado_at):
            cambios["asignado_at"] = {
                "from": timezone.localtime(a.asignado_at).strftime("%Y-%m-%d %H:%M") if a.asignado_at else None,
                "to": timezone.localtime(asignado_at).strftime("%Y-%m-%d %H:%M"),
            }
            a.asignado_at = asignado_at

        # cantidad_entregada
        if nueva_cantidad is not None:
            if nueva_cantidad <= 0:
                messages.error(request, "❌ La cantidad debe ser mayor a 0.")
                return redirect("logistica:herramientas_asignaciones_panel")

            actual = int(getattr(a, "cantidad_entregada", 0) or 0)
            if nueva_cantidad != actual:
                cambios["cantidad_entregada"] = {"from": actual, "to": nueva_cantidad}

                if a.active:
                    delta = nueva_cantidad - actual
                    # delta > 0 => necesito stock adicional
                    if delta > 0 and int(h.cantidad) < delta:
                        messages.error(request, f"❌ No hay stock suficiente para aumentar. Disponible: {h.cantidad}.")
                        return redirect("logistica:herramientas_asignaciones_panel")

                    before_stock = int(h.cantidad)
                    h.cantidad = int(h.cantidad) - delta  # delta>0 resta, delta<0 suma
                    h.updated_at = timezone.now()
                    h.save(update_fields=["cantidad", "updated_at"])

                    cambios["stock"] = {"from": before_stock, "to": int(h.cantidad)}

                # si no está activa, no tocamos stock
                a.cantidad_entregada = nueva_cantidad

        if cambios:
            a.save()

            HerramientaAsignacionLog.objects.create(
                asignacion=a,
                accion="update",
                by_user=request.user,
                cambios=cambios,
                nota=nota,
            )
            messages.success(request, "✅ Asignación actualizada.")
        else:
            messages.info(request, "No hubo cambios para guardar.")

    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def eliminar_asignacion(request, asignacion_id: int):
    """
    Eliminar asignación (solo admin general):
    - Si está activa: devuelve al stock la cantidad_entregada (para no perder inventario)
    - Registra log (delete) y luego elimina
    """
    if not _is_admin_general(request.user):
        return HttpResponseForbidden("Solo admin general puede eliminar.")

    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(HerramientaAsignacion.objects.select_related("herramienta", "asignado_a"), pk=asignacion_id)

    nota = (request.POST.get("nota") or "").strip() or None

    with transaction.atomic():
        a = HerramientaAsignacion.objects.select_for_update().select_related("herramienta", "asignado_a").get(pk=a.pk)
        h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

        before_stock = int(h.cantidad)

        # si está activa, devolvemos stock de lo entregado
        if a.active:
            entregada = int(getattr(a, "cantidad_entregada", 0) or 0)
            if entregada > 0:
                h.cantidad = int(h.cantidad) + entregada
                h.updated_at = timezone.now()
                h.save(update_fields=["cantidad", "updated_at"])

        # log BEFORE delete
        HerramientaAsignacionLog.objects.create(
            asignacion=a,
            accion="delete",
            by_user=request.user,
            cambios={
                "snapshot": {
                    "herramienta": str(a.herramienta),
                    "asignado_a": (a.asignado_a.get_full_name() or a.asignado_a.username),
                    "cantidad_entregada": int(getattr(a, "cantidad_entregada", 0) or 0),
                    "active": bool(a.active),
                    "estado": a.estado,
                },
                "stock": {"from": before_stock, "to": int(h.cantidad)},
            },
            nota=nota,
        )

        a.delete()

    messages.success(request, "🗑️ Asignación eliminada (y stock ajustado si correspondía).")
    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def inventario_historial_asignacion_admin(request, asignacion_id: int):
    """
    Historial de inventarios FILTRADO por asignación.
    (En herramientas se ve el historial general por herramienta, aquí NO.)
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_a", "asignado_por"),
        pk=asignacion_id,
    )

    inventarios = (
        HerramientaInventario.objects
        .filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at", "-id")
    )

    return render(request, "logistica/admin_inventario_historial_asignacion.html", {
        "asignacion": a,
        "herramienta": a.herramienta,
        "inventarios": list(inventarios),
    })

@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def solicitar_inventario_asignacion(request, asignacion_id: int):
    """
    Solicita inventario para UNA asignación específica.
    - No crea inventario (el usuario lo sube), solo deja el sistema en modo "solicitado".
    - Registra log.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")
    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_a"),
        pk=asignacion_id,
    )

    # Si no está activa, igual puedes solicitar (depende de tu regla). Yo lo permito.
    h = a.herramienta

    # ✅ Marca a nivel herramienta (mantienes tu infraestructura actual)
    h.inventory_required = True
    if not h.next_inventory_due:
        h.mark_inventory_due_default()
    h.updated_at = timezone.now()
    h.save(update_fields=["inventory_required", "next_inventory_due", "updated_at"])

    # ✅ Log asociado a la asignación
    HerramientaAsignacionLog.objects.create(
        asignacion=a,
        accion="inventario_solicitado",
        by_user=request.user,
        cambios={
            "inventory_required": {"from": False, "to": True},
            "next_inventory_due": (h.next_inventory_due.strftime("%Y-%m-%d") if h.next_inventory_due else None),
        },
        nota=None,
    )

    messages.success(request, "📸 Inventario solicitado para esta asignación.")

    nxt = (request.GET.get("next") or "").strip()
    if nxt:
        return redirect(nxt)
    return redirect("logistica:herramientas_asignaciones_panel")
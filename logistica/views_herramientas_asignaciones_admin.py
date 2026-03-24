from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

from .forms_herramientas_asignaciones import (
    HerramientaAsignacionCantidadForm, HerramientaAsignacionCerrarForm)
from .models import (Bodega, Herramienta, HerramientaAsignacion,
                     HerramientaAsignacionLog, HerramientaInventario)


def _is_ajax(request) -> bool:
    return (request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest"


def _user_label(u) -> str:
    if not u:
        return ""
    name = (u.get_full_name() or "").strip()
    return name or (u.username or "")


def _build_inventory_payload_for_asignacion(request, a: HerramientaAsignacion) -> dict:
    """
    Devuelve JSON para que el frontend reconstruya la celda Inventario sin template parcial.
    """
    # last_inv (por asignación)
    last_inv = (
        HerramientaInventario.objects
        .filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at", "-id")
        .first()
    )

    inv = None
    if last_inv:
        inv = {
            "id": last_inv.id,
            "estado": last_inv.estado or "",
            "motivo_rechazo": (last_inv.motivo_rechazo or ""),
            "foto_url": (last_inv.foto.url if getattr(last_inv, "foto", None) else ""),
            "puede_aprobar": (last_inv.estado == "pendiente"),
            "aprobar_url": reverse("logistica:aprobar_inventario", args=[last_inv.id]),
            "rechazar_url": reverse("logistica:rechazar_inventario", args=[last_inv.id]),
        }

    # reglas de “solicitar”
    puede_solicitar = (not last_inv) or (last_inv.estado != "pendiente")

    return {
        "ok": True,
        "asig_id": a.id,
        "tool_id": a.herramienta_id,
        "inv": inv,
        "puede_solicitar": puede_solicitar,
        "solicitar_url": reverse("logistica:solicitar_inventario_asignacion", args=[a.id]),
        "historial_url": reverse("logistica:inventario_historial_asignacion_admin", args=[a.id]),
        "prox_due": (a.herramienta.next_inventory_due.strftime("%d/%m/%Y") if a.herramienta.next_inventory_due else ""),
    }

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
    ✅ Soporta AJAX: devuelve JSON para refrescar SOLO la celda Estado (sin recargar página).
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")
    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
    )

    if not a.active:
        # AJAX
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "No puedes reiniciar una asignación terminada."}, status=400)
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

    # ✅ AJAX: refrescar SOLO la celda Estado
    if _is_ajax(request):
        estado_html = (
            '<span class="inline-block px-3 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">'
            f'Activa • {a.get_estado_display()}'
            "</span>"
        )
        return JsonResponse({
            "ok": True,
            "asig_id": a.id,
            "message": "✅ Estado reiniciado a Pendiente.",
            "estado_html": estado_html,
        })

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
    - ✅ Soporta AJAX: devuelve JSON para refrescar solo la celda.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")
    if request.method != "POST":
        raise Http404()

    with transaction.atomic():
        a = get_object_or_404(
            HerramientaAsignacion.objects
            .select_for_update()
            .select_related("herramienta", "asignado_a"),
            pk=asignacion_id,
        )

        h = (
            Herramienta.objects
            .select_for_update()
            .get(pk=a.herramienta_id)
        )

        was_required = bool(h.inventory_required)

        h.inventory_required = True
        if not h.next_inventory_due:
            h.mark_inventory_due_default()
        h.updated_at = timezone.now()
        h.save(update_fields=["inventory_required", "next_inventory_due", "updated_at"])

        HerramientaAsignacionLog.objects.create(
            asignacion=a,
            accion="inventario_solicitado",
            by_user=request.user,
            cambios={
                "inventory_required": {"from": was_required, "to": True},
                "next_inventory_due": (h.next_inventory_due.strftime("%Y-%m-%d") if h.next_inventory_due else None),
            },
            nota=None,
        )

        # refrescar referencia de herramienta en asignación (para prox_due)
        a.herramienta = h

    messages.success(request, "📸 Inventario solicitado para esta asignación.")

    if _is_ajax(request):
        payload = _build_inventory_payload_for_asignacion(request, a)
        payload["message"] = "Inventario solicitado."
        return JsonResponse(payload)

    nxt = (request.GET.get("next") or "").strip()
    if nxt:
        return redirect(nxt)
    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramientas_asignacion_masiva(request):
    """
    Vista tipo preview para asignación masiva (matriz):
    - Se reciben herramientas por querystring: ?ids=1,2,3
    - Se seleccionan trabajadores (multi-select)
    - Se ingresan cantidades por celda
    - Validación en vivo en frontend + validación dura en backend
    - Al guardar: crea asignaciones y descuenta stock de Herramienta.cantidad

    Inputs POST esperados:
      users = [<user_id>, ...]
      qty_<tool_id>_<user_id> = int
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    def _parse_ids(raw: str) -> list[int]:
        out = []
        for part in (raw or "").split(","):
            part = (part or "").strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except Exception:
                continue
        # únicos, orden estable
        seen = set()
        uniq = []
        for x in out:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    # =========================
    # GET: construir preview
    # =========================
    if request.method == "GET":
        ids = _parse_ids(request.GET.get("ids") or "")
        if not ids:
            messages.error(request, "❌ Debes seleccionar al menos una herramienta para asignación masiva.")
            return redirect("logistica:herramientas_list")

        herramientas = list(
            Herramienta.objects
            .filter(id__in=ids)
            .select_related("bodega")
            .order_by("nombre", "id")
        )
        if not herramientas:
            messages.error(request, "❌ No se encontraron herramientas para asignar.")
            return redirect("logistica:herramientas_list")

        # usuarios disponibles
        users_qs = list(
            CustomUser.objects
            .filter(is_active=True)
            .order_by("first_name", "last_name", "username")
        )

        # para que el template tenga un json de stock / nombres
        tools_payload = []
        for h in herramientas:
            tools_payload.append({
                "id": h.id,
                "nombre": h.nombre or "",
                "serial": h.serial or "",
                "stock": int(h.cantidad or 0),
                "bodega": (h.bodega.nombre if h.bodega else ""),
            })

        users_payload = []
        for u in users_qs:
            users_payload.append({
                "id": u.id,
                "label": (u.get_full_name() or u.username or f"User#{u.id}").strip(),
            })

        return render(request, "logistica/admin_herramientas_asignacion_masiva.html", {
            "tools": herramientas,
            "tools_payload": tools_payload,
            "users_qs": users_qs,
            "users_payload": users_payload,
            "ids_str": ",".join(str(x) for x in ids),
        })

    # =========================
    # POST: guardar asignaciones
    # =========================
    ids = _parse_ids(request.POST.get("ids") or "")
    if not ids:
        messages.error(request, "❌ Faltan herramientas seleccionadas (ids).")
        return redirect("logistica:herramientas_list")

    user_ids_raw = request.POST.getlist("users")
    user_ids = []
    for x in user_ids_raw:
        try:
            user_ids.append(int(x))
        except Exception:
            continue
    user_ids = list(dict.fromkeys(user_ids))  # unique preserve order

    if not user_ids:
        messages.error(request, "❌ Debes seleccionar al menos un trabajador.")
        return redirect(f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}")

    # cargar usuarios (y validar que existan)
    users_by_id = {
        u.id: u
        for u in CustomUser.objects.filter(id__in=user_ids, is_active=True)
    }
    if len(users_by_id) != len(user_ids):
        messages.error(request, "❌ Uno o más trabajadores no son válidos (o están inactivos).")
        return redirect(f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}")

    # matriz qty: tool_id -> user_id -> qty
    def _to_int(v) -> int:
        try:
            if v is None:
                return 0
            v = str(v).strip()
            if v == "":
                return 0
            n = int(v)
            return n if n > 0 else 0
        except Exception:
            return 0

    requested = {}  # tool_id -> {user_id: qty}
    for tool_id in ids:
        requested[tool_id] = {}
        for uid in user_ids:
            key = f"qty_{tool_id}_{uid}"
            requested[tool_id][uid] = _to_int(request.POST.get(key))

    # validación rápida (antes de lock)
    # - si todas las celdas de un tool son 0, se ignora
    any_qty = False
    for tool_id in ids:
        if sum(requested[tool_id].values()) > 0:
            any_qty = True
            break
    if not any_qty:
        messages.error(request, "❌ No ingresaste cantidades (todo quedó en 0/blanco).")
        return redirect(f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}")

    # =========================
    # Persistencia: lock + descontar stock + crear asignaciones
    # =========================
    created = 0
    errores = 0

    try:
        with transaction.atomic():
            # bloquear herramientas
            tools_locked = list(
                Herramienta.objects
                .select_for_update()
                .filter(id__in=ids)
            )
            tools_by_id = {h.id: h for h in tools_locked}

            # validar stocks con valores actuales (ya lockeados)
            for tool_id in ids:
                h = tools_by_id.get(tool_id)
                if not h:
                    raise ValidationError("Herramienta inválida en la asignación masiva.")

                total = int(sum(requested[tool_id].values()) or 0)
                if total <= 0:
                    continue

                stock = int(h.cantidad or 0)
                if total > stock:
                    raise ValidationError(
                        f"No hay stock suficiente para '{h.nombre}'. "
                        f"Stock: {stock} • Estás asignando: {total}."
                    )

            # aplicar cambios
            for tool_id in ids:
                h = tools_by_id.get(tool_id)
                if not h:
                    continue

                total = int(sum(requested[tool_id].values()) or 0)
                if total <= 0:
                    continue

                before_stock = int(h.cantidad or 0)
                new_stock = before_stock - total

                # crear asignaciones por usuario (solo si qty>0)
                for uid in user_ids:
                    qty = int(requested[tool_id].get(uid) or 0)
                    if qty <= 0:
                        continue

                    to_user = users_by_id[uid]

                    a = HerramientaAsignacion.objects.create(
                        herramienta=h,
                        asignado_a=to_user,
                        asignado_por=request.user,
                        asignado_at=timezone.now(),
                        cantidad_entregada=qty,
                        active=True,
                        estado="pendiente",
                    )
                    created += 1

                    HerramientaAsignacionLog.objects.create(
                        asignacion=a,
                        accion="create",
                        by_user=request.user,
                        cambios={
                            "herramienta": str(h),
                            "asignado_a": (to_user.get_full_name() or to_user.username),
                            "cantidad_entregada": qty,
                            "stock": {"from": before_stock, "to": new_stock},
                            "massive": True,
                        },
                        nota="Asignación masiva",
                    )

                # descontar stock (una vez por herramienta)
                h.cantidad = new_stock
                if h.cantidad <= 0:
                    h.status = "asignada"
                h.updated_at = timezone.now()
                h.save(update_fields=["cantidad", "status", "updated_at"])

        messages.success(request, f"✅ Asignación masiva lista. Asignaciones creadas: {created}.")
        return redirect("logistica:herramientas_asignaciones_panel")

    except ValidationError as e:
        errores += 1
        messages.error(request, f"❌ {e}")
        return redirect(f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}")

    except Exception as e:
        errores += 1
        messages.error(request, f"❌ Error inesperado guardando asignación masiva: {e}")
        return redirect(f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}")
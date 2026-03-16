from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

from .forms_herramientas import (BodegaForm, HerramientaAsignarForm,
                                 HerramientaForm, InventarioReviewForm)
from .models import (Bodega, Herramienta, HerramientaAsignacion,
                     HerramientaInventario)


def _can_admin_logistica(user) -> bool:
    # Admin (superuser) siempre
    if getattr(user, "es_admin_general", False):
        return True
    # supervisor / pm / logistica
    if getattr(user, "es_supervisor", False) or getattr(user, "es_pm", False) or getattr(user, "es_logistica", False):
        return True
    return False


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def bodegas_manage(request):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tipo_id = request.GET.get("editar")
    edit = None

    if tipo_id:
        edit = get_object_or_404(Bodega, pk=tipo_id)
        form = BodegaForm(request.POST or None, instance=edit)
    else:
        form = BodegaForm(request.POST or None)

    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.creada_por = request.user
            obj.save()
            messages.success(request, "✅ Bodega guardada correctamente.")
            return redirect("logistica:bodegas_manage")
        messages.error(request, "❌ Revisa los campos de la bodega.")

    bodegas = Bodega.objects.all().order_by("nombre")
    can_delete_bodega = bool(getattr(request.user, "es_admin_general", False))

    return render(request, "logistica/admin_bodegas_manage.html", {
        "form": form,
        "bodegas": bodegas,
        "edit": edit,
        "can_delete_bodega": can_delete_bodega,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramientas_list(request):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    # ===== filtros + paginación =====
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    cantidad = (request.GET.get("cantidad") or "20").strip()
    if cantidad not in {"5", "10", "20", "50", "100"}:
        cantidad = "20"

    page_num = request.GET.get("page") or "1"

    qs = (
        Herramienta.objects
        .select_related("bodega", "creada_por")
        .order_by("-created_at")
    )

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q) |
            Q(serial__icontains=q) |
            Q(descripcion__icontains=q)
        )

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, int(cantidad))
    pagina = paginator.get_page(page_num)

    herramientas = list(pagina.object_list)
    tool_ids = [h.id for h in herramientas]

    # ===== asignación activa =====
    active_assignments = (
        HerramientaAsignacion.objects
        .filter(active=True, herramienta_id__in=tool_ids)
        .select_related("asignado_a", "asignado_por", "herramienta")
    )
    by_tool = {a.herramienta_id: a for a in active_assignments}

    # ===== último inventario (solo asignación activa) =====
    active_asig_ids = [a.id for a in active_assignments]
    latest_inv_by_tool = {}
    if active_asig_ids:
        invs = (
            HerramientaInventario.objects
            .filter(asignacion_id__in=active_asig_ids)
            .select_related(
                "revisado_por",
                "asignacion",
                "asignacion__asignado_a",
                "asignacion__asignado_por",
            )
            .order_by("-created_at", "-id")
        )
        for inv in invs:
            hid = inv.herramienta_id
            if hid not in latest_inv_by_tool:
                latest_inv_by_tool[hid] = inv

    # ===== pegar atributos al objeto =====
    for h in herramientas:
        a = by_tool.get(h.id)
        h.asignacion_activa = a
        h.asignada_a = getattr(a, "asignado_a", None) if a else None
        h.asignada_por = getattr(a, "asignado_por", None) if a else None
        h.asignada_at = getattr(a, "asignado_at", None) if a else None
        h.asignacion_estado = getattr(a, "estado", None) if a else None
        h.last_inv = latest_inv_by_tool.get(h.id)

    # reemplazamos el object_list con la lista “enriquecida”
    pagina.object_list = herramientas

    return render(request, "logistica/admin_herramientas_list.html", {
        "pagina": pagina,
        "herramientas": herramientas,  # por si lo usas en algún lado
        "q": q,
        "status": status,
        "cantidad": cantidad,
        "STATUS_CHOICES": Herramienta.STATUS_CHOICES,
        "can_delete": bool(getattr(request.user, "es_admin_general", False)),
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def exportar_herramientas_excel(request):
    """
    Exporta herramientas a Excel, respetando filtros (q, status).
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    qs = (
        Herramienta.objects
        .select_related("bodega", "creada_por")
        .order_by("-created_at")
    )

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q) |
            Q(serial__icontains=q) |
            Q(descripcion__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    herramientas = list(qs[:20000])  # límite duro por seguridad

    tool_ids = [h.id for h in herramientas]

    active_assignments = (
        HerramientaAsignacion.objects
        .filter(active=True, herramienta_id__in=tool_ids)
        .select_related("asignado_a", "asignado_por", "herramienta")
    )
    by_tool = {a.herramienta_id: a for a in active_assignments}

    active_asig_ids = [a.id for a in active_assignments]
    latest_inv_by_tool = {}
    if active_asig_ids:
        invs = (
            HerramientaInventario.objects
            .filter(asignacion_id__in=active_asig_ids)
            .order_by("-created_at", "-id")
        )
        for inv in invs:
            hid = inv.herramienta_id
            if hid not in latest_inv_by_tool:
                latest_inv_by_tool[hid] = inv

    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Herramientas"

    headers = [
        "Nombre",
        "Serial",
        "Descripción",
        "Valor comercial",
        "Bodega",
        "Estado herramienta",
        "Asignada a",
        "Asignación estado",
        "Asignada por",
        "Fecha asignación",
        "Inventario (último estado)",
        "Inventario (último enviado)",
        "Inventario (revisado por)",
        "Inventario (fecha revisión)",
        "Próx. fecha inventario",
        "Creada por",
        "Creada el",
    ]
    ws.append(headers)

    for h in herramientas:
        a = by_tool.get(h.id)
        last_inv = latest_inv_by_tool.get(h.id)

        asignada_a = ""
        asignada_por = ""
        asignada_at = ""
        asignacion_estado = ""
        if a:
            asignada_a = a.asignado_a.get_full_name() or a.asignado_a.username
            asignada_por = (a.asignado_por.get_full_name() if a.asignado_por else "") or (a.asignado_por.username if a and a.asignado_por else "")
            asignada_at = timezone.localtime(a.asignado_at).strftime("%d/%m/%Y %H:%M") if a.asignado_at else ""
            asignacion_estado = a.estado or ""

        inv_estado = ""
        inv_enviado = ""
        inv_revisado_por = ""
        inv_revisado_at = ""
        if last_inv:
            inv_estado = last_inv.estado or ""
            inv_enviado = timezone.localtime(last_inv.created_at).strftime("%d/%m/%Y %H:%M") if last_inv.created_at else ""
            if last_inv.revisado_por:
                inv_revisado_por = last_inv.revisado_por.get_full_name() or last_inv.revisado_por.username
            if last_inv.revisado_at:
                inv_revisado_at = timezone.localtime(last_inv.revisado_at).strftime("%d/%m/%Y %H:%M")

        creada_por = (h.creada_por.get_full_name() or h.creada_por.username) if h.creada_por else ""
        creada_el = timezone.localtime(h.created_at).strftime("%d/%m/%Y %H:%M") if h.created_at else ""
        prox_fecha = h.next_inventory_due.strftime("%d/%m/%Y") if h.next_inventory_due else ""

        ws.append([
            h.nombre or "",
            h.serial or "",
            h.descripcion or "",
            float(h.valor_comercial) if h.valor_comercial is not None else "",
            (h.bodega.nombre if h.bodega else ""),
            h.get_status_display() if hasattr(h, "get_status_display") else (h.status or ""),
            asignada_a,
            asignacion_estado,
            asignada_por,
            asignada_at,
            inv_estado,
            inv_enviado,
            inv_revisado_por,
            inv_revisado_at,
            prox_fecha,
            creada_por,
            creada_el,
        ])

    # anchos básicos
    widths = [28, 18, 40, 16, 18, 18, 22, 16, 22, 18, 20, 20, 20, 20, 18, 20, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    filename = f"herramientas_{timezone.localdate().strftime('%Y-%m-%d')}.xlsx"
    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(resp)
    return resp


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_create(request):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    if request.method == "POST":
        form = HerramientaForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.creada_por = request.user
            if not obj.next_inventory_due:
                obj.mark_inventory_due_default()
            obj.save()
            messages.success(request, "✅ Herramienta creada correctamente.")
            return redirect("logistica:herramientas_list")
        messages.error(request, "❌ Revisa los campos.")
    else:
        form = HerramientaForm()

    return render(request, "logistica/admin_herramienta_form.html", {
        "form": form,
        "mode": "create",
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_edit(request, tool_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    obj = get_object_or_404(Herramienta, pk=tool_id)

    if request.method == "POST":
        form = HerramientaForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.save()
            messages.success(request, "✅ Herramienta actualizada.")
            return redirect("logistica:herramientas_list")
        messages.error(request, "❌ Revisa los campos.")
    else:
        form = HerramientaForm(instance=obj)

    return render(request, "logistica/admin_herramienta_form.html", {
        "form": form,
        "mode": "edit",
        "obj": obj,
        "can_delete": bool(getattr(request.user, "es_admin_general", False)),
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_delete(request, tool_id: int):
    if not getattr(request.user, "es_admin_general", False):
        return HttpResponseForbidden("Solo admin general puede eliminar.")

    obj = get_object_or_404(Herramienta, pk=tool_id)

    if request.method == "POST":
        try:
            if obj.foto and obj.foto.name:
                obj.foto.delete(save=False)
        except Exception:
            pass

        invs = HerramientaInventario.objects.filter(herramienta=obj)
        for inv in invs:
            try:
                if inv.foto and inv.foto.name:
                    inv.foto.delete(save=False)
            except Exception:
                pass

        obj.delete()
        messages.success(request, "✅ Herramienta eliminada.")
        return redirect("logistica:herramientas_list")

    return render(request, "logistica/admin_herramienta_delete.html", {
        "obj": obj,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_assign(request, tool_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)
    user_qs = CustomUser.objects.filter(is_active=True).order_by("first_name", "last_name", "username")

    if request.method == "POST":
        form = HerramientaAsignarForm(request.POST, user_qs=user_qs)
        if form.is_valid():
            to_user = form.cleaned_data.get("asignado_a")  # puede ser None
            asignado_at = form.cleaned_data["asignado_at"]

            with transaction.atomic():
                prev = HerramientaAsignacion.objects.filter(herramienta=tool, active=True).first()
                if prev:
                    prev.active = False
                    prev.save(update_fields=["active"])

                if to_user:
                    # crear nueva asignación
                    HerramientaAsignacion.objects.create(
                        herramienta=tool,
                        asignado_a=to_user,
                        asignado_por=request.user,
                        asignado_at=asignado_at,
                        active=True,
                        estado="pendiente",
                    )

                    # ✅ si hay asignación, estado debe ser asignada
                    tool.status = "asignada"
                    tool.inventory_required = True  # se solicita inventario al nuevo usuario
                    tool.status_changed_at = timezone.now()
                    tool.status_changed_by = request.user
                    if not tool.next_inventory_due:
                        tool.mark_inventory_due_default()
                    tool.save(update_fields=[
                        "status",
                        "inventory_required",
                        "status_changed_at",
                        "status_changed_by",
                        "next_inventory_due",
                        "updated_at",
                    ])

                    messages.success(request, f"✅ Herramienta asignada a {to_user.get_full_name() or to_user.username}.")
                    return redirect("logistica:herramientas_list")

                # ✅ SIN ASIGNAR: queda en bodega
                tool.status = "bodega"
                tool.inventory_required = False  # si no hay responsable, no se solicita inventario
                tool.status_changed_at = timezone.now()
                tool.status_changed_by = request.user
                tool.save(update_fields=[
                    "status",
                    "inventory_required",
                    "status_changed_at",
                    "status_changed_by",
                    "updated_at",
                ])

                messages.success(request, "✅ Herramienta dejada sin asignar (en bodega).")
                return redirect("logistica:herramientas_list")

        messages.error(request, "❌ Revisa el formulario de asignación.")
    else:
        form = HerramientaAsignarForm(user_qs=user_qs)

    return render(request, "logistica/admin_herramienta_assign.html", {
        "tool": tool,
        "form": form,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_reset_assignment_status(request, tool_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)

    if request.method != "POST":
        raise Http404()

    a = HerramientaAsignacion.objects.filter(herramienta=tool, active=True).first()
    if not a:
        messages.error(request, "No hay asignación activa para reiniciar.")
        return redirect("logistica:herramientas_list")

    a.estado = "pendiente"
    a.comentario_rechazo = None
    a.rechazado_at = None
    a.aceptado_at = None
    a.save(update_fields=["estado", "comentario_rechazo", "rechazado_at", "aceptado_at"])

    messages.success(request, "✅ Estado de asignación reiniciado a Pendiente.")
    return redirect("logistica:herramientas_list")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramienta_change_status(request, tool_id: int):
    """
    Cambiar estado de herramienta.
    ✅ regla automática:
      - si se cambia a 'bodega' => se cierra asignación activa (si existe).
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)

    if request.method != "POST":
        raise Http404()

    new_status = (request.POST.get("status") or "").strip()
    just = (request.POST.get("justificacion") or "").strip()

    try:
        # si la mandan a bodega, cerrar asignación activa
        if new_status == "bodega":
            with transaction.atomic():
                a = HerramientaAsignacion.objects.filter(herramienta=tool, active=True).first()
                if a:
                    a.active = False
                    a.save(update_fields=["active"])

                tool.inventory_required = False  # sin responsable, no inventario
                tool.set_status(new_status, by_user=request.user, justification=just)
                tool.save(update_fields=[
                    "status",
                    "inventory_required",
                    "status_justificacion",
                    "status_changed_at",
                    "status_changed_by",
                    "updated_at",
                ])
        else:
            tool.set_status(new_status, by_user=request.user, justification=just)
            tool.save(update_fields=[
                "status",
                "status_justificacion",
                "status_changed_at",
                "status_changed_by",
                "updated_at",
            ])

        messages.success(request, "✅ Estado actualizado.")
    except ValidationError as e:
        messages.error(request, f"❌ {e}")

    return redirect("logistica:herramientas_list")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def solicitar_inventario(request, tool_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)
    if request.method != "POST":
        raise Http404()

    tool.inventory_required = True
    tool.save(update_fields=["inventory_required", "updated_at"])
    messages.success(request, "📸 Inventario solicitado. Se habilitó el botón al usuario.")
    return redirect("logistica:herramientas_list")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def aprobar_inventario(request, inv_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    inv = get_object_or_404(HerramientaInventario, pk=inv_id)
    if request.method != "POST":
        raise Http404()

    inv.approve(request.user)
    messages.success(request, "✅ Inventario aprobado.")

    nxt = (request.GET.get("next") or "").strip()
    if nxt:
        return redirect(nxt)
    return redirect("logistica:herramientas_list")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def rechazar_inventario(request, inv_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    inv = get_object_or_404(HerramientaInventario, pk=inv_id)
    nxt = (request.GET.get("next") or "").strip()

    if request.method == "POST":
        form = InventarioReviewForm(request.POST)
        if form.is_valid():
            inv.reject(request.user, form.cleaned_data["motivo_rechazo"])
            messages.warning(request, "❌ Inventario rechazado. Se re-habilitó el botón al usuario.")
            if nxt:
                return redirect(nxt)
            return redirect("logistica:herramientas_list")
        messages.error(request, "Debes indicar un motivo.")
    else:
        form = InventarioReviewForm()

    return render(request, "logistica/admin_inventario_rechazar.html", {
        "inv": inv,
        "form": form,
        "next": nxt,
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def inventario_historial_admin(request, tool_id: int):
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)

    inventarios = (
        HerramientaInventario.objects
        .filter(herramienta=tool)
        .select_related(
            "asignacion",
            "asignacion__asignado_a",
            "asignacion__asignado_por",
            "revisado_por",
        )
        .order_by("-created_at", "-id")
    )

    return render(request, "logistica/admin_inventario_historial.html", {
        "herramienta": tool,
        "inventarios": list(inventarios),
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def asignaciones_historial_admin(request, tool_id: int):
    """
    Historial completo de asignaciones (trazabilidad):
    - quién se asignó
    - quién asignó
    - cuándo se asignó
    - si quedó inactiva (reasignación o "dejar sin asignar")
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    tool = get_object_or_404(Herramienta, pk=tool_id)

    asignaciones = (
        HerramientaAsignacion.objects
        .filter(herramienta=tool)
        .select_related("asignado_a", "asignado_por")
        .order_by("-asignado_at", "-id")
    )

    return render(request, "logistica/admin_asignaciones_historial.html", {
        "herramienta": tool,
        "asignaciones": list(asignaciones),
    })


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def bodega_delete(request, bodega_id: int):
    if not getattr(request.user, "es_admin_general", False):
        return HttpResponseForbidden("Solo admin general puede eliminar bodegas.")

    if request.method != "POST":
        raise Http404()

    bodega = get_object_or_404(Bodega, pk=bodega_id)

    tools_count = Herramienta.objects.filter(bodega=bodega).count()
    if tools_count > 0:
        messages.error(
            request,
            f"❌ No puedes eliminar esta bodega porque tiene {tools_count} herramienta(s) asociada(s). "
            f"Primero cambia esas herramientas a otra bodega."
        )
        return redirect("logistica:bodegas_manage")

    try:
        bodega.delete()
        messages.success(request, "✅ Bodega eliminada correctamente.")
    except Exception as e:
        messages.error(request, f"❌ No se pudo eliminar la bodega: {e}")

    return redirect("logistica:bodegas_manage")
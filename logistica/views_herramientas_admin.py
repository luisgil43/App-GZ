from __future__ import annotations

from collections import defaultdict
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
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    cantidad = (request.GET.get("cantidad") or "20").strip()
    page_number = (request.GET.get("page") or "1").strip()

    try:
        per_page = int(cantidad)
    except Exception:
        per_page = 20

    if per_page not in (5, 10, 20, 50, 100):
        per_page = 20

    qs = (
        Herramienta.objects
        .select_related("bodega", "creada_por")
        .order_by("-created_at", "-id")
    )

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q) |
            Q(serial__icontains=q) |
            Q(descripcion__icontains=q)
        )

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, per_page)
    pagina = paginator.get_page(page_number)

    tool_ids = [h.id for h in pagina.object_list]

    # ===== asignaciones activas por herramienta (multi) =====
    active_count_by_tool = defaultdict(int)
    active_names_by_tool = defaultdict(list)

    if tool_ids:
        active_asigs = (
            HerramientaAsignacion.objects
            .filter(herramienta_id__in=tool_ids, active=True)
            .select_related("asignado_a")
            .order_by("-asignado_at", "-id")
        )
        for a in active_asigs:
            active_count_by_tool[a.herramienta_id] += 1

            # preview: solo guardar 3 nombres
            if len(active_names_by_tool[a.herramienta_id]) < 3:
                u = a.asignado_a
                name = (u.get_full_name() or u.username or "").strip()
                if name:
                    active_names_by_tool[a.herramienta_id].append(name)

    # ===== hidratar atributos en cada herramienta =====
    for h in pagina.object_list:
        h.active_asig_count = int(active_count_by_tool.get(h.id, 0) or 0)
        preview = active_names_by_tool.get(h.id, [])
        h.active_assignees_preview = ", ".join(preview) if preview else ""

    return render(request, "logistica/admin_herramientas_list.html", {
        "pagina": pagina,
        "q": q,
        "status": status,
        "cantidad": str(per_page),
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

    return render(request, "logistica/admin_inventario_historial_asignacion.html", {
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

@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramientas_importar_plantilla(request):
    """
    Descarga plantilla Excel para importar herramientas con cantidad.
    Columnas:
      nombre, serial(opcional), cantidad, valor_comercial, bodega, descripcion, status
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Herramientas"

    headers = ["nombre", "serial", "cantidad", "valor_comercial", "bodega", "descripcion", "status"]
    ws.append(headers)

    # ejemplo (serial puede ir vacío)
    ws.append(["Taladro percutor", "SN-AX12-8890", 3, 500000, "Bodega Central", "Marca Bosch, con maletín", "bodega"])
    ws.append(["Guantes", "", 20, "", "Bodega Central", "Guantes talla M", "bodega"])

    widths = [24, 22, 10, 16, 22, 40, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    filename = f"plantilla_importar_herramientas_{timezone.localdate().strftime('%Y-%m-%d')}.xlsx"
    resp = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(resp)
    return resp

@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramientas_importar(request):
    """
    Importa herramientas desde Excel con PREVIEW + confirmación.
    - Permite serial vacío: se genera AUTO-XXXX
    - En preview muestra coincidencias (serial) y bodegas no encontradas
    - En confirmación el usuario decide:
        - modo = reemplazar  -> cantidad queda igual al Excel
        - modo = sumar       -> cantidad se suma al stock actual
    - Permite corregir bodega por fila desde un dropdown en el preview
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("No tienes permiso.")

    if request.method != "POST":
        raise Http404()

    from decimal import Decimal
    from uuid import uuid4

    # ===== confirmación (2do POST) =====
    confirmar = (request.POST.get("confirmar") or "").strip()
    if confirmar == "1":
        token = (request.POST.get("token") or "").strip()
        modo = (request.POST.get("modo") or "reemplazar").strip()
        if modo not in {"reemplazar", "sumar"}:
            modo = "reemplazar"

        session_key = f"logistica_import_tools:{token}"
        payload = request.session.get(session_key)
        if not payload:
            messages.error(request, "❌ La sesión de importación expiró. Vuelve a subir el archivo.")
            return redirect("logistica:herramientas_list")

        rows = payload.get("rows") or []
        if not rows:
            messages.error(request, "❌ No hay filas para importar.")
            return redirect("logistica:herramientas_list")

        # bodegas válidas por id (para selección)
        bodegas_by_id = {str(b.id): b for b in Bodega.objects.all()}

        created = 0
        updated = 0
        errores = 0

        with transaction.atomic():
            for i, r in enumerate(rows):
                try:
                    nombre = (r.get("nombre") or "").strip()
                    serial = (r.get("serial") or "").strip()
                    if not nombre or not serial:
                        continue

                    cantidad = int(r.get("cantidad") or 1)
                    if cantidad <= 0:
                        cantidad = 1

                    valor = r.get("valor_comercial")
                    try:
                        valor = Decimal(str(valor)) if valor not in (None, "") else Decimal("0")
                    except Exception:
                        valor = Decimal("0")
                    if valor < 0:
                        valor = Decimal("0")

                    descripcion = (r.get("descripcion") or "").strip() or None
                    status = (r.get("status") or "").strip() or None
                    if status and status not in dict(Herramienta.STATUS_CHOICES):
                        status = None

                    # ✅ bodega seleccionada en preview
                    bodega_sel = (request.POST.get(f"bodega_sel_{i}") or "").strip()
                    bodega = bodegas_by_id.get(bodega_sel) if bodega_sel else None

                    obj = Herramienta.objects.filter(serial=serial).first()
                    if obj:
                        obj.nombre = nombre
                        obj.valor_comercial = valor
                        obj.descripcion = descripcion
                        if bodega is not None:
                            obj.bodega = bodega
                        if status:
                            obj.status = status

                        if modo == "sumar":
                            obj.cantidad = int(obj.cantidad or 0) + cantidad
                        else:
                            obj.cantidad = cantidad

                        obj.save()
                        updated += 1
                    else:
                        obj = Herramienta.objects.create(
                            nombre=nombre,
                            serial=serial,
                            cantidad=cantidad,
                            valor_comercial=valor,
                            descripcion=descripcion,
                            bodega=bodega,
                            status=status or "bodega",
                            creada_por=request.user,
                        )
                        if not obj.next_inventory_due:
                            obj.mark_inventory_due_default()
                            obj.save(update_fields=["next_inventory_due", "updated_at"])
                        created += 1

                except Exception:
                    errores += 1
                    continue

        # limpiar session
        try:
            del request.session[session_key]
        except Exception:
            pass

        messages.success(
            request,
            f"✅ Importación lista ({modo}). Creadas: {created} • Actualizadas: {updated} • Errores: {errores}"
        )
        return redirect("logistica:herramientas_list")

    # ===== preview (1er POST) =====
    f = request.FILES.get("archivo")
    if not f:
        messages.error(request, "❌ Debes seleccionar un archivo.")
        return redirect("logistica:herramientas_list")

    name = (getattr(f, "name", "") or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".xlsm")):
        messages.error(request, "❌ El archivo debe ser .xlsx")
        return redirect("logistica:herramientas_list")

    try:
        from openpyxl import load_workbook
        wb = load_workbook(f, data_only=True)
        ws = wb.active
    except Exception as e:
        messages.error(request, f"❌ No se pudo leer el Excel: {e}")
        return redirect("logistica:herramientas_list")

    rows_xl = list(ws.iter_rows(values_only=True))
    if not rows_xl:
        messages.error(request, "❌ El Excel viene vacío.")
        return redirect("logistica:herramientas_list")

    header = [str(x or "").strip().lower() for x in rows_xl[0]]
    required = {"nombre", "cantidad"}  # ✅ serial ahora es opcional
    if not required.issubset(set(header)):
        messages.error(request, "❌ La plantilla no corresponde. Debe incluir al menos: nombre, cantidad.")
        return redirect("logistica:herramientas_list")

    idx = {col: header.index(col) for col in header if col}

    def get_cell(r, col):
        i = idx.get(col)
        if i is None:
            return None
        return r[i] if i < len(r) else None

    # bodegas por nombre (match por texto)
    bodegas_by_name = {b.nombre.strip().lower(): b for b in Bodega.objects.all()}

    # para detectar coincidencias por serial
    existing_by_serial = {h.serial: h for h in Herramienta.objects.filter(serial__isnull=False).exclude(serial="")}

    preview_rows = []
    bodegas_faltantes = 0
    coincidencias = 0

    def gen_serial_unico() -> str:
        for _ in range(40):
            s = f"AUTO-{uuid4().hex[:10].upper()}"
            if not Herramienta.objects.filter(serial=s).exists():
                return s
        return f"AUTO-{uuid4().hex[:10].upper()}"

    for r in rows_xl[1:]:
        try:
            nombre = str(get_cell(r, "nombre") or "").strip()
            if not nombre:
                continue

            serial = str(get_cell(r, "serial") or "").strip()
            if not serial:
                serial = gen_serial_unico()

            # cantidad
            c_raw = get_cell(r, "cantidad")
            try:
                cantidad = int(c_raw) if c_raw is not None and str(c_raw).strip() != "" else 1
            except Exception:
                cantidad = 1
            if cantidad <= 0:
                cantidad = 1

            # valor
            v_raw = get_cell(r, "valor_comercial")
            try:
                valor = str(v_raw).strip() if v_raw is not None and str(v_raw).strip() != "" else "0"
            except Exception:
                valor = "0"

            bodega_name = str(get_cell(r, "bodega") or "").strip()
            bodega = None
            bodega_ok = True
            if bodega_name:
                bodega = bodegas_by_name.get(bodega_name.lower())
                if not bodega:
                    bodega_ok = False
                    bodegas_faltantes += 1
            else:
                # si viene vacío, lo marcamos como "sin bodega" pero no es error
                bodega_ok = True

            descripcion = str(get_cell(r, "descripcion") or "").strip() or None
            status = str(get_cell(r, "status") or "").strip() or ""
            if status and status not in dict(Herramienta.STATUS_CHOICES):
                status = ""

            existe = existing_by_serial.get(serial)
            if existe:
                coincidencias += 1

            preview_rows.append({
                "nombre": nombre,
                "serial": serial,
                "cantidad": cantidad,
                "valor_comercial": valor,
                "bodega_excel": bodega_name,
                "bodega_id_detectada": str(bodega.id) if bodega else "",
                "bodega_ok": bodega_ok,
                "descripcion": descripcion or "",
                "status": status,
                "existe": bool(existe),
                "existe_cantidad": int(existe.cantidad) if existe else 0,
                "existe_bodega": (existe.bodega.nombre if (existe and existe.bodega) else ""),
            })

        except Exception:
            continue

    if not preview_rows:
        messages.warning(request, "⚠️ No se detectaron filas válidas para importar.")
        return redirect("logistica:herramientas_list")

    token = uuid4().hex
    session_key = f"logistica_import_tools:{token}"
    request.session[session_key] = {"rows": preview_rows}
    request.session.modified = True

    bodegas = list(Bodega.objects.all().order_by("nombre"))

    return render(request, "logistica/admin_herramientas_import_preview.html", {
        "token": token,
        "preview_rows": preview_rows,
        "bodegas": bodegas,
        "coincidencias": coincidencias,
        "bodegas_faltantes": bodegas_faltantes,
    })
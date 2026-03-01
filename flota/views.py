# flota/views.py
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import (VehicleAssignmentForm, VehicleForm,
                    VehicleNotificationSettingsForm, VehicleServiceForm,
                    VehicleServiceTypeForm, VehicleStatusForm)
from .models import (Vehicle, VehicleAssignment, VehicleNotificationSettings,
                     VehicleService, VehicleServiceType, VehicleStatus)


@login_required
def vehicle_list(request):
    cantidad_param = request.GET.get("cantidad", "10")

    if cantidad_param == "todos":
        page_size = 100
    else:
        try:
            page_size = max(5, min(int(cantidad_param), 100))
        except ValueError:
            page_size = 10
            cantidad_param = "10"

    vehicles = (
        Vehicle.objects.all()
        .select_related("status")
        .order_by("-id")
    )

    paginator = Paginator(vehicles, page_size)
    page_number = request.GET.get("page")
    pagina = paginator.get_page(page_number)

    statuses = VehicleStatus.objects.filter(is_active=True).order_by("status_code")

    return render(
        request,
        "flota/vehicle_list.html",
        {
            "pagina": pagina,
            "cantidad": cantidad_param,
            "statuses": statuses,
            "excel_filters": request.GET.get("excel_filters", ""),
        },
    )


@login_required
def vehicle_create(request):
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                messages.success(request, "Vehículo creado correctamente.")
                return redirect("flota:vehicle_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = VehicleForm()

    return render(request, "flota/vehicle_form.html", {"form": form, "mode": "create"})


@login_required
def vehicle_edit(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST":
        form = VehicleForm(request.POST, instance=vehicle)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                messages.success(request, "Vehículo actualizado.")
                return redirect("flota:vehicle_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = VehicleForm(instance=vehicle)

    return render(request, "flota/vehicle_form.html", {"form": form, "mode": "edit", "vehicle": vehicle})


@login_required
@require_POST
def vehicle_delete(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    vehicle.delete()
    messages.success(request, "Vehículo eliminado.")
    return redirect("flota:vehicle_list")


@login_required
@require_POST
def vehicle_change_status(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    status_id = request.POST.get("status_id")

    if not status_id:
        messages.error(request, "Debes seleccionar un status.")
        return redirect("flota:vehicle_list")

    st = get_object_or_404(VehicleStatus, pk=status_id, is_active=True)
    vehicle.status = st
    vehicle.save(update_fields=["status", "updated_at"])
    messages.success(request, "Status actualizado.")
    return redirect("flota:vehicle_list")


@login_required
def status_manage(request, pk=None):
    statuses = VehicleStatus.objects.all().order_by("-created_at")

    editing_status = None
    if pk is not None:
        editing_status = get_object_or_404(VehicleStatus, pk=pk)

    if request.method == "POST":
        form = VehicleStatusForm(request.POST, instance=editing_status)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                if editing_status:
                    messages.success(request, "Status actualizado.")
                    return redirect("flota:status_manage")
                messages.success(request, "Status creado.")
                return redirect("flota:status_manage")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = VehicleStatusForm(instance=editing_status)

    return render(
        request,
        "flota/status_form.html",
        {
            "form": form,
            "statuses": statuses,
            "editing_status": editing_status,
        },
    )


@login_required
@require_POST
def status_toggle_active(request, pk):
    st = get_object_or_404(VehicleStatus, pk=pk)
    st.is_active = not st.is_active
    st.save(update_fields=["is_active"])
    messages.success(request, f"Status '{st.name}' actualizado.")
    return redirect("flota:status_manage")


@login_required
@require_POST
def status_delete(request, pk):
    st = get_object_or_404(VehicleStatus, pk=pk)
    try:
        st.delete()
        messages.success(request, "Status eliminado.")
    except Exception:
        messages.error(request, "No se puede eliminar este status porque está en uso por uno o más vehículos.")
    return redirect("flota:status_manage")


@login_required
def assignment_list(request):
    assignments = VehicleAssignment.objects.select_related("vehicle", "user").order_by("-assigned_at")
    return render(request, "flota/assignment_list.html", {"assignments": assignments})


@login_required
def assignment_create(request):
    from django.db import IntegrityError, transaction

    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    vehicle = form.cleaned_data.get("vehicle")

                    if vehicle:
                        conflict = (
                            VehicleAssignment.objects
                            .select_for_update()
                            .filter(vehicle=vehicle, active=True)
                            .first()
                        )
                        if conflict:
                            conflict_user = (
                                conflict.user.get_full_name()
                                if hasattr(conflict.user, "get_full_name") and conflict.user.get_full_name()
                                else conflict.user.username
                            )
                            form.add_error(
                                "vehicle",
                                (
                                    f"Este vehículo ya tiene una asignación activa con {conflict_user}. "
                                    f"Primero pausa/cierra esa asignación."
                                )
                            )
                            raise ValidationError("Conflicto de asignación activa.")

                    form.save()

                messages.success(request, "Asignación creada.")
                return redirect("flota:assignment_list")

            except ValidationError:
                pass
            except IntegrityError:
                form.add_error(
                    "vehicle",
                    "No se pudo crear la asignación porque ese vehículo ya tiene una asignación activa."
                )
    else:
        form = VehicleAssignmentForm()

    return render(request, "flota/assignment_form.html", {"form": form, "mode": "create"})


@login_required
def assignment_edit(request, pk):
    from django.db import IntegrityError, transaction

    a = get_object_or_404(VehicleAssignment, pk=pk)

    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST, instance=a)
        if form.is_valid():
            try:
                with transaction.atomic():
                    a_locked = (
                        VehicleAssignment.objects
                        .select_for_update()
                        .select_related("vehicle", "user")
                        .get(pk=a.pk)
                    )

                    vehicle = form.cleaned_data.get("vehicle")

                    if vehicle:
                        conflict = (
                            VehicleAssignment.objects
                            .select_for_update()
                            .filter(vehicle=vehicle, active=True)
                            .exclude(pk=a_locked.pk)
                            .first()
                        )
                        if conflict:
                            conflict_user = (
                                conflict.user.get_full_name()
                                if hasattr(conflict.user, "get_full_name") and conflict.user.get_full_name()
                                else conflict.user.username
                            )
                            form.add_error(
                                "vehicle",
                                (
                                    f"Este vehículo ya tiene una asignación activa con {conflict_user}. "
                                    f"Primero pausa/cierra esa asignación."
                                )
                            )
                            raise ValidationError("Conflicto de asignación activa.")

                    form.save()

                messages.success(request, "Asignación actualizada.")
                return redirect("flota:assignment_list")

            except ValidationError:
                pass
            except IntegrityError:
                form.add_error(
                    "vehicle",
                    "No se pudo actualizar porque ese vehículo ya tiene otra asignación activa."
                )
    else:
        form = VehicleAssignmentForm(instance=a)

    return render(request, "flota/assignment_form.html", {"form": form, "mode": "edit", "assignment": a})


@login_required
@require_POST
def assignment_close(request, pk):
    a = get_object_or_404(VehicleAssignment, pk=pk, active=True)
    a.active = False
    a.unassigned_at = a.unassigned_at or timezone.now()
    a.save(update_fields=["active", "unassigned_at"])
    messages.success(request, "Asignación cerrada.")
    return redirect("flota:assignment_list")


@login_required
@require_POST
def assignment_toggle_active(request, pk):
    from django.db import IntegrityError, transaction

    try:
        with transaction.atomic():
            a = (
                VehicleAssignment.objects
                .select_for_update()
                .select_related("vehicle", "user")
                .get(pk=pk)
            )

            if a.active:
                a.active = False
                a.unassigned_at = a.unassigned_at or timezone.now()
                a.save(update_fields=["active", "unassigned_at"])
                messages.success(request, "Asignación pausada (cerrada).")
                return redirect("flota:assignment_list")

            conflict = (
                VehicleAssignment.objects
                .select_for_update()
                .filter(vehicle=a.vehicle, active=True)
                .exclude(pk=a.pk)
                .select_related("user")
                .first()
            )

            if conflict:
                conflict_user = (
                    conflict.user.get_full_name()
                    if hasattr(conflict.user, "get_full_name") and conflict.user.get_full_name()
                    else conflict.user.username
                )
                messages.error(
                    request,
                    (
                        f"No se puede reactivar esta asignación porque el vehículo "
                        f"{a.vehicle} ya tiene una asignación activa con {conflict_user}. "
                        f"Primero pausa/cierra esa asignación."
                    )
                )
                return redirect("flota:assignment_list")

            a.active = True
            a.unassigned_at = None
            a.save(update_fields=["active", "unassigned_at"])
            messages.success(request, "Asignación reactivada.")

    except VehicleAssignment.DoesNotExist:
        messages.error(request, "La asignación no existe.")
    except IntegrityError:
        messages.error(
            request,
            "No se pudo reactivar la asignación porque ya existe otra asignación activa para ese vehículo."
        )

    return redirect("flota:assignment_list")


@login_required
@require_POST
def assignment_delete(request, pk):
    a = get_object_or_404(VehicleAssignment, pk=pk)
    a.delete()
    messages.success(request, "Asignación eliminada.")
    return redirect("flota:assignment_list")


# ---------------------------
# TIPOS DE SERVICIO (NUEVO)
# ---------------------------

@login_required
def service_type_manage(request, pk=None):
    types = VehicleServiceType.objects.all().order_by("-created_at")

    editing_type = None
    if pk is not None:
        editing_type = get_object_or_404(VehicleServiceType, pk=pk)

    if request.method == "POST":
        form = VehicleServiceTypeForm(request.POST, instance=editing_type)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                if editing_type:
                    messages.success(request, "Tipo de servicio actualizado.")
                else:
                    messages.success(request, "Tipo de servicio creado.")
                return redirect("flota:service_type_manage")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = VehicleServiceTypeForm(instance=editing_type)

    return render(
        request,
        "flota/service_type_form.html",
        {
            "form": form,
            "types": types,
            "editing_type": editing_type,
        },
    )


@login_required
@require_POST
def service_type_toggle_active(request, pk):
    t = get_object_or_404(VehicleServiceType, pk=pk)
    t.is_active = not t.is_active
    t.save(update_fields=["is_active"])
    messages.success(request, f"Tipo '{t.name}' actualizado.")
    return redirect("flota:service_type_manage")


@login_required
@require_POST
def service_type_delete(request, pk):
    t = get_object_or_404(VehicleServiceType, pk=pk)
    try:
        t.delete()
        messages.success(request, "Tipo de servicio eliminado.")
    except Exception:
        messages.error(request, "No se puede eliminar este tipo porque está en uso por uno o más servicios.")
    return redirect("flota:service_type_manage")


def _is_fuel_service(service: VehicleService) -> bool:
    """
    Detecta combustible tanto por el nuevo tipo (service_type_obj)
    como por el legacy (service_type).
    """
    try:
        st_obj = getattr(service, "service_type_obj", None)
        if st_obj and (getattr(st_obj, "name", "") or "").strip().lower() == "combustible":
            return True
    except Exception:
        pass

    return (getattr(service, "service_type", "") or "").strip().lower() == "combustible"


def _fuel_prev_event(vehicle_id, service_date, created_at, pk, exclude_pk=None):
    """
    Devuelve el evento de combustible inmediatamente anterior según sortkey:
    (service_date, created_at, pk)
    Soporta ambos tipos: por service_type_obj.name == combustible o legacy.
    """
    qs = VehicleService.objects.filter(vehicle_id=vehicle_id).filter(
        Q(service_type__iexact="combustible") |
        Q(service_type_obj__name__iexact="combustible")
    )

    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    prev_q = (
        Q(service_date__lt=service_date)
        | Q(service_date=service_date, created_at__lt=created_at)
        | Q(service_date=service_date, created_at=created_at, pk__lt=pk)
    )

    return qs.filter(prev_q).order_by("-service_date", "-created_at", "-pk").first()


def _fuel_feedback_messages(request, fuel_service: VehicleService):
    """
    Opción B (sin litros):
    - Calcula KM recorridos desde la carga anterior (delta)
    - Lo compara con el delta anterior y muestra mensaje (más/menos/igual)
    """
    if fuel_service.kilometraje_declarado is None:
        return

    km_now = int(fuel_service.kilometraje_declarado)
    vehicle_id = fuel_service.vehicle_id

    prev = _fuel_prev_event(
        vehicle_id=vehicle_id,
        service_date=fuel_service.service_date,
        created_at=fuel_service.created_at,
        pk=fuel_service.pk,
        exclude_pk=fuel_service.pk,
    )

    if not prev or prev.kilometraje_declarado is None:
        messages.info(
            request,
            f"⛽ Combustible registrado. Primer registro comparable: KM {km_now:,}".replace(",", ".")
        )
        return

    km_prev = int(prev.kilometraje_declarado)
    delta_now = km_now - km_prev

    if delta_now <= 0:
        messages.info(
            request,
            "⛽ Combustible registrado. (No se pudo calcular el tramo porque este registro queda antes o igual al anterior)."
        )
        return

    prev_prev = _fuel_prev_event(
        vehicle_id=vehicle_id,
        service_date=prev.service_date,
        created_at=prev.created_at,
        pk=prev.pk,
        exclude_pk=fuel_service.pk,
    )

    if not prev_prev or prev_prev.kilometraje_declarado is None:
        messages.info(
            request,
            f"⛽ Combustible registrado. Recorriste {delta_now:,} km desde la última carga."
            .replace(",", ".")
        )
        return

    km_prev_prev = int(prev_prev.kilometraje_declarado)
    delta_prev = km_prev - km_prev_prev

    if delta_prev <= 0:
        messages.info(
            request,
            f"⛽ Combustible registrado. Recorriste {delta_now:,} km desde la última carga."
            .replace(",", ".")
        )
        return

    if delta_now > delta_prev:
        diff = delta_now - delta_prev
        pct = (Decimal(delta_now) / Decimal(delta_prev) - Decimal("1")) * Decimal("100")
        messages.success(
            request,
            (
                f"✅ ¡Buenísimo! Esta vez recorriste {delta_now:,} km desde la última carga "
                f"(+{diff:,} km, {pct.quantize(Decimal('1'))}% más que la vez anterior). "
                f"Sigue así 👏"
            ).replace(",", ".")
        )
    elif delta_now < delta_prev:
        diff = delta_prev - delta_now
        pct = (Decimal(delta_now) / Decimal(delta_prev) - Decimal("1")) * Decimal("100")
        messages.warning(
            request,
            (
                f"⚠️ Ojo: esta vez recorriste {delta_now:,} km desde la última carga "
                f"(-{diff:,} km, {abs(pct).quantize(Decimal('1'))}% menos que la vez anterior). "
                f"Tip: evita aceleradas bruscas y mantén velocidad constante."
            ).replace(",", ".")
        )
    else:
        messages.info(
            request,
            f"⛽ Combustible registrado. Recorriste {delta_now:,} km, igual que la vez anterior."
            .replace(",", ".")
        )


# ---------------------------
# SERVICIOS (LIST/CREATE/EDIT/DELETE)
# ---------------------------
@login_required
def service_list(request):
    services = (
        VehicleService.objects
        .select_related("vehicle", "service_type_obj")
        .order_by("vehicle_id", "-service_date", "-created_at")
    )

    grouped = {}
    type_names_by_vehicle = defaultdict(set)

    for s in services:
        v = s.vehicle

        if v.id not in grouped:
            grouped[v.id] = {
                "vehicle": v,
                "types": defaultdict(list),
                "last_service_date": None,
                "last_service_label": "—",
            }

        type_name = s.service_type_obj.name if s.service_type_obj_id else s.get_service_type_display()

        grouped[v.id]["types"][type_name].append(s)
        type_names_by_vehicle[v.id].add(type_name)

        if grouped[v.id]["last_service_date"] is None:
            grouped[v.id]["last_service_date"] = s.service_date
            grouped[v.id]["last_service_label"] = f"{type_name} · {s.service_date.strftime('%d-%m-%Y')}"

    vehicles = Vehicle.objects.all().order_by("-id")
    for v in vehicles:
        if v.id not in grouped:
            grouped[v.id] = {
                "vehicle": v,
                "types": defaultdict(list),
                "last_service_date": None,
                "last_service_label": "—",
            }

    for vid in list(grouped.keys()):
        grouped[vid]["types"] = dict(grouped[vid]["types"])

    return render(
        request,
        "flota/service_list.html",
        {
            "grouped": grouped,
        },
    )


@login_required
def service_create(request):
    if request.method == "POST":
        form = VehicleServiceForm(request.POST)
        if form.is_valid():
            obj = form.save()

            if _is_fuel_service(obj):
                _fuel_feedback_messages(request, obj)
            else:
                messages.success(request, "✅ Servicio registrado.")

            return redirect("flota:service_list")
    else:
        form = VehicleServiceForm()

    return render(request, "flota/service_form.html", {"form": form, "mode": "create"})


@login_required
def service_edit(request, pk):
    obj = get_object_or_404(VehicleService, pk=pk)

    if request.method == "POST":
        form = VehicleServiceForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save()

            if _is_fuel_service(obj):
                _fuel_feedback_messages(request, obj)
            else:
                messages.success(request, "✅ Servicio actualizado.")

            return redirect("flota:service_list")
    else:
        form = VehicleServiceForm(instance=obj)

    return render(request, "flota/service_form.html", {"form": form, "mode": "edit"})


@login_required
@require_POST
def service_delete(request, pk):
    s = get_object_or_404(VehicleService, pk=pk)
    s.delete()
    messages.success(request, "Servicio eliminado.")
    return redirect("flota:service_list")


# ==========================================================
# ✅ NUEVO: NOTIFICACIONES (CONFIG POR VEHÍCULO)
# ==========================================================

@login_required
def notification_list(request):
    vehicles = (
        Vehicle.objects
        .all()
        .select_related("status")
        .order_by("-id")
    )

    settings_by_vehicle = {
        s.vehicle_id: s
        for s in VehicleNotificationSettings.objects.select_related("vehicle").all()
    }

    rows = []
    for v in vehicles:
        cfg = settings_by_vehicle.get(v.id)
        rows.append({
            "vehicle": v,
            "cfg": cfg,
        })

    return render(request, "flota/notification_list.html", {"rows": rows})


@login_required
def notification_edit(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)

    cfg, _created = VehicleNotificationSettings.objects.get_or_create(vehicle=vehicle)

    if request.method == "POST":
        form = VehicleNotificationSettingsForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            messages.success(request, "Notificaciones actualizadas.")
            return redirect("flota:notification_list")
    else:
        form = VehicleNotificationSettingsForm(instance=cfg)

    assigned_email = cfg.get_assigned_driver_email() if cfg else None

    return render(
        request,
        "flota/notification_form.html",
        {
            "vehicle": vehicle,
            "form": form,
            "assigned_email": assigned_email,
        },
    )
from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (Vehicle, VehicleAssignment, VehicleService,
                     VehicleServiceType, VehicleStatus)

# ---------------------------
# VEHICLE
# ---------------------------

class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "nombre_fantasia",
            "marca",
            "modelo",
            "patente",
            "serial",
            "kilometraje_actual",
            "fecha_compra",
            "tipo_compra",
            "monto_compra",
            "fecha_fin_credito",
            "fecha_ultima_revision_tecnica",
            "fecha_permiso_circulacion",
        ]
        labels = {
            "nombre_fantasia": "Nombre fantasía",
            "marca": "Marca",
            "modelo": "Modelo",
            "patente": "Patente",
            "serial": "Serial",
            "kilometraje_actual": "Kilometraje actual",
            "fecha_compra": "Fecha compra",
            "tipo_compra": "Tipo compra",
            "monto_compra": "Monto compra",
            "fecha_fin_credito": "Fecha fin crédito",
            "fecha_ultima_revision_tecnica": "Fecha última revisión técnica",
            "fecha_permiso_circulacion": "Fecha permiso circulación",
        }
        widgets = {
            "fecha_compra": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_fin_credito": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_ultima_revision_tecnica": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_permiso_circulacion": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for f in (
            "fecha_compra",
            "fecha_fin_credito",
            "fecha_ultima_revision_tecnica",
            "fecha_permiso_circulacion",
        ):
            if f in self.fields:
                self.fields[f].input_formats = ["%Y-%m-%d"]

        if "monto_compra" in self.fields:
            self.fields["monto_compra"].widget.attrs.update({"min": "0", "step": "1"})

    def clean_monto_compra(self):
        monto = self.cleaned_data.get("monto_compra")
        if monto is None:
            return monto
        if monto < 0:
            raise ValidationError("El monto de compra no puede ser negativo.")
        return monto

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo_compra")
        fin = cleaned.get("fecha_fin_credito")

        if tipo in ("credito", "leasing") and not fin:
            raise ValidationError("Si es Crédito/Leasing, debes indicar la fecha de finalización.")
        if tipo == "contado":
            cleaned["fecha_fin_credito"] = None

        return cleaned


# ---------------------------
# STATUS
# ---------------------------

class VehicleStatusForm(forms.ModelForm):
    COLOR_CHOICES = [
        ("blue", "Azul"),
        ("green", "Verde"),
        ("red", "Rojo"),
        ("yellow", "Amarillo"),
        ("gray", "Gris"),
    ]

    color = forms.ChoiceField(
        choices=COLOR_CHOICES,
        label="Color",
        help_text="Color del badge en la lista de vehículos.",
        widget=forms.Select(),
    )

    class Meta:
        model = VehicleStatus
        fields = ["name", "color", "is_active"]
        labels = {"name": "Nombre", "is_active": "Activo"}
        help_texts = {"name": "Ej: Activo, En taller, En reparación, Estacionamiento."}

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Debes ingresar un nombre.")
        return name


# ---------------------------
# ASSIGNMENT
# ---------------------------

class VehicleAssignmentForm(forms.ModelForm):
    assigned_at = forms.DateTimeField(
        label="Fecha asignación",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    class Meta:
        model = VehicleAssignment
        fields = ["vehicle", "user", "assigned_at"]
        labels = {"vehicle": "Vehículo", "user": "Asignar a"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        inst = getattr(self, "instance", None)
        if inst and inst.pk and inst.assigned_at:
            dt = inst.assigned_at
            if timezone.is_aware(dt):
                dt = timezone.localtime(dt)
            self.initial["assigned_at"] = dt.strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        vehicle = cleaned.get("vehicle")

        if vehicle:
            qs = VehicleAssignment.objects.filter(vehicle=vehicle, active=True)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise ValidationError({
                    "vehicle": (
                        "Este vehículo ya está asignado a una persona. "
                        "Si quieres asignarlo nuevamente, primero debes pausar (cerrar) la asignación actual."
                    )
                })

        return cleaned


# ---------------------------
# SERVICE TYPE
# ---------------------------

class VehicleServiceTypeForm(forms.ModelForm):
    COLOR_CHOICES = [
        ("blue", "Azul"),
        ("green", "Verde"),
        ("red", "Rojo"),
        ("yellow", "Amarillo"),
        ("gray", "Gris"),
    ]

    color = forms.ChoiceField(choices=COLOR_CHOICES, label="Color", widget=forms.Select())

    # ✅ Nuevo: checkbox de control (solo formulario)
    requires_frequency = forms.BooleanField(
        required=False,
        initial=True,
        label="Este tipo de servicio requiere frecuencia",
        help_text="Márcalo si este servicio debe llevar control por KM y/o días (ej: aceite, revisión). "
                  "Desmárcalo para servicios eventuales (ej: reparación puntual).",
    )

    class Meta:
        model = VehicleServiceType
        fields = [
            "name",
            "is_active",

            # ✅ form-only (NO está en el modelo, pero sí se renderiza)
            "requires_frequency",

            "interval_km",
            "interval_days",

            # ✅ legacy (uno solo)
            "alert_before_km",
            "alert_before_days",

            # ✅ nuevos (múltiples)
            "alert_before_km_steps",
            "alert_before_days_steps",
            "notify_on_overdue",

            "color",
        ]
        labels = {
            "name": "Nombre",
            "is_active": "Activo",
            "interval_km": "Frecuencia (KM)",
            "interval_days": "Frecuencia (días)",
            "alert_before_km": "Avisar antes (KM) [uno]",
            "alert_before_days": "Avisar antes (días) [uno]",
            "alert_before_km_steps": "Avisar antes (KM) [múltiples]",
            "alert_before_days_steps": "Avisar antes (días) [múltiples]",
            "notify_on_overdue": "Avisar cuando esté vencido",
        }
        help_texts = {
            "interval_km": "Ej: 5000 (cambio de aceite).",
            "interval_days": "Ej: 180 (cada 6 meses).",
            "alert_before_km": "Ej: 100 (un aviso único cuando falten 100km).",
            "alert_before_days": "Ej: 7 (un aviso único cuando falten 7 días).",
            "alert_before_km_steps": "Opcional. Varios valores separados por coma. Ej: 1000,500,100",
            "alert_before_days_steps": "Opcional. Varios valores separados por coma. Ej: 10,7,1",
            "notify_on_overdue": "Si está activo, también avisará cuando ya esté vencido.",
        }
        widgets = {
            "alert_before_km_steps": forms.TextInput(attrs={"placeholder": "Ej: 1000,500,100"}),
            "alert_before_days_steps": forms.TextInput(attrs={"placeholder": "Ej: 10,7,1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for f in (
            "interval_km",
            "interval_days",
            "alert_before_km",
            "alert_before_days",
            "alert_before_km_steps",
            "alert_before_days_steps",
        ):
            if f in self.fields:
                self.fields[f].required = False

        for f in ("interval_km", "interval_days", "alert_before_km", "alert_before_days"):
            if f in self.fields:
                self.fields[f].widget.attrs.update({"min": "0", "step": "1"})

        # ✅ Heurística para edición:
        # Si el tipo ya existe y NO tiene frecuencias, asumimos que no requiere frecuencia
        inst = getattr(self, "instance", None)
        if inst and inst.pk:
            has_freq = bool((inst.interval_km or 0) > 0 or (inst.interval_days or 0) > 0)
            self.fields["requires_frequency"].initial = has_freq
        else:
            # En creación lo dejamos marcado por defecto
            self.fields["requires_frequency"].initial = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Debes ingresar un nombre.")
        return name

    def _parse_csv(self, raw: str, field_name: str) -> list[int]:
        raw = (raw or "").strip()
        if not raw:
            return []

        parts = [p.strip() for p in raw.split(",") if p.strip()]
        vals: list[int] = []

        for p in parts:
            if not p.isdigit():
                raise ValidationError({
                    field_name: "Formato inválido. Usa solo números separados por coma. Ej: 1000,500,100"
                })
            v = int(p)
            if v <= 0:
                raise ValidationError({field_name: "Los valores deben ser mayores a 0."})
            vals.append(v)

        return sorted(set(vals), reverse=True)

    def clean(self):
        cleaned = super().clean()

        # Si ya hay errores base (ej. conversión inválida), no seguimos forzando int(...)
        if self.errors:
            return cleaned

        # ✅ Checkbox del form
        requires_frequency = bool(cleaned.get("requires_frequency"))

        # ✅ Normalizar vacíos a 0 para campos numéricos opcionales
        # (evita IntegrityError por NOT NULL en Postgres)
        km = cleaned.get("interval_km")
        days = cleaned.get("interval_days")
        alert_km = cleaned.get("alert_before_km")
        alert_days = cleaned.get("alert_before_days")

        cleaned["interval_km"] = 0 if km in (None, "") else int(km)
        cleaned["interval_days"] = 0 if days in (None, "") else int(days)
        cleaned["alert_before_km"] = 0 if alert_km in (None, "") else int(alert_km)
        cleaned["alert_before_days"] = 0 if alert_days in (None, "") else int(alert_days)

        km = cleaned["interval_km"]
        days = cleaned["interval_days"]
        alert_km = cleaned["alert_before_km"]
        alert_days = cleaned["alert_before_days"]

        km_steps_raw = (cleaned.get("alert_before_km_steps") or "").strip()
        days_steps_raw = (cleaned.get("alert_before_days_steps") or "").strip()

        km_steps = self._parse_csv(km_steps_raw, "alert_before_km_steps") if km_steps_raw else []
        days_steps = self._parse_csv(days_steps_raw, "alert_before_days_steps") if days_steps_raw else []

        # ✅ Validaciones de negativos por seguridad
        if km < 0:
            self.add_error("interval_km", "La frecuencia (KM) no puede ser negativa.")
        if days < 0:
            self.add_error("interval_days", "La frecuencia (días) no puede ser negativa.")
        if alert_km < 0:
            self.add_error("alert_before_km", "El aviso (KM) no puede ser negativo.")
        if alert_days < 0:
            self.add_error("alert_before_days", "El aviso (días) no puede ser negativo.")

        # Si ya hay errores numéricos, cortar aquí
        if self.errors:
            return cleaned

        # ------------------------------------------------------------
        # ✅ Regla principal con checkbox:
        # Si requiere frecuencia, debe tener KM y/o días > 0
        # ------------------------------------------------------------
        if requires_frequency and km == 0 and days == 0:
            raise ValidationError(
                "Marcaste que este tipo requiere frecuencia. Debes indicar una frecuencia por KM y/o por días."
            )

        # ------------------------------------------------------------
        # ✅ Avisos requieren frecuencia correspondiente
        # ------------------------------------------------------------
        # Avisos por KM requieren frecuencia KM
        if km == 0:
            if alert_km > 0:
                self.add_error(
                    "alert_before_km",
                    "Para usar aviso por KM, primero debes indicar una frecuencia (KM)."
                )
            if km_steps:
                self.add_error(
                    "alert_before_km_steps",
                    "Para usar avisos múltiples por KM, primero debes indicar una frecuencia (KM)."
                )

        # Avisos por días requieren frecuencia días
        if days == 0:
            if alert_days > 0:
                self.add_error(
                    "alert_before_days",
                    "Para usar aviso por días, primero debes indicar una frecuencia (días)."
                )
            if days_steps:
                self.add_error(
                    "alert_before_days_steps",
                    "Para usar avisos múltiples por días, primero debes indicar una frecuencia (días)."
                )

        # ------------------------------------------------------------
        # ✅ Si sí hay frecuencia, los avisos deben ser menores
        # ------------------------------------------------------------
        if km > 0:
            for v in km_steps:
                if v >= km:
                    self.add_error("alert_before_km_steps", "Los avisos (KM) deben ser menores que la frecuencia (KM).")
                    break

            if alert_km >= km and alert_km != 0:
                self.add_error("alert_before_km", "El aviso (KM) debe ser menor que la frecuencia (KM).")

        if days > 0:
            for v in days_steps:
                if v >= days:
                    self.add_error("alert_before_days_steps", "Los avisos (días) deben ser menores que la frecuencia (días).")
                    break

            if alert_days >= days and alert_days != 0:
                self.add_error("alert_before_days", "El aviso (días) debe ser menor que la frecuencia (días).")

        # ------------------------------------------------------------
        # ✅ Compat: si llenó múltiples y legacy viene en 0 => setear el menor
        # ------------------------------------------------------------
        if km_steps and cleaned["alert_before_km"] == 0:
            cleaned["alert_before_km"] = min(km_steps)

        if days_steps and cleaned["alert_before_days"] == 0:
            cleaned["alert_before_days"] = min(days_steps)

        return cleaned

# ---------------------------
# SERVICE (INTELIGENTE)
# ---------------------------




class VehicleServiceForm(forms.ModelForm):
    # ✅ Confirmaciones separadas
    confirm_amount_increase = forms.BooleanField(
        required=False,
        label="Confirmo que el monto es correcto y explicaré el motivo en Notas.",
        help_text="Se solicita cuando el monto ingresado es inusualmente alto vs. el monto anterior (no aplica a Combustible).",
    )
    confirm_backdated_date = forms.BooleanField(
        required=False,
        label="Confirmo que la fecha es correcta aunque sea anterior a registros posteriores.",
        help_text="Se solicita cuando la fecha queda anterior a registros posteriores (rompe el orden del historial).",
    )
    confirm_km_below_current = forms.BooleanField(
        required=False,
        label="Confirmo que el KM declarado es menor al KM actual del vehículo porque estoy cargando un servicio antiguo.",
        help_text="Se solicita cuando el KM declarado es menor al KM actual del vehículo.",
    )

    class Meta:
        model = VehicleService
        fields = [
            "vehicle",
            "service_type_obj",
            "service_type",          # compat (oculto)
            "service_date",
            "kilometraje_declarado",
            "monto",
            "notes",
        ]
        labels = {
            "vehicle": "Vehículo",
            "service_type_obj": "Tipo de servicio",
            "service_type": "Tipo legacy (solo compatibilidad)",
            "service_date": "Fecha",
            "kilometraje_declarado": "Kilometraje declarado",
            "monto": "Monto",
            "notes": "Notas",
        }
        widgets = {
            "service_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        inst = getattr(self, "instance", None)
        self._is_edit = bool(inst and inst.pk)

        # Originales
        self._orig_vehicle_id = getattr(inst, "vehicle_id", None)
        self._orig_type_obj_id = getattr(inst, "service_type_obj_id", None)
        self._orig_service_type = getattr(inst, "service_type", None)
        self._orig_date = getattr(inst, "service_date", None)
        self._orig_monto = getattr(inst, "monto", None)
        self._orig_km = getattr(inst, "kilometraje_declarado", None)

        # Para desempates mismo día
        self._anchor_created_at = getattr(inst, "created_at", None) or timezone.now()
        self._anchor_pk = getattr(inst, "pk", None) or 0

        if "service_type_obj" in self.fields:
            self.fields["service_type_obj"].queryset = (
                VehicleServiceType.objects.filter(is_active=True).order_by("name")
            )

        if "service_type" in self.fields:
            self.fields["service_type"].required = False
            self.fields["service_type"].widget = forms.HiddenInput()

        if "service_date" in self.fields:
            self.fields["service_date"].input_formats = ["%Y-%m-%d"]

        # ✅ requeridos
        if "kilometraje_declarado" in self.fields:
            self.fields["kilometraje_declarado"].required = True
            self.fields["kilometraje_declarado"].widget.attrs.update({"min": "0", "step": "1"})

        if "monto" in self.fields:
            self.fields["monto"].required = True
            self.fields["monto"].widget.attrs.update({"min": "0", "step": "1"})

    def clean_kilometraje_declarado(self):
        km = self.cleaned_data.get("kilometraje_declarado")
        if km is None:
            raise ValidationError("Debes ingresar el kilometraje declarado.")
        try:
            km = int(km)
        except Exception:
            raise ValidationError("Kilometraje inválido.")
        if km < 0:
            raise ValidationError("El kilometraje no puede ser negativo.")
        return km

    def clean_monto(self):
        monto = self.cleaned_data.get("monto")
        if monto is None:
            raise ValidationError("Debes ingresar el monto.")
        if monto < 0:
            raise ValidationError("El monto no puede ser negativo.")
        if Decimal(monto) <= 0:
            raise ValidationError("El monto debe ser mayor a 0.")
        return monto

    def _get_type_key(self, cleaned):
        st_obj = cleaned.get("service_type_obj")
        legacy = cleaned.get("service_type") or None
        return (st_obj.id if st_obj else None), (legacy if not st_obj else None)

    def _base_qs_same_bucket(self, vehicle_id, type_obj_id, legacy_type):
        qs = VehicleService.objects.filter(vehicle_id=vehicle_id)
        if type_obj_id:
            qs = qs.filter(service_type_obj_id=type_obj_id)
        else:
            if legacy_type:
                qs = qs.filter(service_type=legacy_type)
        return qs

    def _neighbors_by_sortkey(
        self,
        vehicle_id,
        type_obj_id,
        legacy_type,
        date_value,
        anchor_created_at,
        anchor_pk,
        exclude_pk=None,
    ):
        qs = self._base_qs_same_bucket(vehicle_id, type_obj_id, legacy_type)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)

        prev_q = (
            Q(service_date__lt=date_value)
            | Q(service_date=date_value, created_at__lt=anchor_created_at)
            | Q(service_date=date_value, created_at=anchor_created_at, pk__lt=anchor_pk)
        )
        prev = qs.filter(prev_q).order_by("-service_date", "-created_at", "-pk").first()

        next_q = (
            Q(service_date__gt=date_value)
            | Q(service_date=date_value, created_at__gt=anchor_created_at)
            | Q(service_date=date_value, created_at=anchor_created_at, pk__gt=anchor_pk)
        )
        nxt = qs.filter(next_q).order_by("service_date", "created_at", "pk").first()

        return prev, nxt

    def clean(self):
        cleaned = super().clean()

        vehicle = cleaned.get("vehicle")
        st_obj = cleaned.get("service_type_obj")
        service_date = cleaned.get("service_date")
        km = cleaned.get("kilometraje_declarado")
        monto = cleaned.get("monto")
        notes = (cleaned.get("notes") or "").strip()

        if not vehicle or not service_date:
            return cleaned

        # Compat: si hay tipo configurable, forzar legacy a "otro"
        if st_obj and not cleaned.get("service_type"):
            cleaned["service_type"] = "otro"

        type_obj_id, legacy_type = self._get_type_key(cleaned)

        # ¿Cambió identidad?
        identity_changed = False
        if self._is_edit:
            if vehicle.id != self._orig_vehicle_id:
                identity_changed = True
            if type_obj_id != self._orig_type_obj_id:
                identity_changed = True
            if type_obj_id is None and legacy_type != self._orig_service_type:
                identity_changed = True

        date_changed = (not self._is_edit) or (self._orig_date != service_date) or identity_changed
        km_changed = (not self._is_edit) or (self._orig_km != km) or identity_changed
        amount_changed = (not self._is_edit) or (self._orig_monto != monto) or identity_changed

        prev_row, next_row = self._neighbors_by_sortkey(
            vehicle_id=vehicle.id,
            type_obj_id=type_obj_id,
            legacy_type=legacy_type,
            date_value=service_date,
            anchor_created_at=self._anchor_created_at,
            anchor_pk=self._anchor_pk,
            exclude_pk=self.instance.pk if self._is_edit else None,
        )

        # ------------------------------------------------------------
        # A) FECHA BACKDATED: si estás quedando antes de un registro posterior
        # ------------------------------------------------------------
        if date_changed and next_row is not None:
            if not cleaned.get("confirm_backdated_date"):
                self.add_error(
                    "confirm_backdated_date",
                    "Marca esta confirmación para guardar una fecha anterior a registros posteriores."
                )
                self.add_error(
                    None,
                    f'Estás registrando una fecha ({service_date.strftime("%d/%m/%Y")}) que queda ANTES '
                    f'de un servicio posterior ({next_row.service_date.strftime("%d/%m/%Y")}).'
                )

        # ------------------------------------------------------------
        # B) KM menor al KM actual del vehículo => confirmación separada
        # ------------------------------------------------------------
        km_actual = int(vehicle.kilometraje_actual or 0)
        if km is not None:
            km = int(km)
            if km < km_actual:
                if not cleaned.get("confirm_km_below_current"):
                    self.add_error(
                        "confirm_km_below_current",
                        "Marca esta confirmación para guardar un KM menor al KM actual del vehículo."
                    )
                    self.add_error(
                        None,
                        f"El kilometraje declarado ({km}) es menor al KM actual del vehículo ({km_actual})."
                    )

        # ------------------------------------------------------------
        # C) Consistencia del historial KM (vecinos)
        # ------------------------------------------------------------
        if km_changed and km is not None:
            if prev_row and prev_row.kilometraje_declarado is not None:
                if km < int(prev_row.kilometraje_declarado):
                    self.add_error(
                        "kilometraje_declarado",
                        f"El kilometraje no puede ser menor al del servicio anterior ({prev_row.kilometraje_declarado})."
                    )

            if next_row and next_row.kilometraje_declarado is not None:
                if km > int(next_row.kilometraje_declarado):
                    self.add_error(
                        "kilometraje_declarado",
                        f"El kilometraje no puede ser mayor al del servicio posterior ({next_row.kilometraje_declarado})."
                    )

        # ------------------------------------------------------------
        # D) MONTO alto => confirmación separada + notas obligatorias
        #    ✅ PERO: si el servicio es COMBUSTIBLE => NO aplicar esta regla
        # ------------------------------------------------------------
        is_fuel = (cleaned.get("service_type") == "combustible")

        if (not is_fuel) and amount_changed and monto is not None and prev_row and prev_row.monto is not None and prev_row.monto > 0:
            pct = ((Decimal(monto) / Decimal(prev_row.monto)) - Decimal("1")) * Decimal("100")
            if pct >= Decimal("30"):
                if not cleaned.get("confirm_amount_increase"):
                    self.add_error(
                        "confirm_amount_increase",
                        "Marca esta confirmación para guardar un monto inusualmente alto."
                    )
                    self.add_error(
                        None,
                        f'El monto ingresado (${int(Decimal(monto))}) es {pct.quantize(Decimal("1"))}% mayor '
                        f'que el monto anterior (${int(Decimal(prev_row.monto))}).'
                    )
                elif not notes:
                    self.add_error("notes", "Debes dejar una explicación en Notas para justificar el aumento de monto.")

        cleaned["notes"] = notes
        return cleaned
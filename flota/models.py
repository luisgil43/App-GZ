from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class Sequence(models.Model):
    """
    Secuencias internas para correlativos que NO se reutilizan jamás.
    Sirve para vehicle_code y para status_code y service_code.
    """
    key = models.CharField(max_length=50, unique=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Secuencia"
        verbose_name_plural = "Secuencias"

    def __str__(self):
        return f"{self.key}: {self.last_value}"

    @classmethod
    def next(cls, key: str, start_at: int = 0) -> int:
        """
        Devuelve el próximo correlativo para la 'key'.
        Si start_at=0: el primer valor será 0.
        """
        with transaction.atomic():
            obj, created = cls.objects.select_for_update().get_or_create(
                key=key,
                defaults={"last_value": start_at},
            )
            if created:
                return obj.last_value

            obj.last_value += 1
            obj.save(update_fields=["last_value"])
            return obj.last_value


class VehicleStatus(models.Model):
    """
    Status configurables por el admin (Activo, En taller, etc.)
    Se usan en la lista de vehículos (no hay listado separado).
    """
    status_code = models.PositiveIntegerField(unique=True, editable=False, null=True, blank=True)
    name = models.CharField(max_length=60, unique=True)
    color = models.CharField(
        max_length=30,
        default="blue",
        help_text="Nombre simple (blue, green, red, yellow, gray). Para badge Tailwind."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Status de vehículo"
        verbose_name_plural = "Status de vehículos"
        ordering = ["status_code"]

    def save(self, *args, **kwargs):
        if not self.pk and (self.status_code is None):
            self.status_code = Sequence.next("flota.status_code", start_at=0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.status_code}] {self.name}"


class Vehicle(models.Model):
    """
    Lista de Vehículos / Flota
    """
    PURCHASE_TYPES = [
        ("contado", "Al contado"),
        ("credito", "Crédito"),
        ("leasing", "Leasing"),
    ]

    vehicle_code = models.PositiveIntegerField(unique=True, editable=False, null=True, blank=True)

    nombre_fantasia = models.CharField(max_length=80, blank=True, null=True)

    marca = models.CharField(max_length=60)
    modelo = models.CharField(max_length=60)

    patente = models.CharField(max_length=15, unique=True)
    serial = models.CharField(max_length=80, unique=True)

    kilometraje_actual = models.PositiveIntegerField(default=0)

    fecha_compra = models.DateField(blank=True, null=True)

    tipo_compra = models.CharField(max_length=20, choices=PURCHASE_TYPES, default="contado")
    monto_compra = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    fecha_fin_credito = models.DateField(blank=True, null=True)

    fecha_ultima_revision_tecnica = models.DateField(blank=True, null=True)
    fecha_permiso_circulacion = models.DateField(blank=True, null=True)

    status = models.ForeignKey(
        VehicleStatus,
        on_delete=models.PROTECT,
        related_name="vehicles",
        blank=True,
        null=True,
    )

    last_movement_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vehículo"
        verbose_name_plural = "Vehículos"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.pk and (self.vehicle_code is None):
            self.vehicle_code = Sequence.next("flota.vehicle_code", start_at=0)

        if not self.status_id:
            st = VehicleStatus.objects.filter(name__iexact="Activo", is_active=True).first()
            if st:
                self.status = st

        super().save(*args, **kwargs)

    def clean(self):
        if self.tipo_compra in ("credito", "leasing") and not self.fecha_fin_credito:
            raise ValidationError({"fecha_fin_credito": "Si es Crédito/Leasing, debes indicar la fecha de finalización."})
        if self.tipo_compra == "contado":
            self.fecha_fin_credito = None

        if self.monto_compra is not None and self.monto_compra < 0:
            raise ValidationError({"monto_compra": "El monto de compra no puede ser negativo."})

    def update_kilometraje(self, nuevo_km: int, source: str = "manual", ref: str = "", strict: bool = True):
        """
        - strict=True  (manual): si nuevo_km < actual => error
        - strict=False (servicio/combustible/rendición): si nuevo_km < actual => NO hace nada (no baja el odómetro)
        """
        if nuevo_km is None:
            return

        nuevo_km = int(nuevo_km)

        if nuevo_km < 0:
            raise ValidationError("El kilometraje no puede ser negativo.")

        if nuevo_km < self.kilometraje_actual:
            if strict:
                raise ValidationError(f"El kilometraje no puede ser menor al actual ({self.kilometraje_actual}).")
        # En fuentes automáticas (servicio/combustible/etc) NO bajamos el odómetro
            return

        if nuevo_km == self.kilometraje_actual:
            return

        VehicleOdometerEvent.objects.create(
            vehicle=self,
            previous_km=self.kilometraje_actual,
            new_km=nuevo_km,
            source=source,
            reference=ref,
        )   
        self.kilometraje_actual = nuevo_km
        self.save(update_fields=["kilometraje_actual", "updated_at"])

    @property
    def assigned_to_label(self) -> str:
        a = self.assignments.filter(active=True).select_related("user").first()
        if not a:
            return "—"
        u = a.user
        return (getattr(u, "get_full_name", lambda: "")() or getattr(u, "username", "") or str(u)).strip() or "—"

    @property
    def last_movement_label(self) -> str:
        if not self.last_movement_at:
            return "—"
        return timezone.localtime(self.last_movement_at).strftime("%d-%m-%Y %H:%M")

    @property
    def meses_restantes_credito(self):
        if self.tipo_compra not in ("credito", "leasing") or not self.fecha_fin_credito:
            return None
        today = date.today()
        if self.fecha_fin_credito <= today:
            return 0
        return max(1, (self.fecha_fin_credito - today).days // 30)

    @property
    def credito_restante_texto(self) -> str:
        m = self.meses_restantes_credito
        if m is None:
            return "—"
        if m == 0:
            return "Finalizado"
        return f"{m} mes"

    def __str__(self):
        return f"[{self.vehicle_code}] {self.patente} - {self.marca} {self.modelo}"


class VehicleAssignment(models.Model):
    """
    Asignación de vehículo a un usuario (activa única por vehículo).
    """
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vehicle_assignments")
    assigned_at = models.DateTimeField(default=timezone.now)
    unassigned_at = models.DateTimeField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Asignación de vehículo"
        verbose_name_plural = "Asignaciones de vehículo"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(active=True),
                name="uniq_active_assignment_per_vehicle",
            )
        ]

    def clean(self):
        if self.active and self.unassigned_at:
            raise ValidationError({"unassigned_at": "Si está activa, no debe tener fecha de desasignación."})

    def __str__(self):
        return f"{self.vehicle} -> {self.user} ({'Activa' if self.active else 'Cerrada'})"


class VehicleOdometerEvent(models.Model):
    SOURCE_CHOICES = [
        ("manual", "Manual"),
        ("servicio", "Servicio"),
        ("combustible", "Combustible"),
        ("rendicion", "Rendición"),
        ("otro", "Otro"),
    ]

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="odometer_events")
    previous_km = models.PositiveIntegerField()
    new_km = models.PositiveIntegerField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    reference = models.CharField(max_length=120, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evento de kilometraje"
        verbose_name_plural = "Eventos de kilometraje"
        ordering = ["-created_at"]

    def clean(self):
        if self.new_km < self.previous_km:
            raise ValidationError("El KM nuevo no puede ser menor al anterior.")

    def __str__(self):
        return f"{self.vehicle} {self.previous_km} -> {self.new_km} ({self.source})"


class VehicleServiceType(models.Model):
    """
    Tipo de servicio configurable (como Status).
    Esto alimenta el combo “Tipo de servicio” y permite:
    - Frecuencia por KM y/o por días
    - Umbral de aviso (ej: avisar 100km antes)

    ✅ EXTENSIÓN:
    - Múltiples avisos por KM y por días (CSV)
    - Aviso cuando esté vencido
    """
    type_code = models.PositiveIntegerField(unique=True, editable=False, null=True, blank=True)

    name = models.CharField(max_length=80, unique=True)  # Ej: Cambio de aceite
    is_active = models.BooleanField(default=True)

    # Frecuencia “pro”
    interval_km = models.PositiveIntegerField(blank=True, null=True, help_text="Frecuencia en KM (ej: 5000).")
    interval_days = models.PositiveIntegerField(blank=True, null=True, help_text="Frecuencia en días (ej: 180).")

    # Avisos “pro” (legacy: se mantiene)
    alert_before_km = models.PositiveIntegerField(default=100, help_text="Avisar cuando falten X KM.")
    alert_before_days = models.PositiveIntegerField(default=7, help_text="Avisar cuando falten X días.")

    # ✅ NUEVO: múltiples avisos (CSV). Ej: "1000,500,100" / "10,7,1"
    alert_before_km_steps = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Avisos por KM separados por coma. Ej: 1000,500,100"
    )
    alert_before_days_steps = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Avisos por días separados por coma. Ej: 10,7,1"
    )

    # ✅ NUEVO: avisar cuando esté vencido
    notify_on_overdue = models.BooleanField(
        default=True,
        help_text="Si está activo, también avisará cuando el servicio ya esté vencido."
    )

    color = models.CharField(
        max_length=30,
        default="blue",
        help_text="Nombre simple (blue, green, red, yellow, gray). Para badge Tailwind."
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tipo de servicio"
        verbose_name_plural = "Tipos de servicio"
        ordering = ["type_code", "name"]

    def save(self, *args, **kwargs):
        if not self.pk and (self.type_code is None):
            self.type_code = Sequence.next("flota.service_type_code", start_at=0)
        super().save(*args, **kwargs)

    def clean(self):
        # Validación de frecuencia (se permite sin frecuencia si quieres)
        if (self.interval_km is None or self.interval_km == 0) and (self.interval_days is None or self.interval_days == 0):
            return

        if self.interval_km is not None and self.interval_km < 0:
            raise ValidationError({"interval_km": "La frecuencia en KM no puede ser negativa."})
        if self.interval_days is not None and self.interval_days < 0:
            raise ValidationError({"interval_days": "La frecuencia en días no puede ser negativa."})

        # No negativos en legacy
        if self.alert_before_km is not None and self.alert_before_km < 0:
            raise ValidationError({"alert_before_km": "El aviso (KM) no puede ser negativo."})
        if self.alert_before_days is not None and self.alert_before_days < 0:
            raise ValidationError({"alert_before_days": "El aviso (días) no puede ser negativo."})

    # --------------------------
    # Helpers (para tu lógica)
    # --------------------------
    def _parse_csv_ints(self, raw: str) -> list[int]:
        raw = (raw or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        vals: list[int] = []
        for p in parts:
            if not p.isdigit():
                # Si llega algo sucio, lo ignoramos en runtime (el form debe validar)
                continue
            v = int(p)
            if v > 0:
                vals.append(v)
        # únicos orden desc (1000,500,100)
        return sorted(set(vals), reverse=True)

    @property
    def alert_km_steps_list(self) -> list[int]:
        """
        Lista de avisos por KM.
        Si steps está vacío, cae a legacy (si > 0).
        """
        vals = self._parse_csv_ints(self.alert_before_km_steps)
        if vals:
            return vals
        return [int(self.alert_before_km)] if (self.alert_before_km or 0) > 0 else []

    @property
    def alert_days_steps_list(self) -> list[int]:
        """
        Lista de avisos por días.
        Si steps está vacío, cae a legacy (si > 0).
        """
        vals = self._parse_csv_ints(self.alert_before_days_steps)
        if vals:
            return vals
        return [int(self.alert_before_days)] if (self.alert_before_days or 0) > 0 else []

    def __str__(self):
        return f"[{self.type_code}] {self.name}"


class VehicleService(models.Model):
    """
    Servicios del vehículo (luego lo conectamos con rendiciones).

    ✅ Retrocompatible:
    - mantiene service_type (choices) tal como lo tienes
    - agrega service_type_obj (FK) para usar tipos configurables
    """
    SERVICE_TYPES = [
        ("combustible", "Combustible"),
        ("aceite", "Cambio de aceite"),
        ("neumaticos", "Cambio de neumáticos"),
        ("revision_tecnica", "Revisión técnica"),
        ("permiso_circulacion", "Permiso de circulación"),
        ("otro", "Otro"),
    ]

    service_code = models.PositiveIntegerField(unique=True, editable=False, null=True, blank=True)

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="services")

    # ✅ lo que ya tienes (NO se elimina)
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPES)
    title = models.CharField(max_length=120, blank=True, null=True)

    # ✅ NUEVO: tipo configurable (opcional para no romper)
    service_type_obj = models.ForeignKey(
        VehicleServiceType,
        on_delete=models.PROTECT,
        related_name="services",
        blank=True,
        null=True,
        help_text="Tipo configurable (para alarmas y frecuencias)."
    )

    service_date = models.DateField(default=date.today)
    service_time = models.TimeField(blank=True, null=True)

    kilometraje_declarado = models.PositiveIntegerField(blank=True, null=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True, null=True)

    # ✅ NUEVO: próximos vencimientos calculados (para alarmas)
    next_due_km = models.PositiveIntegerField(blank=True, null=True)
    next_due_date = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Servicio de vehículo"
        verbose_name_plural = "Servicios de vehículos"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["vehicle", "created_at"]),
            models.Index(fields=["vehicle", "service_date"]),
        ]

    def clean(self):
        if self.monto is not None and self.monto < 0:
            raise ValidationError({"monto": "El monto no puede ser negativo."})

        if self.kilometraje_declarado is not None and self.kilometraje_declarado < 0:
            raise ValidationError({"kilometraje_declarado": "El kilometraje no puede ser negativo."})

    def _compute_next_dues(self):
        """
        Calcula next_due_km / next_due_date en base al tipo configurable (si existe).
        Si no existe, no fuerza nada.
        """
        t = self.service_type_obj
        if not t:
            return

        # Próximo vencimiento por KM
        if t.interval_km and self.kilometraje_declarado is not None:
            self.next_due_km = int(self.kilometraje_declarado) + int(t.interval_km)

        # Próximo vencimiento por fecha
        if t.interval_days:
            self.next_due_date = self.service_date + timedelta(days=int(t.interval_days))

    def save(self, *args, **kwargs):
        if not self.pk and (self.service_code is None):
            self.service_code = Sequence.next("flota.service_code", start_at=0)

        # calcular próximos vencimientos antes de guardar
        self._compute_next_dues()

        super().save(*args, **kwargs)

        # actualizar vehículo: último movimiento + km
        if self.service_time:
            dt = timezone.make_aware(timezone.datetime.combine(self.service_date, self.service_time))
        else:
            dt = timezone.now()

        v = self.vehicle

        if self.kilometraje_declarado is not None:
            v.update_kilometraje(self.kilometraje_declarado, source="servicio", ref=f"Servicio #{self.service_code}", strict=False,)

        v.last_movement_at = dt

        if self.service_type == "revision_tecnica":
            v.fecha_ultima_revision_tecnica = self.service_date
        if self.service_type == "permiso_circulacion":
            v.fecha_permiso_circulacion = self.service_date

        v.save(update_fields=[
            "last_movement_at",
            "fecha_ultima_revision_tecnica",
            "fecha_permiso_circulacion",
            "updated_at",
        ])

    def __str__(self):
        # muestra tipo configurable si existe
        if self.service_type_obj_id:
            return f"{self.vehicle} - {self.service_type_obj.name} ({self.service_date})"
        return f"{self.vehicle} - {self.get_service_type_display()} ({self.service_date})"
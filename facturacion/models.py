#models_facturacion
import datetime
from decimal import Decimal

import cloudinary.uploader
from cloudinary.models import CloudinaryField
from cloudinary_storage.storage import RawMediaCloudinaryStorage
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from operaciones.models import ServicioCotizado


class OrdenCompraFacturacion(models.Model):
    du = models.ForeignKey(ServicioCotizado, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='ordenes_compra')

    orden_compra = models.CharField(
        "Orden de Compra", max_length=30, blank=True, null=True)
    pos = models.CharField("POS", max_length=10, blank=True, null=True)
    cantidad = models.DecimalField(
        "Cantidad", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    unidad_medida = models.CharField(
        "UM", max_length=10, blank=True, null=True)
    material_servicio = models.CharField(
        "Material/Servicio", max_length=100, blank=True, null=True)
    descripcion_sitio = models.TextField(
        "Descripción / Sitio", blank=True, null=True)
    fecha_entrega = models.DateField("Fecha de Entrega", blank=True, null=True)
    precio_unitario = models.DecimalField(
        "Precio Unitario", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monto = models.DecimalField(
        "Monto", max_digits=12, decimal_places=2, default=Decimal('0.00'))

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Orden de Compra Facturación"
        verbose_name_plural = "Órdenes de Compra Facturación"

    def __str__(self):
        return f"OC {self.orden_compra} - DU: {self.du.du if self.du else 'Sin DU'}"


class FacturaOC(models.Model):
    orden_compra = models.OneToOneField(
        OrdenCompraFacturacion,
        on_delete=models.CASCADE,
        related_name='factura',
        verbose_name="Orden de Compra"
    )

    hes = models.CharField("HES", max_length=50, blank=True, null=True)
    valor_en_clp = models.DecimalField(
        "Valor en CLP", max_digits=15, decimal_places=2, blank=True, null=True)
    conformidad = models.CharField(
        "Conformidad", max_length=50, blank=True, null=True)
    num_factura = models.CharField(
        "Número de Factura", max_length=50, blank=True, null=True)
    fecha_facturacion = models.DateField(
        "Fecha de Facturación", blank=True, null=True)
    mes_produccion = models.CharField(
        "Mes de Producción", max_length=20, blank=True, null=True)
    factorizado = models.BooleanField("¿Factorizado?", default=False)
    fecha_factoring = models.DateField(
        "Fecha de Factoring", blank=True, null=True)
    cobrado = models.BooleanField("¿Cobrado?", default=False)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Factura"
        verbose_name_plural = "Facturas"

    def __str__(self):
        return f"Factura {self.num_factura or 'Sin número'} - OC {self.orden_compra.orden_compra}"

    # Estado dinámico
    def get_status_factura(self):
        if not self.conformidad:
            return "Pendiente por Conformidad"
        if not self.num_factura:
            return "Pendiente por Facturación"
        if self.num_factura:
            status = "Facturado"
            if self.factorizado:
                status = "En proceso de Factoring"
            if self.cobrado:
                status = "Cobrado"
            return status
        return "Pendiente"


class Proyecto(models.Model):
    nombre = models.CharField(max_length=255)
    mandante = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.mandante})"


class TipoGasto(models.Model):
    CATEGORIAS = [
        ('costo', 'Costo'),
        ('inversion', 'Inversión'),
        ('gasto', 'Gasto'),
        ('abono', 'Abono'),
    ]

    nombre = models.CharField(max_length=255)
    categoria = models.CharField(max_length=50, choices=CATEGORIAS)

    # ✅ Controla si el tipo está disponible para declarar rendiciones
    disponible = models.BooleanField(default=True, verbose_name="Disponible")

    class Meta:
        verbose_name = "Tipo de Gasto"
        verbose_name_plural = "Tipos de Gasto"
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre}"

    # ✅ Compatibilidad con templates viejos/nuevos
    @property
    def disponible_para_declarar(self):
        return self.disponible


def ruta_comprobante_cartola(instance, filename):
    """
    Guardar comprobantes en media/cartola_movimientos/<MES_AÑO>/<ID>.pdf
    """
    mes = datetime.date.today().strftime('%B_%Y')  # Ej: Julio_2025
    extension = filename.split('.')[-1]
    # Si no tiene ID aún, usar 'temp'
    nombre = f"{instance.pk or 'temp'}.{extension}"
    return f"media/cartola_movimientos/{mes}/{nombre}"


class CartolaMovimiento(models.Model):
    ESTADOS = [
        ('pendiente_abono_usuario', 'Pendiente aprobación abono usuario'),
        ('aprobado_abono_usuario', 'Aprobado abono por usuario'),
        ('rechazado_abono_usuario', 'Rechazado abono por usuario'),
        ('pendiente_supervisor', 'Pendiente aprobación supervisor'),
        ('aprobado_supervisor', 'Aprobado por supervisor'),
        ('rechazado_supervisor', 'Rechazado por supervisor'),
        ('aprobado_pm', 'Aprobado por PM'),
        ('rechazado_pm', 'Rechazado por PM'),
        ('aprobado_finanzas', 'Aprobado por finanzas'),
        ('rechazado_finanzas', 'Rechazado por finanzas'),
    ]

    TIPO_DOC_CHOICES = [
        ('boleta', 'Boleta'),
        ('factura', 'Factura'),
        ('otros', 'Otros'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )

    # Fecha/Hora en que se registró en el sistema (declaración)
    fecha = models.DateTimeField(auto_now_add=True, editable=False)

    # ✅ Fecha real del movimiento (cuando ocurrió el gasto/abono)
    fecha_transaccion = models.DateField(
        "Fecha real del movimiento",
        blank=True,
        null=True
    )

    proyecto = models.ForeignKey(
        'Proyecto', on_delete=models.SET_NULL, null=True, blank=True
    )
    tipo = models.ForeignKey(
        'TipoGasto', on_delete=models.SET_NULL, null=True, blank=True
    )

    # =========================
    # ✅ Integración con FLOTA
    # =========================
    # Vehículo seleccionado en la rendición (si aplica)
    vehiculo_flota = models.ForeignKey(
        'flota.Vehicle',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rendiciones_cartola',
        verbose_name='Vehículo (Flota)'
    )

    # Tipo de servicio configurable (Flota)
    tipo_servicio_flota = models.ForeignKey(
        'flota.VehicleServiceType',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rendiciones_cartola',
        verbose_name='Tipo de servicio (Flota)'
    )

    # Servicio real creado en flota a partir de esta rendición
    servicio_flota = models.ForeignKey(
        'flota.VehicleService',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rendiciones_origen',
        verbose_name='Servicio creado en Flota'
    )

    # Snapshot / datos declarados al momento de rendir (útil para auditoría)
    fecha_servicio_flota = models.DateField(
        "Fecha servicio flota",
        null=True,
        blank=True
    )
    hora_servicio_flota = models.TimeField(
        "Hora servicio flota",
        null=True,
        blank=True
    )
    kilometraje_servicio_flota = models.PositiveIntegerField(
        "Kilometraje servicio flota",
        null=True,
        blank=True
    )
    tipo_servicio_flota_snapshot = models.CharField(
        max_length=120,
        blank=True,
        null=True,
        verbose_name="Nombre tipo servicio (snapshot)"
    )

    rut_factura = models.CharField(max_length=12, blank=True, null=True)
    tipo_doc = models.CharField(
        max_length=20, choices=TIPO_DOC_CHOICES, blank=True, null=True, verbose_name="Tipo de Documento"
    )
    numero_doc = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Número de Documento"
    )
    observaciones = models.TextField(blank=True, null=True)
    numero_transferencia = models.CharField(
        max_length=100, blank=True, null=True
    )
    comprobante = models.FileField(
        upload_to=ruta_comprobante_cartola,
        storage=RawMediaCloudinaryStorage(),
        blank=True,
        null=True,
        verbose_name="Comprobante",
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )

    aprobado_por_supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rendiciones_aprobadas_supervisor'
    )
    aprobado_por_pm = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rendiciones_aprobadas_pm'
    )

    aprobado_por_finanzas = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='rendiciones_aprobadas_finanzas'
    )

    # ✅ timestamp cuando queda aprobado por finanzas (para auto-archivar)
    aprobado_finanzas_en = models.DateTimeField(null=True, blank=True)

    # ✅ campos de historial/archivo
    archivado_en = models.DateTimeField(null=True, blank=True)
    archivado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cartola_archivados'
    )

    # ✅ (tu código ya los usa)
    en_historial = models.BooleanField(default=False)
    historial_enviado_el = models.DateTimeField(null=True, blank=True)
    historial_enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cartola_historial_enviados'
    )

    cargos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    abonos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=50, choices=ESTADOS, default='pendiente_abono_usuario'
    )
    motivo_rechazo = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Cartola Movimiento"
        verbose_name_plural = "Cartola Movimientos"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['fecha']),
            models.Index(fields=['fecha_transaccion']),
            models.Index(fields=['usuario', 'status']),
            models.Index(fields=['vehiculo_flota']),
            models.Index(fields=['servicio_flota']),
        ]

    def save(self, *args, **kwargs):
        from django.utils import timezone

        now = timezone.now()

        # Snapshot automático del tipo servicio flota (si no lo enviaron manualmente)
        if self.tipo_servicio_flota and not self.tipo_servicio_flota_snapshot:
            self.tipo_servicio_flota_snapshot = self.tipo_servicio_flota.name

        # Si ya hay servicio flota asociado y faltan snapshots, los rellenamos
        if self.servicio_flota:
            if not self.vehiculo_flota_id and self.servicio_flota.vehicle_id:
                self.vehiculo_flota_id = self.servicio_flota.vehicle_id

            if not self.tipo_servicio_flota_id and self.servicio_flota.service_type_obj_id:
                self.tipo_servicio_flota_id = self.servicio_flota.service_type_obj_id

            if not self.fecha_servicio_flota:
                self.fecha_servicio_flota = self.servicio_flota.service_date

            if not self.hora_servicio_flota:
                self.hora_servicio_flota = self.servicio_flota.service_time

            if self.kilometraje_servicio_flota is None:
                self.kilometraje_servicio_flota = self.servicio_flota.kilometraje_declarado

            if not self.tipo_servicio_flota_snapshot:
                if self.servicio_flota.service_type_obj:
                    self.tipo_servicio_flota_snapshot = self.servicio_flota.service_type_obj.name
                else:
                    self.tipo_servicio_flota_snapshot = self.servicio_flota.get_service_type_display()

        # Si es nuevo, no hay "previo" para comparar transición de status
        if not self.pk:
            super().save(*args, **kwargs)
            return

        prev = (
            CartolaMovimiento.objects
            .filter(pk=self.pk)
            .values("status", "aprobado_finanzas_en", "archivado_en")
            .first()
        )

        if prev:
            prev_status = prev.get("status")
            prev_aprobado_ts = prev.get("aprobado_finanzas_en")
            prev_archivado_ts = prev.get("archivado_en")

            # Detectar transición a aprobado_finanzas
            if prev_status != self.status and self.status == "aprobado_finanzas":
                # Timestamp de aprobación
                if not prev_aprobado_ts and not self.aprobado_finanzas_en:
                    self.aprobado_finanzas_en = now

                # Archivar "al tiro"
                if not prev_archivado_ts and not self.archivado_en:
                    self.archivado_en = now

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.usuario} - {self.proyecto} - {self.tipo} - {self.fecha}"

    @property
    def es_rendicion_flota(self):
        return bool(self.vehiculo_flota_id or self.servicio_flota_id or self.tipo_servicio_flota_id)

    @property
    def tipo_servicio_flota_nombre(self):
        if self.tipo_servicio_flota_snapshot:
            return self.tipo_servicio_flota_snapshot
        if self.tipo_servicio_flota:
            return self.tipo_servicio_flota.name
        if self.servicio_flota:
            if self.servicio_flota.service_type_obj:
                return self.servicio_flota.service_type_obj.name
            return self.servicio_flota.get_service_type_display()
        return None
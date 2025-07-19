# operaciones/models.py

from django.db.models import Max
from django.db import models
from django.conf import settings
from decimal import Decimal
from usuarios.models import CustomUser


class SitioMovil(models.Model):
    id_sites = models.CharField(max_length=100, unique=True)
    id_claro = models.CharField(max_length=100, blank=True, null=True)
    id_sites_new = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=100, blank=True, null=True)
    nombre = models.CharField(max_length=255, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)

    # Convertidos a FloatField
    latitud = models.FloatField(blank=True, null=True)
    longitud = models.FloatField(blank=True, null=True)

    comuna = models.CharField(max_length=100, blank=True, null=True)
    tipo_construccion = models.CharField(max_length=100, blank=True, null=True)
    altura = models.CharField(max_length=100, blank=True, null=True)
    candado_bt = models.CharField(max_length=100, blank=True, null=True)
    condiciones_acceso = models.TextField(blank=True, null=True)
    claves = models.TextField(blank=True, null=True)
    llaves = models.TextField(blank=True, null=True)
    cantidad_llaves = models.CharField(max_length=255, blank=True, null=True)
    observaciones_generales = models.TextField(blank=True, null=True)
    zonas_conflictivas = models.TextField(blank=True, null=True)
    alarmas = models.TextField(blank=True, null=True)
    guardias = models.TextField(blank=True, null=True)
    nivel = models.IntegerField(blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre or self.id_sites


class ServicioCotizado(models.Model):
    ESTADOS = [
        ('cotizado', 'Cotizado'),
        ('aprobado_pendiente', 'Aprobada, pendiente por asignar'),
        ('asignado', 'Técnicos asignados'),
        ('en_progreso', 'En progreso'),
        ('finalizado_trabajador', 'Finalizado por técnico'),
        ('rechazado_supervisor', 'Rechazado por supervisor'),
        ('aprobado_supervisor', 'Aprobado por supervisor'),
        ('informe_subido', 'Informe cargado'),
        ('finalizado', 'Finalizado'),
    ]

    # Se generará automáticamente
    du = models.CharField(max_length=20, blank=True)
    id_claro = models.CharField(max_length=100)
    region = models.CharField(max_length=100)
    mes_produccion = models.CharField(max_length=20)
    id_new = models.CharField(max_length=100)
    detalle_tarea = models.TextField()
    monto_cotizado = models.DecimalField(max_digits=12, decimal_places=2)
    monto_mmoo = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True)

    estado = models.CharField(
        max_length=50, choices=ESTADOS, default='cotizado'
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='servicios_creados'
    )

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    trabajadores_asignados = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='servicios_asignados',
        limit_choices_to={'rol': 'usuario'},
        verbose_name='Técnicos asignados'
    )

    pm_aprueba = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='cotizaciones_aprobadas_pm',
        verbose_name="Aprobado por PM"
    )

    tecnico_aceptado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='servicios_aceptados_tecnico',
        verbose_name="Aceptado por técnico"
    )

    tecnico_finalizo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='servicios_finalizados_tecnico',
        verbose_name="Finalizado por técnico"
    )

    supervisor_aprobo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='servicios_aprobados_supervisor',
        verbose_name="Aprobado por supervisor"
    )

    supervisor_rechazo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='servicios_rechazados_supervisor',
        verbose_name="Rechazado por supervisor"
    )

    supervisor_asigna = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='servicios_asignados_como_supervisor'
    )

    motivo_rechazo = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.du:
            ultimo_id = ServicioCotizado.objects.aggregate(max_id=Max('id'))[
                'max_id'] or 0
            self.du = str(ultimo_id + 1).zfill(8)
        if not self.monto_mmoo:
            self.monto_mmoo = self.monto_cotizado * Decimal('0.2')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"DU {self.du} - {self.id_claro}"

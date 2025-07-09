import os
from datetime import datetime
from django.db import models
from django.conf import settings
from cloudinary_storage.storage import RawMediaCloudinaryStorage


class Bodega(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nombre


def ruta_ingreso_material(instance, filename):
    now = datetime.now()
    mes = now.strftime('%B')  # Ej: Enero, Febrero
    extension = os.path.splitext(filename)[1]  # .pdf
    numero_doc = instance.numero_documento or 'documento'
    return f'Ingreso de materiales/{mes}/{numero_doc}{extension}'


class Material(models.Model):

    codigo_interno = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=255)
    codigo_externo = models.CharField(
        max_length=50, blank=True, null=True)  # ✅ nuevo campo
    bodega = models.ForeignKey(
        Bodega, on_delete=models.SET_NULL, null=True, blank=True)
    stock_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    unidad_medida = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.codigo_interno} - {self.nombre}"


class IngresoMaterial(models.Model):
    OPCIONES_TIPO_DOC = [
        ('guia', 'Guía de Despacho'),
        ('factura', 'Factura'),
    ]

    fecha_ingreso = models.DateField(auto_now_add=True)
    tipo_documento = models.CharField(max_length=10, choices=OPCIONES_TIPO_DOC)
    numero_documento = models.CharField(max_length=50)
    codigo_externo = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Código externo")  # ← nuevo

    bodega = models.ForeignKey(
        Bodega, on_delete=models.PROTECT, related_name='ingresos')

    archivo_documento = models.FileField(
        upload_to=ruta_ingreso_material,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="PDF de respaldo"
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.numero_documento} - {self.get_tipo_documento_display()}"


class DetalleIngresoMaterial(models.Model):
    ingreso = models.ForeignKey(
        IngresoMaterial,
        on_delete=models.CASCADE,
        related_name='detalles'  # <- AÑADE ESTO
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"

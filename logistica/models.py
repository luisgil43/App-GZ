from datetime import date
from django.contrib.auth import get_user_model
import os
from datetime import datetime
from django.db import models
from django.conf import settings
from cloudinary_storage.storage import RawMediaCloudinaryStorage

from django.db import models
# Asumiendo que este es tu modelo de usuario
from usuarios.models import CustomUser


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
    codigo_interno = models.CharField(max_length=50)  # quitar unique=True
    nombre = models.CharField(max_length=255)
    codigo_externo = models.CharField(
        max_length=50, blank=True, null=True)
    bodega = models.ForeignKey(
        Bodega, on_delete=models.SET_NULL, null=True, blank=True)
    stock_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    unidad_medida = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    valor_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name="Valor unitario ($)"
    )

    def __str__(self):
        return f"{self.codigo_interno} - {self.nombre}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['codigo_interno', 'bodega'],
                name='unique_codigo_interno_por_bodega'
            ),
            models.UniqueConstraint(
                fields=['codigo_externo', 'bodega'],
                name='unique_codigo_externo_por_bodega'
            )
        ]


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


def ruta_caf(instance, filename):
    now = datetime.now()
    mes = now.strftime('%B')
    return f"caf/{mes}/{filename}"


class ArchivoCAF(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    nombre_archivo = models.CharField(max_length=255)
    archivo = models.FileField(
        upload_to=ruta_caf,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="Archivo CAF (.xml)"
    )
    tipo_dte = models.PositiveIntegerField()
    rango_inicio = models.PositiveIntegerField()
    rango_fin = models.PositiveIntegerField()
    fecha_subida = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=[(
        'activo', 'Activo'), ('inactivo', 'Inactivo')])

    def __str__(self):
        return f"{self.nombre_archivo} (TD {self.tipo_dte})"


TIPO_DOCUMENTO_CHOICES = [
    ('guia', 'Guía de Despacho'),
    ('factura', 'Factura'),
    ('nota_credito', 'Nota de Crédito'),
    ('nota_debito', 'Nota de Débito'),
    ('otro', 'Otro'),
]


class FolioDisponible(models.Model):
    caf = models.ForeignKey(ArchivoCAF, on_delete=models.CASCADE)
    folio = models.IntegerField()
    usado = models.BooleanField(default=False)

    class Meta:
        unique_together = ('folio', 'caf')  # garantiza folios únicos por CAF

    def __str__(self):
        return f"{self.caf.get_tipo_dte_display()} - Folio {self.folio}"


class DetalleSalidaMaterial(models.Model):
    salida = models.ForeignKey(
        'SalidaMaterial', on_delete=models.CASCADE, related_name='detalles'
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    descripcion = models.CharField(max_length=255, blank=True)  # ← nuevo
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    valor_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)  # ← nuevo
    descuento = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)       # ← nuevo

    def calcular_valor_total(self):
        bruto = self.cantidad * self.valor_unitario
        return max(bruto - self.descuento, 0)

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"


User = get_user_model()


def ruta_certificado(instance, filename):
    rut = instance.rut_emisor or 'sin_rut'
    return f"CertificadosDigitales/{rut}/{filename}"


class CertificadoDigital(models.Model):
    archivo = models.FileField(
        upload_to=ruta_certificado,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="Archivo .pfx"
    )
    clave_certificado = models.CharField(max_length=255)
    rut_emisor = models.CharField(max_length=20)
    fecha_inicio = models.DateField(auto_now_add=True)
    activo = models.BooleanField(default=True)
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Certificado de {self.rut_emisor}"


TIPO_DOCUMENTO_CHOICES = [
    ('guia_despacho', 'Guía de Despacho'),
    # Agrega más si aplica
]


def ruta_salida_material(instance, filename):
    fecha = instance.fecha_salida.strftime(
        '%Y-%m-%d') if instance.fecha_salida else date.today().strftime('%Y-%m-%d')
    return f"SalidasMateriales/{fecha}/{instance.numero_documento}/{filename}"


def ruta_xml_firmado(instance, filename):
    fecha = instance.fecha_salida.strftime(
        '%Y-%m-%d') if instance.fecha_salida else date.today().strftime('%Y-%m-%d')
    return f"SalidasMateriales/{fecha}/{instance.numero_documento}/{filename}"


class SalidaMaterial(models.Model):
    fecha_salida = models.DateField(auto_now_add=True)
    bodega = models.ForeignKey(Bodega, on_delete=models.CASCADE)
    id_proyecto = models.CharField(max_length=100)
    tipo_documento = models.CharField(
        max_length=20, choices=TIPO_DOCUMENTO_CHOICES)
    numero_documento = models.CharField(max_length=50)

    entregado_a = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='entregado_salidas'
    )
    emitido_por = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='emitido_salidas'
    )

    archivo_pdf = models.FileField(
        upload_to=ruta_salida_material,
        storage=RawMediaCloudinaryStorage(),
        null=True,
        blank=True
    )

    archivo_xml = models.FileField(
        upload_to=ruta_xml_firmado,
        storage=RawMediaCloudinaryStorage(),
        null=True,
        blank=True,
        verbose_name="XML firmado"
    )

    # Datos del receptor
    rut_receptor = models.CharField(max_length=15)
    nombre_receptor = models.CharField(max_length=255)
    giro_receptor = models.CharField(max_length=255)
    direccion_receptor = models.CharField(max_length=255)
    comuna_receptor = models.CharField(max_length=100)
    ciudad_receptor = models.CharField(max_length=100)
    fecha_emisión = models.DateField(auto_now_add=True)

    # Transporte

    obra = models.CharField(max_length=255)
    chofer = models.CharField(max_length=255)
    rut_transportista = models.CharField(max_length=20)
    patente = models.CharField(max_length=20)
    origen = models.CharField(max_length=255)
    destino = models.CharField(max_length=255)

    observaciones = models.TextField(blank=True)

    # Estado frente al SII
    estado_envio_sii = models.CharField(
        max_length=20,
        choices=[
            ('pendiente', 'Pendiente'),
            ('enviado', 'Enviado'),
            ('aceptado', 'Aceptado'),
            ('rechazado', 'Rechazado'),
        ],
        default='pendiente'
    )
    mensaje_sii = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Guía #{self.numero_documento} - {self.fecha_salida}"

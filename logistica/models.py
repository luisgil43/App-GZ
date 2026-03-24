# logistica/models.py (agregar / restaurar arriba del archivo)
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from operaciones.storage_backends import GZWasabiStorage
from usuarios.models import CustomUser

# =========================
# Wasabi storage
# =========================
_gz_storage = GZWasabiStorage()



def ruta_ingreso_material(instance, filename):
    now = datetime.now()
    mes = now.strftime('%B')
    extension = os.path.splitext(filename)[1]
    numero_doc = getattr(instance, "numero_documento", None) or "documento"
    return f'Ingreso de materiales/{mes}/{numero_doc}{extension}'


def ruta_caf(instance, filename):
    now = datetime.now()
    mes = now.strftime('%B')
    return f"caf/{mes}/{filename}"


def ruta_certificado(instance, filename):
    rut = getattr(instance, "rut_emisor", None) or "sin_rut"
    return f"CertificadosDigitales/{rut}/{filename}"


def ruta_salida_material(instance, filename):
    fecha = getattr(instance, "fecha_salida", None)
    if fecha:
        fecha_str = fecha.strftime("%Y-%m-%d")
    else:
        fecha_str = date.today().strftime("%Y-%m-%d")

    numero = getattr(instance, "numero_documento", None) or "sin_numero"
    return f"SalidasMateriales/{fecha_str}/{numero}/{filename}"


def ruta_xml_firmado(instance, filename):
    fecha = getattr(instance, "fecha_salida", None)
    if fecha:
        fecha_str = fecha.strftime("%Y-%m-%d")
    else:
        fecha_str = date.today().strftime("%Y-%m-%d")

    numero = getattr(instance, "numero_documento", None) or "sin_numero"
    return f"SalidasMateriales/{fecha_str}/{numero}/{filename}"


def _safe_filename(texto: str) -> str:
    """
    Limpia el nombre para filesystem/URL.
    Permite letras, números, espacios, guiones, guiones bajos, puntos y paréntesis.
    """
    t = (texto or "").strip()
    return re.sub(r"[^\w\s\-\.\(\)]", "", t)


def _slug(texto: str) -> str:
    return slugify((texto or "").strip(), allow_unicode=True)


def _yyyy_mm(dt=None) -> str:
    """
    Devuelve YYYY-MM usando fecha local.
    """
    if dt is None:
        return timezone.localdate().strftime("%Y-%m")
    try:
        return timezone.localtime(dt).date().strftime("%Y-%m")
    except Exception:
        return dt.strftime("%Y-%m")


# ==========================================================
# UPLOAD PATHS (WASABI)
# ==========================================================
def upload_to_ingreso_material(instance, filename: str) -> str:
    """
    logistica/ingresos/2026/2026-03/guia/12345/doc_<uuid>.pdf
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".pdf"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    tipo = _slug(getattr(instance, "tipo_documento", "") or "sin-tipo")
    numero = _safe_filename(getattr(instance, "numero_documento", "") or "sin-numero")

    return f"logistica/ingresos/{year}/{month_folder}/{tipo}/{numero}/doc_{uuid4().hex}{ext}"


def upload_to_caf(instance, filename: str) -> str:
    """
    logistica/caf/2026/2026-03/td_52/caf_<uuid>.xml
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".xml"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    td = getattr(instance, "tipo_dte", None) or "sin-td"
    return f"logistica/caf/{year}/{month_folder}/td_{td}/caf_{uuid4().hex}{ext}"


def upload_to_certificado(instance, filename: str) -> str:
    """
    logistica/certificados/<rut>/pfx_<uuid>.pfx
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".pfx"

    rut = _safe_filename(getattr(instance, "rut_emisor", "") or "sin-rut")
    return f"logistica/certificados/{rut}/pfx_{uuid4().hex}{ext}"


def upload_to_salida_pdf(instance, filename: str) -> str:
    """
    logistica/salidas/2026/2026-03/guia/folio_123/pdf_<uuid>.pdf
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".pdf"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    tipo = _slug(getattr(instance, "tipo_documento", "") or "sin-tipo")
    folio = _safe_filename(getattr(instance, "numero_documento", "") or "sin-folio")

    return f"logistica/salidas/{year}/{month_folder}/{tipo}/folio_{folio}/pdf_{uuid4().hex}{ext}"


def upload_to_salida_xml(instance, filename: str) -> str:
    """
    logistica/salidas/2026/2026-03/guia/folio_123/xml_<uuid>.xml
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".xml"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    tipo = _slug(getattr(instance, "tipo_documento", "") or "sin-tipo")
    folio = _safe_filename(getattr(instance, "numero_documento", "") or "sin-folio")

    return f"logistica/salidas/{year}/{month_folder}/{tipo}/folio_{folio}/xml_{uuid4().hex}{ext}"


def upload_to_herramienta_foto(instance, filename: str) -> str:
    """
    logistica/herramientas/fotos/2026/2026-03/<serial>/foto_<uuid>.jpg
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".jpg"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    serial = _safe_filename(getattr(instance, "serial", "") or "sin-serial")
    return f"logistica/herramientas/fotos/{year}/{month_folder}/{serial}/foto_{uuid4().hex}{ext}"


def upload_to_herramienta_inventario(instance, filename: str) -> str:
    """
    logistica/herramientas/inventarios/2026/2026-03/<serial>/inv_<uuid>.jpg
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".jpg"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    serial = _safe_filename(getattr(getattr(instance, "herramienta", None), "serial", "") or "sin-serial")
    return f"logistica/herramientas/inventarios/{year}/{month_folder}/{serial}/inv_{uuid4().hex}{ext}"


# ==========================================================
# BODEGAS (UNIFICADAS)
# ==========================================================
class Bodega(models.Model):
    """
    Bodega única para TODO Logística (materiales + herramientas).
    """
    nombre = models.CharField(max_length=120, unique=True)
    ubicacion = models.CharField(max_length=200, blank=True, null=True)

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bodegas_creadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bodega"
        verbose_name_plural = "Bodegas"
        ordering = ["nombre"]

    def __str__(self) -> str:
        return self.nombre


# ==========================================================
# MATERIALES
# ==========================================================
class Material(models.Model):
    codigo_interno = models.CharField(max_length=50)
    nombre = models.CharField(max_length=255)
    codigo_externo = models.CharField(max_length=50, blank=True, null=True)

    bodega = models.ForeignKey(Bodega, on_delete=models.SET_NULL, null=True, blank=True)

    stock_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    unidad_medida = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    valor_unitario = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Valor unitario ($)",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["codigo_interno", "bodega"], name="unique_codigo_interno_por_bodega"),
            models.UniqueConstraint(fields=["codigo_externo", "bodega"], name="unique_codigo_externo_por_bodega"),
        ]
        ordering = ["nombre"]

    def __str__(self) -> str:
        return f"{self.codigo_interno} - {self.nombre}"

    def clean(self):
        if self.valor_unitario is not None and self.valor_unitario < 0:
            raise ValidationError({"valor_unitario": "El valor unitario no puede ser negativo."})


# ==========================================================
# INGRESO DE MATERIALES
# ==========================================================
class IngresoMaterial(models.Model):
    OPCIONES_TIPO_DOC = [
        ("guia", "Guía de Despacho"),
        ("factura", "Factura"),
    ]

    fecha_ingreso = models.DateField(auto_now_add=True)
    tipo_documento = models.CharField(max_length=10, choices=OPCIONES_TIPO_DOC)
    numero_documento = models.CharField(max_length=50)
    codigo_externo = models.CharField(max_length=50, blank=True, null=True, verbose_name="Código externo")

    bodega = models.ForeignKey(Bodega, on_delete=models.PROTECT, related_name="ingresos")

    # ✅ Wasabi
    archivo_documento = models.FileField(
        upload_to=upload_to_ingreso_material,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["pdf"])],
        null=True,
        blank=True,
        verbose_name="PDF de respaldo",
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-fecha_ingreso", "-id"]
        indexes = [
            models.Index(fields=["tipo_documento", "numero_documento"]),
            models.Index(fields=["bodega", "fecha_ingreso"]),
        ]

    def __str__(self) -> str:
        return f"{self.numero_documento} - {self.get_tipo_documento_display()}"


class DetalleIngresoMaterial(models.Model):
    ingreso = models.ForeignKey(IngresoMaterial, on_delete=models.CASCADE, related_name="detalles")
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.material.nombre} - {self.cantidad}"

    def clean(self):
        if self.cantidad is None or self.cantidad <= 0:
            raise ValidationError({"cantidad": "La cantidad debe ser mayor a cero."})


# ==========================================================
# CAF + FOLIOS (WASABI)
# ==========================================================
class ArchivoCAF(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    nombre_archivo = models.CharField(max_length=255)

    archivo = models.FileField(
        upload_to=upload_to_caf,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["xml"])],
        verbose_name="Archivo CAF (.xml)",
    )

    tipo_dte = models.PositiveIntegerField()
    rango_inicio = models.PositiveIntegerField()
    rango_fin = models.PositiveIntegerField()
    fecha_subida = models.DateTimeField(auto_now_add=True)

    estado = models.CharField(max_length=20, choices=[("activo", "Activo"), ("inactivo", "Inactivo")], default="activo")

    class Meta:
        ordering = ["-fecha_subida", "-id"]
        indexes = [
            models.Index(fields=["tipo_dte", "estado"]),
        ]

    def __str__(self) -> str:
        return f"{self.nombre_archivo} (TD {self.tipo_dte})"


class FolioDisponible(models.Model):
    caf = models.ForeignKey(ArchivoCAF, on_delete=models.CASCADE)
    folio = models.IntegerField()
    usado = models.BooleanField(default=False)

    class Meta:
        unique_together = ("folio", "caf")
        ordering = ["folio"]

    def __str__(self) -> str:
        return f"TD {self.caf.tipo_dte} - Folio {self.folio}"


# ==========================================================
# CERTIFICADOS (WASABI)
# ==========================================================
class CertificadoDigital(models.Model):
    archivo = models.FileField(
        upload_to=upload_to_certificado,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["pfx"])],
        verbose_name="Archivo .pfx",
    )
    clave_certificado = models.CharField(max_length=255)
    rut_emisor = models.CharField(max_length=20)

    fecha_inicio = models.DateField(auto_now_add=True)
    activo = models.BooleanField(default=True)

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-fecha_inicio", "-id"]

    def __str__(self) -> str:
        return f"Certificado de {self.rut_emisor}"


# ==========================================================
# SALIDAS (WASABI)
# ==========================================================
TIPO_DOCUMENTO_CHOICES_SALIDA = [
    ("guia", "Guía de Despacho"),
    ("factura", "Factura"),
]


class SalidaMaterial(models.Model):
    fecha_salida = models.DateField(auto_now_add=True)
    bodega = models.ForeignKey(Bodega, on_delete=models.CASCADE)

    id_proyecto = models.CharField(max_length=100)
    tipo_documento = models.CharField(max_length=20, choices=TIPO_DOCUMENTO_CHOICES_SALIDA)
    numero_documento = models.CharField(max_length=50)

    entregado_a = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="entregado_salidas"
    )
    emitido_por = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="emitido_salidas"
    )

    archivo_pdf = models.FileField(
        upload_to=upload_to_salida_pdf,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["pdf"])],
        null=True,
        blank=True,
    )
    archivo_xml = models.FileField(
        upload_to=upload_to_salida_xml,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["xml"])],
        null=True,
        blank=True,
        verbose_name="XML firmado",
    )

    # Datos del receptor
    rut_receptor = models.CharField(max_length=15)
    nombre_receptor = models.CharField(max_length=255)
    giro_receptor = models.CharField(max_length=255)
    direccion_receptor = models.CharField(max_length=255)
    comuna_receptor = models.CharField(max_length=100)
    ciudad_receptor = models.CharField(max_length=100)
    fecha_emision = models.DateTimeField(default=timezone.now, verbose_name="Fecha emisión")

    # Transporte
    obra = models.CharField(max_length=255)
    chofer = models.CharField(max_length=255)
    rut_transportista = models.CharField(max_length=20)
    patente = models.CharField(max_length=20)
    origen = models.CharField(max_length=255)
    destino = models.CharField(max_length=255)

    observaciones = models.TextField(blank=True)

    estado_envio_sii = models.CharField(
        max_length=20,
        choices=[
            ("pendiente", "Pendiente"),
            ("enviado", "Enviado"),
            ("aceptado", "Aceptado"),
            ("rechazado", "Rechazado"),
        ],
        default="pendiente",
    )
    mensaje_sii = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-fecha_salida", "-id"]
        indexes = [
            models.Index(fields=["tipo_documento", "numero_documento"]),
            models.Index(fields=["fecha_salida"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_documento_display()} #{self.numero_documento} - {self.fecha_salida}"


class DetalleSalidaMaterial(models.Model):
    salida = models.ForeignKey(SalidaMaterial, on_delete=models.CASCADE, related_name="detalles")
    material = models.ForeignKey(Material, on_delete=models.CASCADE)

    descripcion = models.CharField(max_length=255, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    valor_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def calcular_valor_total(self):
        bruto = self.cantidad * self.valor_unitario
        return max(bruto - self.descuento, 0)

    def __str__(self) -> str:
        return f"{self.material.nombre} - {self.cantidad}"


# ==========================================================
# HERRAMIENTAS (WASABI)
# ==========================================================
class Herramienta(models.Model):
    STATUS_CHOICES = [
        ("operativa", "Operativa"),
        ("asignada", "Asignada"),
        ("danada", "Dañada"),
        ("extraviada", "Extraviada"),
        ("robada", "Robada"),
        ("bodega", "En bodega"),
    ]

    nombre = models.CharField(max_length=160)
    descripcion = models.TextField(blank=True, null=True)

    serial = models.CharField(max_length=120, unique=True)

    # ✅ Stock: ahora puede ser 0 (agotado)
    cantidad = models.PositiveIntegerField(
        default=1,
        help_text="Cantidad disponible de esta herramienta/equipo.",
    )

    valor_comercial = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    foto = models.ImageField(
        upload_to=upload_to_herramienta_foto,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
        blank=True,
        null=True,
    )

    bodega = models.ForeignKey(
        Bodega,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="operativa")
    status_justificacion = models.TextField(blank=True, null=True)
    status_changed_at = models.DateTimeField(blank=True, null=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_status_cambiado",
    )

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_creadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Inventario
    inventory_required = models.BooleanField(
        default=False,
        help_text="Si está activo, el usuario debe realizar inventario (subir foto).",
    )
    next_inventory_due = models.DateField(blank=True, null=True)
    last_inventory_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Herramienta"
        verbose_name_plural = "Herramientas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["serial"]),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.serial})"

    def clean(self):
        if self.valor_comercial is not None and self.valor_comercial < 0:
            raise ValidationError({"valor_comercial": "El valor comercial no puede ser negativo."})

        # ✅ Permitir stock 0 (agotado). Bloquear solo negativos.
        if self.cantidad is None:
            self.cantidad = 0
        if int(self.cantidad) < 0:
            raise ValidationError({"cantidad": "La cantidad no puede ser negativa."})

        if self.status in ("danada", "extraviada", "robada"):
            if not (self.status_justificacion or "").strip():
                raise ValidationError({"status_justificacion": "Debes indicar una justificación para este estado."})

    def mark_inventory_due_default(self):
        today = timezone.localdate()
        self.next_inventory_due = today + timedelta(days=60)

    def set_status(self, new_status: str, by_user=None, justification: str = ""):
        new_status = (new_status or "").strip()
        if new_status not in dict(self.STATUS_CHOICES):
            raise ValidationError("Estado inválido.")

        self.status = new_status
        self.status_changed_at = timezone.now()
        self.status_changed_by = by_user

        if new_status in ("danada", "extraviada", "robada"):
            justification = (justification or "").strip()
            if not justification:
                raise ValidationError("Justificación obligatoria para Dañada/Extraviada/Robada.")
            self.status_justificacion = justification
        else:
            self.status_justificacion = (justification or "").strip() or None


class HerramientaAsignacion(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("aceptada", "Aceptada"),
        ("rechazada", "Rechazada"),
        ("terminada", "Terminada"),
    ]

    herramienta = models.ForeignKey(Herramienta, on_delete=models.CASCADE, related_name="asignaciones")
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="herramientas_asignadas",
    )
    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignadas_por",
    )
    asignado_at = models.DateTimeField(default=timezone.now)

    # ✅ NUEVO: cantidad entregada en esta asignación
    cantidad_entregada = models.PositiveIntegerField(default=1)

    # ✅ NUEVO: cierre / devolución
    closed_at = models.DateTimeField(blank=True, null=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignaciones_cerradas_por",
    )
    cantidad_devuelta = models.PositiveIntegerField(blank=True, null=True)
    comentario_cierre = models.TextField(blank=True, null=True)

    # Si devuelta < entregada, se exige justificar
    justificacion_diferencia = models.TextField(blank=True, null=True)

    # responsable actual
    active = models.BooleanField(default=True)

    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    comentario_rechazo = models.TextField(blank=True, null=True)

    aceptado_at = models.DateTimeField(blank=True, null=True)
    rechazado_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Asignación de herramienta"
        verbose_name_plural = "Asignaciones de herramientas"
        ordering = ["-asignado_at"]
        indexes = [
            models.Index(fields=["active", "estado"]),
            models.Index(fields=["asignado_a", "active"]),
            models.Index(fields=["herramienta", "active"]),
        ]

    def __str__(self) -> str:
        return f"{self.herramienta} -> {self.asignado_a} ({self.estado})"

    def clean(self):
        # rechazo requiere comentario
        if self.estado == "rechazada":
            if not (self.comentario_rechazo or "").strip():
                raise ValidationError({"comentario_rechazo": "Debes indicar un comentario si rechazas."})

        # cantidad entregada > 0
        if self.cantidad_entregada is None or int(self.cantidad_entregada) <= 0:
            raise ValidationError({"cantidad_entregada": "La cantidad entregada debe ser mayor a 0."})

        # si está terminada, debe tener cierre coherente
        if self.estado == "terminada":
            if self.cantidad_devuelta is None:
                raise ValidationError({"cantidad_devuelta": "Debes indicar cantidad devuelta."})
            if int(self.cantidad_devuelta) < 0:
                raise ValidationError({"cantidad_devuelta": "Cantidad devuelta inválida."})
            if int(self.cantidad_devuelta) > int(self.cantidad_entregada):
                raise ValidationError({"cantidad_devuelta": "No puede ser mayor que la entregada."})

            dev = int(self.cantidad_devuelta)
            ent = int(self.cantidad_entregada)

            # si devuelta es menor, exigir justificación
            if dev < ent and not (self.justificacion_diferencia or "").strip():
                raise ValidationError({"justificacion_diferencia": "Debes justificar la diferencia (faltante/daño/pérdida)."})

            # si devuelta es 0, exigir comentario
            if dev == 0 and not (self.comentario_cierre or "").strip():
                raise ValidationError({"comentario_cierre": "Si devolvió 0, debes indicar un comentario."})

    def close(self):
        self.active = False
        self.save(update_fields=["active"])


class HerramientaInventario(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("aprobado", "Aprobado"),
        ("rechazado", "Rechazado"),
    ]

    herramienta = models.ForeignKey(Herramienta, on_delete=models.CASCADE, related_name="inventarios")
    asignacion = models.ForeignKey(HerramientaAsignacion, on_delete=models.CASCADE, related_name="inventarios")

    foto = models.ImageField(
        upload_to=upload_to_herramienta_inventario,
        storage=_gz_storage,
        max_length=1024,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default="pendiente")
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventarios_revisados",
    )
    revisado_at = models.DateTimeField(blank=True, null=True)
    motivo_rechazo = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Inventario de herramienta"
        verbose_name_plural = "Inventarios de herramientas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["estado"]),
        ]

    def __str__(self) -> str:
        return f"Inventario {self.herramienta.serial} ({self.estado})"

    def approve(self, by_user):
        self.estado = "aprobado"
        self.revisado_por = by_user
        self.revisado_at = timezone.now()
        self.motivo_rechazo = None
        self.save(update_fields=["estado", "revisado_por", "revisado_at", "motivo_rechazo"])

        h = self.herramienta
        h.last_inventory_at = timezone.now()
        h.inventory_required = False
        h.mark_inventory_due_default()
        h.save(update_fields=["last_inventory_at", "inventory_required", "next_inventory_due", "updated_at"])

    def reject(self, by_user, motivo: str):
        motivo = (motivo or "").strip()
        if not motivo:
            raise ValidationError("Debes indicar motivo de rechazo.")
        self.estado = "rechazado"
        self.revisado_por = by_user
        self.revisado_at = timezone.now()
        self.motivo_rechazo = motivo
        self.save(update_fields=["estado", "revisado_por", "revisado_at", "motivo_rechazo"])

        h = self.herramienta
        h.inventory_required = True
        h.save(update_fields=["inventory_required", "updated_at"])

class HerramientaAsignacionLog(models.Model):
    """
    Historial/auditoría de cambios en asignaciones de herramientas.
    """
    ACCION_CHOICES = [
        ("create", "Creada"),
        ("update", "Editada"),
        ("close", "Cerrada"),
        ("reset", "Reiniciada"),
        ("delete", "Eliminada"),
    ]

    asignacion = models.ForeignKey(
        HerramientaAsignacion,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES)
    by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignaciones_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # JSON con resumen de cambios. Ej:
    # {"cantidad_entregada": {"from": 2, "to": 3}, "asignado_at": {"from": "...", "to": "..."}}
    cambios = models.JSONField(default=dict, blank=True)

    nota = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["accion", "created_at"]),
            models.Index(fields=["asignacion", "created_at"]),
        ]

    def __str__(self):
        return f"{self.asignacion_id} {self.accion} {self.created_at}"
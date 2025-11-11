import os
import re
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from operaciones.storage_backends import GZWasabiStorage
from usuarios.models import CustomUser

# === Storage Wasabi (GZ Services) ===
_gz_storage = GZWasabiStorage()


# ===== Utilidades de nombres/rutas =====
def _slug(texto: str) -> str:
    return slugify((texto or "").strip(), allow_unicode=True)


def _safe_filename(texto: str) -> str:
    """
    Limpia el nombre para filesystem/URL.
    Permite letras, números, espacios, guiones, guiones bajos, puntos y paréntesis.
    """
    t = (texto or "").strip()
    return re.sub(r'[^\w\s\-\.\(\)]', '', t)


def _site_name_for(servicio) -> str:
    """Busca el nombre del sitio por id_claro en SitioMovil."""
    try:
        sm = SitioMovil.objects.filter(id_claro=servicio.id_claro).first()
        if sm and sm.nombre:
            return sm.nombre.strip()
    except Exception:
        pass
    return "SinNombre"


def _excel_filename(servicio) -> str:
    """
    Nombre exacto del reporte final:
    "<id_claro>_<Nombre del Sitio> - Mantencion Correctiva.xlsx"
    Si falta id_claro, usa DU con padding.
    """
    id_txt = servicio.id_claro or f"DU{str(servicio.du).zfill(8)}"
    nombre = _site_name_for(servicio)
    return f"{_safe_filename(id_txt)}_{_safe_filename(nombre)} - Mantencion Correctiva.xlsx"


def _pdf_filename(servicio, documento_compra: str) -> str:
    """
    "Acta Aceptacion (<doc_compra>)_(<id_claro>) Grupo GZS Services.pdf"
    """
    id_txt = servicio.id_claro or f"DU{str(servicio.du).zfill(8)}"
    return (
        f"Acta Aceptacion ({_safe_filename(documento_compra)})_"
        f"({_safe_filename(id_txt)}) Grupo GZS Services.pdf"
    )


def _du_base_folder(servicio) -> str:
    """
    Carpeta base COMÚN para todo lo del DU (evidencias, reporte, acta):
    operaciones/reporte_fotografico/<AÑO>/DU########
    """
    year = timezone.now().strftime("%Y")
    du_txt = f"DU{str(servicio.du).zfill(8)}"
    return f"operaciones/reporte_fotografico/{year}/{du_txt}"


def upload_to_evidencia(instance, filename: str) -> str:
    """
    Guarda evidencias en:
    operaciones/reporte_fotografico/<AÑO>/DU<########>/<Técnico Nombre>/Evidencias/<archivo>
    """
    try:
        servicio = instance.tecnico_sesion.sesion.servicio
        base = _du_base_folder(servicio)
    except Exception:
        # Fallbacks por si algo no está cargado aún
        base = f"operaciones/reporte_fotografico/{timezone.now().strftime('%Y')}/DU00000000"

    # Nombre del técnico legible, saneado
    try:
        tecnico = instance.tecnico_sesion.tecnico
        tecnico_name = tecnico.get_full_name() or tecnico.username or "Tecnico"
    except Exception:
        tecnico_name = "Tecnico"
    tecnico_name = _safe_filename(tecnico_name)

    # Nombre de archivo seguro
    fname = _safe_filename(filename)

    return f"{base}/{tecnico_name}/Evidencias/{fname}"


def upload_to_reporte(servicio, filename: str) -> str:
    """
    Reporte fotográfico (Excel) en la MISMA base del DU, carpeta Documentos.
    """
    base = _du_base_folder(servicio)
    return f"{base}/Documentos/{_excel_filename(servicio)}"


def upload_to_acta(servicio, documento_compra: str) -> str:
    """
    Acta (PDF) en la MISMA base del DU, carpeta Documentos.
    """
    base = _du_base_folder(servicio)
    return f"{base}/Documentos/{_pdf_filename(servicio, documento_compra)}"


def upload_to_reporte_field(instance, filename: str) -> str:
    # instance es ServicioCotizado
    return upload_to_reporte(instance, filename)


def upload_to_acta_field(instance, filename: str) -> str:
    """
    El generador de PDF calculará el nombre final con documento_compra y lo pasará
    como filename; aquí solo aseguramos la carpeta dentro del DU.
    """
    base = _du_base_folder(instance)
    return f"{base}/Documentos/{_safe_filename(filename)}"


# ===== Modelos =====
class SitioMovil(models.Model):
    id_sites = models.CharField(max_length=100, unique=True)
    id_claro = models.CharField(max_length=100, blank=True, null=True)
    id_sites_new = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=100, blank=True, null=True)
    nombre = models.CharField(max_length=255, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)

    # Coordenadas como float
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
        ('asignado', 'Asignado'),
        ('en_progreso', 'En progreso'),
        ('en_revision_supervisor', 'En revision supervisor'),
        ('rechazado_supervisor', 'Rechazado por supervisor'),
        ('aprobado_supervisor', 'Aprobado por supervisor'),



    ]

    du = models.CharField(max_length=20, blank=True, unique=True)
    id_claro = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=100, blank=True, null=True)
    mes_produccion = models.CharField(max_length=20)
    id_new = models.CharField(max_length=100, blank=True, null=True)
    detalle_tarea = models.TextField()
    monto_cotizado = models.DecimalField(max_digits=12, decimal_places=2)
    monto_mmoo = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True)

    estado = models.CharField(
        max_length=50, choices=ESTADOS, default='cotizado')

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

    fecha_aprobacion_supervisor = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha aprobación del supervisor"
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

    usuario_informe = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='informes_cargados',
        verbose_name='Usuario que cargó el informe'
    )

    motivo_rechazo = models.TextField(blank=True, null=True)

    # === Archivos finales del proyecto ===
    reporte_fotografico = models.FileField(
        upload_to=upload_to_reporte_field,
        storage=_gz_storage,
        blank=True, null=True, max_length=1024
    )
    acta_aceptacion_pdf = models.FileField(
        upload_to=upload_to_acta_field,
        storage=_gz_storage,
        blank=True, null=True, max_length=1024,
        validators=[FileExtensionValidator(["pdf"])]
    )

    # Datos para el acta
    monto_uf = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True,
        help_text="Monto en UF para el acta"
    )
    documento_compra = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="N° de OC/Documento de compra para el acta"
    )

    class Meta:
        managed = True

    def save(self, *args, **kwargs):
        if not self.du:
            ultimo = ServicioCotizado.objects.exclude(
                du='').order_by('-du').first()
            if ultimo and ultimo.du.isdigit():
                nuevo = str(int(ultimo.du) + 1).zfill(8)
            else:
                nuevo = '00000001'
            # Asegura que no exista el nuevo DU
            while ServicioCotizado.objects.filter(du=nuevo).exists():
                nuevo = str(int(nuevo) + 1).zfill(8)
            self.du = nuevo
        super().save(*args, **kwargs)

    def __str__(self):
        return f"DU {self.du} - {self.id_claro}"


class SesionFotos(models.Model):
    """
    Sesión de reporte fotográfico por servicio (proyecto).
    Guarda estado del flujo y si es proyecto especial (sin lista fija).
    """
    ESTADOS = [
        ('asignado', 'Asignado'),
        ('en_proceso', 'En proceso'),
        ('en_revision_supervisor', 'En revisión del supervisor'),
        ('rechazado_supervisor', 'Rechazado por supervisor'),
        ('aprobado_supervisor', 'Aprobado por supervisor'),
        ('rechazado_pm', 'Rechazado por PM'),
        ('aprobado_pm', 'Aprobado por PM'),
    ]

    servicio = models.OneToOneField(
        ServicioCotizado, on_delete=models.CASCADE, related_name='sesion_fotos'
    )
    estado = models.CharField(
        max_length=32, choices=ESTADOS, default='asignado')
    proyecto_especial = models.BooleanField(default=False)

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Sesión fotos DU{self.servicio.du} ({self.servicio.id_claro})"


class SesionFotoTecnico(models.Model):
    ESTADOS = [
        ('asignado', 'Asignado'),
        ('en_proceso', 'En proceso'),
        ('en_revision_supervisor', 'En revisión del supervisor'),
        ('rechazado_supervisor', 'Rechazado por supervisor'),
        ('aprobado_supervisor', 'Aprobado por supervisor'),
        ('rechazado_pm', 'Rechazado por PM'),
        ('aprobado_pm', 'Aprobado por PM'),
    ]

    sesion = models.ForeignKey(
        SesionFotos, on_delete=models.CASCADE, related_name='asignaciones'
    )
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='asignaciones_fotos'
    )
    estado = models.CharField(
        max_length=32, choices=ESTADOS, default='asignado')
    aceptado_en = models.DateTimeField(blank=True, null=True)
    finalizado_en = models.DateTimeField(blank=True, null=True)
    reintento_habilitado = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.sesion} -> {self.tecnico}"


import re
import unicodedata  # si aún no los tienes en models.py

# imports (arriba del models.py)
from django.db.models import Q


def _norm_title_m(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s)


class RequisitoFoto(models.Model):
    tecnico_sesion = models.ForeignKey(
        SesionFotoTecnico, on_delete=models.CASCADE, related_name='requisitos'
    )
    titulo = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, default="")
    obligatorio = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)

    # NUEVO: título normalizado (para comparar/constraint)
    titulo_norm = models.CharField(max_length=220, db_index=True, editable=False, default="")

    class Meta:
        ordering = ['orden', 'id']
        constraints = [
            # ÚNICO por asignación y título normalizado SOLO cuando activo=True
            models.UniqueConstraint(
                fields=['tecnico_sesion', 'titulo_norm'],
                name='uq_req_norm_asig_activo',
                condition=Q(activo=True),
            ),
        ]

    def save(self, *args, **kwargs):
        self.titulo_norm = _norm_title_m(self.titulo)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.orden}] {self.titulo}"

class EvidenciaFoto(models.Model):
    tecnico_sesion = models.ForeignKey(
        SesionFotoTecnico, on_delete=models.CASCADE, related_name='evidencias'
    )
    requisito = models.ForeignKey(
        RequisitoFoto, on_delete=models.SET_NULL, null=True, blank=True, related_name='evidencias'
    )
    imagen = models.FileField(
        upload_to=upload_to_evidencia,
        storage=_gz_storage, max_length=1024
    )
    nota = models.TextField(blank=True, default="")

    # Metadatos de captura
    lat = models.FloatField(blank=True, null=True)
    lng = models.FloatField(blank=True, null=True)
    gps_accuracy_m = models.FloatField(blank=True, null=True)
    client_taken_at = models.DateTimeField(blank=True, null=True)
    tomada_en = models.DateTimeField(auto_now_add=True)

    # Para proyectos especiales (cuando no hay requisito asociado)
    titulo_manual = models.CharField(max_length=200, blank=True, default="")
    direccion_manual = models.CharField(max_length=200, blank=True, default="")

    def __str__(self):
        return f"Evidencia {self.id} de {self.tecnico_sesion}"


# operaciones/models.py (añade al final del archivo, junto a tus imports existentes)


def _name_slug(user) -> str:
    base = (getattr(user, "get_full_name", lambda: "")()
            or getattr(user, "username", "")
            or "").strip()
    return slugify(base) or "user"


def upload_to_payment_receipt(instance, filename: str) -> str:
    """
    operaciones/pagos/<YYYY-MM>/<nombre-slug>/receipt_<uuid>.<ext>
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or ".pdf").lower()
    folder = _name_slug(getattr(instance, "technician", None))
    return f"operaciones/pagos/{instance.month}/{folder}/receipt_{uuid4().hex}{ext}"


class MonthlyPayment(models.Model):
    """
    1 registro por técnico y MES de pago (YYYY-MM).
    amount = total mensual prorrateado desde producción aprobada.
    """
    STATUS = [
        ("pending_user", "Pendiente aprobación del técnico"),
        ("approved_user", "Aprobado por el técnico"),
        ("rejected_user", "Rechazado por el técnico"),
        ("pending_payment", "Pendiente de pago"),
        ("paid", "Pagado"),
    ]

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_payments",
    )
    month = models.CharField(max_length=7, db_index=True)  # YYYY-MM
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=STATUS,
                              default="pending_user", db_index=True)
    reject_reason = models.TextField(blank=True, default="")

    # Mes efectivo en que se marcó como pagado (YYYY-MM)
    paid_month = models.CharField(max_length=7, blank=True, default="")

    # Comprobante en Wasabi
    receipt = models.FileField(
        upload_to=upload_to_payment_receipt,
        storage=_gz_storage,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
        blank=True, null=True, max_length=1024,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("technician", "month")]
        ordering = ["-month", "technician_id"]
        indexes = [
            models.Index(fields=["month", "status"]),
            models.Index(fields=["technician", "month"]),
        ]

    def __str__(self):
        return f"{self.technician} • {self.month} • {self.amount}"

    def mark_paid(self, paid_month: str | None = None):
        if not paid_month:
            paid_month = _yyyy_mm(timezone.now())
        self.status = "paid"
        self.paid_month = paid_month
        self.save(update_fields=["status", "paid_month", "updated_at"])

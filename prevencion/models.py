from __future__ import annotations

import os
import re
from datetime import date
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from operaciones.storage_backends import GZWasabiStorage
from usuarios.models import CustomUser

# =========================
# Wasabi storage
# =========================
_gz_storage = GZWasabiStorage()


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


def upload_to_prevencion_document(instance, filename: str) -> str:
    """
    Guarda documentos de prevención en Wasabi, separados por año/mes/scope/tipo.

    Ejemplo:
    prevencion/documentos/2026/2026-03/trabajador/reglamento-interno/doc_<uuid>.pdf
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower()

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    scope = _safe_filename(getattr(instance, "scope", "") or "sin-scope").lower()
    tipo = _slug(getattr(getattr(instance, "doc_type", None), "name", "") or "sin-tipo")

    return f"prevencion/documentos/{year}/{month_folder}/{scope}/{tipo}/doc_{uuid4().hex}{ext}"


class PrevencionDocumentType(models.Model):
    SCOPE_CHOICES = [
        ("empresa", "Empresa"),
        ("trabajador", "Trabajador"),
        ("ambos", "Ambos"),
    ]

    name = models.CharField(max_length=120, unique=True)
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default="trabajador")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PrevencionDocument(models.Model):
    """
    Un documento es una versión (archivo + fechas).
    - Si se reemplaza, se crea un nuevo PrevencionDocument y el anterior queda vinculado por replaced_by.
    - current=True marca la versión vigente por (tipo + scope + trabajadores).
    """

    STATUS_CHOICES = [
        ("vigente", "Vigente"),
        ("proximo", "Próximo a vencer"),
        ("vencido", "Vencido"),
        ("sin_vencimiento", "Sin vencimiento"),
    ]

    doc_type = models.ForeignKey(
        PrevencionDocumentType,
        on_delete=models.PROTECT,
        related_name="documents",
    )

    # Alcance efectivo del documento (copiado del tipo para congelar)
    scope = models.CharField(max_length=20, choices=PrevencionDocumentType.SCOPE_CHOICES)

    # Para documentos de trabajador o ambos: puede aplicar a 1 o varios
    workers = models.ManyToManyField(
        CustomUser,
        blank=True,
        related_name="prevencion_documents",
        help_text="Aplica si el documento es de trabajador o ambos.",
    )

    # NUEVO:
    # Cuando está activo, el documento aplica a todos los trabajadores
    # actuales y futuros. Útil sobre todo para documentos scope='ambos'.
    apply_to_all_workers = models.BooleanField(
        default=False,
        help_text="Si está marcado, el documento aplica a todos los trabajadores actuales y futuros.",
    )

    title = models.CharField(max_length=200, blank=True, default="")

    file = models.FileField(
        upload_to=upload_to_prevencion_document,
        storage=_gz_storage,
        max_length=1024,
        validators=[
            FileExtensionValidator(
                ["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"]
            )
        ],
        blank=False,
        null=False,
    )

    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    no_requiere_vencimiento = models.BooleanField(
        default=False,
        help_text="Si está marcado, el documento no tiene caducidad y no entra en el cron.",
    )

    # Notificaciones por documento (se puede desactivar; al reemplazar, el viejo se apaga)
    notify_enabled = models.BooleanField(default=True)

    # Control de vigencia de versión
    current = models.BooleanField(default=True)

    replaced_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaces",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="prevencion_docs_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["current", "notify_enabled"]),
            models.Index(fields=["scope", "current"]),
            models.Index(fields=["expiry_date"]),
            models.Index(fields=["apply_to_all_workers"]),
        ]

    def __str__(self) -> str:
        return f"{self.doc_type.name} ({self.get_scope_display()}) #{self.pk}"

    def clean_dates(self):
        if self.no_requiere_vencimiento:
            self.issue_date = self.issue_date or None
            self.expiry_date = None
            return
        return

    def save(self, *args, **kwargs):
        if not self.scope and self.doc_type_id:
            self.scope = self.doc_type.scope

        # Reglas de consistencia del nuevo flag
        if self.scope == "empresa":
            self.apply_to_all_workers = False

        if self.scope == "trabajador":
            self.apply_to_all_workers = False

        self.clean_dates()
        super().save(*args, **kwargs)

    # =========================
    # Status dinámico
    # =========================
    def compute_status(self, today: date | None = None) -> str:
        today = today or timezone.localdate()

        if self.no_requiere_vencimiento or not self.expiry_date:
            return "sin_vencimiento"

        remaining = (self.expiry_date - today).days

        if remaining <= 0:
            return "vencido"
        if remaining <= 20:
            return "proximo"
        return "vigente"

    def status_label(self, today: date | None = None) -> str:
        m = {
            "vigente": "Vigente",
            "proximo": "Próximo a vencer",
            "vencido": "Vencido",
            "sin_vencimiento": "Sin vencimiento",
        }
        return m.get(self.compute_status(today=today), "—")

    def remaining_days(self, today: date | None = None) -> int | None:
        today = today or timezone.localdate()
        if self.no_requiere_vencimiento or not self.expiry_date:
            return None
        return (self.expiry_date - today).days

    # =========================
    # Helpers de grouping
    # =========================
    def workers_display(self) -> str:
        if self.apply_to_all_workers and self.scope == "ambos":
            return "Todos los trabajadores"

        qs = self.workers.all().only("first_name", "last_name", "username")
        names = []
        for u in qs:
            nm = (u.get_full_name() or u.username or "").strip()
            if nm:
                names.append(nm)
        return ", ".join(names) if names else "—"

    def applies_to_worker(self, worker: CustomUser) -> bool:
        """
        Helper útil para futuros listados / validaciones:
        - trabajador: aplica si el worker está vinculado
        - ambos + global: aplica a todos
        - ambos + segmentado: aplica si el worker está vinculado
        - empresa: no es documento por trabajador
        """
        if self.scope == "empresa":
            return False

        if self.scope == "ambos" and self.apply_to_all_workers:
            return True

        if not worker or not getattr(worker, "pk", None):
            return False

        return self.workers.filter(pk=worker.pk).exists()


class PrevencionNotificationSettings(models.Model):
    """
    Settings globales tipo Flota.
    """
    enabled = models.BooleanField(default=True)
    include_worker = models.BooleanField(
        default=True,
        help_text="Si el documento es de trabajador, incluir el correo del/los trabajadores (si tienen email).",
    )
    extra_to = models.TextField(blank=True, default="", help_text="Correos TO separados por coma.")
    extra_cc = models.TextField(blank=True, default="", help_text="Correos CC separados por coma.")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Prevención - Configuración de notificaciones"
        verbose_name_plural = "Prevención - Configuración de notificaciones"

    def __str__(self) -> str:
        return "Notificaciones Prevención"

    def get_to_emails(self) -> list[str]:
        return _parse_emails(self.extra_to)

    def get_cc_emails(self) -> list[str]:
        return _parse_emails(self.extra_cc)


def _parse_emails(raw: str) -> list[str]:
    out: list[str] = []
    if not raw:
        return out
    for part in raw.split(","):
        e = (part or "").strip()
        if e:
            out.append(e)

    seen = set()
    uniq = []
    for e in out:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


class PrevencionCronDiarioEjecutado(models.Model):
    nombre = models.CharField(max_length=64)
    fecha = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("nombre", "fecha")]

    def __str__(self) -> str:
        return f"{self.nombre} {self.fecha}"


class PrevencionAlertaEnviada(models.Model):
    """
    Tracking para NO spamear:
    - pre: una sola vez por doc + threshold (20/10/5/1) -> sent_on NULL
    - overdue: una vez por día por doc -> threshold=0 y sent_on=hoy
    """
    doc = models.ForeignKey(
        PrevencionDocument,
        on_delete=models.CASCADE,
        related_name="alerts_sent",
    )
    mode = models.CharField(max_length=20)  # pre_days | overdue_days
    threshold = models.IntegerField(default=0)
    sent_on = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["mode", "threshold", "sent_on"]),
            models.Index(fields=["doc", "mode", "threshold"]),
        ]

    def __str__(self) -> str:
        return f"Alert {self.mode} t={self.threshold} doc={self.doc_id}"
# notificaciones/models.py

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotificationLog(models.Model):
    """
    Log central de todas las notificaciones salientes
    (Telegram, correo, etc.).
    """

    CANAL_TELEGRAM = "telegram"
    CANAL_EMAIL = "email"

    CANAL_CHOICES = [
        (CANAL_TELEGRAM, "Telegram"),
        (CANAL_EMAIL, "Correo"),
    ]

    # Puedes ir ampliando estos tipos
    TIPO_SERVICIO_ASIGNADO = "servicio_asignado"
    TIPO_SERVICIO_APROBADO_SUP = "servicio_aprobado_supervisor"
    TIPO_SERVICIO_RECHAZADO_SUP = "servicio_rechazado_supervisor"
    TIPO_CONTRATO_GENERADO = "contrato_generado"
    TIPO_OTRO = "otro"

    TIPO_CHOICES = [
        (TIPO_SERVICIO_ASIGNADO, "Servicio asignado"),
        (TIPO_SERVICIO_APROBADO_SUP, "Servicio aprobado por supervisor"),
        (TIPO_SERVICIO_RECHAZADO_SUP, "Servicio rechazado por supervisor"),
        (TIPO_CONTRATO_GENERADO, "Contrato generado"),
        (TIPO_OTRO, "Otro"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_ERROR = "error"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_SENT, "Enviado"),
        (STATUS_ERROR, "Error"),
    ]

    # A quién se envía
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notificaciones_recibidas",
    )

    # Quién originó la acción (ej: supervisor que asignó o aprobó)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notificaciones_generadas",
    )

    canal = models.CharField(
        max_length=20,
        choices=CANAL_CHOICES,
    )

    tipo = models.CharField(
        max_length=50,
        choices=TIPO_CHOICES,
        default=TIPO_OTRO,
    )

    # Texto que se envía (preview)
    titulo = models.CharField(max_length=255, blank=True, default="")
    mensaje = models.TextField()

    # Datos extra en texto plano (JSON serializado, contexto, etc.)
    extra = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    error = models.TextField(blank=True, default="")

    # Enlace opcional a la app web
    url = models.URLField(blank=True, null=True)

    # Relación con operaciones (no obligatorio)
    servicio = models.ForeignKey(
        "operaciones.ServicioCotizado",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notificaciones",
    )
    du = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="DU relacionado, para buscar rápido aunque el servicio ya no exista.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["canal", "tipo", "status"]),
            models.Index(fields=["du"]),
        ]

    def mark_sent(self):
        self.status = self.STATUS_SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at"])

    def mark_error(self, error_msg: str):
        self.status = self.STATUS_ERROR
        self.error = (error_msg or "")[:4000]
        self.save(update_fields=["status", "error"])

    def __str__(self):
        return f"[{self.get_canal_display()}] {self.get_tipo_display()} -> {self.usuario} ({self.status})"
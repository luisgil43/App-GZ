from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models


def geo_upload_to(instance, filename: str) -> str:
    """
    Guardará: geo_cam/<user_id>/<YYYY>/<MM>/<DD>/<HHMMSS>_<filename>
    """
    now = datetime.utcnow()
    stamp = now.strftime("%H%M%S")
    return f"geo_cam/{instance.user_id}/{now:%Y/%m/%d}/{stamp}_{filename}"


class GeoPhoto(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="geo_photos",
        verbose_name="Usuario",
        db_index=True,
    )

    image = models.ImageField(
        upload_to=geo_upload_to,
        verbose_name="Imagen",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creado en",
        db_index=True,
    )

    # Metadatos
    titulo_manual = models.CharField(
        "Título (manual)",
        max_length=200,
        blank=True,
        default="",
    )

    lat = models.DecimalField("Latitud", max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField("Longitud", max_digits=9, decimal_places=6, null=True, blank=True)
    acc = models.FloatField("Precisión GPS (m)", null=True, blank=True)

    client_taken_at = models.DateTimeField(
        "Tomada en (cliente)",
        null=True,
        blank=True,
        help_text="Fecha/hora local del dispositivo al momento de la toma.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Foto con GPS"
        verbose_name_plural = "Fotos con GPS"
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        base = self.titulo_manual or self.image.name.rsplit("/", 1)[-1]
        return f"{base} — {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lng is not None

    @staticmethod
    def _to_decimal_or_none(v):
        try:
            return Decimal(str(v))
        except Exception:
            return None
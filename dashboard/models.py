from django.db import models
# Importa el modelo de usuario personalizado de forma segura
from django.conf import settings


class ProduccionTecnico(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # Esto enlaza con usuarios.CustomUser automáticamente
        on_delete=models.CASCADE,
        related_name='producciones_dashboard'
    )

    class Meta:
        verbose_name = "Producción Técnica"
        verbose_name_plural = "Producciones Técnicas"

    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    id = models.CharField(
        max_length=100,
        unique=True,
        primary_key=True,
        verbose_name="ID de Producción"
    )

    status = models.CharField(
        max_length=20,
        choices=ESTADOS,
        verbose_name="Estado"
    )

    fecha_aprobacion = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha de Aprobación"
    )

    descripcion = models.TextField(
        blank=True,
        verbose_name="Descripción"
    )

    mes = models.CharField(
        max_length=20,
        verbose_name="Mes"
    )

    def __str__(self):
        return f"{self.tecnico} - {self.id}"

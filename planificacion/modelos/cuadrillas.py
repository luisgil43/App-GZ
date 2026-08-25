from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class CuadrillaOperativa(models.Model):
    """
    Catálogo maestro de cuadrillas disponibles para planificación.

    Esta entidad representa la cuadrilla de forma permanente.

    Ejemplos iniciales:

    - Cuadrilla 1
      4x4
      Exequiel Fernández 499, Ñuñoa
      Urbano / Rural

    - Cuadrilla 2
      Partner
      San Pablo 1539, Santiago Centro
      Solo urbano

    - Cuadrilla 3
      4x4
      Avenida Ejército Libertador 521, Santiago Centro
      Urbano / Rural

    La disponibilidad concreta de cada semana continúa viviendo
    en DisponibilidadCuadrillaSemana.
    """

    # ========================================================
    # IDENTIFICACIÓN
    # ========================================================

    codigo = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text=(
            "Código interno único. " "Ejemplo: cuadrilla_1, cuadrilla_2, cuadrilla_4."
        ),
    )

    nombre = models.CharField(
        max_length=100,
    )

    activa = models.BooleanField(
        default=True,
        db_index=True,
    )

    orden = models.PositiveSmallIntegerField(
        default=0,
        db_index=True,
        help_text=("Orden visual de la cuadrilla dentro de planificación."),
    )

    # ========================================================
    # INTEGRANTES
    # ========================================================

    integrantes = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="cuadrillas_operativas",
        help_text=(
            "Usuarios operativos que conforman " "habitualmente esta cuadrilla."
        ),
    )

    # ========================================================
    # VEHÍCULO / CAPACIDAD TERRITORIAL
    # ========================================================

    tipo_vehiculo = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=("Ejemplo: 4x4, Partner, camioneta, furgón."),
    )

    permite_urbano = models.BooleanField(
        default=True,
    )

    permite_rural = models.BooleanField(
        default=False,
    )

    # ========================================================
    # BASE OPERACIONAL HABITUAL
    # ========================================================

    direccion_base = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    base_nombre = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text=(
            "Nombre corto de la base. " "Ejemplo: Base Ñuñoa, Base Santiago Centro."
        ),
    )

    base_latitud = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(-90),
            MaxValueValidator(90),
        ],
    )

    base_longitud = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(-180),
            MaxValueValidator(180),
        ],
    )

    # ========================================================
    # VALORES OPERACIONALES POR DEFECTO
    # ========================================================

    minutos_jornada_default = models.PositiveSmallIntegerField(
        default=540,
        help_text=("Duración nominal de la jornada en minutos. " "540 = 9 horas."),
    )

    minutos_trabajo_sitio_default = models.PositiveSmallIntegerField(
        default=165,
        help_text=("Tiempo estimado inicial por sitio. " "165 = 2 horas 45 minutos."),
    )

    # ========================================================
    # OBSERVACIONES
    # ========================================================

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # AUDITORÍA
    # ========================================================

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuadrillas_operativas_creadas",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuadrillas_operativas_actualizadas",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "orden",
            "nombre",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "activa",
                    "orden",
                ]
            ),
            models.Index(
                fields=[
                    "permite_rural",
                    "activa",
                ]
            ),
        ]

        verbose_name = "Cuadrilla operativa"
        verbose_name_plural = "Cuadrillas operativas"

    def __str__(self):
        return self.nombre or self.codigo

    # ========================================================
    # BASE OPERACIONAL
    # ========================================================

    @property
    def tiene_base_operacional(self):
        """
        Indica si la cuadrilla posee coordenadas suficientes
        para ser utilizada por el motor de planificación.
        """

        return self.base_latitud is not None and self.base_longitud is not None

    # ========================================================
    # FORMATO HUMANO DE JORNADA
    # ========================================================

    @property
    def jornada_formateada(self):
        """
        Convierte minutos_jornada_default a texto amigable.

        Ejemplos:

        600 -> 10 h
        540 -> 9 h
        570 -> 9 h 30 min
        45  -> 45 min
        """

        total_minutos = self.minutos_jornada_default or 0

        horas = total_minutos // 60

        minutos = total_minutos % 60

        if horas and minutos:
            return f"{horas} h " f"{minutos} min"

        if horas:
            return f"{horas} h"

        return f"{minutos} min"

    # ========================================================
    # FORMATO HUMANO DE TIEMPO POR SITIO
    # ========================================================

    @property
    def trabajo_sitio_formateado(self):
        """
        Convierte minutos_trabajo_sitio_default
        a texto amigable.

        Ejemplos:

        180 -> 3 h
        165 -> 2 h 45 min
        150 -> 2 h 30 min
        45  -> 45 min
        """

        total_minutos = self.minutos_trabajo_sitio_default or 0

        horas = total_minutos // 60

        minutos = total_minutos % 60

        if horas and minutos:
            return f"{horas} h " f"{minutos} min"

        if horas:
            return f"{horas} h"

        return f"{minutos} min"

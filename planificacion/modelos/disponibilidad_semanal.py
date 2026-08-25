from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from planificacion.modelos.cuadrillas import CuadrillaOperativa


class DisponibilidadCuadrillaSemana(models.Model):
    """
    Disponibilidad operacional de una cuadrilla dentro de una semana.

    ARQUITECTURA ACTUAL:

    - `cuadrilla_operativa` es la relación nueva y dinámica con el
      catálogo maestro CuadrillaOperativa.

    - `cuadrilla` se conserva temporalmente por compatibilidad con
      registros históricos creados antes del catálogo maestro.

    - Los nuevos registros deben utilizar `cuadrilla_operativa`.

    - Los campos base_nombre/base_latitud/base_longitud funcionan
      como overrides semanales. Si no poseen valor, se utilizan los
      valores habituales de CuadrillaOperativa.

    - Los tiempos de jornada y trabajo por sitio también pueden
      sobrescribirse para una semana específica.

    Esta estructura permite incorporar C4, C5, C6, etc. sin modificar
    nuevamente el modelo.
    """

    # ========================================================
    # LEGACY / COMPATIBILIDAD
    # ========================================================

    CUADRILLA_1 = "cuadrilla_1"
    CUADRILLA_2 = "cuadrilla_2"
    CUADRILLA_3 = "cuadrilla_3"

    CUADRILLAS = [
        (CUADRILLA_1, "Cuadrilla 1"),
        (CUADRILLA_2, "Cuadrilla 2"),
        (CUADRILLA_3, "Cuadrilla 3"),
    ]

    # ========================================================
    # MODALIDAD
    # ========================================================

    LUNES_VIERNES = "lunes_viernes"
    LUNES_SABADO = "lunes_sabado"

    MODALIDADES = [
        (LUNES_VIERNES, "Lunes a viernes"),
        (LUNES_SABADO, "Lunes a sábado"),
    ]

    # ========================================================
    # SEMANA
    # ========================================================

    configuracion_semana = models.ForeignKey(
        "planificacion.ConfiguracionSemana",
        on_delete=models.CASCADE,
        related_name="disponibilidades_cuadrillas",
    )

    # ========================================================
    # CUADRILLA MAESTRA
    # ========================================================

    cuadrilla_operativa = models.ForeignKey(
        CuadrillaOperativa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="disponibilidades_semanales",
        help_text=(
            "Cuadrilla maestra asociada a esta disponibilidad semanal. "
            "Los nuevos registros deben utilizar este campo."
        ),
    )

    # ========================================================
    # CAMPO LEGACY
    # ========================================================

    cuadrilla = models.CharField(
        max_length=30,
        choices=CUADRILLAS,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Campo histórico utilizado antes de CuadrillaOperativa. "
            "Se conserva temporalmente para compatibilidad."
        ),
    )

    # ========================================================
    # DISPONIBILIDAD
    # ========================================================

    modalidad = models.CharField(
        max_length=30,
        choices=MODALIDADES,
        default=LUNES_VIERNES,
        db_index=True,
    )

    activa = models.BooleanField(
        default=True,
        db_index=True,
    )

    # ========================================================
    # CAPACIDAD NOMINAL
    # ========================================================

    capacidad_diaria_objetivo = models.PositiveSmallIntegerField(
        default=3,
        help_text=(
            "Referencia nominal de sitios diarios. "
            "No obliga al motor a ejecutar esa cantidad. "
            "El cálculo operacional puede determinar 1, 2 o 3 "
            "según desplazamientos, jornada y tiempo por sitio."
        ),
    )

    # ========================================================
    # OVERRIDE DE BASE PARA ESTA SEMANA
    # ========================================================

    base_nombre = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text=(
            "Override opcional del nombre de la base para esta semana. "
            "Vacío = utilizar la base habitual de la cuadrilla."
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
        help_text=(
            "Override opcional de latitud para esta semana. "
            "Vacío = utilizar la latitud habitual de la cuadrilla."
        ),
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
        help_text=(
            "Override opcional de longitud para esta semana. "
            "Vacío = utilizar la longitud habitual de la cuadrilla."
        ),
    )

    # ========================================================
    # OVERRIDE OPERACIONAL DE LA SEMANA
    # ========================================================

    minutos_jornada_objetivo = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Override semanal de duración de jornada. "
            "Vacío = utilizar el valor habitual de la cuadrilla."
        ),
    )

    minutos_trabajo_sitio_estimado = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Override semanal del tiempo estimado por sitio. "
            "Vacío = utilizar el valor habitual de la cuadrilla."
        ),
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
        related_name="disponibilidades_cuadrilla_semana_creadas",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disponibilidades_cuadrilla_semana_actualizadas",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "configuracion_semana__fecha_inicio",
            "cuadrilla_operativa__orden",
            "cuadrilla_operativa__nombre",
            "cuadrilla",
            "id",
        ]

        constraints = [
            # ------------------------------------------------
            # LEGACY
            # ------------------------------------------------
            #
            # Solo aplica cuando el campo legacy tiene valor.
            # Esto evita que múltiples cuadrillas nuevas con
            # cuadrilla="" choquen entre sí.
            #
            models.UniqueConstraint(
                fields=[
                    "configuracion_semana",
                    "cuadrilla",
                ],
                condition=~models.Q(cuadrilla=""),
                name="uq_disponibilidad_cuadrilla_semana",
            ),
            # ------------------------------------------------
            # NUEVO SISTEMA
            # ------------------------------------------------
            #
            # Una cuadrilla maestra solamente puede aparecer
            # una vez dentro de una misma semana.
            #
            models.UniqueConstraint(
                fields=[
                    "configuracion_semana",
                    "cuadrilla_operativa",
                ],
                condition=models.Q(
                    cuadrilla_operativa__isnull=False,
                ),
                name="uq_disponibilidad_cuadrilla_operativa_semana",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "configuracion_semana",
                    "activa",
                ]
            ),
            models.Index(
                fields=[
                    "cuadrilla",
                    "modalidad",
                ]
            ),
            models.Index(
                fields=[
                    "cuadrilla_operativa",
                    "activa",
                ]
            ),
        ]

        verbose_name = "Disponibilidad de cuadrilla semanal"
        verbose_name_plural = "Disponibilidades de cuadrillas semanales"

    # ========================================================
    # REPRESENTACIÓN
    # ========================================================

    def __str__(self):
        return (
            f"{self.nombre_cuadrilla} - "
            f"{self.get_modalidad_display()} - "
            f"{self.configuracion_semana.fecha_inicio}"
        )

    # ========================================================
    # IDENTIDAD EFECTIVA
    # ========================================================

    @property
    def codigo_cuadrilla(self):
        """
        Código utilizado por servicios nuevos.

        Prioridad:
        1. CuadrillaOperativa.
        2. Campo legacy.
        """

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.codigo

        return self.cuadrilla or ""

    @property
    def nombre_cuadrilla(self):
        """
        Nombre visible de la cuadrilla.
        """

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.nombre

        if self.cuadrilla:
            return self.get_cuadrilla_display()

        return "Cuadrilla sin asignar"

    # ========================================================
    # DÍAS
    # ========================================================

    @property
    def dias_disponibles(self):
        """
        Número nominal de días disponibles durante la semana.
        """

        if not self.activa:
            return 0

        if self.modalidad == self.LUNES_SABADO:
            return 6

        return 5

    @property
    def trabaja_sabado(self):
        return self.activa and self.modalidad == self.LUNES_SABADO

    # ========================================================
    # CAPACIDAD
    # ========================================================

    @property
    def capacidad_nominal_semana(self):
        """
        Capacidad nominal.

        Sigue existiendo como referencia, pero posteriormente
        el motor operacional podrá determinar la capacidad real
        considerando:

        - base de salida;
        - desplazamiento;
        - tiempo por sitio;
        - retorno;
        - rural/urbano;
        - jornada disponible.
        """

        if not self.activa:
            return 0

        return self.capacidad_diaria_objetivo * self.dias_disponibles

    # ========================================================
    # VEHÍCULO / ZONA
    # ========================================================

    @property
    def permite_rural(self):
        """
        Prioriza la configuración dinámica de CuadrillaOperativa.

        La lógica histórica solamente se utiliza para registros
        antiguos todavía no vinculados.
        """

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.permite_rural

        return self.cuadrilla in {
            self.CUADRILLA_1,
            self.CUADRILLA_3,
        }

    @property
    def permite_urbano(self):
        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.permite_urbano

        # Comportamiento histórico.
        return True

    @property
    def tipo_vehiculo(self):
        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.tipo_vehiculo or "Sin definir"

        # Compatibilidad histórica.
        if self.cuadrilla == self.CUADRILLA_2:
            return "Partner"

        if self.cuadrilla in {
            self.CUADRILLA_1,
            self.CUADRILLA_3,
        }:
            return "4x4"

        return "Sin definir"

    # ========================================================
    # BASE EFECTIVA
    # ========================================================

    @property
    def base_nombre_efectiva(self):
        """
        Base utilizada realmente por el motor.

        El override semanal tiene prioridad sobre el catálogo.
        """

        if self.base_nombre:
            return self.base_nombre

        if self.cuadrilla_operativa_id:
            return (
                self.cuadrilla_operativa.base_nombre
                or self.cuadrilla_operativa.direccion_base
                or "Base operacional"
            )

        return "Base operacional"

    @property
    def base_latitud_efectiva(self):
        if self.base_latitud is not None:
            return self.base_latitud

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.base_latitud

        return None

    @property
    def base_longitud_efectiva(self):
        if self.base_longitud is not None:
            return self.base_longitud

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.base_longitud

        return None

    @property
    def tiene_base_operacional(self):
        return (
            self.base_latitud_efectiva is not None
            and self.base_longitud_efectiva is not None
        )

    # ========================================================
    # TIEMPOS EFECTIVOS
    # ========================================================

    @property
    def minutos_jornada_efectivos(self):
        """
        Jornada utilizada realmente por el motor.
        """

        if self.minutos_jornada_objetivo is not None:
            return self.minutos_jornada_objetivo

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.minutos_jornada_default

        # Compatibilidad histórica.
        return 540

    @property
    def minutos_trabajo_sitio_efectivos(self):
        """
        Tiempo de ejecución por sitio utilizado por el motor.
        """

        if self.minutos_trabajo_sitio_estimado is not None:
            return self.minutos_trabajo_sitio_estimado

        if self.cuadrilla_operativa_id:
            return self.cuadrilla_operativa.minutos_trabajo_sitio_default

        # Compatibilidad histórica.
        return 165

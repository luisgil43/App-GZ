# planificacion/modelos/prioridades_diarias.py

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

# ============================================================
# PRIORIDAD OPERACIONAL DIARIA DE UN SITIO
# ============================================================


class PrioridadPlanificacionDiaria(models.Model):
    """
    Configuración especial de planificación diaria para un sitio
    perteneciente a un batch semanal.

    Este modelo NO modifica el motor semanal.

    Su responsabilidad es indicar al motor diario que un sitio
    posee una condición operacional especial, por ejemplo:

    - debe ejecutarse antes que otros;
    - debe funcionar como sitio ancla de una jornada;
    - debe ser atendido por una cuadrilla específica;
    - debe ejecutarse en una fecha concreta;
    - los demás sitios de la salida deben buscarse alrededor
      de este sitio prioritario.

    El motor diario utilizará esta información antes de construir
    la distribución definitiva de días y cuadrillas.
    """

    TIPOS_PRIORIDAD = [
        ("normal", "Normal"),
        ("alta", "Alta"),
        ("critica", "Crítica"),
    ]

    ESTADOS = [
        ("activa", "Activa"),
        ("cumplida", "Cumplida"),
        ("cancelada", "Cancelada"),
    ]

    # ========================================================
    # SITIO
    # ========================================================

    sitio_batch = models.OneToOneField(
        "planificacion.SitioBatchSemanal",
        on_delete=models.CASCADE,
        related_name="prioridad_diaria",
    )

    # ========================================================
    # PRIORIDAD
    # ========================================================

    prioridad = models.CharField(
        max_length=20,
        choices=TIPOS_PRIORIDAD,
        default="alta",
        db_index=True,
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default="activa",
        db_index=True,
    )

    # ========================================================
    # SITIO ANCLA
    # ========================================================

    es_ancla = models.BooleanField(
        default=True,
        help_text=(
            "Si está activo, este sitio define territorialmente "
            "la salida diaria. Los demás sitios se buscarán "
            "preferentemente alrededor de él."
        ),
    )

    # ========================================================
    # FECHA
    # ========================================================

    fecha_objetivo = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Fecha requerida o preferida para ejecutar el sitio. "
            "Vacío significa que el motor puede escoger el mejor día."
        ),
    )

    fecha_es_obligatoria = models.BooleanField(
        default=False,
        help_text=(
            "Si está activo, el motor no puede mover el sitio "
            "a otra fecha diferente de fecha_objetivo."
        ),
    )

    # ========================================================
    # CUADRILLA OBLIGATORIA
    # ========================================================

    cuadrilla_obligatoria = models.ForeignKey(
        "planificacion.CuadrillaOperativa",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="prioridades_diarias_obligatorias",
        help_text=(
            "Cuadrilla que obligatoriamente debe ejecutar el sitio. "
            "Vacío significa que cualquier cuadrilla compatible "
            "puede realizarlo."
        ),
    )

    # ========================================================
    # RADIO / CERCANÍA
    # ========================================================

    distancia_preferida_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=20,
        validators=[
            MinValueValidator(0),
        ],
        help_text=(
            "Distancia preferida alrededor del sitio ancla para "
            "buscar los demás sitios de la salida."
        ),
    )

    distancia_maxima_km = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=40,
        validators=[
            MinValueValidator(0),
        ],
        help_text=(
            "Distancia máxima de búsqueda automática alrededor "
            "del sitio prioritario antes de considerar una excepción."
        ),
    )

    minutos_preferidos = models.PositiveSmallIntegerField(
        default=30,
        help_text=(
            "Tiempo máximo preferido entre el sitio prioritario "
            "y los demás sitios candidatos."
        ),
    )

    minutos_maximos = models.PositiveSmallIntegerField(
        default=45,
        help_text=(
            "Tiempo máximo tolerado antes de requerir una decisión "
            "manual para completar la salida."
        ),
    )

    # ========================================================
    # COMPLETAR SALIDA
    # ========================================================

    objetivo_sitios_salida = models.PositiveSmallIntegerField(
        default=3,
        help_text=(
            "Cantidad objetivo de sitios que debe contener la salida "
            "generada alrededor del sitio prioritario."
        ),
    )

    permitir_salida_2_sitios = models.BooleanField(
        default=True,
        help_text=(
            "Permite cerrar excepcionalmente la salida con dos sitios "
            "si no existe un tercer sitio operacionalmente razonable."
        ),
    )

    permitir_salida_1_sitio = models.BooleanField(
        default=False,
        help_text=(
            "Permite ejecutar únicamente el sitio prioritario. "
            "Debe utilizarse solo como última excepción."
        ),
    )

    requiere_confirmacion_excepcion = models.BooleanField(
        default=True,
        help_text=(
            "Si no existen suficientes sitios dentro de los límites "
            "preferidos, el sistema debe pedir confirmación antes de "
            "utilizar candidatos más alejados."
        ),
    )

    # ========================================================
    # MOTIVO
    # ========================================================

    motivo = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Motivo operacional de la prioridad. "
            "Ejemplo: solicitud del cliente, acreditación, "
            "ventana de acceso o compromiso especial."
        ),
    )

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
        related_name="prioridades_diarias_creadas",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prioridades_diarias_actualizadas",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-prioridad",
            "fecha_objetivo",
            "sitio_batch__sitio_planificado__sitio__id_claro",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "estado",
                    "prioridad",
                ]
            ),
            models.Index(
                fields=[
                    "fecha_objetivo",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "cuadrilla_obligatoria",
                    "estado",
                ]
            ),
        ]

        verbose_name = "Prioridad de planificación diaria"
        verbose_name_plural = "Prioridades de planificación diaria"

    def __str__(self):
        sitio = self.sitio_batch.sitio_planificado.sitio

        identificador = sitio.id_claro or sitio.id_sites or str(sitio.pk)

        return f"{identificador} - " f"{self.get_prioridad_display()}"

    # ========================================================
    # ACCESOS RÁPIDOS
    # ========================================================

    @property
    def sitio_planificado(self):
        return self.sitio_batch.sitio_planificado

    @property
    def sitio(self):
        return self.sitio_batch.sitio_planificado.sitio

    @property
    def batch(self):
        return self.sitio_batch.batch

    @property
    def id_claro(self):
        return self.sitio.id_claro or self.sitio.id_sites or ""

    @property
    def tiene_cuadrilla_obligatoria(self):
        return self.cuadrilla_obligatoria_id is not None

    @property
    def tiene_fecha_objetivo(self):
        return self.fecha_objetivo is not None

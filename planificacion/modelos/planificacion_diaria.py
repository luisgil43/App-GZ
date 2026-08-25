from django.conf import settings
from django.db import models

# ============================================================
# SALIDA DE PLANIFICACIÓN DIARIA
# ============================================================


class SalidaPlanificacionDiaria(models.Model):
    """
    Representa una salida operacional concreta de una cuadrilla
    dentro de un batch semanal.

    Ejemplo:

        Lunes 24/08/2026
        Cuadrilla 1
        Sitios:
            13_001
            13_002
            13_003

    Esta entidad pertenece exclusivamente a planificación.

    NO reemplaza:

    - ServicioCotizado;
    - trabajadores_asignados;
    - SesionFotos;
    - SesionFotoTecnico;
    - estados operacionales de operaciones.

    Su responsabilidad es conservar qué sitios fueron
    planificados para una cuadrilla y una fecha concreta.
    """

    ESTADOS = [
        ("borrador", "Borrador"),
        ("lista_asignar", "Lista para asignar"),
        ("asignada", "Asignada"),
        ("en_ejecucion", "En ejecución"),
        ("parcial", "Parcial"),
        ("finalizada", "Finalizada"),
        ("reprogramada", "Reprogramada"),
        ("cancelada", "Cancelada"),
    ]

    ORIGENES = [
        ("motor", "Motor"),
        ("manual", "Manual"),
        ("reprogramacion", "Reprogramación"),
    ]

    # ========================================================
    # SEMANA
    # ========================================================

    batch = models.ForeignKey(
        "planificacion.BatchPlanificacionSemanal",
        on_delete=models.CASCADE,
        related_name="salidas_diarias",
    )

    # ========================================================
    # CUADRILLA
    # ========================================================

    disponibilidad_cuadrilla = models.ForeignKey(
        "planificacion.DisponibilidadCuadrillaSemana",
        on_delete=models.PROTECT,
        related_name="salidas_diarias",
    )

    # ========================================================
    # FECHA
    # ========================================================

    fecha = models.DateField(
        db_index=True,
    )

    orden = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Orden visual de la salida dentro de la fecha. "
            "Normalmente una cuadrilla tendrá una sola salida "
            "por día, pero dejamos soporte para escenarios "
            "operacionales especiales."
        ),
    )

    # ========================================================
    # ESTADO
    # ========================================================

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="borrador",
        db_index=True,
    )

    origen = models.CharField(
        max_length=20,
        choices=ORIGENES,
        default="motor",
        db_index=True,
    )

    # ========================================================
    # INFORMACIÓN DEL MOTOR
    # ========================================================

    minutos_viaje_estimados = models.PositiveIntegerField(
        default=0,
    )

    minutos_trabajo_estimados = models.PositiveIntegerField(
        default=0,
    )

    minutos_total_estimados = models.PositiveIntegerField(
        default=0,
    )

    distancia_directa_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )

    distancia_vial_estimada_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )

    jornada_extendida = models.BooleanField(
        default=False,
    )

    exceso_jornada_minutos = models.PositiveIntegerField(
        default=0,
    )

    puntaje_motor = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )

    motivo_motor = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # CONTROL MANUAL
    # ========================================================

    bloqueada = models.BooleanField(
        default=False,
        help_text=(
            "Cuando está activa, futuras recalculaciones del "
            "motor diario no pueden modificar automáticamente "
            "esta salida."
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
        related_name="salidas_planificacion_diaria_creadas",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="salidas_planificacion_diaria_actualizadas",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "fecha",
            "disponibilidad_cuadrilla__cuadrilla_operativa__orden",
            "orden",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "batch",
                    "disponibilidad_cuadrilla",
                    "fecha",
                    "orden",
                ],
                name="uq_salida_diaria_batch_cuadrilla_fecha_orden",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "batch",
                    "fecha",
                ]
            ),
            models.Index(
                fields=[
                    "fecha",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "disponibilidad_cuadrilla",
                    "fecha",
                ]
            ),
            models.Index(
                fields=[
                    "batch",
                    "estado",
                ]
            ),
        ]

        verbose_name = "Salida de planificación diaria"
        verbose_name_plural = "Salidas de planificación diaria"

    def __str__(self):
        return f"{self.fecha} - " f"{self.disponibilidad_cuadrilla.nombre_cuadrilla}"

    # ========================================================
    # CUADRILLA
    # ========================================================

    @property
    def cuadrilla_codigo(self):
        return self.disponibilidad_cuadrilla.codigo_cuadrilla

    @property
    def cuadrilla_nombre(self):
        return self.disponibilidad_cuadrilla.nombre_cuadrilla

    # ========================================================
    # CANTIDAD DE SITIOS
    # ========================================================

    @property
    def cantidad_sitios(self):
        return self.sitios.exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        ).count()


# ============================================================
# SITIO DENTRO DE UNA SALIDA DIARIA
# ============================================================


class SitioSalidaPlanificacionDiaria(models.Model):
    """
    Representa la participación de un sitio del batch semanal
    dentro de una salida diaria concreta.

    El estado de este modelo describe únicamente su posición
    dentro de planificación.

    El estado REAL de ejecución continuará obteniéndose desde
    operaciones.ServicioCotizado.

    Ejemplo:

        planificación:
            sitio programado para lunes / cuadrilla 1

        operaciones:
            aprobado_pendiente
            asignado
            en_progreso
            en_revision_supervisor
            aprobado_supervisor

    No duplicamos ese flujo aquí.
    """

    ESTADOS = [
        ("planificado", "Planificado"),
        ("listo_asignar", "Listo para asignar"),
        ("asignado", "Asignado"),
        ("en_ejecucion", "En ejecución"),
        ("revision", "En revisión"),
        ("finalizado", "Finalizado"),
        ("no_ejecutado", "No ejecutado"),
        ("reprogramado", "Reprogramado"),
        ("retirado", "Retirado"),
        ("cancelado", "Cancelado"),
    ]

    ORIGENES = [
        ("motor", "Motor"),
        ("manual", "Manual"),
        ("reprogramacion", "Reprogramación"),
    ]

    # ========================================================
    # SALIDA
    # ========================================================

    salida = models.ForeignKey(
        SalidaPlanificacionDiaria,
        on_delete=models.CASCADE,
        related_name="sitios",
    )

    # ========================================================
    # SITIO DEL BATCH
    # ========================================================

    sitio_batch = models.ForeignKey(
        "planificacion.SitioBatchSemanal",
        on_delete=models.PROTECT,
        related_name="participaciones_diarias",
    )

    # ========================================================
    # ORDEN DE EJECUCIÓN
    # ========================================================

    orden = models.PositiveSmallIntegerField(
        default=0,
    )

    # ========================================================
    # ESTADO DE PLANIFICACIÓN
    # ========================================================

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="planificado",
        db_index=True,
    )

    origen = models.CharField(
        max_length=20,
        choices=ORIGENES,
        default="motor",
        db_index=True,
    )

    # ========================================================
    # CONTROL DEL MOTOR
    # ========================================================

    bloqueado = models.BooleanField(
        default=False,
        help_text=(
            "Impide que una recalculación automática cambie "
            "este sitio de salida o fecha."
        ),
    )

    puntaje_motor = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )

    motivo_motor = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # REPROGRAMACIÓN
    # ========================================================

    reprogramado_desde = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reprogramaciones_generadas",
    )

    motivo_reprogramacion = models.TextField(
        blank=True,
        default="",
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
        related_name="sitios_salida_diaria_creados",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sitios_salida_diaria_actualizados",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "salida__fecha",
            "salida__orden",
            "orden",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "salida",
                    "sitio_batch",
                ],
                name="uq_sitio_salida_diaria",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "salida",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "sitio_batch",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "estado",
                    "actualizado_en",
                ]
            ),
        ]

        verbose_name = "Sitio de salida diaria"
        verbose_name_plural = "Sitios de salidas diarias"

    def __str__(self):
        return (
            f"{self.salida.fecha} - "
            f"{self.sitio_batch.sitio_planificado.sitio.id_claro}"
        )

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
    def id_claro(self):
        return self.sitio.id_claro or ""

    @property
    def nombre_sitio(self):
        return self.sitio.nombre or ""

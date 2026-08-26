from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from operaciones.models import SitioMovil
from planificacion.modelos.disponibilidad_semanal import \
    DisponibilidadCuadrillaSemana
from planificacion.modelos.planificacion_diaria import (
    SalidaPlanificacionDiaria, SitioSalidaPlanificacionDiaria)

# ============================================================
# BASE DE CONTACTOS DEL CLIENTE
# ============================================================


class ContactoSitio(models.Model):
    """
    Base de contactos asociada a sitios.

    Un sitio puede tener múltiples contactos.

    Ejemplo:
    08_899 puede tener:
    - un teléfono solo llamadas
    - otro teléfono solo WhatsApp
    - uno o más correos

    La información original proveniente del cliente se conserva
    y posteriormente puede ser analizada para generar reglas
    estructuradas de acceso.
    """

    sitio = models.ForeignKey(
        SitioMovil,
        on_delete=models.PROTECT,
        related_name="contactos_planificacion",
        null=True,
        blank=True,
    )

    # Conservamos el ID exactamente como llegó desde la fuente.
    id_origen = models.CharField(
        max_length=100,
        db_index=True,
    )

    region = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    nombre_sitio = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    propietario = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    telefono = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    correo = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    fecha_informacion = models.DateField(
        null=True,
        blank=True,
    )

    responsable = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    accion = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # CLASIFICACIÓN DEL CONTACTO
    # ========================================================

    prioridad_contacto = models.PositiveSmallIntegerField(
        default=1,
        help_text=("Orden preferente de contacto. " "1 = contacto principal."),
    )

    tipo_contacto = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Ejemplo: propietario, cuidador, administrador, "
            "ingeniero de campo, portería."
        ),
    )

    activo = models.BooleanField(
        default=True,
        db_index=True,
    )

    # ========================================================
    # CONTROL DEL ANALIZADOR
    # ========================================================

    requiere_reanalisis = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "Se activa cuando cambia información que puede afectar "
            "las reglas de acceso del sitio."
        ),
    )

    analizado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    firma_contenido = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Hash del contenido relevante del contacto. "
            "Permite detectar cambios reales durante importaciones."
        ),
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contactos_sitios_creados",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contactos_sitios_actualizados",
    )

    class Meta:
        ordering = [
            "id_origen",
            "prioridad_contacto",
            "propietario",
            "id",
        ]

        indexes = [
            models.Index(fields=["id_origen", "activo"]),
            models.Index(fields=["sitio", "activo"]),
            models.Index(fields=["requiere_reanalisis", "activo"]),
        ]

    def __str__(self):
        return (
            f"{self.id_origen} - "
            f"{self.propietario or self.nombre_sitio or 'Contacto'}"
        )


# ============================================================
# CONTROL DE IMPORTACIONES DE CONTACTOS
# ============================================================


class ImportacionContactosSitios(models.Model):
    """
    Auditoría de las importaciones realizadas a la base
    de contactos.
    """

    ESTADOS = [
        ("preview", "Preview"),
        ("aplicada", "Aplicada"),
        ("cancelada", "Cancelada"),
        ("error", "Error"),
    ]

    nombre_archivo = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default="preview",
        db_index=True,
    )

    total_filas = models.PositiveIntegerField(
        default=0,
    )

    nuevos = models.PositiveIntegerField(
        default=0,
    )

    actualizados = models.PositiveIntegerField(
        default=0,
    )

    sin_cambios = models.PositiveIntegerField(
        default=0,
    )

    no_vinculados = models.PositiveIntegerField(
        default=0,
    )

    errores = models.PositiveIntegerField(
        default=0,
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="importaciones_contactos_sitios",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    aplicado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    class Meta:
        ordering = [
            "-creado_en",
        ]

    def __str__(self):
        return f"Importación contactos #{self.pk or '-'} " f"- {self.estado}"

# ============================================================
# FILAS TEMPORALES DE IMPORTACIÓN DE CONTACTOS
# ============================================================


class FilaImportacionContacto(models.Model):
    """
    Fila analizada de una importación de contactos.

    OBJETIVO
    ==========================================================

    Evitar mantener miles de registros del preview dentro de:

        - memoria RAM;
        - sesión;
        - caché;
        - contexto del template.

    Cada fila analizada se persiste temporalmente en PostgreSQL.

    La pantalla carga únicamente la página que el usuario
    está visualizando.

    La confirmación procesa estas filas por lotes.

    IMPORTANTE
    ==========================================================

    Este modelo NO modifica SitioMovil.

    Solamente conserva el resultado temporal del análisis.
    """

    ESTADOS = [
        ("nuevo", "Nuevo"),
        ("actualizar", "Actualizar"),
        ("sin_cambios", "Sin cambios"),
        ("error", "Error"),
    ]

    importacion = models.ForeignKey(
        ImportacionContactosSitios,
        on_delete=models.CASCADE,
        related_name="filas_preview",
    )

    numero_fila = models.PositiveIntegerField(
        db_index=True,
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        db_index=True,
    )

    # ========================================================
    # VINCULACIÓN
    # ========================================================

    sitio = models.ForeignKey(
        SitioMovil,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    contacto = models.ForeignKey(
        ContactoSitio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    vinculado = models.BooleanField(
        default=False,
        db_index=True,
    )

    vinculo_por = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )

    # ========================================================
    # DATOS DEL ARCHIVO
    # ========================================================

    id_origen = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
    )

    region = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    nombre_sitio = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    propietario = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    telefono = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    correo = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    fecha_informacion = models.DateField(
        null=True,
        blank=True,
    )

    responsable = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    accion = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # CAMBIOS DETECTADOS
    # ========================================================

    cambios = models.JSONField(
        default=list,
        blank=True,
    )

    # ========================================================
    # ERROR DE FILA
    # ========================================================

    error = models.TextField(
        blank=True,
        default="",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "numero_fila",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "importacion",
                    "numero_fila",
                ],
                name="uq_fila_importacion_contacto",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "importacion",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "importacion",
                    "vinculado",
                ]
            ),
            models.Index(
                fields=[
                    "importacion",
                    "numero_fila",
                ]
            ),
        ]

    def __str__(self):
        return (
            f"Importación #{self.importacion_id} "
            f"- fila {self.numero_fila} "
            f"- {self.id_origen or 'Sin ID'}"
        )


# ============================================================
# HISTORIAL / VERSIONES DE CONTACTOS
# ============================================================


class VersionContactoSitio(models.Model):
    """
    Snapshot histórico de un ContactoSitio.

    Cada vez que una importación modifica información real,
    conservamos una versión para auditoría.
    """

    contacto = models.ForeignKey(
        ContactoSitio,
        on_delete=models.CASCADE,
        related_name="versiones",
    )

    sitio = models.ForeignKey(
        SitioMovil,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versiones_contactos_planificacion",
    )

    id_origen = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    region = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    nombre_sitio = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    propietario = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    telefono = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    correo = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    responsable = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    accion = models.TextField(
        blank=True,
        default="",
    )

    prioridad_contacto = models.PositiveSmallIntegerField(
        default=1,
    )

    tipo_contacto = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    fecha_fuente = models.DateField(
        null=True,
        blank=True,
    )

    importacion = models.ForeignKey(
        ImportacionContactosSitios,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versiones",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = [
            "-creado_en",
        ]

    def __str__(self):
        return f"{self.id_origen} - " f"{self.creado_en:%d/%m/%Y %H:%M}"


# ============================================================
# REGLAS DE ACCESO DETECTADAS
# ============================================================


class ReglaAccesoSitio(models.Model):
    """
    Regla individual detectada desde Acción / Observaciones
    o ingresada manualmente.

    Estas reglas conservan trazabilidad hacia el texto original.
    """

    TIPOS = [
        ("aviso_previo", "Aviso previo"),
        ("requiere_llamada", "Requiere llamada"),
        ("requiere_whatsapp", "Requiere WhatsApp"),
        ("requiere_correo", "Requiere correo"),
        ("requiere_formulario", "Requiere formulario"),
        ("requiere_nomina", "Requiere nómina"),
        ("requiere_patente", "Requiere patente"),
        ("requiere_documentacion", "Requiere documentación"),
        ("requiere_confirmacion", "Requiere confirmación"),
        ("requiere_aprobacion_formal", "Requiere aprobación formal"),
        ("requiere_fecha_exacta", "Requiere fecha exacta"),
        ("requiere_hora_exacta", "Requiere hora exacta"),
        ("requiere_llave", "Requiere llave"),
        ("requiere_contactar_propietario", "Contactar propietario"),
        ("requiere_contactar_cuidador", "Contactar cuidador"),
        ("acceso_libre", "Acceso libre"),
        ("sin_fin_semana", "Sin acceso fin de semana"),
        ("solo_emergencia", "Solo emergencia"),
        ("restriccion_horaria", "Restricción horaria"),
        ("restriccion_personas", "Restricción cantidad personas"),
        ("restriccion_vehiculo", "Restricción vehículo"),
        ("instruccion_especial", "Instrucción especial"),
    ]

    FUENTES = [
        ("accion", "Acción"),
        ("observacion", "Observación"),
        ("manual", "Manual"),
        ("sistema", "Sistema"),
    ]

    contacto_sitio = models.ForeignKey(
        ContactoSitio,
        on_delete=models.CASCADE,
        related_name="reglas",
    )

    tipo = models.CharField(
        max_length=50,
        choices=TIPOS,
        db_index=True,
    )

    valor_texto = models.TextField(
        blank=True,
        default="",
    )

    valor_numero = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    valor_booleano = models.BooleanField(
        null=True,
        blank=True,
    )

    activa = models.BooleanField(
        default=True,
        db_index=True,
    )

    fuente = models.CharField(
        max_length=20,
        choices=FUENTES,
        default="sistema",
    )

    texto_origen = models.TextField(
        blank=True,
        default="",
    )

    confianza = models.PositiveSmallIntegerField(
        default=100,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
        help_text="Nivel de confianza del análisis entre 0 y 100.",
    )

    confirmada_manualmente = models.BooleanField(
        default=False,
    )

    creada_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizada_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "tipo",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "contacto_sitio",
                    "activa",
                ]
            ),
            models.Index(
                fields=[
                    "tipo",
                    "activa",
                ]
            ),
        ]

    def __str__(self):
        return f"{self.contacto_sitio.id_origen} - " f"{self.get_tipo_display()}"


# ============================================================
# PERFIL CONSOLIDADO DE ACCESO
# ============================================================


class PerfilAccesoSitio(models.Model):
    """
    Resumen estructurado de todas las reglas de acceso
    conocidas para un sitio.

    ContactoSitio y ReglaAccesoSitio conservan la fuente.

    PerfilAccesoSitio contiene la interpretación consolidada
    que utilizará directamente el motor de planificación.
    """

    CANALES = [
        ("no_definido", "No definido"),
        ("prohibido", "Prohibido"),
        ("permitido", "Permitido"),
        ("preferido", "Preferido"),
        ("obligatorio", "Obligatorio"),
    ]

    sitio = models.OneToOneField(
        SitioMovil,
        on_delete=models.CASCADE,
        related_name="perfil_acceso_planificacion",
    )

    # ========================================================
    # ESTADO GENERAL
    # ========================================================

    acceso_libre = models.BooleanField(
        default=False,
        db_index=True,
    )

    requiere_gestion = models.BooleanField(
        default=False,
        db_index=True,
    )

    solo_emergencia = models.BooleanField(
        default=False,
        db_index=True,
    )

    # ========================================================
    # ANTICIPACIÓN
    # ========================================================

    anticipacion_horas = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Horas mínimas de anticipación requeridas. " "Ejemplo: 24, 48, 168."
        ),
    )

    # ========================================================
    # MEDIOS DE CONTACTO
    # ========================================================

    canal_llamada = models.CharField(
        max_length=20,
        choices=CANALES,
        default="no_definido",
    )

    canal_whatsapp = models.CharField(
        max_length=20,
        choices=CANALES,
        default="no_definido",
    )

    canal_correo = models.CharField(
        max_length=20,
        choices=CANALES,
        default="no_definido",
    )

    # ========================================================
    # REQUISITOS
    # ========================================================

    requiere_confirmacion = models.BooleanField(
        default=False,
    )

    requiere_aprobacion_formal = models.BooleanField(
        default=False,
    )

    requiere_fecha_exacta = models.BooleanField(
        default=False,
    )

    requiere_hora_exacta = models.BooleanField(
        default=False,
    )

    requiere_nomina = models.BooleanField(
        default=False,
    )

    requiere_patente = models.BooleanField(
        default=False,
    )

    requiere_formulario = models.BooleanField(
        default=False,
    )

    requiere_documentacion = models.BooleanField(
        default=False,
    )

    requiere_llave = models.BooleanField(
        default=False,
    )

    requiere_contactar_propietario = models.BooleanField(
        default=False,
    )

    requiere_contactar_cuidador = models.BooleanField(
        default=False,
    )

    # ========================================================
    # DÍAS PERMITIDOS
    # ========================================================

    permite_lunes = models.BooleanField(
        default=True,
    )

    permite_martes = models.BooleanField(
        default=True,
    )

    permite_miercoles = models.BooleanField(
        default=True,
    )

    permite_jueves = models.BooleanField(
        default=True,
    )

    permite_viernes = models.BooleanField(
        default=True,
    )

    permite_sabado = models.BooleanField(
        default=True,
    )

    permite_domingo = models.BooleanField(
        default=True,
    )

    # ========================================================
    # HORARIO GENERAL
    # ========================================================

    hora_desde = models.TimeField(
        null=True,
        blank=True,
    )

    hora_hasta = models.TimeField(
        null=True,
        blank=True,
    )

    # ========================================================
    # RESTRICCIONES OPERATIVAS
    # ========================================================

    max_personas = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    altura_maxima_vehiculo_m = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Altura máxima permitida para vehículos, " "cuando exista una restricción."
        ),
    )

    requiere_bano_quimico = models.BooleanField(
        default=False,
    )

    requiere_ubicacion_google = models.BooleanField(
        default=False,
    )

    requiere_foto_lugar = models.BooleanField(
        default=False,
    )

    # ========================================================
    # INFORMACIÓN CONSOLIDADA PARA EL MOTOR
    # ========================================================

    restricciones_resumen = models.TextField(
        blank=True,
        default="",
    )

    instrucciones_especiales = models.TextField(
        blank=True,
        default="",
    )

    confianza_global = models.PositiveSmallIntegerField(
        default=0,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
    )

    necesita_revision = models.BooleanField(
        default=False,
        db_index=True,
    )

    conflicto_informacion = models.BooleanField(
        default=False,
        db_index=True,
    )

    detalle_conflicto = models.TextField(
        blank=True,
        default="",
    )

    # ========================================================
    # CONTROL DEL ANÁLISIS
    # ========================================================

    analizado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "acceso_libre",
                    "requiere_gestion",
                ]
            ),
            models.Index(
                fields=[
                    "necesita_revision",
                    "conflicto_informacion",
                ]
            ),
        ]

    def __str__(self):
        return f"Perfil acceso " f"{self.sitio.id_claro or self.sitio.id_sites}"


# ============================================================
# PLANIFICACIÓN MENSUAL
# ============================================================


class PlanificacionMensual(models.Model):

    ESTADOS = [
        ("borrador", "Borrador"),
        ("en_preparacion", "En preparación"),
        ("activa", "Activa"),
        ("cerrada", "Cerrada"),
    ]

    anio = models.PositiveIntegerField(
        db_index=True,
    )

    mes = models.PositiveSmallIntegerField(
        db_index=True,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(12),
        ],
    )

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="borrador",
        db_index=True,
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planificaciones_creadas",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planificaciones_actualizadas",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-anio",
            "-mes",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "anio",
                    "mes",
                ],
                name="uq_planificacion_mensual",
            ),
        ]

    def __str__(self):
        return f"{self.mes:02d}/{self.anio}"


# ============================================================
# CONFIGURACIÓN SEMANAL
# ============================================================


class ConfiguracionSemana(models.Model):
    """
    Configuración operacional de una semana ISO real.

    ARQUITECTURA
    ==========================================================

    La semana pertenece al calendario, NO a un mes específico.

    Ejemplo:

        W36 2026
        31/08/2026 al 06/09/2026

    puede contener sitios provenientes de:

        Agosto 2026
        Septiembre 2026

    y debe existir UNA sola configuración operacional
    para esa semana.

    La relación histórica con PlanificacionMensual se conserva
    temporalmente para permitir una migración segura de los
    datos existentes.

    Los servicios nuevos deben considerar fecha_inicio como
    identidad global de la semana.
    """

    # ========================================================
    # COMPATIBILIDAD HISTÓRICA
    # ========================================================
    #
    # Antes una ConfiguracionSemana pertenecía directamente
    # a una única PlanificacionMensual.
    #
    # La mantenemos temporalmente nullable para migrar sin
    # destruir registros históricos.
    #
    # Posteriormente podrá eliminarse cuando toda la aplicación
    # utilice exclusivamente la semana global.
    # ========================================================

    planificacion = models.ForeignKey(
        PlanificacionMensual,
        on_delete=models.SET_NULL,
        related_name="semanas_legacy",
        null=True,
        blank=True,
        help_text=(
            "Relación histórica con el mes que creó originalmente "
            "la configuración. Los servicios nuevos no deben "
            "utilizarla como dueño exclusivo de la semana."
        ),
    )

    # ========================================================
    # SEMANA GLOBAL
    # ========================================================

    fecha_inicio = models.DateField(
        db_index=True,
        help_text="Debe corresponder al lunes de la semana ISO.",
    )

    trabaja_sabado = models.BooleanField(
        default=False,
    )

    capacidad_diaria_objetivo = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Capacidad objetivo opcional. " "El motor podrá calcularla automáticamente."
        ),
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="semanas_planificacion_actualizadas",
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "fecha_inicio",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "fecha_inicio",
                ],
                name="uq_configuracion_semana_global",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "fecha_inicio",
                ]
            ),
        ]

    def __str__(self):
        iso = self.fecha_inicio.isocalendar()

        return f"W{iso.week} {iso.year} " f"- {self.fecha_inicio:%d/%m/%Y}"

    # ========================================================
    # IDENTIDAD ISO
    # ========================================================

    @property
    def numero_semana_iso(self):
        return self.fecha_inicio.isocalendar().week

    @property
    def anio_semana_iso(self):
        return self.fecha_inicio.isocalendar().year

    @property
    def codigo_semana(self):
        return f"W{self.numero_semana_iso}"


# ============================================================
# SITIO DENTRO DE UNA PLANIFICACIÓN
# ============================================================


class SitioPlanificado(models.Model):

    ESTADOS = [
        ("pendiente", "Pendiente"),
        ("por_contactar", "Por contactar"),
        ("gestionando_permiso", "Gestionando permiso"),
        ("listo_planificar", "Listo para planificar"),
        ("planificado", "Planificado"),
        ("en_ruta", "En ruta"),
        ("en_ejecucion", "En ejecución"),
        ("completado", "Completado"),
        ("no_ejecutado", "No ejecutado"),
        ("reprogramado", "Reprogramado"),
        ("bloqueado", "Bloqueado"),
        ("cancelado", "Cancelado"),
    ]

    ESTADOS_PERMISO = [
        ("sin_gestion", "Sin gestión"),
        ("por_solicitar", "Por solicitar"),
        ("solicitado", "Solicitado"),
        ("en_espera", "En espera"),
        ("aprobado", "Aprobado"),
        ("rechazado", "Rechazado"),
        ("no_requiere", "No requiere"),
    ]

    TIPOS_RUTA = [
        ("", "Sin definir"),
        ("urbana", "Urbana"),
        ("rural", "Rural"),
        ("mixta", "Mixta"),
        ("infanteria", "Infantería"),
        ("especial", "Especial"),
    ]

    PRIORIDADES = [
        ("baja", "Baja"),
        ("normal", "Normal"),
        ("alta", "Alta"),
        ("critica", "Crítica"),
    ]

    planificacion = models.ForeignKey(
        PlanificacionMensual,
        on_delete=models.CASCADE,
        related_name="sitios",
    )

    sitio = models.ForeignKey(
        SitioMovil,
        on_delete=models.PROTECT,
        related_name="planificaciones",
    )

    # ========================================================
    # ORIGEN / ASIGNACIÓN MENSUAL
    # ========================================================

    importacion_origen = models.ForeignKey(
        "ImportacionAsignacionMensual",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sitios_creados",
    )

    activo_en_mes = models.BooleanField(
        default=True,
        db_index=True,
    )

    fecha_planificada = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )

    orden_dia = models.PositiveIntegerField(
        default=0,
    )

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="pendiente",
        db_index=True,
    )

    estado_permiso = models.CharField(
        max_length=30,
        choices=ESTADOS_PERMISO,
        default="sin_gestion",
        db_index=True,
    )

    tipo_ruta = models.CharField(
        max_length=30,
        choices=TIPOS_RUTA,
        blank=True,
        default="",
        db_index=True,
    )

    prioridad = models.CharField(
        max_length=20,
        choices=PRIORIDADES,
        default="normal",
        db_index=True,
    )

    # ========================================================
    # ESTADO DE CONTACTO
    # ========================================================

    requiere_contacto = models.BooleanField(
        default=False,
    )

    contacto_confirmado = models.BooleanField(
        default=False,
    )

    fecha_contacto_confirmado = models.DateTimeField(
        null=True,
        blank=True,
    )

    # ========================================================
    # DISPONIBILIDAD CALCULADA
    # ========================================================

    fecha_minima_ejecucion = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Fecha y hora mínima en la que puede ejecutarse "
            "el sitio según las gestiones y restricciones vigentes."
        ),
    )

    # ========================================================
    # MOTOR / PLANIFICACIÓN
    # ========================================================

    bloqueado_motor = models.BooleanField(
        default=False,
        help_text=(
            "Si está activo, el motor no puede mover " "automáticamente este sitio."
        ),
    )

    planificado_manualmente = models.BooleanField(
        default=False,
    )

    motivo_bloqueo = models.TextField(
        blank=True,
        default="",
    )

    observacion_planificacion = models.TextField(
        blank=True,
        default="",
    )

    alerta_motor = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Resumen de alertas o advertencias calculadas "
            "por el motor para este sitio."
        ),
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sitios_planificados_creados",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sitios_planificados_actualizados",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "fecha_planificada",
            "orden_dia",
            "sitio__id_claro",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "planificacion",
                    "sitio",
                ],
                name="uq_sitio_planificacion_mes",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "planificacion",
                    "fecha_planificada",
                ]
            ),
            models.Index(
                fields=[
                    "planificacion",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "estado_permiso",
                    "fecha_planificada",
                ]
            ),
            models.Index(
                fields=[
                    "bloqueado_motor",
                    "estado",
                ]
            ),
        ]

    def __str__(self):
        return (
            f"{self.sitio.id_claro or self.sitio.id_sites} " f"- {self.planificacion}"
        )


# ============================================================
# GESTIÓN / CONTACTO / PERMISOS
# ============================================================


class GestionSitioPlanificado(models.Model):

    TIPOS = [
        ("llamada", "Llamada"),
        ("whatsapp", "WhatsApp"),
        ("correo", "Correo"),
        ("formulario", "Formulario"),
        ("permiso", "Permiso"),
        ("nota", "Nota"),
        ("otro", "Otro"),
    ]

    RESULTADOS = [
        ("sin_respuesta", "Sin respuesta"),
        ("contactado", "Contactado"),
        ("pendiente", "Pendiente"),
        ("confirmado", "Confirmado"),
        ("rechazado", "Rechazado"),
        ("informativo", "Informativo"),
    ]

    sitio_planificado = models.ForeignKey(
        SitioPlanificado,
        on_delete=models.CASCADE,
        related_name="gestiones",
    )

    contacto = models.ForeignKey(
        ContactoSitio,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gestiones",
    )

    tipo = models.CharField(
        max_length=30,
        choices=TIPOS,
        db_index=True,
    )

    resultado = models.CharField(
        max_length=30,
        choices=RESULTADOS,
        blank=True,
        default="",
        db_index=True,
    )

    detalle = models.TextField(
        blank=True,
        default="",
    )

    fecha_gestion = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    proxima_accion = models.TextField(
        blank=True,
        default="",
    )

    proxima_fecha = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    realizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gestiones_planificacion",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-fecha_gestion",
        ]

        indexes = [
            models.Index(
                fields=[
                    "sitio_planificado",
                    "resultado",
                ]
            ),
            models.Index(
                fields=[
                    "proxima_fecha",
                    "resultado",
                ]
            ),
        ]

    def __str__(self):
        return f"{self.sitio_planificado} - " f"{self.tipo}"


# ============================================================
# REPROGRAMACIONES
# ============================================================


class ReprogramacionSitio(models.Model):

    sitio_planificado = models.ForeignKey(
        SitioPlanificado,
        on_delete=models.CASCADE,
        related_name="reprogramaciones",
    )

    fecha_anterior = models.DateField(
        null=True,
        blank=True,
    )

    fecha_nueva = models.DateField()

    motivo = models.TextField(
        blank=True,
        default="",
    )

    realizada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reprogramaciones_planificacion",
    )

    creada_en = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-creada_en",
        ]

    def __str__(self):
        return (
            f"{self.sitio_planificado} " f"{self.fecha_anterior} → {self.fecha_nueva}"
        )


# ============================================================
# IMPORTACIÓN DE ASIGNACIÓN MENSUAL
# ============================================================


class ImportacionAsignacionMensual(models.Model):
    """
    Auditoría de archivos utilizados para incorporar sitios
    a una PlanificacionMensual.

    La planilla puede contener muchas columnas, pero el sistema
    utiliza únicamente la columna identificada como ID / ID Claro
    para vincular contra SitioMovil.id_claro.

    Este proceso NUNCA modifica SitioMovil.
    """

    ESTADOS = [
        ("preview", "Preview"),
        ("aplicada", "Aplicada"),
        ("cancelada", "Cancelada"),
        ("error", "Error"),
    ]

    planificacion = models.ForeignKey(
        PlanificacionMensual,
        on_delete=models.CASCADE,
        related_name="importaciones_asignacion",
    )

    nombre_archivo = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    nombre_hoja = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    columna_id_detectada = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default="preview",
        db_index=True,
    )

    total_filas = models.PositiveIntegerField(
        default=0,
    )

    total_ids_detectados = models.PositiveIntegerField(
        default=0,
    )

    ids_unicos = models.PositiveIntegerField(
        default=0,
    )

    ids_repetidos = models.PositiveIntegerField(
        default=0,
    )

    vinculados = models.PositiveIntegerField(
        default=0,
    )

    no_encontrados = models.PositiveIntegerField(
        default=0,
    )

    ya_existentes_mes = models.PositiveIntegerField(
        default=0,
    )

    creados = models.PositiveIntegerField(
        default=0,
    )

    errores = models.PositiveIntegerField(
        default=0,
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="importaciones_asignacion_planificacion",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    aplicado_en = models.DateTimeField(
        null=True,
        blank=True,
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    class Meta:
        ordering = [
            "-creado_en",
        ]

        indexes = [
            models.Index(
                fields=[
                    "planificacion",
                    "estado",
                ]
            ),
        ]

    def __str__(self):
        return f"Asignación {self.planificacion} " f"- {self.nombre_archivo or self.pk}"


# ============================================================
# BATCH / LOTE SEMANAL
# ============================================================


class BatchPlanificacionSemanal(models.Model):
    """
    Semana operacional global.

    ARQUITECTURA
    ==========================================================

    Un batch representa UNA semana real del calendario.

    Ejemplo:

        W36
        31/08/2026 al 06/09/2026

    Esa semana puede recibir sitios provenientes de más de una
    planificación mensual.

    Ejemplo:

        Agosto 2026
            5 sitios pendientes

        Septiembre 2026
            30 sitios nuevos

        ambos pueden convivir dentro del MISMO W36.

    NO deben existir:

        W36 Agosto
        W36 Septiembre

    como dos batches separados.

    Debe existir únicamente:

        W36

    con múltiples PlanificacionMensual como origen.
    """

    ESTADOS = [
        ("borrador", "Borrador"),
        ("propuesto", "Propuesto"),
        ("gestion_permisos", "Gestión de permisos"),
        ("listo_planificar", "Listo para planificar"),
        ("planificado", "Planificado"),
        ("cerrado", "Cerrado"),
        ("cancelado", "Cancelado"),
    ]

    # ========================================================
    # MES DE CREACIÓN HISTÓRICO
    # ========================================================
    #
    # Se conserva temporalmente para compatibilidad con todo
    # el código existente.
    #
    # NO representa ya que el batch pertenezca exclusivamente
    # a ese mes.
    #
    # Los servicios nuevos deben utilizar:
    #
    #     planificaciones_origen
    #
    # ========================================================

    planificacion = models.ForeignKey(
        PlanificacionMensual,
        on_delete=models.SET_NULL,
        related_name="batches_legacy",
        null=True,
        blank=True,
        help_text=(
            "Planificación mensual que originó inicialmente "
            "el batch. Campo legacy; una semana puede actualmente "
            "recibir sitios de múltiples meses."
        ),
    )

    # ========================================================
    # MESES QUE ALIMENTAN LA SEMANA
    # ========================================================

    planificaciones_origen = models.ManyToManyField(
        PlanificacionMensual,
        related_name="batches_semanales",
        blank=True,
        help_text=(
            "Planificaciones mensuales cuyos sitios pueden "
            "participar dentro de esta semana operacional."
        ),
    )

    # ========================================================
    # CONFIGURACIÓN GLOBAL DE LA SEMANA
    # ========================================================

    configuracion_semana = models.OneToOneField(
        ConfiguracionSemana,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batch",
    )

    # ========================================================
    # SEMANA ISO
    # ========================================================

    fecha_inicio = models.DateField(
        db_index=True,
        help_text="Lunes de la semana ISO objetivo.",
    )

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="borrador",
        db_index=True,
    )

    nombre = models.CharField(
        max_length=150,
        blank=True,
        default="",
    )

    objetivo_sitios = models.PositiveIntegerField(
        default=40,
        help_text=(
            "Cantidad objetivo aproximada de sitios activos " "dentro de la semana."
        ),
    )

    generado_por_motor = models.BooleanField(
        default=False,
    )

    observaciones = models.TextField(
        blank=True,
        default="",
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches_planificacion_creados",
    )

    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches_planificacion_actualizados",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "fecha_inicio",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "fecha_inicio",
                ],
                name="uq_batch_semana_global",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "fecha_inicio",
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

    def __str__(self):
        return self.nombre or self.codigo_semana

    # ========================================================
    # IDENTIDAD ISO
    # ========================================================

    @property
    def numero_semana_iso(self):
        return self.fecha_inicio.isocalendar().week

    @property
    def anio_semana_iso(self):
        return self.fecha_inicio.isocalendar().year

    @property
    def codigo_semana(self):
        return f"W{self.numero_semana_iso}"

    # ========================================================
    # RANGO DE LA SEMANA
    # ========================================================

    @property
    def fecha_fin(self):
        return self.fecha_inicio + timedelta(
            days=6,
        )

# ============================================================
# SITIO DENTRO DEL BATCH SEMANAL
# ============================================================


class SitioBatchSemanal(models.Model):
    """
    Relación entre un SitioPlanificado y un BatchPlanificacionSemanal.

    No usamos un ManyToMany simple porque necesitamos conservar
    el estado y la historia de cada sitio dentro de la propuesta
    semanal.
    """

    ESTADOS = [
        ("candidato", "Candidato"),
        ("seleccionado", "Seleccionado"),
        ("gestion_permiso", "Gestión de permiso"),
        ("disponible", "Disponible"),
        ("rechazado", "Rechazado"),
        ("sin_respuesta", "Sin respuesta"),
        ("excluido", "Excluido"),
        ("reemplazado", "Reemplazado"),
        ("confirmado", "Confirmado"),
    ]

    ORIGENES = [
        ("motor", "Motor"),
        ("manual", "Manual"),
        ("prioridad", "Prioridad"),
        ("reemplazo", "Reemplazo"),
    ]

    batch = models.ForeignKey(
        BatchPlanificacionSemanal,
        on_delete=models.CASCADE,
        related_name="sitios",
    )

    sitio_planificado = models.ForeignKey(
        SitioPlanificado,
        on_delete=models.CASCADE,
        related_name="participaciones_batch",
    )

    estado = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default="candidato",
        db_index=True,
    )

    origen = models.CharField(
        max_length=20,
        choices=ORIGENES,
        default="manual",
        db_index=True,
    )

    puntaje_motor = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Puntaje calculado por el motor para recomendar "
            "este sitio dentro del batch."
        ),
    )

    motivo_recomendacion = models.TextField(
        blank=True,
        default="",
    )

    motivo_exclusion = models.TextField(
        blank=True,
        default="",
    )

    agregado_manualmente = models.BooleanField(
        default=False,
    )

    bloqueado_en_batch = models.BooleanField(
        default=False,
        help_text=(
            "Evita que futuras recalculaciones automáticas "
            "eliminen el sitio de este batch."
        ),
    )

    es_reserva = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Indica que el sitio pertenece al batch como alternativa "
            "o respaldo y no como sitio principal."
        ),
    )

    agregado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sitios_batch_agregados",
    )

    creado_en = models.DateTimeField(
        auto_now_add=True,
    )

    actualizado_en = models.DateTimeField(
        auto_now=True,
    )

    cluster_codigo = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Identificador del cluster geográfico asignado "
            "por el motor dentro del batch semanal."
        ),
    )

    class Meta:
        ordering = [
            "-puntaje_motor",
            "sitio_planificado__sitio__id_claro",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "batch",
                    "sitio_planificado",
                ],
                name="uq_sitio_batch_semanal",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "batch",
                    "estado",
                ]
            ),
            models.Index(
                fields=[
                    "origen",
                    "estado",
                ]
            ),
        ]

    def __str__(self):
        return f"{self.batch} - " f"{self.sitio_planificado.sitio.id_claro}"

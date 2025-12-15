# bot_gz/models.py

from django.conf import settings
from django.db import models
from django.utils import timezone


class BotIntent(models.Model):
    """
    Intento / intención del bot.
    Ejemplos:
    - "mis_liquidaciones"
    - "mi_contrato"
    - "mi_produccion_hasta_hoy"
    - "mis_proyectos_rechazados"
    - "mis_rendiciones_pendientes"
    """

    SCOPE_CHOICES = [
        ("global", "Global"),
        ("tecnico", "Técnico"),
        ("admin", "Administrativo"),
        ("finanzas", "Finanzas"),
        ("rrhh", "RRHH"),
    ]

    slug = models.SlugField(
        unique=True,
        help_text="Identificador interno, ej: 'mis_liquidaciones'",
    )
    nombre = models.CharField(
        max_length=100,
        help_text="Nombre amigable del intent, visible en admin.",
    )
    descripcion = models.TextField(
        blank=True,
        default="",
        help_text="Descripción de qué hace este intent.",
    )
    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES,
        default="tecnico",
        help_text="Ámbito principal donde aplica este intent.",
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si está desactivado, el motor no lo usará.",
    )

    # Opcional: marcar intents que siempre queremos revisar
    requiere_revision_humana = models.BooleanField(
        default=False,
        help_text="Si está activo, las respuestas de este intent se marcan para revisión en la consola de entrenamiento.",
    )

    class Meta:
        verbose_name = "Intent del bot"
        verbose_name_plural = "Intents del bot"

    def __str__(self):
        return f"{self.slug} ({self.get_scope_display()})"


class BotTrainingExample(models.Model):
    """
    Frases de entrenamiento que asociamos a un intent.
    Estas son las frases que el bot va a usar para aprender
    a detectar las intenciones (aunque al inicio lo hagamos
    con reglas y matching manual).
    """

    intent = models.ForeignKey(
        BotIntent,
        on_delete=models.CASCADE,
        related_name="training_examples",
    )
    texto = models.CharField(
        max_length=512,
        help_text="Frase tal como la escribiría un usuario.",
    )
    locale = models.CharField(
        max_length=10,
        default="es",
        help_text="Idioma / variante. Ej: 'es', 'es-CL'.",
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si está desactivado, no se usa para entrenamiento.",
    )

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bot_training_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ejemplo de entrenamiento"
        verbose_name_plural = "Ejemplos de entrenamiento"

    def __str__(self):
        return f"[{self.intent.slug}] {self.texto[:70]}"


class BotSession(models.Model):
    """
    Estado de sesión del usuario con el bot.
    Aquí guardamos en qué contexto está (modo técnico / modo admin),
    si hay un flujo pendiente (ej: rendición de gastos a medio completar),
    etc.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bot_sesiones",
        help_text="Usuario interno vinculado a este chat (si se pudo mapear).",
    )
    chat_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Chat ID de Telegram.",
    )

    # Contexto actual: 'tecnico', 'admin', 'finanzas', etc.
    contexto = models.CharField(
        max_length=20,
        default="tecnico",
        help_text="Contexto actual del bot para este chat.",
    )

    # Estado de flujo (ej: 'rendicion_gasto_paso_1', etc.)
    estado = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Identificador del estado actual del flujo conversacional.",
    )

    # Datos temporales del flujo (JSON)
    datos_contexto = models.JSONField(
        blank=True,
        null=True,
        help_text="Datos temporales para completar flujos (rendiciones, filtros, etc.).",
    )

    ultimo_intent = models.ForeignKey(
        BotIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sesiones_ultimo_intent",
    )

    activa = models.BooleanField(
        default=True,
        help_text="Para pausar sesiones si es necesario.",
    )

    ultima_interaccion = models.DateTimeField(
        default=timezone.now,
        help_text="Última vez que el usuario habló con el bot.",
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Sesión del bot"
        verbose_name_plural = "Sesiones del bot"
        indexes = [
            models.Index(fields=["chat_id"]),
            models.Index(fields=["usuario", "activa"]),
        ]

    def __str__(self):
        if self.usuario:
            return f"Sesion bot {self.chat_id} ({self.usuario})"
        return f"Sesion bot {self.chat_id}"


class BotMessageLog(models.Model):
    """
    Historial de mensajes entre usuario y bot.
    Esto es la base de:
    - auditoría
    - trazabilidad
    - consola de entrenamiento (ver qué no entendió)
    """

    DIRECCION_CHOICES = [
        ("in", "Usuario → Bot"),
        ("out", "Bot → Usuario"),
    ]

    STATUS_CHOICES = [
        ("ok", "OK"),
        ("fallback", "Fallback (no entendido)"),
        ("error", "Error interno"),
    ]

    sesion = models.ForeignKey(
        BotSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bot_mensajes",
        help_text="Usuario interno vinculado (si lo hay).",
    )
    chat_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Chat ID de Telegram (duplicado por comodidad).",
    )

    direccion = models.CharField(
        max_length=3,
        choices=DIRECCION_CHOICES,
    )
    texto = models.TextField(
        help_text="Texto del mensaje enviado o recibido.",
    )

    intent_detectado = models.ForeignKey(
        BotIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes_intent_detectado",
        help_text="Intent que el motor cree que corresponde.",
    )
    # Cuando un admin corrige manualmente el intent en la vista de entrenamiento
    intent_corregido = models.ForeignKey(
        BotIntent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensajes_intent_corregido",
        help_text="Intent corregido manualmente para entrenamiento.",
    )

    confianza = models.FloatField(
        null=True,
        blank=True,
        help_text="Score de confianza del clasificador (si aplica).",
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="ok",
        help_text="Resultado interno del procesamiento de este mensaje.",
    )

    # Para marcar qué mensajes deben salir en la consola de entrenamiento
    marcar_para_entrenamiento = models.BooleanField(
        default=False,
        help_text="Si está activo, este mensaje aparece en la vista de entrenamiento.",
    )

    # Datos extra (por ejemplo, filtros interpretados, parámetros, errores)
    meta = models.JSONField(
        blank=True,
        null=True,
        help_text="Metadata adicional (parámetros interpretados, errores, etc.).",
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mensaje del bot"
        verbose_name_plural = "Mensajes del bot"
        ordering = ["-creado_en"]
        indexes = [
            models.Index(fields=["chat_id", "creado_en"]),
            models.Index(fields=["status"]),
            models.Index(fields=["marcar_para_entrenamiento"]),
        ]

    def __str__(self):
        dir_str = dict(self.DIRECCION_CHOICES).get(self.direccion, self.direccion)
        return f"[{dir_str}] {self.chat_id} - {self.texto[:50]}"
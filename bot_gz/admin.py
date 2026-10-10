# bot_gz/admin.py

from django.contrib import admin

from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample


@admin.register(BotIntent)
class BotIntentAdmin(admin.ModelAdmin):
    # Tus campos reales: slug, nombre, scope, activo
    list_display = ("slug", "nombre", "scope", "activo")
    list_filter = ("scope", "activo")
    search_fields = ("slug", "nombre", "descripcion")
    prepopulated_fields = {"slug": ("nombre",)}
    ordering = ("scope", "slug")


@admin.register(BotTrainingExample)
class BotTrainingExampleAdmin(admin.ModelAdmin):
    # En el modelo el campo de fecha es 'creado_en'
    list_display = ("texto", "intent", "activo", "creado_en")
    # El contexto está en BotIntent.scope, no en 'contexto'
    list_filter = ("activo", "intent__scope")
    search_fields = ("texto", "intent__slug", "intent__nombre")
    autocomplete_fields = ("intent",)
    ordering = ("-creado_en",)


@admin.register(BotSession)
class BotSessionAdmin(admin.ModelAdmin):
    list_display = (
        "chat_id",
        "usuario",
        "contexto",
        "estado",
        "activa",
        "ultimo_intent",
        "ultima_interaccion",
        "creado_en",
    )
    list_filter = ("contexto", "activa")
    search_fields = (
        "chat_id",
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
    )
    autocomplete_fields = ("usuario", "ultimo_intent")
    # En el modelo no existe 'created_at', es 'creado_en'
    readonly_fields = ("creado_en", "ultima_interaccion")
    ordering = ("-ultima_interaccion",)


@admin.register(BotMessageLog)
class BotMessageLogAdmin(admin.ModelAdmin):
    list_display = (
        "creado_en",
        "chat_id",
        "usuario",
        "direccion",
        "resumen_texto",
        "intent_detectado",
        "confianza",
        "status",
        "marcar_para_entrenamiento",
    )
    list_filter = (
        "direccion",
        "status",
        "marcar_para_entrenamiento",
        # contexto → scope en BotIntent
        "intent_detectado__scope",
        "intent_detectado",
    )
    search_fields = ("chat_id", "usuario__username", "texto")
    autocomplete_fields = (
        "sesion",
        "usuario",
        "intent_detectado",
        "intent_corregido",
    )
    # Igual: no hay 'created_at', sino 'creado_en'
    readonly_fields = ("creado_en", "meta")

    def resumen_texto(self, obj):
        txt = (obj.texto or "").strip()
        return (txt[:60] + "…") if len(txt) > 60 else txt

    resumen_texto.short_description = "Texto"
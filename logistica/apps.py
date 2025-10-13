from django.apps import AppConfig


class LogisticaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'logistica'

    def ready(self):  # ← Ahora sí está dentro de la clase
        import logistica.signals

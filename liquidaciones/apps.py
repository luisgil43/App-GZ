from django.apps import AppConfig


class LiquidacionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'liquidaciones'
    verbose_name = 'Gestión de Liquidaciones'


"""
    def ready(self):
        from simple_history import register
        from .models import Liquidacion

        register(Liquidacion) """

from django.apps import AppConfig
import sys


class UsuariosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'usuarios'

    def ready(self):
        import usuarios.signals  # Señales
        # Iniciar el scheduler SOLO si el servidor está corriendo
        if 'runserver' in sys.argv or 'gunicorn' in sys.argv:
            from . import schedulers
            schedulers.iniciar_scheduler()

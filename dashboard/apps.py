from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'
    verbose_name = "Dashboard"

    def ready(self):
        from simple_history import register
        # 👉 importa todos los modelos que quieras auditar
        from .models import ProduccionTecnico

        register(ProduccionTecnico)  # 👉 registra cada modelo

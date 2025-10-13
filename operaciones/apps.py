
# operaciones/apps.py
from django.apps import AppConfig


class OperacionesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "operaciones"

    def ready(self):
        from . import signals  # noqa

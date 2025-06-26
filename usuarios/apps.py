from django.apps import AppConfig
# from simple_history import register


class UsuariosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'usuarios'


"""
    def ready(self):
        import usuarios.signals
        from .models import CustomUser
        register(CustomUser)"""

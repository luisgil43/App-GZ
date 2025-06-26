from django.apps import AppConfig
# from simple_history import register


class RrhhConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rrhh'
    verbose_name = "Recursos Humanos"


"""
    def ready(self):
        from .models import (
            ContratoTrabajo,
            FichaIngreso,
            SolicitudVacaciones,
            DocumentoTrabajador,
            TipoDocumento,
            Feriado,
            CronogramaPago
        )
        register(ContratoTrabajo)
        register(FichaIngreso)
        register(SolicitudVacaciones)
        register(DocumentoTrabajador)
        register(TipoDocumento)
        register(Feriado)
        register(CronogramaPago)
"""

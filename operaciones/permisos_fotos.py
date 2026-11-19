from .models import ServicioCotizado

_ORIGEN_CAMARA = object()


def archivos_evidencias_habilitados(asignacion, usuario):
    return ServicioCotizado.objects.filter(
        pk=asignacion.sesion.servicio_id,
        permitir_archivos_evidencias=True,
        trabajadores_asignados=usuario,
    ).exists()

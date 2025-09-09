# operaciones/utils.py
from .models import SesionFotoTecnico


def ensure_sesion_y_asignaciones(servicio, reset_estados=True):
    """
    - Garantiza que exista la sesión de fotos del servicio.
    - Para cada técnico asignado crea/ajusta su SesionFotoTecnico.
    - Opcionalmente (reset_estados=True) fuerza estado 'asignado' para que aparezca el botón Aceptar.
    - Quita asignaciones de técnicos que ya no estén asignados.
    """
    sesion = _get_or_create_sesion(servicio)

    # (1) Mapear las asignaciones actuales
    actuales = {
        a.tecnico_id: a for a in SesionFotoTecnico.objects.filter(sesion=sesion)}
    asignados_ids = set(
        servicio.trabajadores_asignados.values_list('id', flat=True))

    # (2) Crear/actualizar para cada técnico asignado
    for tid in asignados_ids:
        a = actuales.get(tid)
        if not a:
            a = SesionFotoTecnico.objects.create(
                sesion=sesion, tecnico_id=tid, estado='asignado')
        elif reset_estados:
            # Reseteo para que pueda aceptar nuevamente
            a.estado = 'asignado'
            # Limpia marcas de tiempos si existen
            if hasattr(a, 'aceptado_en'):
                a.aceptado_en = None
            if hasattr(a, 'finalizado_en'):
                a.finalizado_en = None
            if hasattr(a, 'rechazado_en'):
                a.rechazado_en = None
            a.save()

    # (3) Eliminar/invalidar asignaciones de técnicos que ya no están asignados
    for tid, a in actuales.items():
        if tid not in asignados_ids:
            a.delete()

    return sesion

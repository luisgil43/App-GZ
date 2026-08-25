# planificacion/templatetags/planificacion_formatos.py

from django import template

register = template.Library()


@register.filter
def duracion_humana(total_minutos):
    """
    Convierte minutos a un formato operacional legible.

    Ejemplos:

        45  -> 45 min
        60  -> 1 h
        86  -> 1 h 26 min
        120 -> 2 h
        558 -> 9 h 18 min
    """

    try:
        total_minutos = int(total_minutos or 0)

    except (
        TypeError,
        ValueError,
    ):
        return "0 min"

    total_minutos = max(
        total_minutos,
        0,
    )

    horas = total_minutos // 60
    minutos = total_minutos % 60

    if horas and minutos:
        return f"{horas} h {minutos} min"

    if horas:
        return f"{horas} h"

    return f"{minutos} min"

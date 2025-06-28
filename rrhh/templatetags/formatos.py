from django.template.defaultfilters import date as django_date
from django import template

register = template.Library()


@register.filter
def reemplazar(value, args):
    old, new = args.split(',')
    return value.replace(old, new)


@register.filter
def punto_miles(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (ValueError, TypeError):
        return value


@register.filter
def fecha(value, formato):
    return django_date(value, formato)

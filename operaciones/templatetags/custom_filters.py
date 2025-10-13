# operaciones/templatetags/custom_filters.py
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django import template

register = template.Library()


@register.filter
def miles(value):
    try:
        value = int(value)
        return f"{value:,}".replace(",", ".")
    except (ValueError, TypeError):
        return value


@register.filter
def decimal_coma(value):
    try:
        return str(value).replace('.', ',')
    except (ValueError, TypeError):
        return value


@register.filter
def miles_decimales(value):
    try:
        valor = float(value)
        entero, decimal = f"{valor:.2f}".split(".")
        entero_con_miles = f"{int(entero):,}".replace(",", ".")
        return f"{entero_con_miles},{decimal}"
    except (ValueError, TypeError):
        return value


@register.filter
def formato_clp(value):
    """CLP sin decimales, miles con punto."""
    try:
        valor = int(float(value))
        return f"{valor:,}".replace(",", ".")
    except (ValueError, TypeError):
        return value


@register.filter
def formato_uf(value):
    try:
        valor = float(value)
        entero, decimal = f"{valor:.2f}".split(".")
        entero_con_miles = f"{int(entero):,}".replace(",", ".")
        return f"{entero_con_miles},{decimal}"
    except (ValueError, TypeError):
        return value


@register.filter
def field_label(form, field_name):
    return form.fields[field_name].label


@register.filter
def miles_dec(value):
    """
    Miles '.' y decimales ',' (0–2), sin ceros de cola.
    1234.50 -> '1.234,5'; 1234.00 -> '1.234'
    """
    if value in (None, ""):
        return ""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    s = f"{d:,.2f}"                         # '1,234.50'
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")  # '1.234,50'
    if "," in s:
        s = s.rstrip("0").rstrip(",")        # quita ceros/ coma final
    return s


@register.filter
def clp_round(value):
    try:
        d = Decimal(str(value or 0)).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP)
        return f"{int(d):,}".replace(",", ".")
    except Exception:
        return value

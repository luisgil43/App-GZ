from decimal import Decimal, InvalidOperation
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
    """Convierte un número decimal con punto a formato con coma."""
    try:
        return str(value).replace('.', ',')
    except (ValueError, TypeError):
        return value


@register.filter
def miles_decimales(value):
    """
    Formatea decimales con separador de miles y coma como separador decimal.
    Ejemplo: 3065.5 -> 3.065,50
    """
    try:
        valor = float(value)
        entero, decimal = f"{valor:.2f}".split(".")
        entero_con_miles = f"{int(entero):,}".replace(",", ".")
        return f"{entero_con_miles},{decimal}"
    except (ValueError, TypeError):
        return value


@register.filter
def formato_clp(value):
    """
    Formatea un número como CLP sin decimales y con puntos de miles.
    Ejemplo: 1324234 -> 1.324.234
    """
    try:
        valor = int(float(value))
        return f"{valor:,}".replace(",", ".")
    except (ValueError, TypeError):
        return value


@register.filter
def formato_uf(value):
    """Formatea valores en UF con separador de miles y dos decimales."""
    try:
        valor = float(value)
        entero, decimal = f"{valor:.2f}".split(".")
        entero_con_miles = f"{int(entero):,}".replace(",", ".")
        return f"{entero_con_miles},{decimal}"
    except (ValueError, TypeError):
        return value


@register.filter
def field_label(form, field_name):
    """Obtiene el label de un campo del formulario."""
    return form.fields[field_name].label


@register.filter
def miles_dec(value):
    """
    Formatea con miles '.' y decimales ',' (0–2), sin ceros de cola.
    1234.50 -> '1.234,5'
    1234.20 -> '1.234,2'
    1234.23 -> '1.234,23'
    1234.00 -> '1.234'
    """
    if value in (None, ""):
        return ""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    # 2 decimales fijos en notación US ('1,234.50')
    s = f"{d:,.2f}"

    # Cambia a notación CL: miles '.' y decimales ','
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")

    # Quita ceros de cola y la coma si no quedan decimales
    if "," in s:
        s = s.rstrip("0").rstrip(",")

    return s

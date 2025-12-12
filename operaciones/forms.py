# servicios/forms.py


import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import ModelMultipleChoiceField

from facturacion.models import CartolaMovimiento

from .models import ServicioCotizado, SitioMovil


class ServicioCotizadoForm(forms.ModelForm):
    monto_cotizado = forms.CharField()
    monto_mmoo = forms.CharField(required=False)

    class Meta:
        model = ServicioCotizado
        fields = [
            'id_claro', 'region', 'mes_produccion',
            'id_new', 'detalle_tarea', 'monto_cotizado', 'monto_mmoo'
        ]
        widgets = {
            'detalle_tarea': forms.Textarea(attrs={'rows': 3}),
            'mes_produccion': forms.TextInput(attrs={'placeholder': 'Ej: Julio 2025'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

        # Campo monto cotizado como texto con coma
        self.fields['monto_cotizado'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 10,00 UF'}
        )
        # Campo monto MMOO como texto para aceptar miles y coma decimal
        self.fields['monto_mmoo'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 632.543,76'}
        )

        # Preformatear valores iniciales para edición
        if self.instance and self.instance.pk:
            if self.instance.monto_cotizado is not None:
                # Mostrar coma como separador decimal
                self.initial['monto_cotizado'] = str(
                    self.instance.monto_cotizado).replace(".", ",")
            if self.instance.monto_mmoo is not None:
                # Mostrar con separador de miles y coma como decimal
                self.initial['monto_mmoo'] = f"{self.instance.monto_mmoo:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")

    def clean_monto_cotizado(self):
        """Convierte UF a Decimal, acepta coma o punto como separador y fuerza 2 decimales."""
        data = self.cleaned_data.get('monto_cotizado')
        if not data:
            raise forms.ValidationError("Este campo es obligatorio.")
        data = data.replace(" ", "").replace(",", ".")
        try:
            value = Decimal(data).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value <= 0:
                raise forms.ValidationError("El monto debe ser mayor que 0.")
            return value
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 0,00")

    def clean_monto_mmoo(self):
        """Convierte CLP formateado (1.234.567,89) a Decimal."""
        data = self.cleaned_data.get('monto_mmoo')
        if not data:
            return None
        # Eliminar espacios, puntos de miles y reemplazar coma por punto
        data = data.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(data).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError("El monto no puede ser negativo.")
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 1.234,56")


User = get_user_model()


class AsignarTrabajadoresForm(forms.Form):
    trabajadores = ModelMultipleChoiceField(
        queryset=User.objects.filter(roles__nombre='usuario', is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Selecciona uno o más trabajadores",          # 🔹 antes: "uno o dos"
        help_text="Debes seleccionar al menos un trabajador.",  # 🔹 opcional
    )

    def clean_trabajadores(self):
        trabajadores = self.cleaned_data.get('trabajadores')
        # 🔹 Solo exigimos mínimo 1, sin límite máximo
        if not trabajadores or trabajadores.count() == 0:
            raise forms.ValidationError(
                "Debes seleccionar al menos un trabajador."
            )
        return trabajadores


class MovimientoUsuarioForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        label="Monto",
        required=True
    )

    class Meta:
        model = CartolaMovimiento
        fields = ['proyecto', 'tipo', 'tipo_doc', 'rut_factura',
                  'numero_doc', 'cargos', 'observaciones', 'comprobante']
        widgets = {
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo_doc': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'numero_doc': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'rut_factura': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Ej: 12.345.678-5'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 3}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 🔑 Nunca dejes que el propio campo FileField dispare "required".
        # Lo controlamos manualmente en clean().
        self.fields['comprobante'].required = False

        # Prellenar monto en edición
        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial['cargos'] = (
                f"{self.instance.cargos:,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            )

    def clean_cargos(self):
        valor = self.cleaned_data.get('cargos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 1.234,56")

    def clean(self):
        cleaned = super().clean()

        # 1) Detectar si ya existía comprobante (edición)
        has_old = bool(
            self.instance and self.instance.pk and self.instance.comprobante)

        # 2) Tomar el comprobante que venga por cualquiera de los inputs
        #    Ajusta estos nombres si en tu template usas otros.
        uploaded = (
            self.files.get('comprobante') or
            self.files.get('comprobante_foto') or
            self.files.get('comprobante_archivo')
        )

        # 3) Copiar al campo del modelo para que se guarde
        if uploaded:
            cleaned['comprobante'] = uploaded

        # 4) Reglas de obligatoriedad
        is_create = not (self.instance and self.instance.pk)
        if (is_create and not uploaded) or (not is_create and not has_old and not uploaded):
            self.add_error('comprobante', "Este campo es obligatorio.")

        # (Opcional) tus otras validaciones (RUT si es factura, etc.) pueden quedarse
        return cleaned


def validar_rut_chileno(rut):
    """Valida el dígito verificador del RUT chileno."""
    if not rut:
        return False
    rut = rut.replace(".", "").replace("-", "").strip().upper()
    if not rut[:-1].isdigit():
        return False
    cuerpo = rut[:-1]
    dv = rut[-1]
    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1
    resto = suma % 11
    dv_esperado = "0" if resto == 0 else "K" if resto == 1 else str(11 - resto)
    return dv == dv_esperado


def verificar_rut_sii(rut):
    """Verifica el RUT en el sitio del SII (básico)."""
    url = "https://zeus.sii.cl/cgi_rut/CONSULTA.cgi"
    data = {"RUT": rut}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        response = requests.post(url, data=data, headers=headers, timeout=5)
        return "RUT no válido" not in response.text
    except:
        return True


class SitioMovilForm(forms.ModelForm):
    class Meta:
        model = SitioMovil
        fields = [
            "id_sites", "id_claro", "id_sites_new", "region", "nombre", "direccion",
            "latitud", "longitud", "comuna", "tipo_construccion", "altura",
            "candado_bt", "condiciones_acceso", "claves", "llaves", "cantidad_llaves",
            "observaciones_generales", "zonas_conflictivas", "alarmas", "guardias",
            "nivel", "descripcion",
        ]
        widgets = {
            "direccion": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "condiciones_acceso": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "observaciones_generales": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "descripcion": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "zonas_conflictivas": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "claves": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "llaves": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "tipo_construccion": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "region": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "nombre": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "comuna": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_sites": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_claro": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_sites_new": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "candado_bt": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "alarmas": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "guardias": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "nivel": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "altura": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
            "cantidad_llaves": forms.NumberInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "latitud": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
            "longitud": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
        }

# servicios/forms.py


from django.core.exceptions import ValidationError
import requests
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from facturacion.models import CartolaMovimiento
from decimal import Decimal
from django.forms import ModelMultipleChoiceField
from django.contrib.auth import get_user_model
from django import forms
from .models import ServicioCotizado
from decimal import Decimal, InvalidOperation


from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django import forms
from .models import ServicioCotizado


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
        label="Selecciona uno o dos trabajadores"
    )

    def clean_trabajadores(self):
        trabajadores = self.cleaned_data['trabajadores']
        if not (1 <= trabajadores.count() <= 2):
            raise forms.ValidationError(
                "Debes seleccionar uno o dos trabajadores.")
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
        labels = {
            'cargos': 'Monto',
            'tipo_doc': 'Tipo de Documento',
            'numero_doc': 'Número de Documento',
            'rut_factura': 'RUT Factura',
            'comprobante': 'Comprobante',
            'observaciones': 'Observaciones',
            'proyecto': 'Proyecto',
            'tipo': 'Tipo',
        }
        widgets = {
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'required': 'required'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'required': 'required'}),
            'tipo_doc': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'required': 'required'}),
            'numero_doc': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'required': 'required'}),
            'rut_factura': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Ej: 12.345.678-5'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 3, 'required': 'required'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Preformatear para edición (mostrar con miles y coma)
        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                ",", "X").replace(".", ",").replace("X", ".")

    def clean_cargos(self):
        """Convierte texto con separadores de miles a Decimal."""
        valor = self.cleaned_data.get('cargos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 1.234,56")

    def clean(self):
        cleaned_data = super().clean()
        tipo_doc = cleaned_data.get("tipo_doc")
        rut = cleaned_data.get("rut_factura")

        # --- Unificar comprobante (foto o archivo)
        comprobante_foto = self.files.get("comprobante_foto")
        comprobante_archivo = self.files.get("comprobante_archivo")
        comprobante = comprobante_foto or comprobante_archivo
        if not comprobante:
            self.add_error(
                None, "Debe adjuntar un comprobante (PDF o imagen).")
        else:
            cleaned_data["comprobante"] = comprobante  # Guardar en el dict

        # --- RUT obligatorio si es factura
        if tipo_doc and "factura" in tipo_doc.lower():
            if not rut:
                self.add_error(
                    "rut_factura", "El RUT es obligatorio para facturas.")
            elif not validar_rut_chileno(rut):
                self.add_error("rut_factura", "El RUT ingresado no es válido.")
            elif not verificar_rut_sii(rut):
                self.add_error(
                    "rut_factura", "El RUT no está registrado en el SII.")

        # --- Validar que todos los campos obligatorios no estén vacíos
        campos_obligatorios = ['proyecto', 'tipo',
                               'tipo_doc', 'numero_doc', 'cargos', 'observaciones']
        for campo in campos_obligatorios:
            if not cleaned_data.get(campo):
                self.add_error(campo, "Este campo es obligatorio.")

        return cleaned_data


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

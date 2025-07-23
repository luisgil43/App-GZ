# servicios/forms.py

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

        # Forzar como texto para evitar validación HTML5 numérica
        self.fields['monto_cotizado'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 10,00 UF'}
        )
        self.fields['monto_mmoo'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 60.000 CLP'}
        )

        # Preformatear valores iniciales para edición
        if self.instance and self.instance.pk:
            if self.instance.monto_cotizado is not None:
                # Mostrar coma como separador decimal
                self.initial['monto_cotizado'] = str(
                    self.instance.monto_cotizado).replace(".", ",")
            if self.instance.monto_mmoo is not None:
                # Mostrar sin decimales y con puntos de miles
                self.initial['monto_mmoo'] = f"{int(self.instance.monto_mmoo):,}".replace(
                    ",", ".")

    def clean_monto_cotizado(self):
        """Convierte UF a Decimal, acepta coma o punto como separador y fuerza 2 decimales."""
        data = self.cleaned_data.get('monto_cotizado')
        if not data:
            raise forms.ValidationError("Este campo es obligatorio.")
        # Reemplaza coma por punto
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
        """Convierte CLP a Decimal eliminando separadores de miles."""
        data = self.cleaned_data.get('monto_mmoo')
        if not data:
            return None
        data = data.replace(".", "").replace(",", "")
        try:
            value = Decimal(data)
            if value < 0:
                raise forms.ValidationError("El monto no puede ser negativo.")
            return value
        except InvalidOperation:
            raise forms.ValidationError("Ingrese un monto válido en CLP.")


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

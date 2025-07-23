# servicios/forms.py

from decimal import Decimal
from django.forms import ModelMultipleChoiceField
from django.contrib.auth import get_user_model
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

        # Forzar como texto para que no valide como number HTML5
        self.fields['monto_cotizado'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 10,00 UF'})
        self.fields['monto_mmoo'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 60.000 CLP'})

        # Preformatear valores iniciales para edición (sin decimales en CLP)
        if self.instance and self.instance.pk:
            if self.instance.monto_cotizado:
                # Reemplaza punto decimal por coma para la visualización de UF
                self.initial['monto_cotizado'] = str(
                    self.instance.monto_cotizado).replace(".", ",")
            if self.instance.monto_mmoo is not None:
                # Quitar decimales y aplicar puntos de miles
                self.initial['monto_mmoo'] = f"{int(self.instance.monto_mmoo):,}".replace(
                    ",", ".")

    def clean_monto_cotizado(self):
        """Convierte UF a float reemplazando coma por punto."""
        data = self.cleaned_data['monto_cotizado']
        if isinstance(data, str):
            data = data.replace('.', '').replace(',', '.')
        return float(data) if data else 0

    def clean_monto_mmoo(self):
        """Convierte CLP a Decimal eliminando puntos de miles."""
        data = self.cleaned_data['monto_mmoo']
        if isinstance(data, str):
            data = data.replace('.', '').replace(',', '')
        return Decimal(data) if data else None


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

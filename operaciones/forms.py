# servicios/forms.py

from django.forms import ModelMultipleChoiceField
from django.contrib.auth import get_user_model
from django import forms
from .models import ServicioCotizado


class ServicioCotizadoForm(forms.ModelForm):
    class Meta:
        model = ServicioCotizado
        fields = ['id_claro', 'region', 'mes_produccion',
                  'id_new', 'detalle_tarea', 'monto_cotizado']
        widgets = {
            'detalle_tarea': forms.Textarea(attrs={'rows': 3}),
            'mes_produccion': forms.TextInput(attrs={'placeholder': 'Ej: Julio 2025'}),
        }


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

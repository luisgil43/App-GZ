
from django import forms
from .models import IngenieroCampoSitio, OficinaRetiroLlaves


class IngenieroCampoSitioForm(forms.ModelForm):
    class Meta:
        model = IngenieroCampoSitio
        fields = ["nombre", "telefono"]


class OficinaRetiroLlavesForm(forms.ModelForm):
    class Meta:
        model = OficinaRetiroLlaves
        fields = ["nombre", "direccion"]

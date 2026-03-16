from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (Bodega, Herramienta, HerramientaAsignacion,
                     HerramientaInventario)


class BodegaForm(forms.ModelForm):
    class Meta:
        model = Bodega
        fields = ["nombre", "ubicacion"]
        labels = {"nombre": "Nombre", "ubicacion": "Ubicación"}
        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring focus:ring-emerald-500",
                    "placeholder": "Nombre de la bodega",
                }
            ),
            "ubicacion": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring focus:ring-emerald-500",
                    "placeholder": "Ubicación",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ ambos obligatorios SIEMPRE
        self.fields["nombre"].required = True
        self.fields["ubicacion"].required = True

    def clean(self):
        cleaned = super().clean()

        nombre = (cleaned.get("nombre") or "").strip()
        ubicacion = (cleaned.get("ubicacion") or "").strip()

        if not nombre:
            self.add_error("nombre", "El nombre es obligatorio.")

        if not ubicacion:
            self.add_error("ubicacion", "La ubicación es obligatoria.")

        cleaned["nombre"] = nombre
        cleaned["ubicacion"] = ubicacion
        return cleaned


class HerramientaForm(forms.ModelForm):
    class Meta:
        model = Herramienta
        fields = [
            "nombre",
            "descripcion",
            "serial",
            "valor_comercial",
            "foto",
            "bodega",
            "status",
            "status_justificacion",
        ]
        labels = {
            "nombre": "Nombre",
            "descripcion": "Descripción",
            "serial": "Serial",
            "valor_comercial": "Valor comercial",
            "foto": "Foto (opcional)",
            "bodega": "Bodega",
            "status": "Estado",
            "status_justificacion": "Justificación (obligatoria si es dañada/extraviada/robada)",
        }
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "status_justificacion": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_valor_comercial(self):
        v = self.cleaned_data.get("valor_comercial")
        if v is None:
            return v
        if v < 0:
            raise ValidationError("El valor comercial no puede ser negativo.")
        return v

    def clean(self):
        cleaned = super().clean()
        status = (cleaned.get("status") or "").strip()
        just = (cleaned.get("status_justificacion") or "").strip()

        if status in ("danada", "extraviada", "robada") and not just:
            self.add_error("status_justificacion", "Debes indicar una justificación para este estado.")
        return cleaned


class HerramientaAsignarForm(forms.Form):
    """
    Se usa para asignar / reasignar o dejar SIN ASIGNAR.
    - asignado_a ahora es opcional:
        - Si viene vacío => se cierra asignación activa (si existe) y queda en bodega.
    """
    asignado_a = forms.ModelChoiceField(
        queryset=None,
        label="Asignar a",
        required=False,  # ✅ permite "sin asignar"
        empty_label="— Sin asignar (dejar en bodega) —",
        widget=forms.Select(attrs={"class": "w-full"}),
    )
    asignado_at = forms.DateTimeField(
        label="Fecha asignación",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, user_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user_qs is None:
            raise ValueError("Debes pasar user_qs al formulario de asignación.")
        self.fields["asignado_a"].queryset = user_qs

        # default: ahora
        dt = timezone.localtime(timezone.now())
        self.initial.setdefault("asignado_at", dt.strftime("%Y-%m-%dT%H:%M"))


class InventarioUploadForm(forms.ModelForm):
    class Meta:
        model = HerramientaInventario
        fields = ["foto"]
        labels = {"foto": "Foto de inventario"}
        widgets = {
            "foto": forms.ClearableFileInput(),
        }


class InventarioReviewForm(forms.Form):
    motivo_rechazo = forms.CharField(
        label="Motivo (obligatorio)",
        required=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_motivo_rechazo(self):
        m = (self.cleaned_data.get("motivo_rechazo") or "").strip()
        if not m:
            raise ValidationError("Debes indicar un motivo.")
        return m


class RejectAssignmentForm(forms.Form):
    comentario = forms.CharField(
        label="Comentario (obligatorio)",
        required=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_comentario(self):
        c = (self.cleaned_data.get("comentario") or "").strip()
        if not c:
            raise ValidationError("Debes indicar un comentario.")
        return c
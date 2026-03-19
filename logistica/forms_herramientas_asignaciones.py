from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from usuarios.models import CustomUser

from .models import Herramienta, HerramientaAsignacion


class HerramientaAsignacionCantidadForm(forms.Form):
    herramienta_id = forms.IntegerField(widget=forms.HiddenInput())
    asignado_a = forms.ModelChoiceField(
        queryset=CustomUser.objects.filter(is_active=True).order_by("first_name", "last_name", "username"),
        label="Asignar a",
        required=True,
        widget=forms.Select(attrs={"class": "w-full"}),
    )
    cantidad_entregada = forms.IntegerField(
        label="Cantidad a entregar",
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "w-full", "min": "1"}),
    )
    asignado_at = forms.DateTimeField(
        label="Fecha asignación",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    solicitar_inventario = forms.BooleanField(
        required=False,
        initial=True,
        label="Solicitar inventario al asignar",
    )

    def __init__(self, *args, herramienta: Herramienta | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.herramienta = herramienta

        dt = timezone.localtime(timezone.now())
        self.initial.setdefault("asignado_at", dt.strftime("%Y-%m-%dT%H:%M"))

        if herramienta:
            self.initial.setdefault("herramienta_id", herramienta.id)

    def clean(self):
        cleaned = super().clean()
        cantidad = cleaned.get("cantidad_entregada")

        if self.herramienta and cantidad:
            if int(cantidad) > int(self.herramienta.cantidad):
                self.add_error("cantidad_entregada", f"No hay stock suficiente. Disponible: {self.herramienta.cantidad}.")
        return cleaned


class HerramientaAsignacionCerrarForm(forms.Form):
    cantidad_devuelta = forms.IntegerField(
        label="Cantidad devuelta",
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "w-full", "min": "0"}),
    )
    comentario_cierre = forms.CharField(
        label="Comentario",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "w-full"}),
    )
    justificacion_diferencia = forms.CharField(
        label="Justificación (si devuelve menos)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "w-full"}),
    )

    def __init__(self, *args, asignacion: HerramientaAsignacion | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asignacion = asignacion

    def clean(self):
        cleaned = super().clean()
        if not self.asignacion:
            raise ValidationError("Asignación inválida.")

        dev = cleaned.get("cantidad_devuelta")
        ent = int(self.asignacion.cantidad_entregada or 0)

        if dev is None:
            self.add_error("cantidad_devuelta", "Debes indicar cantidad devuelta.")
            return cleaned

        dev = int(dev)

        if dev > ent:
            self.add_error("cantidad_devuelta", "No puede ser mayor que la entregada.")
            return cleaned

        if dev < ent and not (cleaned.get("justificacion_diferencia") or "").strip():
            self.add_error("justificacion_diferencia", "Debes justificar la diferencia (faltante/daño/pérdida).")

        if dev == 0 and not (cleaned.get("comentario_cierre") or "").strip():
            self.add_error("comentario_cierre", "Si devuelve 0, debes indicar un comentario.")

        return cleaned
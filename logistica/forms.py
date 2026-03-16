from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from .models import (Bodega, CertificadoDigital, DetalleIngresoMaterial,
                     DetalleSalidaMaterial, IngresoMaterial, Material,
                     SalidaMaterial)


class IngresoMaterialForm(forms.ModelForm):
    class Meta:
        model = IngresoMaterial
        fields = ["tipo_documento", "numero_documento", "codigo_externo", "bodega", "archivo_documento"]
        widgets = {
            "tipo_documento": forms.Select(attrs={"class": "form-select"}),
            "numero_documento": forms.TextInput(attrs={"class": "form-input"}),
            "codigo_externo": forms.TextInput(attrs={"class": "form-input"}),
            "bodega": forms.Select(attrs={"class": "form-select"}),
            "archivo_documento": forms.FileInput(attrs={"accept": "application/pdf"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # si ya existe la instancia, el archivo puede quedar opcional
        if self.instance and self.instance.pk:
            self.fields["archivo_documento"].required = False

    def clean_archivo_documento(self):
        f = self.cleaned_data.get("archivo_documento")
        if not f:
            return f
        name = (getattr(f, "name", "") or "").lower()
        if name and not name.endswith(".pdf"):
            raise ValidationError("El archivo debe ser PDF.")
        return f


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = [
            "codigo_interno",
            "nombre",
            "codigo_externo",
            "bodega",
            "stock_actual",
            "stock_minimo",
            "unidad_medida",
            "valor_unitario",
            "descripcion",
            "activo",
        ]
        widgets = {
            "bodega": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "unidad_medida": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "valor_unitario": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "step": "0.01", "placeholder": "Ej: 12500"}
            ),
            "descripcion": forms.Textarea(attrs={"class": "w-full border rounded-xl px-3 py-2 resize-y"}),
        }

    def clean(self):
        cleaned = super().clean()

        nombre = (cleaned.get("nombre") or "").strip()
        codigo_interno = (cleaned.get("codigo_interno") or "").strip()
        codigo_externo = (cleaned.get("codigo_externo") or "").strip()
        bodega = cleaned.get("bodega")
        material_id = self.instance.pk

        if not bodega:
            return cleaned

        if nombre and Material.objects.filter(nombre__iexact=nombre, bodega=bodega).exclude(pk=material_id).exists():
            self.add_error("nombre", "Ya existe un material con ese nombre en esta bodega.")

        if codigo_interno and Material.objects.filter(codigo_interno__iexact=codigo_interno, bodega=bodega).exclude(
            pk=material_id
        ).exists():
            self.add_error("codigo_interno", "Ya existe un material con ese código interno en esta bodega.")

        if codigo_externo:
            if Material.objects.filter(codigo_externo__iexact=codigo_externo, bodega=bodega).exclude(pk=material_id).exists():
                self.add_error("codigo_externo", "Ya existe un material con ese código externo en esta bodega.")

        valor_unitario = cleaned.get("valor_unitario")
        if valor_unitario is not None and valor_unitario < 0:
            self.add_error("valor_unitario", "El valor unitario no puede ser negativo.")

        return cleaned


class ImportarExcelForm(forms.Form):
    archivo_excel = forms.FileField(
        label="Selecciona un archivo .xlsx",
        required=True,
        widget=forms.FileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_archivo_excel(self):
        archivo = self.cleaned_data.get("archivo_excel")
        if archivo:
            nombre = (archivo.name or "").lower().strip()
            if not nombre.endswith(".xlsx"):
                raise forms.ValidationError("Solo se permiten archivos .xlsx")
        return archivo


class FiltroIngresoForm(forms.Form):
    MESES = [
        ("", "Todos los meses"),
        (1, "Enero"),
        (2, "Febrero"),
        (3, "Marzo"),
        (4, "Abril"),
        (5, "Mayo"),
        (6, "Junio"),
        (7, "Julio"),
        (8, "Agosto"),
        (9, "Septiembre"),
        (10, "Octubre"),
        (11, "Noviembre"),
        (12, "Diciembre"),
    ]
    AÑOS = [(año, año) for año in range(2024, 2031)]

    mes = forms.ChoiceField(choices=MESES, label="Mes", required=False)
    anio = forms.ChoiceField(choices=AÑOS, label="Año")


class MaterialIngresoForm(forms.ModelForm):
    class Meta:
        model = DetalleIngresoMaterial
        fields = ["material", "cantidad"]
        widgets = {
            "material": forms.Select(attrs={"class": "form-select"}),
            "cantidad": forms.NumberInput(attrs={"class": "form-input", "min": 1}),
        }

    def clean_cantidad(self):
        cantidad = self.cleaned_data.get("cantidad")
        if cantidad is None or cantidad <= 0:
            raise forms.ValidationError("La cantidad debe ser mayor a cero.")
        return cantidad

class BodegaForm(forms.ModelForm):
    class Meta:
        model = Bodega
        fields = ["nombre", "ubicacion"]
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

        # ✅ ambos obligatorios SIEMPRE (independiente del modelo)
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

        # devolver strings limpios
        cleaned["nombre"] = nombre
        cleaned["ubicacion"] = ubicacion
        return cleaned


class SalidaMaterialForm(forms.ModelForm):
    class Meta:
        model = SalidaMaterial
        fields = [
            "bodega",
            "id_proyecto",
            "tipo_documento",
            "numero_documento",
            "rut_receptor",
            "nombre_receptor",
            "giro_receptor",
            "direccion_receptor",
            "comuna_receptor",
            "ciudad_receptor",
            "entregado_a",
            "emitido_por",
            "chofer",
            "patente",
            "origen",
            "destino",
            "obra",
            "rut_transportista",
        ]
        widgets = {
            "bodega": forms.Select(attrs={"class": "form-select"}),
            "id_proyecto": forms.TextInput(attrs={"class": "form-input"}),
            "tipo_documento": forms.Select(attrs={"class": "form-select"}),
            "numero_documento": forms.TextInput(attrs={"class": "form-input"}),
            "rut_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "nombre_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "giro_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "direccion_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "comuna_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "ciudad_receptor": forms.TextInput(attrs={"class": "form-input"}),
            "entregado_a": forms.Select(attrs={"class": "form-select"}),
            "emitido_por": forms.Select(attrs={"class": "form-select"}),
            "chofer": forms.TextInput(attrs={"class": "form-input"}),
            "patente": forms.TextInput(attrs={"class": "form-input"}),
            "origen": forms.TextInput(attrs={"class": "form-input"}),
            "destino": forms.TextInput(attrs={"class": "form-input"}),
            "obra": forms.TextInput(attrs={"class": "form-input"}),
            "rut_transportista": forms.TextInput(attrs={"class": "form-input"}),
        }


class DetalleSalidaForm(forms.ModelForm):
    class Meta:
        model = DetalleSalidaMaterial
        fields = ["material", "descripcion", "cantidad", "valor_unitario", "descuento"]
        widgets = {
            "material": forms.Select(attrs={"class": "form-select"}),
            "descripcion": forms.TextInput(attrs={"class": "form-input"}),
            "cantidad": forms.NumberInput(attrs={"class": "form-input text-center"}),
            "valor_unitario": forms.NumberInput(attrs={"class": "form-input text-center", "step": "0.01"}),
            "descuento": forms.NumberInput(attrs={"class": "form-input text-center", "step": "0.01"}),
        }


DetalleSalidaFormSet = inlineformset_factory(
    SalidaMaterial,
    DetalleSalidaMaterial,
    form=DetalleSalidaForm,
    extra=1,
    can_delete=True,
)


class ImportarCAFForm(forms.Form):
    archivo_caf = forms.FileField(label="Archivo CAF (.xml)", required=True)

    def clean_archivo_caf(self):
        f = self.cleaned_data.get("archivo_caf")
        if not f:
            return f
        name = (getattr(f, "name", "") or "").lower()
        if name and not name.endswith(".xml"):
            raise ValidationError("El CAF debe ser un archivo .xml")
        return f


class ImportarCertificadoForm(forms.ModelForm):
    class Meta:
        model = CertificadoDigital
        fields = ["archivo", "clave_certificado", "rut_emisor"]
        widgets = {"clave_certificado": forms.PasswordInput()}

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")
        if archivo and not (archivo.name or "").lower().endswith(".pfx"):
            raise forms.ValidationError("El archivo debe ser un .pfx")
        return archivo
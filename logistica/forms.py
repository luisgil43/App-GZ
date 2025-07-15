from .models import CertificadoDigital
from django.forms import inlineformset_factory
from .models import DetalleSalidaMaterial
from .models import SalidaMaterial
from .models import Bodega
from .models import Material, Bodega
from .models import Material
from datetime import datetime
from django import forms
from .models import IngresoMaterial


from django import forms
from .models import IngresoMaterial, DetalleIngresoMaterial


class IngresoMaterialForm(forms.ModelForm):
    class Meta:
        model = IngresoMaterial
        fields = ['tipo_documento', 'numero_documento',
                  'codigo_externo', 'bodega', 'archivo_documento']
        widgets = {
            'tipo_documento': forms.Select(attrs={'class': 'form-select'}),
            'numero_documento': forms.TextInput(attrs={'class': 'form-input'}),
            'codigo_externo': forms.TextInput(attrs={'class': 'form-input'}),
            'bodega': forms.Select(attrs={'class': 'form-select'}),
            'archivo_documento': forms.FileInput(attrs={'accept': 'application/pdf'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si ya existe la instancia, el archivo no es obligatorio
        if self.instance and self.instance.pk:
            self.fields['archivo_documento'].required = False


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = [
            'codigo_interno', 'nombre', 'codigo_externo', 'bodega',
            'stock_actual', 'stock_minimo', 'unidad_medida', 'valor_unitario', 'descripcion'
        ]
        widgets = {
            'bodega': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'unidad_medida': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'valor_unitario': forms.NumberInput(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'step': '0.01',
                'placeholder': 'Ej: 12500'
            }),
            'descripcion': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2 resize-y'}),
        }

    def clean(self):
        cleaned = super().clean()
        nombre = cleaned.get('nombre', '').strip()
        codigo_interno = cleaned.get('codigo_interno', '').strip()
        codigo_externo = cleaned.get('codigo_externo', '').strip()
        bodega = cleaned.get('bodega')
        material_id = self.instance.pk

        if not bodega:
            return cleaned  # No validar si no hay bodega

    # Validar nombre duplicado en esta bodega
        if nombre and Material.objects.filter(
            nombre__iexact=nombre,
            bodega=bodega
        ).exclude(pk=material_id).exists():
            self.add_error(
                'nombre', 'Ya existe un material con ese nombre en esta bodega.')

    # Validar código interno duplicado en esta bodega
        if codigo_interno and Material.objects.filter(
            codigo_interno__iexact=codigo_interno,
            bodega=bodega
        ).exclude(pk=material_id).exists():
            self.add_error(
                'codigo_interno', 'Ya existe un material con ese código interno en esta bodega.')

    # Validar código externo duplicado en esta bodega (si hay)
        if codigo_externo and Material.objects.filter(
            codigo_externo__iexact=codigo_externo,
            bodega=bodega
        ).exclude(pk=material_id).exists():
            self.add_error(
                'codigo_externo', 'Ya existe un material con ese código externo en esta bodega.')

        return cleaned


class ImportarExcelForm(forms.Form):
    archivo_excel = forms.FileField(
        label="Selecciona un archivo .xlsx",
        required=True,
        widget=forms.FileInput(attrs={'accept': '.xlsx'})
    )

    def clean_archivo_excel(self):
        archivo = self.cleaned_data.get('archivo_excel')
        if archivo:
            nombre = archivo.name.lower().strip()
            if not nombre.endswith('.xlsx'):
                raise forms.ValidationError("Solo se permiten archivos .xlsx")
        return archivo


class FiltroIngresoForm(forms.Form):
    MESES = [
        ('', 'Todos los meses'),
        (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
        (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
        (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre'),
    ]
    AÑOS = [(año, año) for año in range(2024, 2031)]

    mes = forms.ChoiceField(choices=MESES, label='Mes', required=False)
    anio = forms.ChoiceField(choices=AÑOS, label='Año')


class MaterialIngresoForm(forms.ModelForm):
    class Meta:
        model = DetalleIngresoMaterial
        fields = ['material', 'cantidad']
        widgets = {
            'material': forms.Select(attrs={'class': 'form-select'}),
            'cantidad': forms.NumberInput(attrs={'class': 'form-input', 'min': 1}),
        }

    def clean_cantidad(self):
        cantidad = self.cleaned_data.get('cantidad')
        if cantidad is None or cantidad <= 0:
            raise forms.ValidationError("La cantidad debe ser mayor a cero.")
        return cantidad


class BodegaForm(forms.ModelForm):
    class Meta:
        model = Bodega
        fields = ['nombre']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border rounded-xl focus:ring focus:ring-emerald-500',
                'placeholder': 'Nombre de la bodega'
            })
        }


class SalidaMaterialForm(forms.ModelForm):
    class Meta:
        model = SalidaMaterial
        fields = [
            'bodega', 'id_proyecto', 'tipo_documento', 'numero_documento',
            'rut_receptor', 'nombre_receptor', 'giro_receptor',
            'direccion_receptor', 'comuna_receptor', 'ciudad_receptor',
            'entregado_a', 'emitido_por', 'chofer', 'patente',
            'origen', 'destino', 'obra',
            # 'fecha_salida',  # ← CAMPO DE FECHA
            'rut_transportista'  # ← CAMPOS DE TRANSPORTE
        ]
        widgets = {
            'bodega': forms.Select(attrs={'class': 'form-select'}),
            'id_proyecto': forms.TextInput(attrs={'class': 'form-input'}),
            'tipo_documento': forms.Select(attrs={'class': 'form-select'}),
            'numero_documento': forms.TextInput(attrs={'class': 'form-input'}),
            'rut_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'nombre_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'giro_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'direccion_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'comuna_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'ciudad_receptor': forms.TextInput(attrs={'class': 'form-input'}),
            'entregado_a': forms.Select(attrs={'class': 'form-select'}),
            'emitido_por': forms.Select(attrs={'class': 'form-select'}),
            'chofer': forms.TextInput(attrs={'class': 'form-input'}),
            'patente': forms.TextInput(attrs={'class': 'form-input'}),
            'origen': forms.TextInput(attrs={'class': 'form-input'}),
            'destino': forms.TextInput(attrs={'class': 'form-input'}),
            'obra': forms.TextInput(attrs={'class': 'form-input'}),
            # 'fecha_salida': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'rut_transportista': forms.TextInput(attrs={'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DetalleSalidaForm(forms.ModelForm):
    class Meta:
        model = DetalleSalidaMaterial
        fields = ['material', 'descripcion',
                  'cantidad', 'valor_unitario', 'descuento']
        widgets = {
            'material': forms.Select(attrs={'class': 'form-select'}),
            'descripcion': forms.TextInput(attrs={'class': 'form-input'}),
            'cantidad': forms.NumberInput(attrs={'class': 'form-input text-center'}),
            'valor_unitario': forms.NumberInput(attrs={'class': 'form-input text-center', 'step': '0.01'}),
            'descuento': forms.NumberInput(attrs={'class': 'form-input text-center', 'step': '0.01'}),
        }


DetalleSalidaFormSet = inlineformset_factory(
    SalidaMaterial,
    DetalleSalidaMaterial,
    form=DetalleSalidaForm,
    extra=1,
    can_delete=True
)


class ImportarCAFForm(forms.Form):
    archivo_caf = forms.FileField(label="Archivo CAF (.xml)", required=True)


class ImportarCertificadoForm(forms.ModelForm):
    class Meta:
        model = CertificadoDigital
        fields = ['archivo', 'clave_certificado', 'rut_emisor']
        widgets = {
            'clave_certificado': forms.PasswordInput(),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo and not archivo.name.endswith('.pfx'):
            raise forms.ValidationError("El archivo debe ser un .pfx")
        return archivo

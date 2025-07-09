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
            'stock_actual', 'stock_minimo', 'unidad_medida', 'descripcion'
        ]
        widgets = {
            'bodega': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'unidad_medida': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'descripcion': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2 resize-y'}),
        }

    def clean(self):
        cleaned = super().clean()
        nombre = cleaned.get('nombre')
        codigo = cleaned.get('codigo_interno')

        # Obtener el ID del material actual (si existe)
        material_id = self.instance.pk

        # Validar duplicado por nombre (ignorando el actual)
        if nombre and Material.objects.filter(nombre__iexact=nombre).exclude(pk=material_id).exists():
            self.add_error('nombre', 'Ya existe un material con ese nombre.')

        # Validar duplicado por código (ignorando el actual)
        if codigo and Material.objects.filter(codigo_interno__iexact=codigo).exclude(pk=material_id).exists():
            self.add_error('codigo_interno',
                           'Ya existe un material con ese código interno.')

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

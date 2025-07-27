from .models import FacturaOC
from django import forms
from .models import OrdenCompraFacturacion


# facturacion/forms.py

from django import forms
from .models import OrdenCompraFacturacion


class OrdenCompraFacturacionForm(forms.ModelForm):
    class Meta:
        model = OrdenCompraFacturacion
        fields = [
            'du',
            'orden_compra',
            'pos',
            'cantidad',
            'unidad_medida',
            'material_servicio',
            'descripcion_sitio',
            'fecha_entrega',
            'precio_unitario',
            'monto',
        ]
        widgets = {
            'fecha_entrega': forms.DateInput(
                attrs={
                    'type': 'date',
                    'class': 'campo-formulario w-full'
                },
                format='%Y-%m-%d'  # 👈 Agrega este formato
            ),
            'cantidad': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'precio_unitario': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'monto': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'du': forms.Select(attrs={'class': 'campo-formulario w-full'}),
            'orden_compra': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'pos': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'unidad_medida': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'material_servicio': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'descripcion_sitio': forms.Textarea(attrs={'class': 'campo-formulario w-full', 'rows': 2}),
        }
        labels = {
            'du': 'DU',
            'orden_compra': 'Orden de Compra',
            'pos': 'POS',
            'cantidad': 'Cantidad',
            'unidad_medida': 'UM',
            'material_servicio': 'Material/Servicio',
            'descripcion_sitio': 'Descripción Sitio',
            'fecha_entrega': 'Fecha Entrega',
            'precio_unitario': 'Precio Unitario',
            'monto': 'Monto',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 👇 Esto es clave para precargar bien la fecha
        if self.instance and self.instance.fecha_entrega:
            self.initial['fecha_entrega'] = self.instance.fecha_entrega.strftime(
                '%Y-%m-%d')


class ImportarFacturasForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo Excel",
        widget=forms.ClearableFileInput(
            attrs={"class": "border rounded-lg px-3 py-2"})
    )


class FacturaOCForm(forms.ModelForm):
    class Meta:
        model = FacturaOC
        fields = [
            'hes',
            'valor_en_clp',
            'conformidad',
            'num_factura',
            'fecha_facturacion',
            'mes_produccion',
            'factorizado',
            'fecha_factoring',
            'cobrado',
        ]
        widgets = {
            'hes': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'valor_en_clp': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'conformidad': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'num_factura': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'fecha_facturacion': forms.DateInput(
                format='%Y-%m-%d',
                attrs={'type': 'date',
                       'class': 'w-full border rounded-xl px-3 py-2'}
            ),
            'mes_produccion': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'factorizado': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
            'fecha_factoring': forms.DateInput(
                format='%Y-%m-%d',
                attrs={'type': 'date',
                       'class': 'w-full border rounded-xl px-3 py-2'}
            ),
            'cobrado': forms.CheckboxInput(attrs={'class': 'h-4 w-4'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Para que Django use el formato correcto en valores iniciales
        self.fields['fecha_facturacion'].input_formats = ['%Y-%m-%d']
        self.fields['fecha_factoring'].input_formats = ['%Y-%m-%d']

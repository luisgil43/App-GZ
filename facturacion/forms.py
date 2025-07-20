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

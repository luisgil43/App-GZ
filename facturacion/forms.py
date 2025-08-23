import re
from .models import CartolaMovimiento
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from decimal import Decimal
from operaciones.templatetags.custom_filters import formato_clp  # Usa tu filtro de CLP
from .models import TipoGasto
from .models import CartolaMovimiento, TipoGasto, Proyecto
from .models import FacturaOC
from django import forms
from .models import OrdenCompraFacturacion


# facturacion/forms.py

from django import forms
from .models import OrdenCompraFacturacion


class OrdenCompraFacturacionForm(forms.ModelForm):
    class Meta:
        model = OrdenCompraFacturacion
        # 👇 OJO: 'du' no se edita aquí
        fields = [
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
                attrs={'type': 'date', 'class': 'campo-formulario w-full'},
                format='%Y-%m-%d'
            ),
            'cantidad': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'precio_unitario': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'monto': forms.NumberInput(attrs={'step': 'any', 'class': 'campo-formulario w-full'}),
            'orden_compra': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'pos': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'unidad_medida': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'material_servicio': forms.TextInput(attrs={'class': 'campo-formulario w-full'}),
            'descripcion_sitio': forms.Textarea(attrs={'class': 'campo-formulario w-full', 'rows': 2}),
        }
        labels = {
            'orden_compra': 'Orden de Compra',
            'pos': 'POS',
            'cantidad': 'Cantidad',
            'unidad_medida': 'UM',
            'material_servicio': 'Material/Servicio',
            'descripcion_sitio': 'Descripción / Sitio',
            'fecha_entrega': 'Fecha de Entrega',
            'precio_unitario': 'Precio Unitario',
            'monto': 'Monto',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Asegura YYYY-MM-DD en el date input
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


class CartolaAbonoForm(forms.ModelForm):
    abonos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        required=True,
        label="Abonos"
    )

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'observaciones',
                  'numero_transferencia', 'comprobante', 'abonos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'rows': 2,
                'placeholder': 'Escribe una breve observación...'
            }),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].label_from_instance = lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
        if self.instance and self.instance.pk and self.instance.abonos is not None:
            self.initial['abonos'] = f"{self.instance.abonos:,.2f}".replace(
                ",", "X").replace(".", ",").replace("X", ".")

    def clean_abonos(self):
        valor = self.cleaned_data.get('abonos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un número válido para Abonos.")


class CartolaGastoForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        required=True,
        label="Cargos"
    )

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'tipo', 'observaciones',
                  'numero_transferencia', 'comprobante', 'cargos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'rows': 2,
                'placeholder': 'Escribe una breve observación...'
            }),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Ej: 123456789'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].label_from_instance = lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                ",", "X").replace(".", ",").replace("X", ".")

    def clean_cargos(self):
        valor = self.cleaned_data.get('cargos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un número válido para Cargos.")


class CartolaMovimientoCompletoForm(forms.ModelForm):
    cargos = forms.CharField()
    abonos = forms.CharField()

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'tipo', 'observaciones',
                  'numero_transferencia', 'comprobante', 'cargos', 'abonos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 2}),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
        if self.instance and self.instance.pk:
            if self.instance.cargos is not None:
                self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")
            if self.instance.abonos is not None:
                self.initial['abonos'] = f"{self.instance.abonos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")

    def _clean_monto(self, value, field_name):
        """Convierte texto con puntos miles y coma decimal a Decimal con 2 decimales."""
        if not value:
            return Decimal("0.00")
        value = value.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(value).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError(
                    f"El {field_name} no puede ser negativo.")
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                f"Ingrese un {field_name} válido en formato 1.234,56")

    def clean_cargos(self):
        return self._clean_monto(self.cleaned_data.get('cargos'), "cargo")

    def clean_abonos(self):
        return self._clean_monto(self.cleaned_data.get('abonos'), "abono")


class TipoGastoForm(forms.ModelForm):
    class Meta:
        model = TipoGasto
        fields = ['nombre', 'categoria']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'placeholder': 'Ej: Combustible'
            }),
            'categoria': forms.Select(attrs={
                'class': 'w-full border rounded-xl px-3 py-2'
            })
        }


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['nombre', 'mandante']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'mandante': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

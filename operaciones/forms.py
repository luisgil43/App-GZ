import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import ModelMultipleChoiceField
from django.utils import timezone

from facturacion.models import CartolaMovimiento, TipoGasto

from .models import ServicioCotizado, SitioMovil


class ServicioCotizadoForm(forms.ModelForm):
    monto_cotizado = forms.CharField()
    monto_mmoo = forms.CharField(required=False)

    class Meta:
        model = ServicioCotizado
        fields = [
            'id_claro', 'region', 'mes_produccion',
            'id_new', 'detalle_tarea', 'monto_cotizado', 'monto_mmoo'
        ]
        widgets = {
            'detalle_tarea': forms.Textarea(attrs={'rows': 3}),
            'mes_produccion': forms.TextInput(attrs={'placeholder': 'Ej: Julio 2025'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

        # Campo monto cotizado como texto con coma
        self.fields['monto_cotizado'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 10,00 UF'}
        )
        # Campo monto MMOO como texto para aceptar miles y coma decimal
        self.fields['monto_mmoo'].widget = forms.TextInput(
            attrs={'placeholder': 'Ej: 632.543,76'}
        )

        # Preformatear valores iniciales para edición
        if self.instance and self.instance.pk:
            if self.instance.monto_cotizado is not None:
                # Mostrar coma como separador decimal
                self.initial['monto_cotizado'] = str(
                    self.instance.monto_cotizado).replace(".", ",")
            if self.instance.monto_mmoo is not None:
                # Mostrar con separador de miles y coma como decimal
                self.initial['monto_mmoo'] = f"{self.instance.monto_mmoo:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")

    def clean_monto_cotizado(self):
        """Convierte UF a Decimal, acepta coma o punto como separador y fuerza 2 decimales."""
        data = self.cleaned_data.get('monto_cotizado')
        if not data:
            raise forms.ValidationError("Este campo es obligatorio.")
        data = data.replace(" ", "").replace(",", ".")
        try:
            value = Decimal(data).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value <= 0:
                raise forms.ValidationError("El monto debe ser mayor que 0.")
            return value
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 0,00")

    def clean_monto_mmoo(self):
        """Convierte CLP formateado (1.234.567,89) a Decimal."""
        data = self.cleaned_data.get('monto_mmoo')
        if not data:
            return None
        # Eliminar espacios, puntos de miles y reemplazar coma por punto
        data = data.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(data).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError("El monto no puede ser negativo.")
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un monto válido en formato 1.234,56")


User = get_user_model()


class AsignarTrabajadoresForm(forms.Form):
    trabajadores = ModelMultipleChoiceField(
        queryset=User.objects.filter(roles__nombre='usuario', is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Selecciona uno o más trabajadores",
        help_text="Debes seleccionar al menos un trabajador.",
    )

    def clean_trabajadores(self):
        trabajadores = self.cleaned_data.get('trabajadores')
        if not trabajadores or trabajadores.count() == 0:
            raise forms.ValidationError(
                "Debes seleccionar al menos un trabajador."
            )
        return trabajadores





class MovimientoUsuarioForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}
        ),
        label="Monto",
        required=True
    )

    # ✅ Fecha real del gasto
    fecha_transaccion = forms.DateField(
        required=True,
        label="Fecha real del gasto",
        widget=forms.DateInput(
            attrs={'type': 'date', 'class': 'w-full border rounded-xl px-3 py-2'},
            format='%Y-%m-%d'
        ),
        input_formats=['%Y-%m-%d']
    )

    # ✅ Hora servicio flota (HH:MM)
    hora_servicio_flota = forms.TimeField(
        required=False,
        widget=forms.TimeInput(
            attrs={'type': 'time', 'class': 'w-full border rounded-xl px-3 py-2'},
            format='%H:%M'
        ),
        input_formats=['%H:%M', '%H:%M:%S']
    )

    class Meta:
        model = CartolaMovimiento
        fields = [
            'fecha_transaccion',

            # Datos normales de rendición
            'proyecto', 'tipo', 'tipo_doc', 'rut_factura',
            'numero_doc', 'cargos', 'observaciones', 'comprobante',

            # ✅ Integración flota
            'vehiculo_flota',
            'tipo_servicio_flota',
            'fecha_servicio_flota',
            'hora_servicio_flota',
            'kilometraje_servicio_flota',
        ]
        widgets = {
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo_doc': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'numero_doc': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'rut_factura': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Ej: 12.345.678-5'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 3}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),

            # ✅ Flota
            'vehiculo_flota': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo_servicio_flota': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'fecha_servicio_flota': forms.DateInput(
                attrs={'type': 'date', 'class': 'w-full border rounded-xl px-3 py-2'},
                format='%Y-%m-%d'
            ),
            'kilometraje_servicio_flota': forms.NumberInput(
                attrs={'class': 'w-full border rounded-xl px-3 py-2', 'min': '0', 'placeholder': 'Ej: 125000'}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("user", None)  # ✅ recibimos usuario desde la vista
        super().__init__(*args, **kwargs)

        # ✅ Mostrar solo tipos disponibles para declarar
        # (y excluir abonos del formulario de rendición)
        if 'tipo' in self.fields:
            self.fields['tipo'].queryset = (
                TipoGasto.objects
                .filter(disponible=True)
                .exclude(categoria='abono')
                .order_by('nombre')
            )

        # Nunca dejar que el propio FileField marque required automáticamente
        if 'comprobante' in self.fields:
            self.fields['comprobante'].required = False  # se valida manualmente por foto/archivo

        # ✅ Campos visibles normales (obligatorios)
        self.fields['proyecto'].required = True
        self.fields['tipo'].required = True
        self.fields['fecha_transaccion'].required = True
        self.fields['rut_factura'].required = True
        self.fields['tipo_doc'].required = True
        self.fields['numero_doc'].required = True
        self.fields['cargos'].required = True

        # ✅ Observaciones NO obligatoria
        self.fields['observaciones'].required = False

        # --- Campos flota opcionales a nivel field (se exigen en clean si tipo=Servicios) ---
        if 'vehiculo_flota' in self.fields:
            self.fields['vehiculo_flota'].required = False
        if 'tipo_servicio_flota' in self.fields:
            self.fields['tipo_servicio_flota'].required = False
        if 'fecha_servicio_flota' in self.fields:
            self.fields['fecha_servicio_flota'].required = False  # se autocompleta desde fecha_transaccion
        if 'hora_servicio_flota' in self.fields:
            self.fields['hora_servicio_flota'].required = False
        if 'kilometraje_servicio_flota' in self.fields:
            self.fields['kilometraje_servicio_flota'].required = False

        # ==========================================================
        # ✅ FILTRO CORRECTO DE VEHÍCULOS FLOTA
        # Solo vehículos ACTIVOS por status + ASIGNADOS ACTIVAMENTE al usuario
        # (SIN excepción para staff/superuser)
        # ==========================================================
        try:
            qs_veh = (
                self.fields['vehiculo_flota'].queryset
                .select_related('status')
                .filter(
                    status__isnull=False,
                    status__is_active=True,
                    status__name__iexact='Activo',
                )
            )

            user = self.request_user
            if user and getattr(user, "is_authenticated", False):
                # ✅ SIEMPRE filtrar por asignación activa del usuario
                qs_veh = qs_veh.filter(
                    assignments__user=user,
                    assignments__active=True
                ).distinct().order_by('patente')
            else:
                # Por seguridad, si no viene user, no mostrar nada
                qs_veh = qs_veh.none()

            self.fields['vehiculo_flota'].queryset = qs_veh

            if not qs_veh.exists():
                self.fields['vehiculo_flota'].help_text = (
                    "No tienes vehículos asignados actualmente. "
                    "Si necesitas rendir un servicio de flota, solicita una asignación activa."
                )
        except Exception:
            try:
                self.fields['vehiculo_flota'].queryset = self.fields['vehiculo_flota'].queryset.none()
            except Exception:
                pass

        # Ordenar tipos de servicio
        try:
            self.fields['tipo_servicio_flota'].queryset = (
                self.fields['tipo_servicio_flota'].queryset
                .filter(is_active=True)
                .order_by('name')
            )
        except Exception:
            try:
                self.fields['tipo_servicio_flota'].queryset = (
                    self.fields['tipo_servicio_flota'].queryset.order_by('name')
                )
            except Exception:
                pass

        # ✅ Defaults al crear: hoy + hora actual (solo si no está bound y no es edición)
        if not self.is_bound and not (self.instance and self.instance.pk):
            now_local = timezone.localtime()
            fecha_str = now_local.strftime('%Y-%m-%d')
            hora_str = now_local.strftime('%H:%M')

            self.initial.setdefault('fecha_transaccion', fecha_str)
            self.initial.setdefault('hora_servicio_flota', hora_str)

            # Forzar value del input para que el navegador lo muestre
            self.fields['fecha_transaccion'].widget.attrs['value'] = fecha_str
            self.fields['hora_servicio_flota'].widget.attrs['value'] = hora_str

        # ✅ Si viene POST (bound), mantener valores ingresados visibles en date/time
        if self.is_bound:
            fecha_post = self.data.get(self.add_prefix('fecha_transaccion'))
            hora_post = self.data.get(self.add_prefix('hora_servicio_flota'))
            if fecha_post:
                self.fields['fecha_transaccion'].widget.attrs['value'] = fecha_post
            if hora_post:
                self.fields['hora_servicio_flota'].widget.attrs['value'] = hora_post

        # Prellenar monto en edición
        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial['cargos'] = (
                f"{self.instance.cargos:,.2f}"
                .replace(",", "X").replace(".", ",").replace("X", ".")
            )

        # Prellenar fecha_transaccion en edición (YYYY-MM-DD para input date)
        if self.instance and self.instance.pk and self.instance.fecha_transaccion:
            fecha_edit = self.instance.fecha_transaccion.strftime('%Y-%m-%d')
            self.initial['fecha_transaccion'] = fecha_edit
            self.fields['fecha_transaccion'].widget.attrs['value'] = fecha_edit

        # ✅ Prellenar fecha/hora flota (edición)
        if self.instance and self.instance.pk:
            if getattr(self.instance, 'fecha_servicio_flota', None):
                self.initial['fecha_servicio_flota'] = self.instance.fecha_servicio_flota.strftime('%Y-%m-%d')
            if getattr(self.instance, 'hora_servicio_flota', None):
                hora_edit = self.instance.hora_servicio_flota.strftime('%H:%M')
                self.initial['hora_servicio_flota'] = hora_edit
                self.fields['hora_servicio_flota'].widget.attrs['value'] = hora_edit

    def clean_cargos(self):
        valor = self.cleaned_data.get('cargos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value <= 0:
                raise forms.ValidationError("El monto debe ser mayor que 0.")
            return value
        except InvalidOperation:
            raise forms.ValidationError("Ingrese un monto válido en formato 1.234,56")

    def clean(self):
        cleaned = super().clean()

        # 1) Detectar si ya existía comprobante (edición)
        has_old = bool(self.instance and self.instance.pk and getattr(self.instance, 'comprobante', None))

        # 2) Tomar el comprobante que venga por cualquiera de los inputs
        uploaded = (
            self.files.get('comprobante') or
            self.files.get('comprobante_foto') or
            self.files.get('comprobante_archivo')
        )

        # 3) Copiar al campo del modelo para que se guarde
        if uploaded:
            cleaned['comprobante'] = uploaded

        # 4) Reglas de obligatoriedad del comprobante (✅ obligatorio siempre)
        is_create = not (self.instance and self.instance.pk)
        if (is_create and not uploaded) or (not is_create and not has_old and not uploaded):
            self.add_error('comprobante', "Este campo es obligatorio.")

        # ==========================================================
        # ✅ Validaciones de Flota (solo si tipo de rendición = Servicios)
        # - fecha_servicio_flota NO se pide en UI
        # - se copia automáticamente desde fecha_transaccion
        # ==========================================================
        tipo = cleaned.get('tipo')
        vehiculo = cleaned.get('vehiculo_flota')
        tipo_servicio = cleaned.get('tipo_servicio_flota')
        hora_serv = cleaned.get('hora_servicio_flota')
        km_serv = cleaned.get('kilometraje_servicio_flota')

        # Detectar "Servicios" por nombre del tipo (robusto)
        tipo_text = ""
        try:
            tipo_text = (
                getattr(tipo, 'nombre', None)
                or getattr(tipo, 'name', None)
                or str(tipo)
                or ''
            ).strip().lower()
        except Exception:
            tipo_text = str(tipo).strip().lower() if tipo else ""

        # ✅ Regla principal: nombre contiene "servicio"
        es_servicios = ('servicio' in tipo_text)

        if es_servicios:
            if not vehiculo:
                self.add_error('vehiculo_flota', "Selecciona un vehículo.")
            if not tipo_servicio:
                self.add_error('tipo_servicio_flota', "Selecciona un tipo de servicio.")
            if not hora_serv:
                self.add_error('hora_servicio_flota', "Ingresa la hora del servicio.")

            if km_serv in (None, ''):
                self.add_error('kilometraje_servicio_flota', "Ingresa el kilometraje del servicio.")
            else:
                try:
                    km_int = int(km_serv)
                    if km_int < 0:
                        self.add_error('kilometraje_servicio_flota', "El kilometraje no puede ser negativo.")
                    else:
                        cleaned['kilometraje_servicio_flota'] = km_int
                except Exception:
                    self.add_error('kilometraje_servicio_flota', "Ingresa un kilometraje válido.")

            # ✅ Tomar fecha del servicio desde fecha real del gasto
            if cleaned.get('fecha_transaccion'):
                cleaned['fecha_servicio_flota'] = cleaned.get('fecha_transaccion')

            # ✅ Seguridad backend:
            # si por POST manipulado meten un vehículo no asignado, bloquearlo.
            user = self.request_user
            if user and getattr(user, "is_authenticated", False) and vehiculo:
                asignado = vehiculo.assignments.filter(user=user, active=True).exists()
                if not asignado:
                    self.add_error(
                        'vehiculo_flota',
                        "Ese vehículo no está asignado a tu usuario."
                    )
        else:
            # Si NO es servicio, limpiar flota para evitar datos basura
            cleaned['vehiculo_flota'] = None
            cleaned['tipo_servicio_flota'] = None
            cleaned['fecha_servicio_flota'] = None
            cleaned['hora_servicio_flota'] = None
            cleaned['kilometraje_servicio_flota'] = None

        return cleaned
    
def validar_rut_chileno(rut):
    """Valida el dígito verificador del RUT chileno."""
    if not rut:
        return False
    rut = rut.replace(".", "").replace("-", "").strip().upper()
    if not rut[:-1].isdigit():
        return False
    cuerpo = rut[:-1]
    dv = rut[-1]
    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1
    resto = suma % 11
    dv_esperado = "0" if resto == 0 else "K" if resto == 1 else str(11 - resto)
    return dv == dv_esperado


def verificar_rut_sii(rut):
    """Verifica el RUT en el sitio del SII (básico)."""
    url = "https://zeus.sii.cl/cgi_rut/CONSULTA.cgi"
    data = {"RUT": rut}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        response = requests.post(url, data=data, headers=headers, timeout=5)
        return "RUT no válido" not in response.text
    except:
        return True


class SitioMovilForm(forms.ModelForm):
    class Meta:
        model = SitioMovil
        fields = [
            "id_sites", "id_claro", "id_sites_new", "region", "nombre", "direccion",
            "latitud", "longitud", "comuna", "tipo_construccion", "altura",
            "candado_bt", "condiciones_acceso", "claves", "llaves", "cantidad_llaves",
            "observaciones_generales", "zonas_conflictivas", "alarmas", "guardias",
            "nivel", "descripcion",
        ]
        widgets = {
            "direccion": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "condiciones_acceso": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "observaciones_generales": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "descripcion": forms.Textarea(attrs={"rows": 3, "class": "border rounded-lg w-full px-3 py-2"}),
            "zonas_conflictivas": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "claves": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "llaves": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "tipo_construccion": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "region": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "nombre": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "comuna": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_sites": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_claro": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "id_sites_new": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "candado_bt": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "alarmas": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "guardias": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "nivel": forms.TextInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "altura": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
            "cantidad_llaves": forms.NumberInput(attrs={"class": "border rounded-lg w-full px-3 py-2"}),
            "latitud": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
            "longitud": forms.NumberInput(attrs={"step": "any", "class": "border rounded-lg w-full px-3 py-2"}),
        }
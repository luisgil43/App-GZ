from rrhh.models import SolicitudAdelanto
import re
from rrhh.models import CronogramaPago
from .models import CronogramaPago
from .models import DocumentoTrabajador
from django import forms
from .models import ContratoTrabajo
from .models import FichaIngreso
from .models import SolicitudVacaciones
from datetime import timedelta
import holidays
from .models import Feriado
from .models import TipoDocumento
from datetime import date
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.db.models import Sum
from rrhh.utils import calcular_dias_habiles


class ContratoTrabajoForm(forms.ModelForm):
    reemplazar_archivo = forms.BooleanField(
        required=False,
        label='Reemplazar archivo existente',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox rounded text-emerald-600'
        })
    )

    class Meta:
        model = ContratoTrabajo
        fields = ['tecnico', 'fecha_inicio', 'fecha_termino', 'archivo']
        widgets = {
            'tecnico': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'fecha_inicio': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'fecha_termino': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'archivo': forms.ClearableFileInput(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 bg-white shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_termino'].required = False

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise forms.ValidationError(
                    "Solo se permiten archivos en formato PDF.")
            if archivo.content_type != 'application/pdf':
                raise forms.ValidationError(
                    "El archivo debe ser un PDF válido.")
        return archivo


class FichaIngresoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        validaciones_html = {
            'nombres': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'apellidos': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'rut': {'pattern': r'[0-9kK]+', 'title': 'Solo números y letras (sin puntos ni guion)'},
            'edad': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'estado_civil': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'nacionalidad': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'hijos': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'nivel_estudios': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'profesion_u_oficio': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'direccion': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'comuna': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'region': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'email': {'type': 'email', 'pattern': r'[^@]+@[^@]+\.[^@]+', 'title': 'Formato válido: correo@ejemplo.com'},
            'telefono': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'nombre_contacto_emergencia': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'telefono_emergencia': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'parentesco_emergencia': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'direccion_emergencia': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'afp': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'salud': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'banco': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'tipo_cuenta': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'numero_cuenta': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'banco_2': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'tipo_cuenta_2': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'numero_cuenta_2': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'cargo': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'jefe_directo': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'proyecto': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'jornada': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'sueldo_base': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'bono': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'colacion': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'movilizacion': {'pattern': r'\d+', 'inputmode': 'numeric', 'title': 'Solo números'},
            'observaciones': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'sexo': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'tipo_contrato': {'pattern': r'[A-Za-zÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Solo letras'},
            'talla_polera': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'talla_zapato': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
            'talla_pantalon': {'pattern': r'[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ\s]+', 'title': 'Letras y números'},
        }

        placeholders = {
            'nombres': 'Ej: Juan',
            'apellidos': 'Ej: Pérez',
            'rut': 'Ej: 12345678K',
            'edad': 'Ej: 35',
            'estado_civil': 'Ej: Soltero',
            'hijos': 'Ej: 2',
            'nivel_estudios': 'Ej: Universidad Completa',
            'profesion_u_oficio': 'Ej: Ingeniero de Telecomunicaciones',
            'direccion': 'Ej: Calle Falsa 123',
            'comuna': 'Ej: Maipú',
            'region': 'Ej: Region Metropolitana',
            'telefono': 'Ej: 56 9 8765 4321',
            'email': 'Ej: nombre@correo.com',
            'nombre_contacto_emergencia': 'Ej: María González',
            'telefono_emergencia': 'Ej: 56 9 1122 3344',
            'parentesco_emergencia': 'Ej: Esposa',
            'direccion_emergencia': 'Ej: Calle de Emergencia 789',
            'afp': 'Ej: Provida',
            'salud': 'Ej: Fonasa',
            'banco': 'Ej: Banco Estado',
            'tipo_cuenta': 'Ej: Cuenta Rut',
            'numero_cuenta': 'Ej: 123456789',
            'banco_2': 'Ej: Banco Falabella',
            'tipo_cuenta_2': 'Ej: Vista',
            'numero_cuenta_2': 'Ej: 987654321',
            'cargo': 'Ej: Maestro de Obras',
            'proyecto': 'Ej: Proyecto Wom',
            'jornada': 'Ej: Lunes a Viernes',
            'sueldo_base': 'Ej: 500000',
            'bono': 'Ej: 50000',
            'colacion': 'Ej: 30000',
            'movilizacion': 'Ej: 20000',
            'observaciones': 'Ej: Tiene experiencia previa',
            'sexo': 'Ej: Masculino',
            'nacionalidad': 'Ej: Chilena',
            'tipo_contrato': 'Ej: Indefinido',
            'talla_polera': 'Ej: L',
            'talla_pantalon': 'Ej: 42',
            'talla_zapato': 'Ej: 41',
            'jefe_directo': 'Ej: Pedro Gómez',
        }

        for campo, attrs in validaciones_html.items():
            if campo in self.fields:
                self.fields[campo].widget.attrs.update(attrs)
                if campo in placeholders:
                    self.fields[campo].widget.attrs['placeholder'] = placeholders[campo]

        # ✅ Corregir campos de fecha para mantener valor en edición
        self.fields['fecha_nacimiento'].input_formats = ['%Y-%m-%d']
        self.fields['fecha_inicio'].input_formats = ['%Y-%m-%d']

    class Meta:
        model = FichaIngreso
        exclude = ['creado_por', 'usuario', 'pm', 'archivo',
                   'firma_trabajador', 'firma_pm', 'firma_rrhh', 'estado']
        widgets = {
            'fecha_nacimiento': forms.DateInput(
                format='%Y-%m-%d',
                attrs={'type': 'date', 'class': 'w-full rounded border-gray-300'}
            ),
            'fecha_inicio': forms.DateInput(
                format='%Y-%m-%d',
                attrs={'type': 'date', 'class': 'w-full rounded border-gray-300'}
            ),
        }

    def clean_rut(self):
        rut = self.cleaned_data.get('rut', '')
        if '.' in rut or '-' in rut:
            raise ValidationError("El RUT no debe contener puntos ni guión.")
        if not re.match(r'^[0-9kK]+$', rut):
            raise ValidationError(
                "El RUT solo puede contener números y la letra K.")
        return rut

    def clean_telefono(self):
        telefono = self.cleaned_data.get("telefono")
        if telefono and not telefono.strip().isdigit():
            raise ValidationError("El teléfono debe contener solo números.")
        return telefono

    def clean_telefono_emergencia(self):
        telefono = self.cleaned_data.get('telefono_emergencia', '')
        if telefono and not telefono.isdigit():
            raise ValidationError(
                "El teléfono de emergencia debe contener solo números.")
        return telefono

    def clean_numero_cuenta(self):
        cuenta = self.cleaned_data.get('numero_cuenta', '')
        if cuenta and not cuenta.isdigit():
            raise ValidationError(
                "El número de cuenta debe contener solo números.")
        return cuenta

    def clean_numero_cuenta_2(self):
        cuenta = self.cleaned_data.get('numero_cuenta_2', '')
        if cuenta and not cuenta.isdigit():
            raise ValidationError(
                "El número de cuenta (2) debe contener solo números.")
        return cuenta

    def clean_edad(self):
        edad = self.cleaned_data.get('edad')
        if edad is not None and edad < 0:
            raise ValidationError("La edad no puede ser negativa.")
        return edad

    def clean_hijos(self):
        hijos = self.cleaned_data.get('hijos')
        if hijos is not None and hijos < 0:
            raise ValidationError(
                "La cantidad de hijos no puede ser negativa.")
        return hijos

    def clean_email(self):
        email = self.cleaned_data.get('email', '')
        if email and not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            raise ValidationError("El correo electrónico no es válido.")
        return email


class SolicitudVacacionesForm(forms.ModelForm):
    class Meta:
        model = SolicitudVacaciones
        fields = ['fecha_inicio', 'fecha_fin']
        widgets = {
            'fecha_inicio': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-input'  # usa tu clase Tailwind o Bootstrap si quieres
            }),
            'fecha_fin': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-input'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop('usuario', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        inicio = cleaned_data.get("fecha_inicio")
        fin = cleaned_data.get("fecha_fin")

        if inicio and fin and inicio > fin:
            raise forms.ValidationError(
                "La fecha de inicio no puede ser posterior a la fecha de término."
            )

        if self.usuario and inicio and fin:
            dias_solicitados = calcular_dias_habiles(inicio, fin)
            dias_disponibles = self.usuario.obtener_dias_vacaciones_disponibles()

            if dias_solicitados > dias_disponibles:
                raise forms.ValidationError(
                    f"No puedes solicitar más días de los disponibles. Disponibles: {dias_disponibles:.2f}, solicitados: {dias_solicitados}"
                )

            # ✅ Añadimos esto para que la vista lo pueda usar
            cleaned_data['dias_solicitados'] = dias_solicitados

        return cleaned_data


class RevisionVacacionesForm(forms.Form):
    observacion = forms.CharField(
        label="Observación (opcional)",
        widget=forms.Textarea(
            attrs={'rows': 3, 'class': 'form-textarea w-full'}),
        required=False
    )
    accion = forms.ChoiceField(
        choices=[('aprobar', 'Aprobar'), ('rechazar', 'Rechazar')],
        widget=forms.HiddenInput()
    )


class FeriadoForm(forms.ModelForm):
    class Meta:
        model = Feriado
        fields = ['nombre', 'fecha']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full rounded-xl border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400'
            }),
            'fecha': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full rounded-xl border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400'
            }),
        }


class DocumentoTrabajadorForm(forms.ModelForm):
    class Meta:
        model = DocumentoTrabajador
        fields = ['trabajador', 'tipo_documento',
                  'fecha_emision', 'fecha_vencimiento', 'archivo']
        widgets = {
            'fecha_emision': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'fecha_vencimiento': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'trabajador': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'tipo_documento': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'archivo': forms.ClearableFileInput(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 bg-white shadow-sm focus:ring-2 focus:ring-blue-500',
                'accept': 'application/pdf'
            }),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')

        if not archivo:
            raise forms.ValidationError("Debes adjuntar un archivo PDF.")

        if not archivo.name.lower().endswith('.pdf'):
            raise forms.ValidationError(
                "Solo se permiten archivos en formato PDF.")

        if archivo.content_type != 'application/pdf':
            raise forms.ValidationError("El archivo debe ser un PDF válido.")

        return archivo

    def clean(self):
        cleaned_data = super().clean()
        fecha_emision = cleaned_data.get("fecha_emision")
        fecha_vencimiento = cleaned_data.get("fecha_vencimiento")
        trabajador = cleaned_data.get("trabajador")
        tipo_documento = cleaned_data.get("tipo_documento")
        hoy = date.today()

        if fecha_emision and fecha_emision > hoy:
            self.add_error("fecha_emision",
                           "La fecha de emisión no puede ser en el futuro.")

        if fecha_vencimiento and fecha_vencimiento < hoy:
            self.add_error("fecha_vencimiento",
                           "La fecha de vencimiento no puede ser en el pasado.")

        if fecha_emision and fecha_vencimiento and fecha_vencimiento < fecha_emision:
            self.add_error(
                "fecha_vencimiento", "La fecha de vencimiento no puede ser anterior a la fecha de emisión.")

        # Validación de documento duplicado
        if trabajador and tipo_documento:
            qs = DocumentoTrabajador.objects.filter(
                trabajador=trabajador,
                tipo_documento=tipo_documento
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise forms.ValidationError(
                    "Este trabajador ya tiene un documento de este tipo registrado.")


class TipoDocumentoForm(forms.ModelForm):
    class Meta:
        model = TipoDocumento
        fields = ['nombre']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border rounded-xl',
                'placeholder': 'Ej. Carnet de identidad'
            }),
        }
        labels = {
            'nombre': 'Nombre del Tipo de Documento'
        }


class ReemplazoDocumentoForm(forms.Form):
    archivo = forms.FileField(label="Nuevo archivo PDF", required=True)
    fecha_emision = forms.DateField(
        label="Fecha de emisión",
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    fecha_vencimiento = forms.DateField(
        label="Fecha de expiración",
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        if not archivo.name.endswith('.pdf'):
            raise forms.ValidationError(
                "El archivo debe estar en formato PDF.")
        return archivo

    def clean(self):
        cleaned_data = super().clean()
        fecha_emision = cleaned_data.get("fecha_emision")
        fecha_vencimiento = cleaned_data.get("fecha_vencimiento")

        if fecha_emision and fecha_vencimiento:
            if fecha_vencimiento <= fecha_emision:
                raise forms.ValidationError(
                    "La fecha de expiración debe ser posterior a la fecha de emisión.")
            if fecha_emision > date.today():
                raise forms.ValidationError(
                    "La fecha de emisión no puede ser en el futuro.")


class FirmaForm(forms.Form):
    firma = forms.ImageField(
        label="Firma Digital (PNG)",
        required=True,
        widget=forms.ClearableFileInput(attrs={
            'accept': 'image/png',
            'class': 'w-full border border-gray-300 rounded p-2 text-sm'
        })
    )

    def clean_firma(self):
        firma = self.cleaned_data['firma']
        if not firma.name.endswith('.png'):
            raise forms.ValidationError("La firma debe estar en formato PNG.")
        return firma


class CronogramaPagoForm(forms.ModelForm):
    class Meta:
        model = CronogramaPago
        exclude = ['actualizado']
        widgets = {
            field.name: forms.DateInput(attrs={'type': 'date'})
            for field in CronogramaPago._meta.fields
            if 'fecha' in field.name
        }


class SolicitudAdelantoForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.trabajador = kwargs.pop('trabajador', None)
        self.monto_maximo = kwargs.pop(
            'monto_maximo', None)  # ✅ definido correctamente
        super().__init__(*args, **kwargs)

        # Eliminamos el campo trabajador porque se asigna en la vista
        if 'trabajador' in self.fields:
            del self.fields['trabajador']

    def clean(self):
        cleaned_data = super().clean()
        monto = cleaned_data.get('monto_solicitado')

        if self.monto_maximo is not None and monto is not None:
            if monto > self.monto_maximo:
                self.add_error(
                    'monto_solicitado',
                    f"El monto solicitado supera el disponible (${self.monto_maximo:,.0f}). Ingresa un monto menor."
                )

        return cleaned_data

    class Meta:
        model = SolicitudAdelanto
        fields = ['monto_solicitado']


"""
class SolicitudAdelantoAdminForm(forms.ModelForm):
    class Meta:
        model = SolicitudAdelanto
        fields = ['monto_solicitado', 'comprobante_transferencia']
        widgets = {
            'monto_solicitado': forms.NumberInput(attrs={
                'placeholder': 'Ej. 200000',
                'class': 'w-full border px-4 py-2 rounded-xl shadow-sm'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.trabajador = kwargs.pop('trabajador', None)
        self.usuario_actual = kwargs.pop('usuario_actual', None)
        super().__init__(*args, **kwargs)

        if not self.trabajador and self.instance and self.instance.trabajador:
            self.trabajador = self.instance.trabajador

        self.tiene_pendiente = False
        self.ficha = None
        self.maximo = 0

        if self.trabajador:
            self.tiene_pendiente = self.trabajador.solicitudes_adelanto.filter(
                estado__in=['pendiente_pm', 'pendiente_rrhh']
            ).exclude(id=self.instance.id).exists()

            # Solo deshabilita si NO es admin/RRHH
            if self.tiene_pendiente and not (self.usuario_actual and self.usuario_actual.is_staff):
                self.fields['monto_solicitado'].disabled = True
                self.fields['monto_solicitado'].help_text = "Ya tienes una solicitud pendiente."
                return

            self.ficha = FichaIngreso.objects.filter(
                usuario__identidad=self.trabajador.identidad
            ).first()

            if self.ficha and self.ficha.sueldo_base:
                sueldo_base = self.ficha.sueldo_base
                maximo_base = sueldo_base * Decimal('0.5')

                hoy = date.today()
                total_aprobado = self.trabajador.solicitudes_adelanto.filter(
                    estado='aprobada',
                    fecha_solicitud__month=hoy.month,
                    fecha_solicitud__year=hoy.year
                ).aggregate(total=Sum('monto_aprobado'))['total'] or 0

                self.maximo = int(maximo_base - total_aprobado)
                if self.maximo < 0:
                    self.maximo = 0

                self.fields[
                    'monto_solicitado'].help_text = f"Máximo disponible este mes: ${self.maximo:,}"
            else:
                self.fields['monto_solicitado'].help_text = "⚠️ No se encontró sueldo base registrado."

        self.fields['monto_solicitado'].widget.attrs['data-maximo'] = str(
            self.maximo)

    def clean_monto_solicitado(self):
        if self.tiene_pendiente and not (self.usuario_actual and self.usuario_actual.is_staff):
            raise forms.ValidationError("Ya tienes una solicitud pendiente.")

        monto = self.cleaned_data.get('monto_solicitado')

        if monto is None:
            raise forms.ValidationError("Debe ingresar un monto válido.")
        if monto <= 0:
            raise forms.ValidationError(
                "El monto solicitado debe ser mayor a cero.")
        if not self.ficha:
            raise forms.ValidationError("No se encontró una ficha de ingreso.")

        if monto > self.maximo:
            # Solo permite superar el máximo si es RRHH o admin
            if not (self.usuario_actual and self.usuario_actual.is_staff):
                raise forms.ValidationError(
                    f"El monto solicitado supera tu saldo disponible (${self.maximo:,}). "
                    "Para montos mayores, contacta a RR.HH."
                )

        return monto
"""


class SolicitudAdelantoAdminForm(forms.ModelForm):
    class Meta:
        model = SolicitudAdelanto
        fields = ['monto_aprobado', 'comprobante_transferencia']
        widgets = {
            'monto_aprobado': forms.NumberInput(attrs={
                'placeholder': 'Ej. 200000',
                'class': 'w-full border px-4 py-2 rounded-xl shadow-sm'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.trabajador = kwargs.pop('trabajador', None)
        self.usuario_actual = kwargs.pop('usuario_actual', None)
        super().__init__(*args, **kwargs)

        # Opcionalmente podrías validar máximo aprobado si deseas
        if self.instance and self.instance.trabajador:
            self.trabajador = self.instance.trabajador

            self.ficha = FichaIngreso.objects.filter(
                usuario__identidad=self.trabajador.identidad
            ).first()

            if self.ficha and self.ficha.sueldo_base:
                sueldo_base = self.ficha.sueldo_base
                maximo_base = sueldo_base * Decimal('0.5')

                hoy = date.today()
                total_aprobado = self.trabajador.solicitudes_adelanto.filter(
                    estado='aprobada',
                    fecha_solicitud__month=hoy.month,
                    fecha_solicitud__year=hoy.year
                ).exclude(id=self.instance.id).aggregate(total=Sum('monto_aprobado'))['total'] or 0

                self.maximo = int(maximo_base - total_aprobado)
                if self.maximo < 0:
                    self.maximo = 0

                self.fields['monto_aprobado'].help_text = f"Máximo recomendado: ${self.maximo:,}"
            else:
                self.fields['monto_aprobado'].help_text = "⚠️ No se encontró sueldo base registrado."

    def clean_monto_aprobado(self):
        monto = self.cleaned_data.get('monto_aprobado')

        if monto is None:
            raise forms.ValidationError("Debe ingresar un monto válido.")
        if monto <= 0:
            raise forms.ValidationError(
                "El monto aprobado debe ser mayor a cero.")

        return monto


class AprobacionAdelantoForm(forms.Form):
    monto_aprobado = forms.IntegerField(
        min_value=1,
        label="Monto aprobado",
        widget=forms.NumberInput(attrs={
            'class': 'w-full border px-4 py-2 rounded-xl shadow-sm',
            'placeholder': 'Monto aprobado en pesos'
        })
    )

    comprobante = forms.FileField(
        label="Comprobante de transferencia (PDF)",
        widget=forms.ClearableFileInput(attrs={
            'class': 'w-full border px-4 py-2 rounded-xl shadow-sm',
        })
    )

    def clean_comprobante(self):
        archivo = self.cleaned_data.get('comprobante')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise forms.ValidationError(
                    "El comprobante debe estar en formato PDF.")
            if archivo.content_type != 'application/pdf':
                raise forms.ValidationError(
                    "El archivo debe ser un PDF válido.")
        return archivo

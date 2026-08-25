from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Max
from django.utils.text import slugify

from planificacion.modelos import CuadrillaOperativa

CustomUser = get_user_model()


class CuadrillaOperativaForm(forms.ModelForm):

    # ========================================================
    # INTEGRANTES
    # ========================================================

    integrantes = forms.ModelMultipleChoiceField(
        queryset=CustomUser.objects.none(),
        required=False,
        label="Integrantes",
        widget=forms.CheckboxSelectMultiple(),
    )

    # ========================================================
    # CAMPOS HUMANOS DE JORNADA
    # ========================================================

    jornada_horas = forms.IntegerField(
        min_value=0,
        max_value=24,
        initial=9,
        label="Horas",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control-cuadrilla",
                "min": 0,
                "max": 24,
                "step": 1,
            }
        ),
    )

    jornada_minutos = forms.IntegerField(
        min_value=0,
        max_value=59,
        initial=0,
        label="Minutos",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control-cuadrilla",
                "min": 0,
                "max": 59,
                "step": 1,
            }
        ),
    )

    trabajo_sitio_horas = forms.IntegerField(
        min_value=0,
        max_value=12,
        initial=2,
        label="Horas",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control-cuadrilla",
                "min": 0,
                "max": 12,
                "step": 1,
            }
        ),
    )

    trabajo_sitio_minutos = forms.IntegerField(
        min_value=0,
        max_value=59,
        initial=45,
        label="Minutos",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control-cuadrilla",
                "min": 0,
                "max": 59,
                "step": 1,
            }
        ),
    )

    class Meta:
        model = CuadrillaOperativa

        fields = [
            "nombre",
            "integrantes",
            "tipo_vehiculo",
            "permite_urbano",
            "permite_rural",
            "direccion_base",
            "base_nombre",
            "activa",
            "observaciones",
        ]

        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "class": "form-control-cuadrilla",
                    "placeholder": "Ej. Cuadrilla 1",
                    "autocomplete": "off",
                }
            ),
            "tipo_vehiculo": forms.TextInput(
                attrs={
                    "class": "form-control-cuadrilla",
                    "placeholder": ("Ej. Camioneta L200 4x4, Partner, furgón"),
                    "autocomplete": "off",
                }
            ),
            "direccion_base": forms.TextInput(
                attrs={
                    "class": "form-control-cuadrilla",
                    "placeholder": ("Ej. Exequiel Fernández 499, " "Ñuñoa, Santiago"),
                    "autocomplete": "street-address",
                }
            ),
            "base_nombre": forms.TextInput(
                attrs={
                    "class": "form-control-cuadrilla",
                    "placeholder": ("Ej. Base Casa de César"),
                    "autocomplete": "off",
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "class": "form-control-cuadrilla",
                    "rows": 4,
                    "placeholder": ("Observaciones operacionales " "de la cuadrilla."),
                }
            ),
            "permite_urbano": forms.CheckboxInput(
                attrs={
                    "class": "checkbox-cuadrilla",
                }
            ),
            "permite_rural": forms.CheckboxInput(
                attrs={
                    "class": "checkbox-cuadrilla",
                }
            ),
            "activa": forms.CheckboxInput(
                attrs={
                    "class": "checkbox-cuadrilla",
                }
            ),
        }

    # ========================================================
    # INICIALIZACIÓN
    # ========================================================

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        # ====================================================
        # USUARIOS DISPONIBLES
        # ====================================================
        #
        # Solamente usuarios que tengan explícitamente
        # el rol "usuario".
        #
        # No se incluyen administradores, PM o supervisores
        # por el simple hecho de tener otro rol.
        # ====================================================

        self.fields["integrantes"].queryset = (
            CustomUser.objects.filter(
                roles__nombre="usuario",
                is_active=True,
            )
            .distinct()
            .order_by(
                "first_name",
                "last_name",
                "username",
                "id",
            )
        )

        # ----------------------------------------------------
        # VALORES POR DEFECTO AL CREAR
        # ----------------------------------------------------

        if not self.instance.pk:

            self.fields["jornada_horas"].initial = 9

            self.fields["jornada_minutos"].initial = 0

            self.fields["trabajo_sitio_horas"].initial = 2

            self.fields["trabajo_sitio_minutos"].initial = 45

            return

        # ----------------------------------------------------
        # EDICIÓN:
        # CONVERTIMOS MINUTOS GUARDADOS → HORAS + MINUTOS
        # ----------------------------------------------------

        minutos_jornada = self.instance.minutos_jornada_default or 0

        self.fields["jornada_horas"].initial = minutos_jornada // 60

        self.fields["jornada_minutos"].initial = minutos_jornada % 60

        minutos_sitio = self.instance.minutos_trabajo_sitio_default or 0

        self.fields["trabajo_sitio_horas"].initial = minutos_sitio // 60

        self.fields["trabajo_sitio_minutos"].initial = minutos_sitio % 60

    # ========================================================
    # VALIDACIONES
    # ========================================================

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()

        if not nombre:
            raise forms.ValidationError("Debes indicar un nombre para la cuadrilla.")

        return nombre

    def clean_tipo_vehiculo(self):
        return (self.cleaned_data.get("tipo_vehiculo") or "").strip()

    def clean_direccion_base(self):
        return (self.cleaned_data.get("direccion_base") or "").strip()

    def clean_base_nombre(self):
        return (self.cleaned_data.get("base_nombre") or "").strip()

    def clean(self):
        cleaned_data = super().clean()

        # ====================================================
        # JORNADA
        # ====================================================

        jornada_horas = cleaned_data.get("jornada_horas")

        jornada_minutos = cleaned_data.get("jornada_minutos")

        if jornada_horas is not None and jornada_minutos is not None:

            total_jornada = jornada_horas * 60 + jornada_minutos

            if total_jornada <= 0:

                self.add_error(
                    "jornada_horas",
                    ("La jornada debe ser mayor " "que cero."),
                )

        # ====================================================
        # TIEMPO POR SITIO
        # ====================================================

        trabajo_horas = cleaned_data.get("trabajo_sitio_horas")

        trabajo_minutos = cleaned_data.get("trabajo_sitio_minutos")

        if trabajo_horas is not None and trabajo_minutos is not None:

            total_trabajo = trabajo_horas * 60 + trabajo_minutos

            if total_trabajo <= 0:

                self.add_error(
                    "trabajo_sitio_horas",
                    ("El tiempo estimado por sitio " "debe ser mayor que cero."),
                )

        # ====================================================
        # COBERTURA
        # ====================================================

        permite_urbano = cleaned_data.get("permite_urbano")

        permite_rural = cleaned_data.get("permite_rural")

        if not permite_urbano and not permite_rural:

            raise forms.ValidationError(
                (
                    "La cuadrilla debe poder ejecutar "
                    "al menos sitios urbanos o rurales."
                )
            )

        return cleaned_data

    # ========================================================
    # CÓDIGO AUTOMÁTICO
    # ========================================================

    def _generar_codigo_unico(
        self,
        nombre,
    ):
        """
        El código solamente se genera cuando nace la cuadrilla.

        Ejemplo:

        Cuadrilla 1
        -> cuadrilla_1

        C1
        -> cuadrilla_c1

        Cuadrilla Norte
        -> cuadrilla_norte

        Si ya existe:
        -> cuadrilla_norte_2
        """

        nombre_normalizado = (
            slugify(nombre)
            .replace(
                "-",
                "_",
            )
            .strip("_")
        )

        if not nombre_normalizado:
            nombre_normalizado = "nueva"

        if nombre_normalizado.startswith("cuadrilla_"):

            base_codigo = nombre_normalizado

        elif nombre_normalizado == "cuadrilla":

            base_codigo = "cuadrilla"

        else:

            base_codigo = f"cuadrilla_" f"{nombre_normalizado}"

        base_codigo = base_codigo[:30].rstrip("_")

        codigo = base_codigo

        contador = 2

        while CuadrillaOperativa.objects.filter(codigo=codigo).exists():

            sufijo = f"_{contador}"

            longitud_base = 30 - len(sufijo)

            codigo = f"{base_codigo[:longitud_base].rstrip('_')}" f"{sufijo}"

            contador += 1

        return codigo

    # ========================================================
    # SAVE
    # ========================================================

    def save(
        self,
        commit=True,
    ):
        objeto = super().save(commit=False)

        # ====================================================
        # TIEMPOS HUMANOS → MINUTOS
        # ====================================================

        objeto.minutos_jornada_default = (
            self.cleaned_data["jornada_horas"] * 60
            + self.cleaned_data["jornada_minutos"]
        )

        objeto.minutos_trabajo_sitio_default = (
            self.cleaned_data["trabajo_sitio_horas"] * 60
            + self.cleaned_data["trabajo_sitio_minutos"]
        )

        # ====================================================
        # CREACIÓN:
        # CÓDIGO Y ORDEN SON AUTOMÁTICOS
        # ====================================================

        if not objeto.pk:

            objeto.codigo = self._generar_codigo_unico(objeto.nombre)

            maximo_orden = CuadrillaOperativa.objects.aggregate(
                maximo=Max("orden")
            ).get("maximo")

            objeto.orden = (maximo_orden or 0) + 1

        # ====================================================
        # EDICIÓN:
        #
        # No regeneramos código ni orden.
        # Ambos conservan su identidad original.
        # ====================================================

        if commit:

            objeto.save()

            self.save_m2m()

        return objeto

# planificacion/forms/planificacion_diaria_manual.py

from datetime import timedelta

from django import forms
from django.core.exceptions import ObjectDoesNotExist

from planificacion.modelos import DisponibilidadCuadrillaSemana

# ============================================================
# PROGRAMAR SITIO MANUALMENTE
# ============================================================


class ProgramarSitioManualPlanificacionDiariaForm(forms.Form):
    """
    Formulario para programar manualmente un sitio dentro de
    una fecha y cuadrilla concreta de la planificación diaria.

    IMPORTANTE
    ==========================================================

    Programar manualmente y marcar como prioridad son dos
    decisiones distintas.

    La programación manual determina:

        - fecha;
        - cuadrilla;
        - bloqueo frente a recálculos.

    La prioridad determina:

        - si aparece la estrella;
        - si existe PrioridadPlanificacionDiaria activa;
        - si el motor debe tratar el sitio como prioritario.

    La validación operacional fuerte continúa realizándose en:

        planificacion.services.planificacion_diaria_manual
    """

    fecha = forms.DateField(
        label="Fecha",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": (
                    "w-full rounded-xl "
                    "border border-slate-300 "
                    "bg-white px-3 py-2.5 "
                    "text-sm text-slate-800 "
                    "shadow-sm "
                    "focus:border-blue-500 "
                    "focus:ring-blue-500"
                ),
            },
            format="%Y-%m-%d",
        ),
    )

    disponibilidad_cuadrilla = forms.ModelChoiceField(
        label="Cuadrilla",
        queryset=DisponibilidadCuadrillaSemana.objects.none(),
        empty_label="Selecciona una cuadrilla",
        widget=forms.Select(
            attrs={
                "class": (
                    "w-full rounded-xl "
                    "border border-slate-300 "
                    "bg-white px-3 py-2.5 "
                    "text-sm text-slate-800 "
                    "shadow-sm "
                    "focus:border-blue-500 "
                    "focus:ring-blue-500"
                ),
            }
        ),
    )

    confirmar_excepcion = forms.BooleanField(
        label="Confirmar excepción operacional",
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                "class": (
                    "h-4 w-4 rounded "
                    "border-slate-300 "
                    "text-amber-600 "
                    "focus:ring-amber-500"
                ),
            }
        ),
        help_text=(
            "Debe marcarse cuando la programación quede con menos "
            "de 3 sitios, genere jornada extendida o se encuentre "
            "fuera de una condición operacional normal."
        ),
    )

    bloquear_salida = forms.BooleanField(
        label="Proteger esta salida contra recálculos automáticos",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": (
                    "h-4 w-4 rounded "
                    "border-slate-300 "
                    "text-blue-600 "
                    "focus:ring-blue-500"
                ),
            }
        ),
        help_text=(
            "Cuando está activa, el motor diario no podrá "
            "reemplazar automáticamente esta salida."
        ),
    )

    marcar_como_prioridad = forms.BooleanField(
        label="Marcar este sitio como prioritario",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={
                "class": (
                    "h-4 w-4 rounded "
                    "border-slate-300 "
                    "text-yellow-500 "
                    "focus:ring-yellow-400"
                ),
            }
        ),
        help_text=(
            "Si está activo, el sitio quedará registrado como "
            "prioridad diaria y aparecerá con una estrella amarilla "
            "en la planificación."
        ),
    )

    observaciones = forms.CharField(
        label="Observaciones",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": (
                    "Ej.: cliente solicita ejecutar este sitio " "el jueves con C1."
                ),
                "class": (
                    "w-full rounded-xl "
                    "border border-slate-300 "
                    "bg-white px-3 py-2.5 "
                    "text-sm text-slate-800 "
                    "shadow-sm "
                    "focus:border-blue-500 "
                    "focus:ring-blue-500"
                ),
            }
        ),
    )

    # ========================================================
    # INICIALIZACIÓN
    # ========================================================

    def __init__(
        self,
        *args,
        batch=None,
        sitio_batch=None,
        **kwargs,
    ):
        self.batch = batch
        self.sitio_batch = sitio_batch

        super().__init__(
            *args,
            **kwargs,
        )

        # ====================================================
        # PRIORIDAD ACTUAL DEL SITIO
        # ====================================================

        if sitio_batch is not None:

            try:

                prioridad = sitio_batch.prioridad_diaria

            except ObjectDoesNotExist:

                prioridad = None

            self.fields["marcar_como_prioridad"].initial = bool(
                prioridad and prioridad.estado == "activa"
            )

        if batch is None:
            return

        # ====================================================
        # RANGO DE FECHAS
        # ====================================================

        inicio = batch.fecha_inicio

        fin = inicio + timedelta(
            days=5,
        )

        self.fields["fecha"].widget.attrs["min"] = inicio.strftime("%Y-%m-%d")

        self.fields["fecha"].widget.attrs["max"] = fin.strftime("%Y-%m-%d")

        # ====================================================
        # CUADRILLAS DISPONIBLES
        # ====================================================

        if batch.configuracion_semana_id:

            queryset = (
                batch.configuracion_semana.disponibilidades_cuadrillas.select_related(
                    "cuadrilla_operativa",
                )
                .filter(
                    activa=True,
                )
                .order_by(
                    "cuadrilla_operativa__orden",
                    "cuadrilla_operativa__nombre",
                    "cuadrilla",
                    "id",
                )
            )

        else:

            queryset = DisponibilidadCuadrillaSemana.objects.none()

        self.fields["disponibilidad_cuadrilla"].queryset = queryset

        self.fields["disponibilidad_cuadrilla"].label_from_instance = (
            self._label_cuadrilla
        )

    # ========================================================
    # LABEL CUADRILLA
    # ========================================================

    @staticmethod
    def _label_cuadrilla(
        disponibilidad,
    ):
        return (
            f"{disponibilidad.nombre_cuadrilla} · "
            f"{disponibilidad.tipo_vehiculo} · "
            f"{disponibilidad.get_modalidad_display()}"
        )

    # ========================================================
    # VALIDACIÓN
    # ========================================================

    def clean(self):

        cleaned_data = super().clean()

        if self.batch is None:
            return cleaned_data

        fecha = cleaned_data.get(
            "fecha",
        )

        disponibilidad = cleaned_data.get(
            "disponibilidad_cuadrilla",
        )

        # ====================================================
        # VALIDAR SITIO CONTRA BATCH
        # ====================================================

        if self.sitio_batch is not None:

            if self.sitio_batch.batch_id != self.batch.pk:

                raise forms.ValidationError(
                    (
                        "El sitio que intentas programar "
                        "no pertenece a este batch semanal."
                    )
                )

        # ====================================================
        # FECHA DENTRO DEL BATCH
        # ====================================================

        if fecha is not None:

            inicio = self.batch.fecha_inicio

            fin = inicio + timedelta(
                days=5,
            )

            if not (inicio <= fecha <= fin):

                self.add_error(
                    "fecha",
                    (
                        "La fecha debe encontrarse dentro "
                        f"de la semana {inicio:%d/%m/%Y} "
                        f"al {fin:%d/%m/%Y}."
                    ),
                )

            if fecha.weekday() == 6:

                self.add_error(
                    "fecha",
                    "El domingo no es un día operacional.",
                )

        # ====================================================
        # CUADRILLA DEL BATCH
        # ====================================================

        if disponibilidad is not None:

            if not self.batch.configuracion_semana_id:

                self.add_error(
                    "disponibilidad_cuadrilla",
                    ("Este batch no posee configuración " "semanal asociada."),
                )

            elif (
                disponibilidad.configuracion_semana_id
                != self.batch.configuracion_semana_id
            ):

                self.add_error(
                    "disponibilidad_cuadrilla",
                    ("La cuadrilla seleccionada no " "pertenece a esta semana."),
                )

            elif not disponibilidad.activa:

                self.add_error(
                    "disponibilidad_cuadrilla",
                    ("La cuadrilla seleccionada no " "está activa esta semana."),
                )

        # ====================================================
        # SÁBADO
        # ====================================================

        if (
            fecha is not None
            and disponibilidad is not None
            and fecha.weekday() == 5
            and not disponibilidad.trabaja_sabado
        ):

            self.add_error(
                "fecha",
                (f"{disponibilidad.nombre_cuadrilla} " "no trabaja los sábados."),
            )

        return cleaned_data

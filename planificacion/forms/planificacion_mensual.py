from django import forms
from django.utils import timezone

from planificacion.models import PlanificacionMensual

# ============================================================
# MESES
# ============================================================


MESES = [
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


# ============================================================
# FORMULARIO PLANIFICACIÓN MENSUAL
# ============================================================


class PlanificacionMensualForm(forms.ModelForm):

    # ========================================================
    # MES
    #
    # Lo declaramos explícitamente porque en el modelo
    # corresponde a un PositiveSmallIntegerField.
    #
    # TypedChoiceField permite:
    #
    # - mostrar correctamente Enero...Diciembre;
    # - recibir el valor desde el select;
    # - convertir automáticamente "8" -> 8;
    # - entregar un entero válido al modelo.
    # ========================================================

    mes = forms.TypedChoiceField(
        label="Mes",
        choices=MESES,
        coerce=int,
        empty_value=None,
        widget=forms.Select(
            attrs={
                "class": (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-3 py-2.5 text-sm font-semibold "
                    "text-slate-700 shadow-sm outline-none "
                    "focus:border-cyan-400 focus:ring-2 "
                    "focus:ring-cyan-100"
                ),
            }
        ),
    )

    class Meta:
        model = PlanificacionMensual

        fields = [
            "anio",
            "mes",
            "observaciones",
        ]

        widgets = {
            "anio": forms.NumberInput(
                attrs={
                    "min": "2024",
                    "max": "2100",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm font-semibold "
                        "text-slate-700 shadow-sm outline-none "
                        "focus:border-cyan-400 focus:ring-2 "
                        "focus:ring-cyan-100"
                    ),
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": ("Observaciones generales de la planificación..."),
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm text-slate-700 "
                        "shadow-sm outline-none resize-y "
                        "focus:border-cyan-400 focus:ring-2 "
                        "focus:ring-cyan-100"
                    ),
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

        # ----------------------------------------------------
        # VALORES INICIALES
        # ----------------------------------------------------

        if not self.is_bound and not self.instance.pk:
            hoy = timezone.localdate()

            self.initial.setdefault(
                "anio",
                hoy.year,
            )

            self.initial.setdefault(
                "mes",
                hoy.month,
            )

    # ========================================================
    # VALIDACIÓN
    # ========================================================

    def clean(self):
        cleaned = super().clean()

        anio = cleaned.get("anio")
        mes = cleaned.get("mes")

        if not anio or not mes:
            return cleaned

        existente = PlanificacionMensual.objects.filter(
            anio=anio,
            mes=mes,
        )

        if self.instance.pk:
            existente = existente.exclude(
                pk=self.instance.pk,
            )

        if existente.exists():
            raise forms.ValidationError("Ya existe una planificación para ese mes.")

        return cleaned

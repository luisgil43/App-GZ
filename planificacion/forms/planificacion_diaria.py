# planificacion/forms/planificacion_diaria.py

from django import forms

from planificacion.modelos import SalidaPlanificacionDiaria
from planificacion.services.motor_batch_semanal.orquestador import (
    ESTRATEGIA_BALANCEADA, ESTRATEGIA_COMPACTA, ESTRATEGIA_OPERATIVA)

# ============================================================
# ESTRATEGIAS DISPONIBLES
# ============================================================


ESTRATEGIAS_PLANIFICACION_DIARIA = [
    (
        ESTRATEGIA_OPERATIVA,
        "Operativa",
    ),
    (
        ESTRATEGIA_COMPACTA,
        "Compacta",
    ),
    (
        ESTRATEGIA_BALANCEADA,
        "Balanceada",
    ),
]


# ============================================================
# GENERAR / RECALCULAR PLANIFICACIÓN DIARIA
# ============================================================


class GenerarPlanificacionDiariaForm(forms.Form):
    """
    Formulario utilizado para generar o recalcular
    la propuesta operacional diaria de un batch semanal.

    La selección del usuario solamente define la estrategia
    utilizada por el motor.

    No asigna técnicos.

    No modifica Operaciones.

    El flujo es:

        permisos aprobados
            ↓
        motor diario
            ↓
        salidas por cuadrilla y fecha
            ↓
        revisión de planificación
            ↓
        botón Asignar
            ↓
        flujo existente de Operaciones
    """

    estrategia = forms.ChoiceField(
        choices=ESTRATEGIAS_PLANIFICACION_DIARIA,
        initial=ESTRATEGIA_OPERATIVA,
        required=True,
        label="Estrategia",
        widget=forms.Select(
            attrs={
                "class": (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-3 py-2.5 text-sm font-semibold "
                    "text-slate-700 shadow-sm "
                    "focus:border-cyan-500 focus:outline-none "
                    "focus:ring-2 focus:ring-cyan-100"
                ),
            }
        ),
    )

    confirmar_recalculo = forms.BooleanField(
        required=False,
        initial=False,
        label="Recalcular salidas todavía editables",
        widget=forms.CheckboxInput(
            attrs={
                "class": (
                    "h-4 w-4 rounded border-slate-300 "
                    "text-cyan-600 focus:ring-cyan-500"
                ),
            }
        ),
    )


# ============================================================
# EDICIÓN DE UNA SALIDA
# ============================================================


class SalidaPlanificacionDiariaForm(forms.ModelForm):
    """
    Edición administrativa de una salida diaria.

    Permite modificar únicamente información perteneciente
    a planificación.

    No permite modificar desde aquí:

    - cuadrilla operacional;
    - técnicos;
    - trabajadores_asignados;
    - ServicioCotizado;
    - estados reales de ejecución.

    La asignación de técnicos continuará utilizando
    el flujo actual de Operaciones.
    """

    class Meta:
        model = SalidaPlanificacionDiaria

        fields = [
            "fecha",
            "orden",
            "bloqueada",
            "observaciones",
        ]

        widgets = {
            "fecha": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-700 shadow-sm "
                        "focus:border-cyan-500 focus:outline-none "
                        "focus:ring-2 focus:ring-cyan-100"
                    ),
                }
            ),
            "orden": forms.NumberInput(
                attrs={
                    "min": "0",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-700 shadow-sm "
                        "focus:border-cyan-500 focus:outline-none "
                        "focus:ring-2 focus:ring-cyan-100"
                    ),
                }
            ),
            "bloqueada": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-cyan-600 focus:ring-cyan-500"
                    ),
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": ("Observaciones internas de planificación..."),
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-700 shadow-sm "
                        "focus:border-cyan-500 focus:outline-none "
                        "focus:ring-2 focus:ring-cyan-100"
                    ),
                }
            ),
        }

        labels = {
            "fecha": "Fecha de salida",
            "orden": "Orden",
            "bloqueada": "Bloquear salida",
            "observaciones": "Observaciones",
        }

        help_texts = {
            "bloqueada": (
                "Una salida bloqueada no podrá ser modificada "
                "por futuras recalculaciones automáticas."
            ),
        }


# ============================================================
# MOVER / REPROGRAMAR SITIO
# ============================================================


class ReprogramarSitioPlanificacionDiariaForm(forms.Form):
    """
    Permite mover un sitio a otra fecha.

    Este formulario pertenece exclusivamente a planificación.

    Todavía no altera el flujo real de Operaciones.

    Si un sitio ya se encuentra asignado/en ejecución/revisión,
    el servicio deberá impedir su movimiento antes de aplicar
    este formulario.
    """

    fecha_nueva = forms.DateField(
        required=True,
        label="Nueva fecha",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-3 py-2.5 text-sm "
                    "text-slate-700 shadow-sm "
                    "focus:border-cyan-500 focus:outline-none "
                    "focus:ring-2 focus:ring-cyan-100"
                ),
            }
        ),
    )

    motivo = forms.CharField(
        required=True,
        label="Motivo",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": ("Indica por qué se está reprogramando el sitio."),
                "class": (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-3 py-2.5 text-sm "
                    "text-slate-700 shadow-sm "
                    "focus:border-cyan-500 focus:outline-none "
                    "focus:ring-2 focus:ring-cyan-100"
                ),
            }
        ),
    )

    def clean_motivo(self):
        motivo = (
            self.cleaned_data.get(
                "motivo",
                "",
            )
            or ""
        ).strip()

        if not motivo:
            raise forms.ValidationError("Debes indicar el motivo de la reprogramación.")

        return motivo


# ============================================================
# RETIRAR SITIO DE UNA SALIDA
# ============================================================


class RetirarSitioPlanificacionDiariaForm(forms.Form):
    """
    Retira un sitio de una salida todavía editable.

    El sitio continúa perteneciendo al batch semanal y puede
    volver a entrar posteriormente en otra planificación diaria.
    """

    motivo = forms.CharField(
        required=True,
        label="Motivo",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": ("Indica por qué el sitio se retira " "de esta salida."),
                "class": (
                    "w-full rounded-xl border border-slate-300 "
                    "bg-white px-3 py-2.5 text-sm "
                    "text-slate-700 shadow-sm "
                    "focus:border-red-400 focus:outline-none "
                    "focus:ring-2 focus:ring-red-100"
                ),
            }
        ),
    )

    def clean_motivo(self):
        motivo = (
            self.cleaned_data.get(
                "motivo",
                "",
            )
            or ""
        ).strip()

        if not motivo:
            raise forms.ValidationError("Debes indicar el motivo del retiro.")

        return motivo

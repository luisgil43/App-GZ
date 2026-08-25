# planificacion/forms/prioridades_diarias.py

from datetime import timedelta

from django import forms

from planificacion.modelos import (CuadrillaOperativa,
                                   PrioridadPlanificacionDiaria)
from planificacion.models import SitioBatchSemanal

# ============================================================
# FORMULARIO DE PRIORIDAD DE PLANIFICACIÓN DIARIA
# ============================================================


class PrioridadPlanificacionDiariaForm(forms.ModelForm):
    """
    Configura una condición operacional especial para un sitio
    dentro de la planificación diaria.

    IMPORTANTE
    ==========================================================

    Este formulario NO modifica:

    - planificación mensual;
    - planificación semanal;
    - selección del batch semanal;
    - motor semanal;
    - Operaciones.

    Únicamente configura cómo debe tratar el motor DIARIO
    un sitio que ya forma parte del batch semanal.

    El formulario puede recibir:

        batch=<BatchPlanificacionSemanal>

    para limitar el selector de sitios exclusivamente al batch
    que se está administrando.
    """

    class Meta:
        model = PrioridadPlanificacionDiaria

        fields = [
            "sitio_batch",
            "prioridad",
            "es_ancla",
            "fecha_objetivo",
            "fecha_es_obligatoria",
            "cuadrilla_obligatoria",
            "distancia_preferida_km",
            "distancia_maxima_km",
            "minutos_preferidos",
            "minutos_maximos",
            "objetivo_sitios_salida",
            "permitir_salida_2_sitios",
            "permitir_salida_1_sitio",
            "requiere_confirmacion_excepcion",
            "motivo",
            "observaciones",
        ]

        widgets = {
            # =================================================
            # SITIO
            # =================================================
            "sitio_batch": forms.Select(
                attrs={
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # PRIORIDAD
            # =================================================
            "prioridad": forms.Select(
                attrs={
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "font-semibold text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # SITIO ANCLA
            # =================================================
            "es_ancla": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-blue-600 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # FECHA
            # =================================================
            "fecha_objetivo": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                },
                format="%Y-%m-%d",
            ),
            "fecha_es_obligatoria": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-blue-600 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # CUADRILLA
            # =================================================
            "cuadrilla_obligatoria": forms.Select(
                attrs={
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # DISTANCIAS
            # =================================================
            "distancia_preferida_km": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.1",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            "distancia_maxima_km": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.1",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # TIEMPOS
            # =================================================
            "minutos_preferidos": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "1",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            "minutos_maximos": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "1",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            # =================================================
            # CAPACIDAD DE LA SALIDA
            # =================================================
            "objetivo_sitios_salida": forms.NumberInput(
                attrs={
                    "min": "1",
                    "max": "3",
                    "step": "1",
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "font-semibold text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            "permitir_salida_2_sitios": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-blue-600 focus:ring-blue-500"
                    ),
                }
            ),
            "permitir_salida_1_sitio": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-red-600 focus:ring-red-500"
                    ),
                }
            ),
            "requiere_confirmacion_excepcion": forms.CheckboxInput(
                attrs={
                    "class": (
                        "h-4 w-4 rounded border-slate-300 "
                        "text-amber-600 focus:ring-amber-500"
                    ),
                }
            ),
            # =================================================
            # TEXTO
            # =================================================
            "motivo": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": (
                        "Ej.: cliente solicita ejecutar este sitio "
                        "el miércoles y solamente puede asistir C2."
                    ),
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": (
                        "Información adicional para la planificación diaria."
                    ),
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm "
                        "text-slate-800 shadow-sm "
                        "focus:border-blue-500 focus:ring-blue-500"
                    ),
                }
            ),
        }

        labels = {
            "sitio_batch": "Sitio prioritario",
            "prioridad": "Nivel de prioridad",
            "es_ancla": "Usar como sitio ancla",
            "fecha_objetivo": "Fecha objetivo",
            "fecha_es_obligatoria": "La fecha es obligatoria",
            "cuadrilla_obligatoria": "Cuadrilla obligatoria",
            "distancia_preferida_km": "Distancia preferida",
            "distancia_maxima_km": "Distancia máxima",
            "minutos_preferidos": "Tiempo preferido",
            "minutos_maximos": "Tiempo máximo",
            "objetivo_sitios_salida": "Objetivo de sitios del día",
            "permitir_salida_2_sitios": "Permitir salida con 2 sitios",
            "permitir_salida_1_sitio": "Permitir salida con 1 sitio",
            "requiere_confirmacion_excepcion": (
                "Confirmar antes de utilizar sitios alejados"
            ),
            "motivo": "Motivo de la prioridad",
            "observaciones": "Observaciones",
        }

        help_texts = {
            "sitio_batch": ("Sitio que determinará la planificación de esta salida."),
            "prioridad": ("Crítica tendrá precedencia sobre Alta y Normal."),
            "es_ancla": (
                "Los otros sitios del día se buscarán alrededor de este sitio."
            ),
            "fecha_objetivo": (
                "Puedes dejarla vacía para que el motor determine el mejor día."
            ),
            "fecha_es_obligatoria": (
                "Si se activa, el motor no podrá mover el sitio a otra fecha."
            ),
            "cuadrilla_obligatoria": (
                "Déjalo vacío cuando cualquier cuadrilla compatible pueda ejecutarlo."
            ),
            "distancia_preferida_km": (
                "Radio que el motor intentará respetar primero."
            ),
            "distancia_maxima_km": (
                "Límite para considerar candidatos automáticamente."
            ),
            "minutos_preferidos": ("Tiempo deseado entre el ancla y otro sitio."),
            "minutos_maximos": (
                "Por encima de este tiempo se considera una excepción."
            ),
            "objetivo_sitios_salida": ("Operativamente el valor normal será 3."),
            "permitir_salida_2_sitios": (
                "Solo se utilizará si no podemos completar razonablemente 3."
            ),
            "permitir_salida_1_sitio": (
                "Debe quedar como última excepción operacional."
            ),
            "requiere_confirmacion_excepcion": (
                "Si solo existen sitios alejados, no se decidirá automáticamente."
            ),
        }

    # ========================================================
    # INICIALIZACIÓN
    # ========================================================

    def __init__(
        self,
        *args,
        batch=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.batch = batch

        # ====================================================
        # SITIOS DEL BATCH
        # ====================================================

        sitios_queryset = SitioBatchSemanal.objects.select_related(
            "sitio_planificado",
            "sitio_planificado__sitio",
        ).exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )

        if batch is not None:

            sitios_queryset = sitios_queryset.filter(
                batch=batch,
            )

        # Al editar debemos mantener visible el sitio actual.
        if self.instance and self.instance.pk:

            sitios_queryset = sitios_queryset.filter(
                models_q_sitio_actual(
                    self.instance.sitio_batch_id,
                )
            )

        else:

            # Para nuevos registros evitamos ofrecer sitios
            # que ya poseen una PrioridadPlanificacionDiaria.
            sitios_queryset = sitios_queryset.filter(
                prioridad_diaria__isnull=True,
            )

        sitios_queryset = sitios_queryset.order_by(
            "sitio_planificado__sitio__comuna",
            "sitio_planificado__sitio__id_claro",
            "id",
        )

        self.fields["sitio_batch"].queryset = sitios_queryset

        self.fields["sitio_batch"].label_from_instance = self._label_sitio

        # ====================================================
        # CUADRILLAS ACTIVAS
        # ====================================================

        self.fields[
            "cuadrilla_obligatoria"
        ].queryset = CuadrillaOperativa.objects.filter(
            activa=True,
        ).order_by(
            "orden",
            "nombre",
            "codigo",
            "id",
        )

        self.fields["cuadrilla_obligatoria"].empty_label = (
            "Cualquier cuadrilla compatible"
        )

        # ====================================================
        # FECHA HTML
        # ====================================================

        if self.instance and self.instance.pk and self.instance.fecha_objetivo:

            self.initial["fecha_objetivo"] = self.instance.fecha_objetivo.strftime(
                "%Y-%m-%d"
            )

    # ========================================================
    # ETIQUETA DEL SITIO
    # ========================================================

    @staticmethod
    def _label_sitio(
        item,
    ):
        sitio = item.sitio_planificado.sitio

        identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

        nombre = sitio.nombre or "Sin nombre"

        comuna = sitio.comuna or "Sin comuna"

        return f"{identificador} · " f"{nombre} · " f"{comuna}"

    # ========================================================
    # VALIDACIONES
    # ========================================================

    def clean(self):
        cleaned_data = super().clean()

        sitio_batch = cleaned_data.get("sitio_batch")

        fecha_objetivo = cleaned_data.get("fecha_objetivo")

        fecha_es_obligatoria = cleaned_data.get("fecha_es_obligatoria")

        distancia_preferida = cleaned_data.get("distancia_preferida_km")

        distancia_maxima = cleaned_data.get("distancia_maxima_km")

        minutos_preferidos = cleaned_data.get("minutos_preferidos")

        minutos_maximos = cleaned_data.get("minutos_maximos")

        objetivo_sitios = cleaned_data.get("objetivo_sitios_salida")

        permitir_2 = cleaned_data.get("permitir_salida_2_sitios")

        permitir_1 = cleaned_data.get("permitir_salida_1_sitio")

        # ====================================================
        # SITIO PERTENECE AL BATCH
        # ====================================================

        if (
            self.batch is not None
            and sitio_batch is not None
            and sitio_batch.batch_id != self.batch.pk
        ):

            self.add_error(
                "sitio_batch",
                ("El sitio seleccionado no pertenece " "a este batch semanal."),
            )

        # ====================================================
        # FECHA OBLIGATORIA
        # ====================================================

        if fecha_es_obligatoria and not fecha_objetivo:

            self.add_error(
                "fecha_objetivo",
                (
                    "Debes seleccionar una fecha cuando "
                    "la fecha se marca como obligatoria."
                ),
            )

        # ====================================================
        # FECHA DENTRO DE LA SEMANA
        # ====================================================

        if self.batch is not None and fecha_objetivo is not None:

            inicio = self.batch.fecha_inicio

            fin = inicio + timedelta(days=5)

            if not (inicio <= fecha_objetivo <= fin):

                self.add_error(
                    "fecha_objetivo",
                    (
                        "La fecha objetivo debe encontrarse "
                        "dentro de la semana operacional "
                        f"{inicio:%d/%m/%Y} al "
                        f"{fin:%d/%m/%Y}."
                    ),
                )

        # ====================================================
        # DISTANCIAS
        # ====================================================

        if (
            distancia_preferida is not None
            and distancia_maxima is not None
            and distancia_maxima < distancia_preferida
        ):

            self.add_error(
                "distancia_maxima_km",
                (
                    "La distancia máxima no puede ser menor "
                    "que la distancia preferida."
                ),
            )

        # ====================================================
        # MINUTOS
        # ====================================================

        if (
            minutos_preferidos is not None
            and minutos_maximos is not None
            and minutos_maximos < minutos_preferidos
        ):

            self.add_error(
                "minutos_maximos",
                ("El tiempo máximo no puede ser menor " "que el tiempo preferido."),
            )

        # ====================================================
        # OBJETIVO OPERACIONAL
        # ====================================================

        if objetivo_sitios is not None:

            if objetivo_sitios < 1 or objetivo_sitios > 3:

                self.add_error(
                    "objetivo_sitios_salida",
                    ("El objetivo diario debe estar " "entre 1 y 3 sitios."),
                )

        # ====================================================
        # EXCEPCIONES COHERENTES
        # ====================================================

        if objetivo_sitios == 1 and not permitir_1:

            self.add_error(
                "permitir_salida_1_sitio",
                (
                    "Si el objetivo de la salida es un solo sitio, "
                    "debes permitir expresamente las salidas "
                    "de un sitio."
                ),
            )

        if objetivo_sitios == 2 and not permitir_2:

            self.add_error(
                "permitir_salida_2_sitios",
                (
                    "Si el objetivo de la salida es dos sitios, "
                    "debes permitir expresamente las salidas "
                    "de dos sitios."
                ),
            )

        return cleaned_data


# ============================================================
# HELPER PARA MANTENER EL SITIO ACTUAL DURANTE EDICIÓN
# ============================================================


def models_q_sitio_actual(
    sitio_batch_id,
):
    """
    Durante edición necesitamos mostrar:

    - sitios sin otra prioridad;
    - O el sitio actualmente vinculado al registro.

    Se deja como helper para mantener legible el __init__.
    """

    from django.db.models import Q

    return Q(
        prioridad_diaria__isnull=True,
    ) | Q(
        pk=sitio_batch_id,
    )

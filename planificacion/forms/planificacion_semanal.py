from datetime import date, timedelta

from django import forms

from planificacion.modelos import (CuadrillaOperativa,
                                   DisponibilidadCuadrillaSemana)
from planificacion.models import BatchPlanificacionSemanal

# ============================================================
# UTILIDADES DE SEMANAS
# ============================================================


def _primer_dia_mes(
    anio,
    mes,
):
    return date(
        int(anio),
        int(mes),
        1,
    )


def _ultimo_dia_mes(
    anio,
    mes,
):
    anio = int(anio)
    mes = int(mes)

    if mes == 12:

        siguiente = date(
            anio + 1,
            1,
            1,
        )

    else:

        siguiente = date(
            anio,
            mes + 1,
            1,
        )

    return siguiente - timedelta(
        days=1,
    )


def _lunes_semana(
    fecha,
):
    """
    Devuelve el lunes correspondiente a cualquier fecha.
    """

    return fecha - timedelta(
        days=fecha.weekday(),
    )


# ============================================================
# OBTENER SEMANAS OPERACIONALES DEL MES
# ============================================================


def obtener_semanas_operacionales_mes(
    mensual,
):
    """
    Devuelve todas las semanas operacionales disponibles para
    una planificación mensual.

    REGLA NATURAL
    ==========================================================

    Incluye todas las semanas ISO que intersectan el mes.

    Ejemplo:

        Septiembre 2026

    incluye:

        W36 -> 31/08/2026 al 06/09/2026
        W37 -> 07/09/2026 al 13/09/2026
        W38 -> 14/09/2026 al 20/09/2026
        W39 -> 21/09/2026 al 27/09/2026
        W40 -> 28/09/2026 al 04/10/2026

    SEMANAS PENDIENTES ANTERIORES
    ==========================================================

    También revisamos cuál es el último batch semanal global
    existente antes de la primera semana operacional natural
    del mes.

    Si existen semanas consecutivas sin batch entre ambos
    puntos, esas semanas también se ofrecen.

    Ejemplo:

        último batch existente:
            W34

        primera semana natural de septiembre:
            W36

    entonces falta:

        W35

    y el formulario mostrará:

        W35 · Semana pendiente anterior
        W36
        W37
        W38
        W39
        W40

    IMPORTANTE
    ==========================================================

    Las semanas canceladas NO cuentan como semanas existentes.

    Por tanto una semana cuyo único batch esté cancelado puede
    volver a ofrecerse como semana pendiente.
    """

    if mensual is None:
        return []

    # ========================================================
    # RANGO DEL MES
    # ========================================================

    primer_dia = _primer_dia_mes(
        mensual.anio,
        mensual.mes,
    )

    ultimo_dia = _ultimo_dia_mes(
        mensual.anio,
        mensual.mes,
    )

    primer_lunes = _lunes_semana(
        primer_dia,
    )

    ultimo_lunes = _lunes_semana(
        ultimo_dia,
    )

    # ========================================================
    # SEMANAS NATURALES DEL MES
    # ========================================================

    semanas_naturales = []

    fecha_inicio = primer_lunes

    while fecha_inicio <= ultimo_lunes:

        iso = fecha_inicio.isocalendar()

        fecha_fin = fecha_inicio + timedelta(
            days=6,
        )

        semanas_naturales.append(
            {
                "numero": iso.week,
                "codigo": f"W{iso.week}",
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
                "anio_iso": iso.year,
                "cruza_inicio_mes": (
                    fecha_inicio.month != int(mensual.mes)
                    or fecha_inicio.year != int(mensual.anio)
                ),
                "cruza_fin_mes": (
                    fecha_fin.month != int(mensual.mes)
                    or fecha_fin.year != int(mensual.anio)
                ),
                "pendiente_anterior": False,
            }
        )

        fecha_inicio += timedelta(
            days=7,
        )

    # ========================================================
    # ÚLTIMO BATCH GLOBAL ANTERIOR
    # ========================================================

    ultimo_batch_anterior = (
        BatchPlanificacionSemanal.objects.filter(
            fecha_inicio__lt=primer_lunes,
        )
        .exclude(
            estado="cancelado",
        )
        .order_by(
            "-fecha_inicio",
            "-id",
        )
        .first()
    )

    # ========================================================
    # NO EXISTE HISTORIAL ANTERIOR
    # ========================================================

    if ultimo_batch_anterior is None:

        return semanas_naturales

    # ========================================================
    # PRIMERA SEMANA QUE DEBERÍA EXISTIR DESPUÉS DEL ÚLTIMO
    # BATCH
    # ========================================================

    fecha_pendiente = ultimo_batch_anterior.fecha_inicio + timedelta(
        days=7,
    )

    # ========================================================
    # NO EXISTE HUECO
    # ========================================================

    if fecha_pendiente >= primer_lunes:

        return semanas_naturales

    # ========================================================
    # SEMANAS PENDIENTES
    # ========================================================

    semanas_pendientes = []

    while fecha_pendiente < primer_lunes:

        existe_batch = (
            BatchPlanificacionSemanal.objects.filter(
                fecha_inicio=fecha_pendiente,
            )
            .exclude(
                estado="cancelado",
            )
            .exists()
        )

        if not existe_batch:

            iso = fecha_pendiente.isocalendar()

            fecha_fin = fecha_pendiente + timedelta(
                days=6,
            )

            semanas_pendientes.append(
                {
                    "numero": iso.week,
                    "codigo": f"W{iso.week}",
                    "fecha_inicio": fecha_pendiente,
                    "fecha_fin": fecha_fin,
                    "anio_iso": iso.year,
                    "cruza_inicio_mes": True,
                    "cruza_fin_mes": True,
                    "pendiente_anterior": True,
                }
            )

        fecha_pendiente += timedelta(
            days=7,
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    resultado = semanas_pendientes + semanas_naturales

    resultado.sort(key=lambda semana: (semana["fecha_inicio"],))

    return resultado


# ============================================================
# OBTENER UNA SEMANA OPERACIONAL POR FECHA
# ============================================================


def obtener_semana_operacional_por_fecha(
    *,
    mensual,
    fecha_inicio,
):
    """
    Devuelve una semana operacional válida para el formulario.

    Puede ser:

        - una semana natural que intersecta el mes;
        - una semana pendiente inmediatamente anterior.

    La validación utiliza exactamente la misma lista que se
    presenta al usuario.

    Por tanto:

        si aparece en el selector,
        es válida al enviar el formulario.
    """

    semanas = obtener_semanas_operacionales_mes(
        mensual,
    )

    for semana in semanas:

        if semana["fecha_inicio"] == fecha_inicio:

            return semana

    return None


# ============================================================
# FORMULARIO
# ============================================================


class CrearBatchSemanalForm(forms.ModelForm):
    """
    Formulario para seleccionar una semana operacional.

    REGLA FUNDAMENTAL
    ==========================================================

    La semana es GLOBAL.

    Ejemplo:

        W36
        31/08/2026 -> 06/09/2026

    Puede verse desde:

        Agosto 2026
        Septiembre 2026

    pero existe un solo BatchPlanificacionSemanal.

    Si la semana todavía no existe:

        se crea.

    Si ya existe:

        NO se crea otro batch;
        la vista vinculará el mes actual a esa semana
        y abrirá el batch existente.

    También pueden aparecer semanas pendientes anteriores
    cuando existe un hueco real en la secuencia semanal.

    Ejemplo:

        último batch:
            W34

        mes actual comienza operacionalmente:
            W36

    se ofrecerá:

        W35 · Semana pendiente anterior

    El código del batch se obtiene automáticamente utilizando
    el número ISO real:

        W35
        W36
        W37
        ...
    """

    class Meta:

        model = BatchPlanificacionSemanal

        fields = [
            "fecha_inicio",
            "objetivo_sitios",
            "observaciones",
        ]

        labels = {
            "fecha_inicio": "Semana objetivo",
            "objetivo_sitios": "Cantidad de sitios a proponer",
            "observaciones": "Observaciones",
        }

        widgets = {
            "fecha_inicio": forms.Select(
                attrs={
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm text-slate-800 "
                        "outline-none transition "
                        "focus:border-blue-500 focus:ring-2 "
                        "focus:ring-blue-100"
                    ),
                }
            ),
            "objetivo_sitios": forms.NumberInput(
                attrs={
                    "min": 1,
                    "step": 1,
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm text-slate-800 "
                        "outline-none transition "
                        "focus:border-blue-500 focus:ring-2 "
                        "focus:ring-blue-100"
                    ),
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": (
                        "w-full rounded-xl border border-slate-300 "
                        "bg-white px-3 py-2.5 text-sm text-slate-800 "
                        "outline-none transition "
                        "focus:border-blue-500 focus:ring-2 "
                        "focus:ring-blue-100"
                    ),
                    "placeholder": (
                        "Información adicional para la preparación " "de esta semana."
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
        mensual=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.mensual = mensual

        self.semanas_operacionales = []

        self.semanas_disponibles = []

        self.semanas_ocupadas = []

        self.semana_seleccionada = None

        self.batch_existente = None

        # ====================================================
        # VALORES GENERALES
        # ====================================================

        self.fields["objetivo_sitios"].initial = 40

        # ====================================================
        # SEMANAS
        # ====================================================

        self._configurar_semanas()

        # ====================================================
        # ESTILOS
        # ====================================================

        select_class = (
            "w-full rounded-xl "
            "border border-slate-300 "
            "bg-white px-3 py-2.5 "
            "text-sm text-slate-800 "
            "outline-none transition "
            "focus:border-blue-500 "
            "focus:ring-2 "
            "focus:ring-blue-100"
        )

        checkbox_class = (
            "h-4 w-4 rounded "
            "border-slate-300 "
            "text-blue-600 "
            "focus:ring-blue-500"
        )

        number_class = (
            "w-full rounded-xl "
            "border border-slate-300 "
            "bg-white px-3 py-2.5 "
            "text-sm text-slate-800 "
            "outline-none transition "
            "focus:border-blue-500 "
            "focus:ring-2 "
            "focus:ring-blue-100"
        )

        # ====================================================
        # CUADRILLAS ACTIVAS
        # ====================================================

        self.cuadrillas_operativas = list(
            CuadrillaOperativa.objects.filter(
                activa=True,
            ).order_by(
                "orden",
                "nombre",
                "id",
            )
        )

        # ====================================================
        # CAMPOS DINÁMICOS
        # ====================================================

        for cuadrilla in self.cuadrillas_operativas:

            campo_activa = f"cuadrilla_{cuadrilla.pk}_activa"

            campo_modalidad = f"cuadrilla_{cuadrilla.pk}_modalidad"

            campo_capacidad = f"cuadrilla_{cuadrilla.pk}_capacidad"

            self.fields[campo_activa] = forms.BooleanField(
                required=False,
                initial=True,
                label=f"{cuadrilla.nombre} activa",
                widget=forms.CheckboxInput(
                    attrs={
                        "class": checkbox_class,
                        "data-cuadrilla-activa": str(cuadrilla.pk),
                    }
                ),
            )

            self.fields[campo_modalidad] = forms.ChoiceField(
                choices=(DisponibilidadCuadrillaSemana.MODALIDADES),
                initial=(DisponibilidadCuadrillaSemana.LUNES_VIERNES),
                label=f"Jornada {cuadrilla.nombre}",
                widget=forms.Select(
                    attrs={
                        "class": select_class,
                        "data-cuadrilla-modalidad": str(cuadrilla.pk),
                    }
                ),
            )

            self.fields[campo_capacidad] = forms.IntegerField(
                required=True,
                min_value=1,
                initial=3,
                label=(f"Capacidad nominal " f"{cuadrilla.nombre}"),
                widget=forms.NumberInput(
                    attrs={
                        "min": 1,
                        "step": 1,
                        "class": number_class,
                        "data-cuadrilla-capacidad": str(cuadrilla.pk),
                    }
                ),
            )

        # ====================================================
        # ESTRUCTURA DEL TEMPLATE
        # ====================================================

        self.cuadrillas_configuracion = []

        for cuadrilla in self.cuadrillas_operativas:

            campo_activa = f"cuadrilla_{cuadrilla.pk}_activa"

            campo_modalidad = f"cuadrilla_{cuadrilla.pk}_modalidad"

            campo_capacidad = f"cuadrilla_{cuadrilla.pk}_capacidad"

            self.cuadrillas_configuracion.append(
                {
                    "cuadrilla": cuadrilla,
                    "campo_activa": self[campo_activa],
                    "campo_modalidad": self[campo_modalidad],
                    "campo_capacidad": self[campo_capacidad],
                }
            )

    # ========================================================
    # CONFIGURAR SEMANAS
    # ========================================================

    def _configurar_semanas(
        self,
    ):
        """
        Configura el selector de semanas.

        Muestra:

        - semanas naturales del mes;
        - semanas pendientes anteriores;
        - semanas globales ya existentes.

        Una semana ya existente permanece visible, pero su
        etiqueta informa que será reutilizada.

        Una semana pendiente anterior muestra explícitamente:

            Semana pendiente anterior
        """

        if self.mensual is None:

            self.fields["fecha_inicio"].widget.choices = [
                (
                    "",
                    "Selecciona una semana",
                ),
            ]

            return

        semanas = obtener_semanas_operacionales_mes(
            self.mensual,
        )

        self.semanas_operacionales = semanas

        # ====================================================
        # BATCHES EXISTENTES
        # ====================================================

        fechas = [semana["fecha_inicio"] for semana in semanas]

        batches_existentes = {
            batch.fecha_inicio: batch
            for batch in (
                BatchPlanificacionSemanal.objects.filter(
                    fecha_inicio__in=fechas,
                )
                .exclude(
                    estado="cancelado",
                )
                .select_related(
                    "planificacion",
                )
                .order_by(
                    "fecha_inicio",
                    "id",
                )
            )
        }

        # ====================================================
        # OPCIONES
        # ====================================================

        opciones = [
            (
                "",
                "Selecciona una semana",
            ),
        ]

        for semana in semanas:

            fecha_inicio = semana["fecha_inicio"]

            batch_existente = batches_existentes.get(fecha_inicio)

            semana_ui = dict(semana)

            semana_ui["batch_existente"] = batch_existente

            semana_ui["batch_existente_id"] = (
                batch_existente.pk if batch_existente else None
            )

            # =================================================
            # SEMANA EXISTENTE
            # =================================================

            if batch_existente:

                semana_ui["ocupada"] = True

                self.semanas_ocupadas.append(
                    semana_ui,
                )

                etiqueta = (
                    f'{semana["codigo"]} · '
                    f'{semana["fecha_inicio"]:%d/%m/%Y} '
                    f"al "
                    f'{semana["fecha_fin"]:%d/%m/%Y} '
                    f"· Semana existente"
                )

            # =================================================
            # SEMANA DISPONIBLE
            # =================================================

            else:

                semana_ui["ocupada"] = False

                self.semanas_disponibles.append(
                    semana_ui,
                )

                # =============================================
                # PENDIENTE ANTERIOR
                # =============================================

                if semana.get(
                    "pendiente_anterior",
                    False,
                ):

                    etiqueta = (
                        f'{semana["codigo"]} · '
                        f'{semana["fecha_inicio"]:%d/%m/%Y} '
                        f"al "
                        f'{semana["fecha_fin"]:%d/%m/%Y} '
                        f"· Semana pendiente anterior"
                    )

                # =============================================
                # SEMANA NORMAL
                # =============================================

                else:

                    etiqueta = (
                        f'{semana["codigo"]} · '
                        f'{semana["fecha_inicio"]:%d/%m/%Y} '
                        f"al "
                        f'{semana["fecha_fin"]:%d/%m/%Y}'
                    )

            opciones.append(
                (
                    fecha_inicio.isoformat(),
                    etiqueta,
                )
            )

        self.fields["fecha_inicio"].widget.choices = opciones

    # ========================================================
    # VALIDACIÓN DE SEMANA
    # ========================================================

    def clean_fecha_inicio(
        self,
    ):
        fecha_inicio = self.cleaned_data["fecha_inicio"]

        if self.mensual is None:

            raise forms.ValidationError(
                ("No fue posible determinar " "la planificación mensual.")
            )

        if fecha_inicio.weekday() != 0:

            raise forms.ValidationError("La semana debe comenzar un lunes.")

        semana = obtener_semana_operacional_por_fecha(
            mensual=self.mensual,
            fecha_inicio=fecha_inicio,
        )

        if semana is None:

            raise forms.ValidationError(
                (
                    "La semana seleccionada no pertenece "
                    "a las semanas operacionales disponibles "
                    "para esta planificación mensual."
                )
            )

        self.semana_seleccionada = semana

        # ====================================================
        # BUSCAR SEMANA GLOBAL EXISTENTE
        # ====================================================

        self.batch_existente = (
            BatchPlanificacionSemanal.objects.filter(
                fecha_inicio=fecha_inicio,
            )
            .exclude(
                estado="cancelado",
            )
            .select_related(
                "planificacion",
            )
            .first()
        )

        # ====================================================
        # NO GENERAMOS ERROR SI EXISTE
        # ========================================================
        #
        # La vista decidirá:
        #
        # existe:
        #     vincular el mes
        #     abrir el batch
        #
        # no existe:
        #     crear nuevo batch global
        # ========================================================

        return fecha_inicio

    # ========================================================
    # VALIDACIÓN OBJETIVO
    # ========================================================

    def clean_objetivo_sitios(
        self,
    ):
        objetivo = self.cleaned_data["objetivo_sitios"]

        if objetivo <= 0:

            raise forms.ValidationError(
                ("La cantidad de sitios debe " "ser mayor que cero.")
            )

        return objetivo

    # ========================================================
    # VALIDACIÓN GENERAL
    # ========================================================

    def clean(
        self,
    ):
        cleaned_data = super().clean()

        # ====================================================
        # SEMANA GLOBAL YA EXISTENTE
        # ========================================================
        #
        # Si la semana ya existe:
        #
        # - no estamos creando configuración;
        # - no estamos creando disponibilidades;
        # - solamente vinculamos el mes y abrimos el batch.
        #
        # Por tanto no exigimos configurar nuevamente las
        # cuadrillas.
        # ====================================================

        if self.batch_existente is not None:

            return cleaned_data

        # ====================================================
        # NUEVA SEMANA: DEBE HABER CUADRILLAS
        # ====================================================

        if not self.cuadrillas_operativas:

            raise forms.ValidationError(
                (
                    "No existen cuadrillas operativas activas. "
                    "Debes crear o activar al menos una "
                    "cuadrilla antes de preparar la semana."
                )
            )

        existe_activa = False

        for cuadrilla in self.cuadrillas_operativas:

            campo_activa = f"cuadrilla_{cuadrilla.pk}_activa"

            if bool(
                cleaned_data.get(
                    campo_activa,
                    False,
                )
            ):

                existe_activa = True

                break

        if not existe_activa:

            raise forms.ValidationError(
                ("Debe existir al menos una cuadrilla " "activa para la semana.")
            )

        return cleaned_data

    # ========================================================
    # NOMBRE AUTOMÁTICO
    # ========================================================

    def obtener_nombre_batch(
        self,
    ):
        """
        Devuelve el código ISO real.

        Ejemplo:

            W35
            W36
            W37
        """

        if not hasattr(
            self,
            "cleaned_data",
        ):

            raise RuntimeError(
                ("Debes validar el formulario " "antes de obtener el nombre.")
            )

        if self.errors:

            raise RuntimeError(
                ("No se puede obtener el nombre " "desde un formulario con errores.")
            )

        fecha_inicio = self.cleaned_data.get(
            "fecha_inicio",
        )

        semana = obtener_semana_operacional_por_fecha(
            mensual=self.mensual,
            fecha_inicio=fecha_inicio,
        )

        if semana is None:

            raise RuntimeError(("No fue posible determinar " "la semana operacional."))

        return semana["codigo"]

    # ========================================================
    # BATCH EXISTENTE
    # ========================================================

    def obtener_batch_existente(
        self,
    ):
        """
        Devuelve el batch global de la semana si ya existe.

        Debe utilizarse después de:

            form.is_valid()
        """

        if not hasattr(
            self,
            "cleaned_data",
        ):

            raise RuntimeError("Primero debes validar el formulario.")

        fecha_inicio = self.cleaned_data.get(
            "fecha_inicio",
        )

        if fecha_inicio is None:

            return None

        return (
            BatchPlanificacionSemanal.objects.filter(
                fecha_inicio=fecha_inicio,
            )
            .exclude(
                estado="cancelado",
            )
            .select_related(
                "planificacion",
                "configuracion_semana",
            )
            .first()
        )

    # ========================================================
    # DISPONIBILIDADES
    # ========================================================

    def obtener_disponibilidades(
        self,
    ):
        """
        Devuelve las disponibilidades normalizadas.
        """

        if not hasattr(
            self,
            "cleaned_data",
        ):

            raise RuntimeError(
                (
                    "Debes validar el formulario antes "
                    "de obtener las disponibilidades."
                )
            )

        if self.errors:

            raise RuntimeError(
                (
                    "No se pueden obtener disponibilidades "
                    "desde un formulario con errores."
                )
            )

        resultado = []

        for cuadrilla in self.cuadrillas_operativas:

            campo_activa = f"cuadrilla_{cuadrilla.pk}_activa"

            campo_modalidad = f"cuadrilla_{cuadrilla.pk}_modalidad"

            campo_capacidad = f"cuadrilla_{cuadrilla.pk}_capacidad"

            activa = bool(
                self.cleaned_data.get(
                    campo_activa,
                    False,
                )
            )

            modalidad = self.cleaned_data.get(
                campo_modalidad,
                DisponibilidadCuadrillaSemana.LUNES_VIERNES,
            )

            capacidad_diaria = self.cleaned_data.get(
                campo_capacidad,
                3,
            )

            resultado.append(
                {
                    "cuadrilla_operativa": cuadrilla,
                    "activa": activa,
                    "modalidad": modalidad,
                    "capacidad_diaria": int(capacidad_diaria),
                }
            )

        return resultado

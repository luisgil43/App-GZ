from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from planificacion.models import (BatchPlanificacionSemanal,
                                  PlanificacionMensual, SitioBatchSemanal,
                                  SitioPlanificado)
from planificacion.services.motor_batch_semanal.clustering import \
    detectar_clusters
from planificacion.services.motor_batch_semanal.cuadrillas import \
    construir_configuracion_cuadrilla
from planificacion.services.motor_batch_semanal.salidas import \
    encontrar_mejor_salida
from planificacion.services.motor_batch_semanal.tipos import SitioMotor

# ============================================================
# CONFIGURACIÓN
# ============================================================

OBJETIVO_SITIOS_POR_DIA = 3

HORA_LIMITE_INCORPORAR_DIA_ACTUAL = 12

ESTADOS_BATCH_NO_COMPLETABLES = {
    "cerrado",
    "cancelado",
}


ESTADOS_SITIO_NO_DISPONIBLES = {
    "completado",
    "cancelado",
    "bloqueado",
    "en_ejecucion",
    "en_ruta",
}


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(valor):

    if valor in (
        None,
        "",
    ):
        return None

    try:
        return float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _normalizar_tipo_zona(valor):
    return str(valor or "").strip().lower()


def _inicio_semana(fecha):
    return fecha - timedelta(
        days=fecha.weekday(),
    )


def _fin_semana(fecha_inicio):
    return fecha_inicio + timedelta(
        days=6,
    )


def _sitio_planificado_a_motor(
    sitio_planificado,
):
    sitio = sitio_planificado.sitio

    tipo_zona = _normalizar_tipo_zona(
        sitio.tipo_zona,
    )

    return SitioMotor(
        sitio_planificado_id=sitio_planificado.pk,
        sitio_id=sitio.pk,
        id_claro=(sitio.id_claro or sitio.id_sites or ""),
        nombre=sitio.nombre or "",
        comuna=sitio.comuna or "",
        tipo_zona=sitio.tipo_zona or "",
        latitud=_float_seguro(
            sitio.latitud,
        ),
        longitud=_float_seguro(
            sitio.longitud,
        ),
        condicion_acceso=(sitio.condiciones_acceso or ""),
        estado_permiso=(sitio_planificado.estado_permiso),
        prioridad=(sitio_planificado.prioridad),
        urbano=("urb" in tipo_zona),
        rural=("rural" in tipo_zona),
    )


# ============================================================
# SEMANA ACTUAL COMPLETABLE
# ============================================================


def obtener_batches_completables(
    *,
    planificacion_nueva,
    fecha_referencia=None,
    fecha_hora_referencia=None,
):
    """
    Devuelve las semanas operacionales que todavía pueden
    recibir sitios desde una planificación mensual nueva.

    REGLA OPERACIONAL
    ==========================================================

    Solamente pueden proponerse:

        1. la semana REAL actualmente vigente;
        2. la semana INMEDIATAMENTE SIGUIENTE.

    Nunca se muestran:

        - semanas vencidas;
        - semanas anteriores aunque hayan quedado incompletas;
        - semanas posteriores a la inmediatamente siguiente;
        - batches cerrados;
        - batches cancelados;
        - batches que ya alcanzaron su objetivo;
        - batches que ya no tienen capacidad operacional real;
        - batches sin candidatos disponibles en el nuevo mes.

    MUY IMPORTANTE
    ==========================================================

    Cuando ya se incorporaron sitios desde la planificación
    nueva, esos sitios deben descontarse de la recomendación
    operacional calculada.

    Ejemplo:

        objetivo batch = 45
        sitios originales = 40
        déficit original = 5

        quedan 4 de 5 días operativos

        recomendación temporal = 4

        si ya se incorporaron 4 sitios desde septiembre:

            pendiente = 4 - 4 = 0

        La semana deja de mostrarse como oportunidad aunque
        su cantidad física actual sea 44 de 45.

    De esta manera no volvemos a recomendar repetidamente
    después de que el usuario ya cumplió la recomendación
    correspondiente al momento operacional actual.
    """

    # ========================================================
    # FECHA / HORA DE REFERENCIA
    # ========================================================

    if fecha_hora_referencia is None:

        fecha_hora_referencia = timezone.localtime()

    else:

        if timezone.is_aware(
            fecha_hora_referencia,
        ):

            fecha_hora_referencia = timezone.localtime(
                fecha_hora_referencia,
            )

    if fecha_referencia is None:

        fecha_referencia = fecha_hora_referencia.date()

    # ========================================================
    # SEMANA REAL ACTUAL
    # ========================================================

    inicio_actual = _inicio_semana(
        fecha_referencia,
    )

    # ========================================================
    # SEMANA INMEDIATAMENTE SIGUIENTE
    # ========================================================

    inicio_siguiente = inicio_actual + timedelta(
        days=7,
    )

    fechas_inicio_admitidas = {
        inicio_actual,
        inicio_siguiente,
    }

    # ========================================================
    # BATCHES POSIBLES
    # ========================================================

    batches = (
        BatchPlanificacionSemanal.objects.filter(
            fecha_inicio__in=fechas_inicio_admitidas,
        )
        .exclude(
            estado__in=ESTADOS_BATCH_NO_COMPLETABLES,
        )
        .select_related(
            "configuracion_semana",
            "planificacion",
        )
        .prefetch_related(
            "planificaciones_origen",
            "sitios",
        )
        .order_by(
            "fecha_inicio",
            "id",
        )
    )

    resultado = []

    # ========================================================
    # EVALUAR CADA SEMANA
    # ========================================================

    for batch in batches:

        # ====================================================
        # FECHA FINAL REAL
        # ====================================================

        fecha_fin_batch = getattr(
            batch,
            "fecha_fin",
            None,
        )

        if fecha_fin_batch is None:

            fecha_fin_batch = _fin_semana(
                batch.fecha_inicio,
            )

        # ====================================================
        # NUNCA SEMANA VENCIDA
        # ====================================================

        if fecha_fin_batch < fecha_referencia:

            continue

        # ====================================================
        # SEGURIDAD:
        # SOLO SEMANA ACTUAL O SIGUIENTE
        # ====================================================

        if batch.fecha_inicio not in fechas_inicio_admitidas:

            continue

        # ====================================================
        # DEBE PERTENECER ORIGINALMENTE A OTRO MES
        # ====================================================

        tiene_otro_mes = batch.planificaciones_origen.exclude(
            pk=planificacion_nueva.pk,
        ).exists()

        pertenece_legacy_a_otro_mes = bool(
            batch.planificacion_id and batch.planificacion_id != planificacion_nueva.pk
        )

        if not tiene_otro_mes and not pertenece_legacy_a_otro_mes:

            continue

        # ====================================================
        # SITIOS YA INCORPORADOS DESDE EL MES NUEVO
        # ====================================================
        #
        # Estos NO deben provocar que recalculamos una nueva
        # recomendación desde cero.
        #
        # Necesitamos conocer cuánto tenía realmente el batch
        # antes de recibir aportes desde planificacion_nueva.
        # ====================================================

        cantidad_incorporada_desde_nuevo_mes = (
            SitioBatchSemanal.objects.filter(
                batch=batch,
                sitio_planificado__planificacion=(planificacion_nueva),
            )
            .exclude(
                estado__in=[
                    "excluido",
                    "reemplazado",
                    "rechazado",
                ],
            )
            .count()
        )

        # ====================================================
        # DIAGNÓSTICO ACTUAL
        # ====================================================

        diagnostico = diagnosticar_batch_completable(
            batch=batch,
            fecha_referencia=fecha_referencia,
            fecha_hora_referencia=(fecha_hora_referencia),
        )

        # ====================================================
        # RECONSTRUIR CANTIDAD ORIGINAL DEL BATCH
        # ====================================================
        #
        # Ejemplo:
        #
        # actual = 44
        # septiembre aportó = 4
        #
        # original = 40
        # ====================================================

        cantidad_original_batch = max(
            (
                diagnostico["cantidad_actual_batch"]
                - cantidad_incorporada_desde_nuevo_mes
            ),
            0,
        )

        objetivo_batch = max(
            int(diagnostico["objetivo_batch"] or 0),
            0,
        )

        # ====================================================
        # DÉFICIT ORIGINAL
        # ====================================================

        faltantes_originales = max(
            (objetivo_batch - cantidad_original_batch),
            0,
        )

        # ====================================================
        # SEMANA FUTURA
        # ====================================================
        #
        # Todavía no comenzó.
        #
        # La necesidad original es completar todo el déficit.
        # ====================================================

        if diagnostico["semana_futura"]:

            recomendacion_original = faltantes_originales

        # ====================================================
        # SEMANA ACTUAL
        # ====================================================
        #
        # Se reduce el déficit original según los días que
        # realmente permanecen disponibles.
        # ====================================================

        else:

            cantidad_dias_semana = int(diagnostico["cantidad_dias_semana"] or 0)

            cantidad_dias_restantes = int(diagnostico["cantidad_dias_restantes"] or 0)

            if (
                cantidad_dias_semana <= 0
                or cantidad_dias_restantes <= 0
                or faltantes_originales <= 0
            ):

                recomendacion_original = 0

            else:

                proporcion_restante = cantidad_dias_restantes / cantidad_dias_semana

                recomendacion_original = int(
                    round(faltantes_originales * proporcion_restante)
                )

                # ============================================
                # MÍNIMO DE 1 SI TODAVÍA EXISTE DÉFICIT
                # Y QUEDA AL MENOS UNA JORNADA.
                # ============================================

                if faltantes_originales > 0 and recomendacion_original <= 0:

                    recomendacion_original = 1

                recomendacion_original = min(
                    recomendacion_original,
                    faltantes_originales,
                )

        # ====================================================
        # RESTAR LO QUE YA APORTÓ EL NUEVO MES
        # ====================================================
        #
        # Este es el punto fundamental.
        #
        # objetivo original temporal = 4
        # ya incorporados = 4
        #
        # pendiente = 0
        # ====================================================

        sitios_a_incorporar = max(
            (recomendacion_original - cantidad_incorporada_desde_nuevo_mes),
            0,
        )

        # ====================================================
        # ACTUALIZAR DIAGNÓSTICO PARA LA INTERFAZ
        # ====================================================

        diagnostico["cantidad_original_batch"] = cantidad_original_batch

        diagnostico["faltantes_originales"] = faltantes_originales

        diagnostico["cantidad_incorporada_desde_nuevo_mes"] = (
            cantidad_incorporada_desde_nuevo_mes
        )

        diagnostico["recomendacion_original"] = recomendacion_original

        diagnostico["sitios_a_incorporar"] = sitios_a_incorporar

        # ====================================================
        # YA SE CUMPLIÓ LA RECOMENDACIÓN
        # ====================================================

        if sitios_a_incorporar <= 0:

            continue

        # ====================================================
        # DEBEN EXISTIR CANDIDATOS DEL NUEVO MES
        # ====================================================

        existen_candidatos = obtener_candidatos_planificacion_nueva(
            planificacion_nueva=(planificacion_nueva),
            batch_destino=batch,
        ).exists()

        if not existen_candidatos:

            continue

        # ====================================================
        # OPCIÓN VÁLIDA
        # ====================================================

        resultado.append(
            {
                "batch": batch,
                "diagnostico": diagnostico,
            }
        )

    return resultado


# ============================================================
# DÍAS OPERATIVOS SEMANA
# ============================================================


def obtener_dias_operativos_semana(
    *,
    batch,
):
    """
    Devuelve todos los días operativos definidos para el batch.

    Domingo nunca se considera operativo.

    El sábado solamente se considera cuando la configuración
    semanal indica que la operación trabaja sábado.
    """

    dias = []

    fecha = batch.fecha_inicio

    fin = batch.fecha_fin

    while fecha <= fin:

        # ====================================================
        # DOMINGO
        # ====================================================

        if fecha.weekday() == 6:

            fecha += timedelta(
                days=1,
            )

            continue

        # ====================================================
        # SÁBADO
        # ====================================================

        if fecha.weekday() == 5:

            trabaja_sabado = False

            if batch.configuracion_semana_id:

                trabaja_sabado = bool(batch.configuracion_semana.trabaja_sabado)

            if not trabaja_sabado:

                fecha += timedelta(
                    days=1,
                )

                continue

        dias.append(
            fecha,
        )

        fecha += timedelta(
            days=1,
        )

    return dias


# ============================================================
# DÍAS OPERATIVOS RESTANTES
# ============================================================


def obtener_dias_operativos_restantes(
    *,
    batch,
    fecha_referencia=None,
    fecha_hora_referencia=None,
):
    """
    Devuelve solamente los días operativos que todavía pueden
    utilizarse desde el momento actual hacia adelante.

    REGLA DE HORA
    ==========================================================

    Para una semana actualmente en curso:

    - antes de las 12:00, el día actual todavía se considera;
    - desde las 12:00, el día actual ya no se utiliza para
      incorporar nuevos sitios;
    - la capacidad comienza desde el siguiente día operativo.

    Esto evita que el sistema recomiende sitios a última hora
    del día como si todavía quedara disponible toda la jornada.

    Si la semana todavía no comienza, devuelve todos sus días
    operativos.
    """

    # ========================================================
    # FECHA / HORA DE REFERENCIA
    # ========================================================

    if fecha_hora_referencia is None:

        ahora = timezone.localtime()

    else:

        ahora = fecha_hora_referencia

        if timezone.is_aware(ahora):
            ahora = timezone.localtime(ahora)

    if fecha_referencia is None:

        fecha_referencia = ahora.date()

    # ========================================================
    # TODOS LOS DÍAS OPERATIVOS DE LA SEMANA
    # ========================================================

    dias_semana = obtener_dias_operativos_semana(
        batch=batch,
    )

    # ========================================================
    # SEMANA TODAVÍA FUTURA
    # ========================================================

    if batch.fecha_inicio > fecha_referencia:

        return dias_semana

    # ========================================================
    # DETERMINAR DESDE QUÉ FECHA SE PUEDE PLANIFICAR
    # ========================================================

    fecha_minima = fecha_referencia

    # ========================================================
    # SI YA PASÓ LA HORA LÍMITE DEL DÍA ACTUAL,
    # NO CONTAMOS HOY
    # ========================================================

    if ahora.hour >= HORA_LIMITE_INCORPORAR_DIA_ACTUAL:

        fecha_minima = fecha_referencia + timedelta(
            days=1,
        )

    # ========================================================
    # FILTRAR DÍAS REALMENTE DISPONIBLES
    # ========================================================

    return [fecha for fecha in dias_semana if fecha >= fecha_minima]


# ============================================================
# SITIOS RESIDUALES DEL BATCH
# ============================================================


def obtener_sitios_residuales_batch(
    batch,
):
    """
    Sitios todavía útiles del batch anterior.

    Excluye:
    - ejecutados;
    - cancelados;
    - bloqueados;
    - eliminados/reemplazados del batch.

    No exige permiso aprobado porque precisamente este
    análisis puede realizarse antes de completar la gestión
    de permisos de los nuevos sitios.
    """

    items = (
        SitioBatchSemanal.objects.filter(
            batch=batch,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
                "rechazado",
            ],
        )
        .exclude(
            sitio_planificado__estado__in=(ESTADOS_SITIO_NO_DISPONIBLES),
        )
        .select_related(
            "sitio_planificado",
            "sitio_planificado__sitio",
        )
        .order_by(
            "sitio_planificado__sitio__comuna",
            "sitio_planificado__sitio__id_claro",
            "id",
        )
    )

    return list(items)


# ============================================================
# DIAGNÓSTICO DEL BATCH
# ============================================================


def diagnosticar_batch_completable(
    *,
    batch,
    fecha_referencia=None,
    fecha_hora_referencia=None,
):
    """
    Determina cuánto puede recibir realmente una semana.

    REGLAS
    ==========================================================

    SEMANA FUTURA
    ----------------------------------------------------------

    Si todavía no comenzó:

        sitios_a_incorporar =
            objetivo semanal - cantidad actual

    porque todavía se dispone de toda la semana.

    SEMANA ACTUAL
    ----------------------------------------------------------

    Si ya comenzó:

    1. se determina cuántos días operativos tenía la semana;
    2. se determina cuántos días realmente quedan;
    3. el día actual deja de contar desde la hora límite;
    4. se calcula el déficit semanal original;
    5. ese déficit se reduce proporcionalmente según la parte
       de la semana operacional que todavía queda;
    6. jamás se supera el déficit original del batch.

    EJEMPLO
    ==========================================================

    Objetivo semanal:

        45

    Sitios actuales:

        40

    Déficit:

        5

    Semana lunes-viernes:

        5 días

    Lunes antes de las 12:00:

        quedan 5 / 5 días
        recomendación = 5

    Lunes después de las 12:00:

        quedan 4 / 5 días
        recomendación = 4

    Jueves antes de las 12:00:

        quedan 2 / 5 días
        recomendación = 2

    Jueves después de las 12:00:

        queda 1 / 5 día
        recomendación = 1
    """

    # ========================================================
    # FECHA / HORA DE REFERENCIA
    # ========================================================

    if fecha_hora_referencia is None:

        ahora = timezone.localtime()

    else:

        ahora = fecha_hora_referencia

        if timezone.is_aware(
            ahora,
        ):

            ahora = timezone.localtime(
                ahora,
            )

    if fecha_referencia is None:

        fecha_referencia = ahora.date()

    # ========================================================
    # TODOS LOS DÍAS OPERATIVOS DE LA SEMANA
    # ========================================================

    dias_operativos_semana = obtener_dias_operativos_semana(
        batch=batch,
    )

    cantidad_dias_semana = len(
        dias_operativos_semana,
    )

    # ========================================================
    # DÍAS OPERATIVOS QUE TODAVÍA QUEDAN
    # ========================================================

    dias_restantes = obtener_dias_operativos_restantes(
        batch=batch,
        fecha_referencia=fecha_referencia,
        fecha_hora_referencia=ahora,
    )

    cantidad_dias_restantes = len(
        dias_restantes,
    )

    # ========================================================
    # SITIOS RESIDUALES DEL BATCH
    # ========================================================

    sitios_residuales = obtener_sitios_residuales_batch(
        batch,
    )

    cantidad_residuales = len(
        sitios_residuales,
    )

    # ========================================================
    # CANTIDAD ACTUAL REAL DEL BATCH
    # ========================================================

    cantidad_actual_batch = (
        SitioBatchSemanal.objects.filter(
            batch=batch,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
                "rechazado",
            ],
        )
        .count()
    )

    # ========================================================
    # OBJETIVO SEMANAL ORIGINAL
    # ========================================================

    objetivo_batch = max(
        int(batch.objetivo_sitios or 0),
        0,
    )

    # ========================================================
    # DÉFICIT ABSOLUTO DEL BATCH
    # ========================================================

    faltantes_objetivo = max(
        objetivo_batch - cantidad_actual_batch,
        0,
    )

    # ========================================================
    # ESTADO TEMPORAL
    # ========================================================

    semana_futura = batch.fecha_inicio > fecha_referencia

    semana_iniciada = not semana_futura

    # ========================================================
    # CAPACIDAD PROMEDIO POR DÍA
    # ========================================================

    if objetivo_batch > 0 and cantidad_dias_semana > 0:

        capacidad_promedio_dia = objetivo_batch / cantidad_dias_semana

    else:

        capacidad_promedio_dia = 0.0

    # ========================================================
    # CAPACIDAD TEÓRICA DE LOS DÍAS RESTANTES
    # ========================================================

    capacidad_operacional_restante = int(
        round(capacidad_promedio_dia * cantidad_dias_restantes)
    )

    capacidad_operacional_restante = max(
        capacidad_operacional_restante,
        0,
    )

    # ========================================================
    # SEMANA FUTURA
    # ========================================================
    #
    # Todavía no comenzó.
    #
    # Se puede intentar completar todo el déficit original.
    # ========================================================

    if semana_futura:

        sitios_a_incorporar = faltantes_objetivo

    # ========================================================
    # SEMANA ACTUAL
    # ========================================================
    #
    # Ya comenzó.
    #
    # No utilizamos los sitios residuales para cancelar
    # automáticamente el déficit.
    #
    # Lo correcto es reducir el déficit proporcionalmente
    # según la cantidad de jornadas que todavía quedan.
    # ========================================================

    else:

        # ====================================================
        # NO QUEDA NINGÚN DÍA OPERATIVO
        # ====================================================

        if cantidad_dias_restantes <= 0 or cantidad_dias_semana <= 0:

            sitios_a_incorporar = 0

        # ====================================================
        # TODAVÍA QUEDAN JORNADAS
        # ====================================================

        else:

            proporcion_semana_restante = cantidad_dias_restantes / cantidad_dias_semana

            # ================================================
            # REDUCIR EL DÉFICIT SEGÚN LA PARTE DE SEMANA
            # QUE TODAVÍA ES OPERACIONALMENTE UTILIZABLE
            # ================================================

            faltantes_ajustados = int(
                round(faltantes_objetivo * proporcion_semana_restante)
            )

            # ================================================
            # SI EXISTE DÉFICIT Y TODAVÍA QUEDA AL MENOS
            # UN DÍA, CONSERVAMOS COMO MÍNIMO 1 OPCIÓN
            # ================================================
            #
            # Esto evita que un déficit pequeño desaparezca
            # únicamente por efecto del redondeo.
            # ================================================

            if faltantes_objetivo > 0 and faltantes_ajustados <= 0:

                faltantes_ajustados = 1

            # ================================================
            # NUNCA SUPERAR EL DÉFICIT ORIGINAL
            # ================================================

            sitios_a_incorporar = min(
                faltantes_objetivo,
                faltantes_ajustados,
            )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "batch_id": (batch.pk),
        "codigo_semana": (batch.codigo_semana),
        "fecha_inicio": (batch.fecha_inicio),
        "fecha_fin": (batch.fecha_fin),
        "objetivo_batch": (objetivo_batch),
        "cantidad_actual_batch": (cantidad_actual_batch),
        "faltantes_objetivo": (faltantes_objetivo),
        "dias_operativos_semana": (dias_operativos_semana),
        "cantidad_dias_semana": (cantidad_dias_semana),
        "dias_restantes": (dias_restantes),
        "cantidad_dias_restantes": (cantidad_dias_restantes),
        "capacidad_promedio_dia": (capacidad_promedio_dia),
        "capacidad_operacional_restante": (capacidad_operacional_restante),
        "capacidad_restante": (capacidad_operacional_restante),
        "sitios_residuales": (sitios_residuales),
        "cantidad_sitios_residuales": (cantidad_residuales),
        "sitios_a_incorporar": (sitios_a_incorporar),
        "objetivo_diario": (OBJETIVO_SITIOS_POR_DIA),
        "semana_iniciada": (semana_iniciada),
        "semana_futura": (semana_futura),
        "hora_referencia": (ahora.time()),
        "hora_limite_dia_actual": (HORA_LIMITE_INCORPORAR_DIA_ACTUAL),
    }


# ============================================================
# CANDIDATOS DEL MES NUEVO
# ============================================================


def obtener_candidatos_planificacion_nueva(
    *,
    planificacion_nueva,
    batch_destino,
):
    """
    Sitios del nuevo mes que todavía pueden transferirse
    operacionalmente al batch anterior.

    Conservan planificacion=planificacion_nueva.
    """

    ids_ya_en_batch = SitioBatchSemanal.objects.filter(
        batch=batch_destino,
    ).values_list(
        "sitio_planificado_id",
        flat=True,
    )

    return (
        SitioPlanificado.objects.filter(
            planificacion=planificacion_nueva,
            activo_en_mes=True,
        )
        .exclude(
            pk__in=ids_ya_en_batch,
        )
        .exclude(
            estado__in=ESTADOS_SITIO_NO_DISPONIBLES,
        )
        .select_related(
            "sitio",
        )
        .order_by(
            "sitio__comuna",
            "sitio__id_claro",
        )
    )


# ============================================================
# CALCULAR SCORE DE ACOMPAÑAMIENTO
# ============================================================


def _evaluar_combinacion(
    *,
    motores,
    disponibilidades,
):
    """
    Prueba la combinación en todas las cuadrillas
    compatibles y devuelve la mejor calidad encontrada.
    """

    mejor = None

    for disponibilidad in disponibilidades:

        configuracion = construir_configuracion_cuadrilla(disponibilidad)

        if not configuracion.get(
            "activa",
            False,
        ):
            continue

        calculo = encontrar_mejor_salida(
            sitios=motores,
            configuracion_cuadrilla=(configuracion),
        )

        if not calculo:
            continue

        viable = bool(
            calculo.get(
                "viable",
                False,
            )
        )

        jornada_extendida = bool(
            calculo.get(
                "jornada_extendida",
                False,
            )
        )

        minutos_total = int(
            calculo.get(
                "minutos_total",
                0,
            )
            or 0
        )

        minutos_viaje = int(
            calculo.get(
                "minutos_viaje",
                0,
            )
            or 0
        )

        score_salida = float(
            calculo.get(
                "score_salida",
                0,
            )
            or 0
        )

        clave = (
            viable,
            not jornada_extendida,
            score_salida,
            -minutos_total,
            -minutos_viaje,
        )

        if mejor is None or clave > mejor["clave"]:

            mejor = {
                "clave": clave,
                "calculo": calculo,
                "disponibilidad": (disponibilidad),
            }

    return mejor


# ============================================================
# RECOMENDAR SITIOS
# ============================================================


def generar_recomendacion_completar_semana(
    *,
    planificacion_nueva,
    batch_destino,
    cantidad=None,
    fecha_referencia=None,
    excluir_sitio_planificado_ids=None,
):
    """
    Genera una recomendación SIN guardar.

    Intenta completar grupos alrededor de los sitios residuales
    del batch anterior.

    La recomendación se basa en compatibilidad territorial
    y operativa.

    Cuando se analizan varias semanas simultáneamente,
    excluir_sitio_planificado_ids evita recomendar el mismo
    SitioPlanificado en más de una semana.
    """

    # ========================================================
    # EXCLUSIONES ENTRE SEMANAS
    # ========================================================

    excluir_ids = {
        int(valor)
        for valor in (excluir_sitio_planificado_ids or [])
        if str(valor).isdigit()
    }

    # ========================================================
    # DIAGNÓSTICO
    # ========================================================

    diagnostico = diagnosticar_batch_completable(
        batch=batch_destino,
        fecha_referencia=fecha_referencia,
    )

    cantidad_objetivo = (
        diagnostico["sitios_a_incorporar"]
        if cantidad is None
        else max(
            int(cantidad or 0),
            0,
        )
    )

    if cantidad_objetivo <= 0:

        return {
            "ok": True,
            "diagnostico": diagnostico,
            "recomendados": [],
            "cantidad_recomendada": 0,
            "grupos": [],
            "advertencias": [],
        }

    # ========================================================
    # CANDIDATOS
    # ========================================================

    candidatos_queryset = obtener_candidatos_planificacion_nueva(
        planificacion_nueva=planificacion_nueva,
        batch_destino=batch_destino,
    )

    if excluir_ids:

        candidatos_queryset = candidatos_queryset.exclude(
            pk__in=excluir_ids,
        )

    candidatos = list(candidatos_queryset)

    if not candidatos:

        return {
            "ok": True,
            "diagnostico": diagnostico,
            "recomendados": [],
            "cantidad_recomendada": 0,
            "grupos": [],
            "advertencias": [
                (
                    "La planificación nueva no posee "
                    "sitios disponibles para completar "
                    "esta semana."
                )
            ],
        }

    # ========================================================
    # CUADRILLAS DISPONIBLES
    # ========================================================

    disponibilidades = []

    if batch_destino.configuracion_semana_id:

        disponibilidades = list(
            batch_destino.configuracion_semana.disponibilidades_cuadrillas.select_related(
                "cuadrilla_operativa",
            ).filter(
                activa=True,
            )
        )

    if not disponibilidades:

        return {
            "ok": False,
            "diagnostico": diagnostico,
            "recomendados": [],
            "cantidad_recomendada": 0,
            "grupos": [],
            "advertencias": [
                "La semana no posee cuadrillas " "activas para evaluar combinaciones."
            ],
        }

    # ========================================================
    # SITIOS RESIDUALES
    # ========================================================

    residuales = diagnostico["sitios_residuales"]

    # ========================================================
    # PREPARAR CANDIDATOS
    # ========================================================

    motores_candidatos = {}

    candidatos_validos = []

    for candidato in candidatos:

        motor = _sitio_planificado_a_motor(
            candidato,
        )

        if motor.latitud is None or motor.longitud is None:
            continue

        motores_candidatos[candidato.pk] = motor

        candidatos_validos.append(candidato)

    seleccionados = []

    seleccionados_ids = set()

    grupos = []

    # ========================================================
    # PRIMERA PASADA
    # COMPLETAR SITIOS RESIDUALES
    # ========================================================

    for item_residual in residuales:

        if len(seleccionados) >= cantidad_objetivo:
            break

        motor_ancla = _sitio_planificado_a_motor(
            item_residual.sitio_planificado,
        )

        if motor_ancla.latitud is None or motor_ancla.longitud is None:
            continue

        restantes_necesarios = min(
            2,
            (cantidad_objetivo - len(seleccionados)),
        )

        if restantes_necesarios <= 0:
            break

        mejores = []

        candidatos_disponibles = [
            candidato
            for candidato in candidatos_validos
            if candidato.pk not in seleccionados_ids
        ]

        # ====================================================
        # BUSCAR PARES DE ACOMPAÑANTES
        # ====================================================

        if restantes_necesarios >= 2:

            for (
                indice,
                candidato_a,
            ) in enumerate(candidatos_disponibles):

                motor_a = motores_candidatos[candidato_a.pk]

                for candidato_b in candidatos_disponibles[indice + 1 :]:

                    motor_b = motores_candidatos[candidato_b.pk]

                    evaluacion = _evaluar_combinacion(
                        motores=[
                            motor_ancla,
                            motor_a,
                            motor_b,
                        ],
                        disponibilidades=(disponibilidades),
                    )

                    if not evaluacion:
                        continue

                    mejores.append(
                        {
                            "sitios": [
                                candidato_a,
                                candidato_b,
                            ],
                            "evaluacion": (evaluacion),
                        }
                    )

        # ====================================================
        # SI NO EXISTE PAR, PROBAR INDIVIDUAL
        # ====================================================

        if not mejores:

            for candidato in candidatos_disponibles:

                motor_candidato = motores_candidatos[candidato.pk]

                evaluacion = _evaluar_combinacion(
                    motores=[
                        motor_ancla,
                        motor_candidato,
                    ],
                    disponibilidades=(disponibilidades),
                )

                if not evaluacion:
                    continue

                mejores.append(
                    {
                        "sitios": [
                            candidato,
                        ],
                        "evaluacion": (evaluacion),
                    }
                )

        if not mejores:
            continue

        mejores.sort(
            key=lambda dato: (dato["evaluacion"]["clave"]),
            reverse=True,
        )

        ganador = mejores[0]

        agregados_grupo = []

        for candidato in ganador["sitios"]:

            if len(seleccionados) >= cantidad_objetivo:
                break

            if candidato.pk in seleccionados_ids:
                continue

            seleccionados.append(candidato)

            seleccionados_ids.add(candidato.pk)

            agregados_grupo.append(candidato)

        if agregados_grupo:

            grupos.append(
                {
                    "ancla": (item_residual.sitio_planificado),
                    "acompanantes": (agregados_grupo),
                    "calculo": (ganador["evaluacion"]["calculo"]),
                    "cuadrilla": (ganador["evaluacion"]["disponibilidad"]),
                }
            )

    # ========================================================
    # SEGUNDA PASADA
    # CLUSTERS DEL MES NUEVO
    # ========================================================

    faltantes = cantidad_objetivo - len(seleccionados)

    if faltantes > 0:

        motores_restantes = []

        mapa_motor = {}

        for candidato in candidatos_validos:

            if candidato.pk in seleccionados_ids:
                continue

            motor = motores_candidatos[candidato.pk]

            motores_restantes.append(motor)

            mapa_motor[motor.sitio_planificado_id] = candidato

        if motores_restantes:

            clusters = detectar_clusters(motores_restantes)

            for cluster in clusters:

                if len(seleccionados) >= cantidad_objetivo:
                    break

                sitios_cluster = getattr(
                    cluster,
                    "sitios",
                    None,
                )

                if sitios_cluster is None:

                    if isinstance(
                        cluster,
                        dict,
                    ):

                        sitios_cluster = cluster.get(
                            "sitios",
                            [],
                        )

                    else:

                        sitios_cluster = []

                for motor in sitios_cluster:

                    if len(seleccionados) >= cantidad_objetivo:
                        break

                    candidato = mapa_motor.get(motor.sitio_planificado_id)

                    if candidato is None:
                        continue

                    if candidato.pk in seleccionados_ids:
                        continue

                    seleccionados.append(candidato)

                    seleccionados_ids.add(candidato.pk)

    # ========================================================
    # SERIALIZAR RESULTADOS
    # ========================================================

    recomendados = []

    for sitio_planificado in seleccionados:

        sitio = sitio_planificado.sitio

        recomendados.append(
            {
                "sitio_planificado_id": (sitio_planificado.pk),
                "sitio_id": sitio.pk,
                "id_claro": (sitio.id_claro or sitio.id_sites or ""),
                "nombre": (sitio.nombre or ""),
                "region": (sitio.region or ""),
                "comuna": (sitio.comuna or ""),
                "tipo_zona": (sitio.tipo_zona or ""),
                "direccion": (
                    getattr(
                        sitio,
                        "direccion_proyecto",
                        "",
                    )
                    or sitio.direccion
                    or ""
                ),
                "condicion_acceso": (sitio.condiciones_acceso or ""),
            }
        )

    # ========================================================
    # ADVERTENCIAS
    # ========================================================

    advertencias = []

    if len(recomendados) < cantidad_objetivo:

        advertencias.append(
            (
                f"Se necesitaban aproximadamente "
                f"{cantidad_objetivo} sitio(s), "
                f"pero solo fue posible recomendar "
                f"{len(recomendados)} con información "
                "territorial utilizable."
            )
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "ok": True,
        "diagnostico": diagnostico,
        "recomendados": recomendados,
        "cantidad_recomendada": (len(recomendados)),
        "grupos": grupos,
        "advertencias": advertencias,
    }


# ============================================================
# CONFIRMAR TRANSFERENCIA OPERACIONAL
# ============================================================


@transaction.atomic
def confirmar_sitios_para_completar_semana(
    *,
    planificacion_nueva,
    batch_destino,
    sitio_planificado_ids,
    usuario,
):
    """
    Incorpora sitios del mes nuevo al batch anterior.

    MUY IMPORTANTE
    ==========================================================

    NO cambia:

        SitioPlanificado.planificacion

    El sitio continúa perteneciendo a su mes contractual.

    Solamente agregamos participación operacional en el batch.

    ESTADO DEL SITIO EN EL BATCH
    ==========================================================

    Si el sitio ya posee:

        aprobado
        no_requiere

    entra directamente como:

        disponible

    porque no necesita volver a pasar por gestión de permisos.

    Si todavía no posee permiso utilizable:

        SitioBatchSemanal -> gestion_permiso
        SitioPlanificado  -> gestionando_permiso
        permiso           -> por_solicitar

    Esto permite que los sitios transferidos con permiso
    aprobado sean inmediatamente visibles tanto en el batch
    semanal como para la planificación diaria.
    """

    # ========================================================
    # NORMALIZAR IDS
    # ========================================================

    ids = {int(valor) for valor in sitio_planificado_ids if str(valor).isdigit()}

    if not ids:

        raise ValueError("No se seleccionaron sitios.")

    # ========================================================
    # BLOQUEAR BATCH DESTINO
    # ========================================================

    batch_destino = BatchPlanificacionSemanal.objects.select_for_update().get(
        pk=batch_destino.pk,
    )

    # ========================================================
    # VALIDAR ESTADO DEL BATCH
    # ========================================================

    if batch_destino.estado in ESTADOS_BATCH_NO_COMPLETABLES:

        raise ValueError(
            ("La semana ya está cerrada o " "cancelada y no puede recibir sitios.")
        )

    # ========================================================
    # VALIDAR SEMANA OPERACIONAL
    # ========================================================

    hoy = timezone.localdate()

    inicio_actual = _inicio_semana(
        hoy,
    )

    inicio_siguiente = inicio_actual + timedelta(
        days=7,
    )

    if batch_destino.fecha_inicio not in {
        inicio_actual,
        inicio_siguiente,
    }:

        raise ValueError(
            (
                "La semana seleccionada ya no se "
                "encuentra disponible para recibir "
                "sitios desde esta planificación."
            )
        )

    # ========================================================
    # OBTENER SITIOS DEL MES NUEVO
    # ========================================================

    sitios = list(
        SitioPlanificado.objects.select_for_update()
        .filter(
            pk__in=ids,
            planificacion=planificacion_nueva,
            activo_en_mes=True,
        )
        .select_related(
            "sitio",
        )
    )

    if len(sitios) != len(ids):

        raise ValueError(
            (
                "Uno o más sitios seleccionados ya no "
                "están disponibles en la planificación "
                "mensual."
            )
        )

    # ========================================================
    # VINCULAR PLANIFICACIÓN ORIGEN
    # ========================================================

    batch_destino.planificaciones_origen.add(
        planificacion_nueva,
    )

    creados = []

    existentes = []

    # ========================================================
    # INCORPORAR SITIOS
    # ========================================================

    for sitio_planificado in sitios:

        # ====================================================
        # DETERMINAR SI YA POSEE PERMISO UTILIZABLE
        # ====================================================

        permiso_utilizable = sitio_planificado.estado_permiso in {
            "aprobado",
            "no_requiere",
        }

        # ====================================================
        # ESTADO CORRECTO DENTRO DEL BATCH
        # ====================================================

        if permiso_utilizable:

            estado_batch = "disponible"

        else:

            estado_batch = "gestion_permiso"

        # ====================================================
        # CREAR PARTICIPACIÓN EN SEMANA DESTINO
        # ====================================================

        item, creado = SitioBatchSemanal.objects.get_or_create(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
            defaults={
                "estado": estado_batch,
                "origen": "manual",
                "motivo_recomendacion": (
                    "Incorporado desde "
                    f"{planificacion_nueva} "
                    "para completar la semana "
                    "operacional anterior."
                ),
                "agregado_manualmente": True,
                "bloqueado_en_batch": True,
                "es_reserva": False,
                "agregado_por": usuario,
            },
        )

        # ====================================================
        # NUEVO
        # ====================================================

        if creado:

            creados.append(
                item,
            )

        # ====================================================
        # YA EXISTÍA
        # ====================================================

        else:

            existentes.append(
                item,
            )

            # ================================================
            # SINCRONIZAR ESTADO DEL ITEM EXISTENTE
            # ================================================
            #
            # Solamente tocamos estados administrativos
            # todavía compatibles con este flujo.
            #
            # Esto permite:
            #
            # gestion_permiso -> disponible
            #
            # cuando el permiso ya está aprobado.
            #
            # Y también:
            #
            # disponible -> gestion_permiso
            #
            # si el sitio todavía necesita permiso.
            #
            # No tocamos estados más avanzados o especiales.
            # ================================================

            if item.estado in {
                "gestion_permiso",
                "disponible",
            }:

                if item.estado != estado_batch:

                    item.estado = estado_batch

                    item.save(
                        update_fields=[
                            "estado",
                        ]
                    )

        # ====================================================
        # ACTUALIZAR SITIO PLANIFICADO
        # ====================================================

        if permiso_utilizable:

            # No alteramos el permiso.
            #
            # Ya es:
            #
            # aprobado
            # no_requiere

            if sitio_planificado.estado not in {
                "completado",
                "cancelado",
                "bloqueado",
                "en_ejecucion",
                "en_ruta",
            }:

                sitio_planificado.estado = "listo_planificar"

        else:

            # Los sitios incorporados que todavía no poseen
            # permiso deben entrar al flujo normal de gestión.

            sitio_planificado.estado_permiso = "por_solicitar"

            if sitio_planificado.estado not in {
                "completado",
                "cancelado",
                "bloqueado",
                "en_ejecucion",
                "en_ruta",
            }:

                sitio_planificado.estado = "gestionando_permiso"

        sitio_planificado.actualizado_por = usuario

        sitio_planificado.save(
            update_fields=[
                "estado_permiso",
                "estado",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "creados": creados,
        "existentes": existentes,
        "cantidad_creados": len(
            creados,
        ),
        "cantidad_existentes": len(
            existentes,
        ),
    }

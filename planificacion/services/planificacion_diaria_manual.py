# planificacion/services/planificacion_diaria_manual.py

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from planificacion.modelos import (SalidaPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
from planificacion.services.motor_batch_semanal.cuadrillas import \
    construir_configuracion_cuadrilla
from planificacion.services.motor_batch_semanal.salidas import \
    encontrar_mejor_salida
from planificacion.services.planificacion_diaria import _sitio_batch_a_motor

# ============================================================
# CONSTANTES
# ============================================================

ESTADOS_SITIO_SALIDA_COMPROMETIDOS = {
    "asignado",
    "en_ejecucion",
    "revision",
    "finalizado",
}


ESTADOS_SALIDA_COMPROMETIDOS = {
    "asignada",
    "en_ejecucion",
    "parcial",
    "finalizada",
}


ESTADOS_SITIO_BATCH_NO_DISPONIBLES = {
    "excluido",
    "reemplazado",
}


ESTADOS_PERMISO_VALIDOS = {
    "aprobado",
    "no_requiere",
}


CAPACIDAD_DIARIA_NORMAL = 3


# ============================================================
# IDENTIFICADOR
# ============================================================


def _identificador_sitio(
    sitio_batch,
):
    sitio = sitio_batch.sitio_planificado.sitio

    return sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"


# ============================================================
# DETECTAR SI EL SITIO ES RURAL
# ============================================================


def _sitio_es_rural(
    sitio_batch,
):
    sitio = sitio_batch.sitio_planificado.sitio

    tipo_zona = (
        getattr(
            sitio,
            "tipo_zona",
            "",
        )
        or ""
    )

    return "rural" in tipo_zona.lower()


# ============================================================
# DETECTAR SI CUADRILLA ADMITE RURAL
# ============================================================


def _cuadrilla_admite_rural(
    disponibilidad,
):
    """
    Intenta determinar la compatibilidad rural utilizando
    únicamente información ya existente en la disponibilidad
    y/o CuadrillaOperativa.

    No inventamos una regla nueva de base de datos.

    Si existe alguno de estos atributos booleanos, se respeta:

        permite_rural
        puede_rural
        habilitada_rural
        trabaja_rural

    También se revisa tipo_vehiculo como respaldo porque
    actualmente C1 utiliza 4x4 para rural.

    Si el proyecto ya posee una propiedad específica para esta
    regla, este helper es el único punto que necesitaremos
    ajustar posteriormente.
    """

    cuadrilla = disponibilidad.cuadrilla_operativa

    atributos_booleanos = [
        "permite_rural",
        "puede_rural",
        "habilitada_rural",
        "trabaja_rural",
    ]

    for objeto in [
        disponibilidad,
        cuadrilla,
    ]:
        for atributo in atributos_booleanos:

            if hasattr(
                objeto,
                atributo,
            ):
                valor = getattr(
                    objeto,
                    atributo,
                )

                if valor is not None:
                    return bool(valor)

    tipo_vehiculo = (
        getattr(
            disponibilidad,
            "tipo_vehiculo",
            "",
        )
        or ""
    ).lower()

    if "4x4" in tipo_vehiculo:
        return True

    # Si no existe información suficiente no bloqueamos aquí.
    # La validación estricta podrá conectarse después al campo
    # real de capacidad territorial de CuadrillaOperativa.

    return None


# ============================================================
# VALIDAR SITIO
# ============================================================


def validar_sitio_para_programacion_manual(
    *,
    batch,
    sitio_batch,
):
    """
    Valida únicamente condiciones estructurales del sitio.

    No crea ni modifica registros.
    """

    errores = []

    identificador = _identificador_sitio(
        sitio_batch,
    )

    # ========================================================
    # BATCH
    # ========================================================

    if sitio_batch.batch_id != batch.pk:
        errores.append(
            (
                f"{identificador}: el sitio no pertenece "
                "al batch semanal seleccionado."
            )
        )

    # ========================================================
    # ESTADO DEL SITIO EN EL BATCH
    # ========================================================

    if sitio_batch.estado in ESTADOS_SITIO_BATCH_NO_DISPONIBLES:
        errores.append(
            (
                f"{identificador}: el sitio se encuentra "
                f"en estado {sitio_batch.estado} y no puede "
                "ser programado."
            )
        )

    sitio_planificado = sitio_batch.sitio_planificado

    # ========================================================
    # PERMISO
    # ========================================================

    if sitio_planificado.estado_permiso not in ESTADOS_PERMISO_VALIDOS:
        errores.append(
            (
                f"{identificador}: el sitio no posee "
                "permiso aprobado para ser programado."
            )
        )

    # ========================================================
    # PARTICIPACIONES EXISTENTES
    # ========================================================

    participaciones = (
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch=sitio_batch,
        )
        .exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        )
        .select_related(
            "salida",
        )
    )

    for participacion in participaciones:

        if participacion.estado in ESTADOS_SITIO_SALIDA_COMPROMETIDOS:
            errores.append(
                (
                    f"{identificador}: el sitio ya posee "
                    "una participación diaria comprometida "
                    f"para el "
                    f"{participacion.salida.fecha:%d/%m/%Y}."
                )
            )

            break

    return errores


# ============================================================
# BUSCAR PARTICIPACIÓN EDITABLE EXISTENTE
# ============================================================


def obtener_participacion_editable_existente(
    *,
    sitio_batch,
):
    """
    Busca una planificación anterior que todavía puede ser
    reemplazada por la decisión manual.

    Nunca devuelve participaciones comprometidas.
    """

    return (
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch=sitio_batch,
        )
        .exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        )
        .exclude(
            estado__in=ESTADOS_SITIO_SALIDA_COMPROMETIDOS,
        )
        .exclude(
            salida__estado__in=ESTADOS_SALIDA_COMPROMETIDOS,
        )
        .select_related(
            "salida",
        )
        .order_by(
            "-actualizado_en",
            "-id",
        )
        .first()
    )


# ============================================================
# OBTENER SALIDA EXISTENTE DE LA CUADRILLA / FECHA
# ============================================================


def obtener_salida_destino_manual(
    *,
    batch,
    disponibilidad_cuadrilla,
    fecha,
):
    """
    Obtiene la ÚNICA jornada operacional existente para la
    misma cuadrilla y fecha.

    REGLA FUNDAMENTAL
    ==========================================================

    Una cuadrilla física solamente puede poseer una jornada
    operacional por fecha dentro del mismo batch.

    Por ejemplo:

        W36
        01/09/2026
        B1

    debe representarse como:

        una SalidaPlanificacionDiaria
            +
        varios SitioSalidaPlanificacionDiaria

    y NUNCA como:

        B1 -> salida #120
        B1 -> salida #123

    simplemente porque la primera salida ya fue asignada.

    SALIDA ASIGNADA
    ==========================================================

    Una salida cuyo estado es:

        asignada

    continúa siendo la jornada real de esa cuadrilla para ese
    día.

    Por tanto, si posteriormente se programa manualmente otro
    sitio para esa misma cuadrilla/fecha, debe incorporarse a
    ESA MISMA salida.

    Ejemplo:

        B1
        1. 05_416  asignado
        2. 05_031  asignado

    y posteriormente agregamos:

        05_409

    el resultado correcto es:

        B1
        1. 05_416  asignado
        2. 05_031  asignado
        3. 05_409  planificado

    Los dos servicios ya asignados NO cambian de estado.

    ESTADOS NO REUTILIZABLES
    ==========================================================

    No incorporamos nuevos sitios manualmente cuando la jornada
    ya se encuentra:

        cancelada
        en_ejecucion
        parcial
        finalizada

    porque en esos casos la ejecución operacional del día ya
    comenzó o alcanzó una etapa posterior.

    BLOQUEO
    ==========================================================

    Una salida bloqueada SÍ puede encontrarse aquí.

    El bloqueo existe para protegerla frente al motor
    automático, no para impedir una decisión manual explícita
    del usuario.

    Si por datos históricos existieran varias salidas para la
    misma cuadrilla/fecha, devolvemos la más antigua como
    jornada principal.

    Esa inconsistencia debe corregirse aparte; esta función
    evita seguir creando nuevas duplicadas.
    """

    return (
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
            disponibilidad_cuadrilla=disponibilidad_cuadrilla,
            fecha=fecha,
        )
        .exclude(
            estado__in=[
                "cancelada",
                "finalizada",
                "en_ejecucion",
                "parcial",
            ],
        )
        .order_by(
            "orden",
            "id",
        )
        .first()
    )


# ============================================================
# CONTAR SITIOS ACTIVOS DE UNA SALIDA
# ============================================================


def contar_sitios_activos_salida(
    salida,
):
    return salida.sitios.exclude(
        estado__in=[
            "retirado",
            "reprogramado",
            "cancelado",
        ],
    ).count()


# ============================================================
# ANALIZAR PROGRAMACIÓN MANUAL
# ============================================================


def analizar_programacion_manual(
    *,
    batch,
    sitio_batch,
    disponibilidad_cuadrilla,
    fecha,
):
    """
    Analiza la decisión antes de guardarla.

    Devuelve:

        errores
        advertencias
        requiere_confirmacion
        salida_existente
        cantidad_actual
        cantidad_resultante

    Este método NO modifica la base de datos.
    """

    errores = validar_sitio_para_programacion_manual(
        batch=batch,
        sitio_batch=sitio_batch,
    )

    advertencias = []

    identificador = _identificador_sitio(
        sitio_batch,
    )

    # ========================================================
    # DISPONIBILIDAD PERTENECE AL BATCH
    # ========================================================
    #
    # DisponibilidadCuadrillaSemana NO posee batch_id.
    #
    # La relación correcta es:
    #
    #   BatchPlanificacionSemanal.configuracion_semana
    #       ==
    #   DisponibilidadCuadrillaSemana.configuracion_semana
    #
    # ========================================================

    if not batch.configuracion_semana_id:

        errores.append(("El batch no posee una configuración " "semanal asociada."))

    elif (
        disponibilidad_cuadrilla.configuracion_semana_id
        != batch.configuracion_semana_id
    ):

        errores.append(
            (
                "La cuadrilla seleccionada no pertenece "
                "a la disponibilidad de este batch semanal."
            )
        )

    elif not disponibilidad_cuadrilla.activa:

        errores.append(
            ("La cuadrilla seleccionada no se encuentra " "activa durante esta semana.")
        )

    # ========================================================
    # RURAL
    # ========================================================

    sitio_rural = _sitio_es_rural(
        sitio_batch,
    )

    admite_rural = _cuadrilla_admite_rural(
        disponibilidad_cuadrilla,
    )

    if sitio_rural and admite_rural is False:
        errores.append(
            (
                f"{identificador}: el sitio es rural y "
                "la cuadrilla seleccionada no está habilitada "
                "para trabajo rural."
            )
        )

    elif sitio_rural and admite_rural is None:
        advertencias.append(
            (
                f"{identificador}: el sitio es rural y no fue "
                "posible confirmar automáticamente la capacidad "
                "rural de la cuadrilla seleccionada."
            )
        )

    # ========================================================
    # SALIDA DESTINO
    # ========================================================

    salida_existente = obtener_salida_destino_manual(
        batch=batch,
        disponibilidad_cuadrilla=disponibilidad_cuadrilla,
        fecha=fecha,
    )

    cantidad_actual = 0

    if salida_existente is not None:
        cantidad_actual = contar_sitios_activos_salida(
            salida_existente,
        )

    participacion_actual = obtener_participacion_editable_existente(
        sitio_batch=sitio_batch,
    )

    ya_esta_en_salida_destino = bool(
        participacion_actual is not None
        and salida_existente is not None
        and participacion_actual.salida_id == salida_existente.pk
    )

    cantidad_resultante = cantidad_actual

    if not ya_esta_en_salida_destino:
        cantidad_resultante += 1

    # ========================================================
    # CAPACIDAD
    # ========================================================

    capacidad_objetivo = (
        getattr(
            disponibilidad_cuadrilla,
            "capacidad_diaria_objetivo",
            None,
        )
        or CAPACIDAD_DIARIA_NORMAL
    )

    try:
        capacidad_objetivo = int(capacidad_objetivo)
    except (
        TypeError,
        ValueError,
    ):
        capacidad_objetivo = CAPACIDAD_DIARIA_NORMAL

    if cantidad_resultante > capacidad_objetivo:
        errores.append(
            (
                f"La cuadrilla ya posee {cantidad_actual} "
                f"sitio(s) el {fecha:%d/%m/%Y}. "
                f"Agregar {identificador} superaría la "
                f"capacidad diaria objetivo de "
                f"{capacidad_objetivo} sitio(s)."
            )
        )

    # ========================================================
    # SALIDA DE UN SOLO SITIO
    # ========================================================

    if cantidad_resultante == 1:
        advertencias.append(
            (
                f"{identificador} quedará como único sitio "
                f"de la cuadrilla el {fecha:%d/%m/%Y}. "
                "Operativamente las salidas de un solo sitio "
                "deben utilizarse únicamente como última opción."
            )
        )

    # ========================================================
    # SALIDA DE DOS SITIOS
    # ========================================================

    elif cantidad_resultante == 2:
        advertencias.append(
            (
                "La salida quedará con 2 sitios. "
                "El objetivo operacional normal es completar "
                "3 sitios por cuadrilla y día."
            )
        )

    # ========================================================
    # CAMBIO DESDE OTRA SALIDA
    # ========================================================

    if participacion_actual is not None and (
        salida_existente is None
        or participacion_actual.salida_id != salida_existente.pk
    ):
        advertencias.append(
            (
                f"{identificador} actualmente pertenece a "
                "otra salida diaria editable. "
                "La programación manual lo retirará de esa "
                "salida y lo moverá a la fecha/cuadrilla "
                "seleccionada."
            )
        )

    # ========================================================
    # JORNADA YA EXISTENTE
    # ========================================================

    if salida_existente is not None and cantidad_actual:
        advertencias.append(
            (
                f"La cuadrilla ya posee una salida con "
                f"{cantidad_actual} sitio(s) para "
                f"{fecha:%d/%m/%Y}. "
                f"{identificador} será incorporado a esa "
                "misma jornada."
            )
        )

    requiere_confirmacion = bool(advertencias)

    return {
        "errores": errores,
        "advertencias": advertencias,
        "requiere_confirmacion": requiere_confirmacion,
        "salida_existente": salida_existente,
        "participacion_actual": participacion_actual,
        "cantidad_actual": cantidad_actual,
        "cantidad_resultante": cantidad_resultante,
        "capacidad_objetivo": capacidad_objetivo,
        "sitio_rural": sitio_rural,
        "compatibilidad_rural": admite_rural,
    }


# ============================================================
# RECALCULAR ORDEN DE SITIOS
# ============================================================


def _reordenar_sitios_salida(
    salida,
):
    participaciones = list(
        salida.sitios.exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        ).order_by(
            "orden",
            "id",
        )
    )

    for numero, participacion in enumerate(
        participaciones,
        start=1,
    ):
        if participacion.orden != numero:

            participacion.orden = numero

            participacion.save(
                update_fields=[
                    "orden",
                    "actualizado_en",
                ]
            )


# ============================================================
# ACTUALIZAR MÉTRICAS REALES DE SALIDA MANUAL
# ============================================================


def _actualizar_metricas_basicas_salida(
    salida,
):
    """
    Recalcula las métricas reales de una salida manual.

    A diferencia de la implementación anterior, una salida
    manual NO puede conservar viaje=0 simplemente porque fue
    creada fuera del motor automático.

    Si conocemos:

        - los sitios;
        - sus coordenadas;
        - la cuadrilla;
        - la configuración operacional;

    utilizamos exactamente el mismo motor de rutas utilizado
    por Planificación Diaria.

    Se recalculan:

        - orden operacional;
        - minutos de viaje;
        - minutos de trabajo;
        - minutos totales;
        - distancia directa;
        - distancia vial estimada;
        - jornada extendida;
        - exceso de jornada.

    Si alguno de los sitios no posee coordenadas válidas y no
    puede realizarse el cálculo geográfico, se mantiene un
    fallback seguro basado exclusivamente en tiempo de trabajo.
    """

    # ========================================================
    # PARTICIPACIONES ACTIVAS
    # ========================================================

    participaciones = list(
        salida.sitios.exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        )
        .select_related(
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
        )
        .order_by(
            "orden",
            "id",
        )
    )

    cantidad = len(
        participaciones,
    )

    # ========================================================
    # SALIDA VACÍA
    # ========================================================

    if cantidad == 0:

        salida.minutos_viaje_estimados = 0

        salida.minutos_trabajo_estimados = 0

        salida.minutos_total_estimados = 0

        salida.distancia_directa_km = None

        salida.distancia_vial_estimada_km = None

        salida.jornada_extendida = False

        salida.exceso_jornada_minutos = 0

        return

    # ========================================================
    # CONSTRUIR UNIVERSO GEOGRÁFICO REAL
    # ========================================================

    motores = []

    coordenadas_validas = True

    mapa_participaciones = {}

    for participacion in participaciones:

        item_batch = participacion.sitio_batch

        motor = _sitio_batch_a_motor(
            item_batch,
        )

        if motor.latitud is None or motor.longitud is None:

            coordenadas_validas = False

            break

        motores.append(
            motor,
        )

        mapa_participaciones[motor.sitio_planificado_id] = participacion

    # ========================================================
    # CONFIGURACIÓN REAL DE LA CUADRILLA
    # ========================================================

    disponibilidad = salida.disponibilidad_cuadrilla

    calculo = None

    if coordenadas_validas and motores and disponibilidad is not None:

        configuracion = construir_configuracion_cuadrilla(
            disponibilidad,
        )

        calculo = encontrar_mejor_salida(
            sitios=motores,
            configuracion_cuadrilla=configuracion,
        )

    # ========================================================
    # CÁLCULO GEOGRÁFICO COMPLETO
    # ========================================================

    if calculo:

        sitios_ordenados = list(
            calculo.get(
                "sitios",
                [],
            )
            or []
        )

        # ====================================================
        # SINCRONIZAR ORDEN REAL DE LA RUTA
        # ====================================================

        for nuevo_orden, motor in enumerate(
            sitios_ordenados,
            start=1,
        ):

            participacion = mapa_participaciones.get(
                motor.sitio_planificado_id,
            )

            if participacion is None:
                continue

            if participacion.orden == nuevo_orden:
                continue

            participacion.orden = nuevo_orden

            participacion.save(
                update_fields=[
                    "orden",
                    "actualizado_en",
                ]
            )

        # ====================================================
        # MÉTRICAS DEL MOTOR
        # ====================================================

        salida.minutos_viaje_estimados = int(
            calculo.get(
                "minutos_viaje",
                0,
            )
            or 0
        )

        salida.minutos_trabajo_estimados = int(
            calculo.get(
                "minutos_trabajo",
                0,
            )
            or 0
        )

        salida.minutos_total_estimados = int(
            calculo.get(
                "minutos_total",
                0,
            )
            or 0
        )

        salida.distancia_directa_km = calculo.get(
            "distancia_directa_km",
        )

        salida.distancia_vial_estimada_km = calculo.get(
            "distancia_vial_estimada_km",
        )

        salida.jornada_extendida = bool(
            calculo.get(
                "jornada_extendida",
                False,
            )
        )

        salida.exceso_jornada_minutos = int(
            calculo.get(
                "exceso_jornada_minutos",
                0,
            )
            or 0
        )

        return

    # ========================================================
    # FALLBACK SIN CÁLCULO GEOGRÁFICO
    # ========================================================
    #
    # Solamente ocurre si:
    #
    # - falta alguna coordenada;
    # - no existe disponibilidad;
    # - o el motor no puede construir una ruta.
    #
    # En ese escenario NO inventamos viaje ni distancia.
    # ========================================================

    minutos_por_sitio = (
        getattr(
            disponibilidad,
            "minutos_trabajo_sitio_estimado",
            None,
        )
        if disponibilidad is not None
        else None
    ) or 180

    try:

        minutos_por_sitio = int(
            minutos_por_sitio,
        )

    except (
        TypeError,
        ValueError,
    ):

        minutos_por_sitio = 180

    minutos_trabajo = cantidad * minutos_por_sitio

    salida.minutos_trabajo_estimados = minutos_trabajo

    salida.minutos_total_estimados = (
        int(salida.minutos_viaje_estimados or 0) + minutos_trabajo
    )

    jornada_objetivo = (
        getattr(
            disponibilidad,
            "minutos_jornada_objetivo",
            None,
        )
        if disponibilidad is not None
        else None
    ) or 600

    try:

        jornada_objetivo = int(
            jornada_objetivo,
        )

    except (
        TypeError,
        ValueError,
    ):

        jornada_objetivo = 600

    exceso = max(
        0,
        (salida.minutos_total_estimados - jornada_objetivo),
    )

    salida.exceso_jornada_minutos = exceso

    salida.jornada_extendida = exceso > 0

# ============================================================
# LIMPIAR SALIDA ORIGEN VACÍA
# ============================================================


def _limpiar_salida_origen_si_corresponde(
    salida,
):
    """
    Si después de mover manualmente un sitio una salida editable
    queda completamente vacía, se elimina.

    Si todavía conserva sitios:

    - se reordena;
    - se recalculan viaje, trabajo y jornada;
    - se recalculan distancias;
    - se mantienen intactos sus demás datos.

    Nunca elimina una salida comprometida.
    """

    if salida is None:
        return False

    # ========================================================
    # SALIDA COMPROMETIDA
    # ========================================================

    if salida.estado in ESTADOS_SALIDA_COMPROMETIDOS:
        return False

    # ========================================================
    # SALIDA PROTEGIDA
    # ========================================================

    if salida.bloqueada:
        return False

    # ========================================================
    # CONTAR SITIOS ACTIVOS
    # ========================================================

    cantidad = contar_sitios_activos_salida(
        salida,
    )

    # ========================================================
    # TODAVÍA CONSERVA SITIOS
    # ========================================================

    if cantidad > 0:

        _reordenar_sitios_salida(
            salida,
        )

        _actualizar_metricas_basicas_salida(
            salida,
        )

        salida.save(
            update_fields=[
                "minutos_viaje_estimados",
                "minutos_trabajo_estimados",
                "minutos_total_estimados",
                "distancia_directa_km",
                "distancia_vial_estimada_km",
                "jornada_extendida",
                "exceso_jornada_minutos",
                "actualizado_en",
            ]
        )

        return False

    # ========================================================
    # SALIDA VACÍA
    # ========================================================

    salida.delete()

    return True


# ============================================================
# PROGRAMAR MANUALMENTE
# ============================================================


@transaction.atomic
def programar_sitio_manual(
    *,
    batch,
    sitio_batch,
    disponibilidad_cuadrilla,
    fecha,
    usuario=None,
    confirmar_excepcion=False,
    bloquear_salida=True,
    observaciones="",
):
    """
    Programa manualmente un sitio dentro de una fecha/cuadrilla.

    REGLAS
    ==========================================================

    1. El sitio debe pertenecer al batch.
    2. Debe tener permiso aprobado/no requiere.
    3. No puede existir una ejecución comprometida.
    4. La cuadrilla debe pertenecer a la semana.
    5. No se supera la capacidad diaria.
    6. Si existe una excepción, requiere confirmación.
    7. Si ya existe una salida editable para esa cuadrilla/día,
       se incorpora allí.
    8. Si no existe, se crea una salida manual.
    9. La decisión manual puede quedar bloqueada para evitar
       que un recálculo automático la elimine.
    10. No modifica Operaciones.

    MÉTRICAS
    ==========================================================

    Después de programar o mover manualmente el sitio se
    recalcula la salida completa utilizando el mismo motor
    geográfico de Planificación Diaria.

    Se recalculan:

        viaje;
        trabajo;
        jornada;
        distancia directa;
        distancia vial estimada;
        orden operacional;
        jornada extendida.

    BLOQUEOS SQL
    ==========================================================

    Los select_for_update() se ejecutan únicamente sobre
    la tabla concreta que necesitamos bloquear.

    NO combinamos select_for_update() con select_related()
    porque algunas relaciones pueden ser nullable y
    PostgreSQL no permite aplicar FOR UPDATE sobre el lado
    nullable de un OUTER JOIN.

    Las relaciones se cargan posteriormente mediante
    consultas normales independientes.
    """

    # ========================================================
    # BLOQUEAR SITIO BATCH
    # ========================================================

    sitio_batch = sitio_batch.__class__.objects.select_for_update().get(
        pk=sitio_batch.pk,
    )

    # ========================================================
    # CARGAR RELACIONES DEL SITIO FUERA DEL FOR UPDATE
    # ========================================================

    sitio_planificado = sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    batch_sitio = sitio_batch.batch

    sitio_planificado.pk

    sitio.pk

    batch_sitio.pk

    # ========================================================
    # BLOQUEAR DISPONIBILIDAD
    # ========================================================

    disponibilidad_cuadrilla = (
        disponibilidad_cuadrilla.__class__.objects.select_for_update().get(
            pk=disponibilidad_cuadrilla.pk,
        )
    )

    # ========================================================
    # CARGAR CUADRILLA FUERA DEL FOR UPDATE
    # ========================================================

    if disponibilidad_cuadrilla.cuadrilla_operativa_id:

        cuadrilla_operativa = disponibilidad_cuadrilla.cuadrilla_operativa

        cuadrilla_operativa.pk

    # ========================================================
    # ANÁLISIS
    # ========================================================

    analisis = analizar_programacion_manual(
        batch=batch,
        sitio_batch=sitio_batch,
        disponibilidad_cuadrilla=disponibilidad_cuadrilla,
        fecha=fecha,
    )

    if analisis["errores"]:

        raise ValidationError(
            analisis["errores"],
        )

    if analisis["requiere_confirmacion"] and not confirmar_excepcion:

        raise ValidationError(
            [
                (
                    "La programación requiere confirmación "
                    "de una excepción operacional."
                ),
                *analisis["advertencias"],
            ]
        )

    # ========================================================
    # PARTICIPACIÓN ACTUAL
    # ========================================================

    participacion_actual = analisis["participacion_actual"]

    salida_origen = None

    if participacion_actual is not None:

        # ====================================================
        # BLOQUEAR ÚNICAMENTE PARTICIPACIÓN
        # ====================================================

        participacion_actual = (
            SitioSalidaPlanificacionDiaria.objects.select_for_update().get(
                pk=participacion_actual.pk,
            )
        )

        # ====================================================
        # CARGAR SALIDA FUERA DEL FOR UPDATE
        # ====================================================

        salida_origen = participacion_actual.salida

    # ========================================================
    # SALIDA DESTINO
    # ========================================================

    salida_destino = analisis["salida_existente"]

    salida_creada = False

    if salida_destino is not None:

        salida_destino = SalidaPlanificacionDiaria.objects.select_for_update().get(
            pk=salida_destino.pk,
        )

    else:

        # ====================================================
        # BUSCAR ORDEN DISPONIBLE
        # ====================================================

        orden_salida = 0

        ordenes_existentes = SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
            disponibilidad_cuadrilla=(disponibilidad_cuadrilla),
            fecha=fecha,
        ).values_list(
            "orden",
            flat=True,
        )

        ordenes_existentes = set(
            ordenes_existentes,
        )

        while orden_salida in ordenes_existentes:

            orden_salida += 1

        # ====================================================
        # CREAR SALIDA
        # ====================================================

        salida_destino = SalidaPlanificacionDiaria.objects.create(
            batch=batch,
            disponibilidad_cuadrilla=(disponibilidad_cuadrilla),
            fecha=fecha,
            orden=orden_salida,
            estado="lista_asignar",
            origen="manual",
            bloqueada=bool(
                bloquear_salida,
            ),
            observaciones=(observaciones or ""),
            creado_por=usuario,
            actualizado_por=usuario,
        )

        salida_creada = True

    # ========================================================
    # MISMA SALIDA
    # ========================================================

    if (
        participacion_actual is not None
        and participacion_actual.salida_id == salida_destino.pk
    ):

        participacion_actual.origen = "manual"

        participacion_actual.bloqueado = bool(
            bloquear_salida,
        )

        if observaciones:

            participacion_actual.observaciones = observaciones

        participacion_actual.actualizado_por = usuario

        participacion_actual.save(
            update_fields=[
                "origen",
                "bloqueado",
                "observaciones",
                "actualizado_por",
                "actualizado_en",
            ]
        )

        # ====================================================
        # SALIDA
        # ====================================================

        salida_destino.origen = "manual"

        if bloquear_salida:

            salida_destino.bloqueada = True

        if observaciones:

            salida_destino.observaciones = observaciones

        salida_destino.actualizado_por = usuario

        # ====================================================
        # REORDENAR
        # ====================================================

        _reordenar_sitios_salida(
            salida_destino,
        )

        # ====================================================
        # RECALCULAR RUTA Y MÉTRICAS
        # ====================================================

        _actualizar_metricas_basicas_salida(
            salida_destino,
        )

        salida_destino.save(
            update_fields=[
                "origen",
                "bloqueada",
                "observaciones",
                "actualizado_por",
                "minutos_viaje_estimados",
                "minutos_trabajo_estimados",
                "minutos_total_estimados",
                "distancia_directa_km",
                "distancia_vial_estimada_km",
                "jornada_extendida",
                "exceso_jornada_minutos",
                "actualizado_en",
            ]
        )

        # ====================================================
        # SINCRONIZAR SITIO PLANIFICADO
        # ====================================================

        sitio_planificado = sitio_batch.sitio_planificado

        sitio_planificado.fecha_planificada = salida_destino.fecha

        sitio_planificado.orden_dia = participacion_actual.orden

        if sitio_planificado.estado not in {
            "completado",
            "cancelado",
            "bloqueado",
        }:

            sitio_planificado.estado = "planificado"

        sitio_planificado.planificado_manualmente = True

        sitio_planificado.actualizado_por = usuario

        sitio_planificado.save(
            update_fields=[
                "fecha_planificada",
                "orden_dia",
                "estado",
                "planificado_manualmente",
                "actualizado_por",
                "actualizado_en",
            ]
        )

        return {
            "salida": salida_destino,
            "sitio_salida": (participacion_actual),
            "salida_creada": False,
            "sitio_movido": False,
            "salida_origen_eliminada": False,
            "advertencias": (analisis["advertencias"]),
            "cantidad_sitios": (
                contar_sitios_activos_salida(
                    salida_destino,
                )
            ),
        }

    # ========================================================
    # RETIRAR PARTICIPACIÓN ANTERIOR EDITABLE
    # ========================================================

    sitio_movido = False

    if participacion_actual is not None:

        participacion_actual.estado = "reprogramado"

        participacion_actual.motivo_reprogramacion = (
            "Reprogramado manualmente desde " "Planificación Diaria."
        )

        participacion_actual.actualizado_por = usuario

        participacion_actual.save(
            update_fields=[
                "estado",
                "motivo_reprogramacion",
                "actualizado_por",
                "actualizado_en",
            ]
        )

        sitio_movido = True

    # ========================================================
    # ORDEN NUEVO
    # ========================================================

    ultimo_orden = (
        salida_destino.sitios.exclude(
            estado__in=[
                "retirado",
                "reprogramado",
                "cancelado",
            ],
        )
        .order_by(
            "-orden",
            "-id",
        )
        .values_list(
            "orden",
            flat=True,
        )
        .first()
    )

    nuevo_orden = int(ultimo_orden or 0) + 1

    # ========================================================
    # PARTICIPACIÓN DESTINO EXISTENTE
    # ========================================================

    participacion_destino_existente = (
        SitioSalidaPlanificacionDiaria.objects.select_for_update()
        .filter(
            salida=salida_destino,
            sitio_batch=sitio_batch,
        )
        .order_by(
            "-id",
        )
        .first()
    )

    # ========================================================
    # REACTIVAR PARTICIPACIÓN EXISTENTE
    # ========================================================

    if participacion_destino_existente is not None:

        sitio_salida = participacion_destino_existente

        sitio_salida.orden = nuevo_orden

        sitio_salida.estado = "planificado"

        sitio_salida.origen = "manual"

        sitio_salida.bloqueado = bool(
            bloquear_salida,
        )

        if (
            participacion_actual is not None
            and participacion_actual.pk != sitio_salida.pk
        ):

            sitio_salida.reprogramado_desde = participacion_actual

        sitio_salida.motivo_reprogramacion = (
            "Reincorporado manualmente a esta " "jornada desde Planificación Diaria."
        )

        sitio_salida.observaciones = observaciones or ""

        sitio_salida.actualizado_por = usuario

        sitio_salida.save(
            update_fields=[
                "orden",
                "estado",
                "origen",
                "bloqueado",
                "reprogramado_desde",
                "motivo_reprogramacion",
                "observaciones",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    # ========================================================
    # CREAR PARTICIPACIÓN NUEVA
    # ========================================================

    else:

        sitio_salida = SitioSalidaPlanificacionDiaria.objects.create(
            salida=salida_destino,
            sitio_batch=sitio_batch,
            orden=nuevo_orden,
            estado="planificado",
            origen="manual",
            bloqueado=bool(
                bloquear_salida,
            ),
            reprogramado_desde=(
                participacion_actual if participacion_actual is not None else None
            ),
            motivo_reprogramacion=(
                ("Programación manual desde " "Planificación Diaria.")
                if participacion_actual is not None
                else ""
            ),
            observaciones=(observaciones or ""),
            creado_por=usuario,
            actualizado_por=usuario,
        )

    # ========================================================
    # MARCAR SALIDA COMO MANUAL
    # ========================================================

    salida_destino.origen = "manual"

    if bloquear_salida:

        salida_destino.bloqueada = True

    if observaciones:

        salida_destino.observaciones = observaciones

    salida_destino.actualizado_por = usuario

    # ========================================================
    # REORDENAR
    # ========================================================

    _reordenar_sitios_salida(
        salida_destino,
    )

    # ========================================================
    # RECALCULAR RUTA Y MÉTRICAS
    # ========================================================

    _actualizar_metricas_basicas_salida(
        salida_destino,
    )

    salida_destino.save(
        update_fields=[
            "origen",
            "bloqueada",
            "observaciones",
            "actualizado_por",
            "minutos_viaje_estimados",
            "minutos_trabajo_estimados",
            "minutos_total_estimados",
            "distancia_directa_km",
            "distancia_vial_estimada_km",
            "jornada_extendida",
            "exceso_jornada_minutos",
            "actualizado_en",
        ]
    )

    # ========================================================
    # SITIO PLANIFICADO
    # ========================================================

    sitio_planificado = sitio_batch.sitio_planificado

    sitio_planificado.fecha_planificada = salida_destino.fecha

    sitio_planificado.orden_dia = sitio_salida.orden

    if sitio_planificado.estado not in {
        "completado",
        "cancelado",
        "bloqueado",
    }:

        sitio_planificado.estado = "planificado"

    sitio_planificado.planificado_manualmente = True

    sitio_planificado.actualizado_por = usuario

    sitio_planificado.save(
        update_fields=[
            "fecha_planificada",
            "orden_dia",
            "estado",
            "planificado_manualmente",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    # ========================================================
    # LIMPIAR / RECALCULAR SALIDA ANTERIOR
    # ========================================================

    salida_origen_eliminada = False

    if salida_origen is not None and salida_origen.pk != salida_destino.pk:

        salida_origen_eliminada = _limpiar_salida_origen_si_corresponde(
            salida_origen,
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "salida": salida_destino,
        "sitio_salida": sitio_salida,
        "salida_creada": salida_creada,
        "sitio_movido": sitio_movido,
        "salida_origen_eliminada": (salida_origen_eliminada),
        "advertencias": (analisis["advertencias"]),
        "cantidad_sitios": (
            contar_sitios_activos_salida(
                salida_destino,
            )
        ),
    }

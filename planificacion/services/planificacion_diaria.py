# planificacion/services/planificacion_diaria.py

from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from operaciones.models import ServicioCotizado
from planificacion.modelos import (SalidaPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal
from planificacion.services.motor_batch_semanal.clustering import \
    detectar_clusters
from planificacion.services.motor_batch_semanal.cuadrillas import \
    construir_configuracion_cuadrilla
from planificacion.services.motor_batch_semanal.orquestador import (
    ESTRATEGIA_OPERATIVA, construir_plan_operativo_semana)
from planificacion.services.motor_batch_semanal.salidas import \
    encontrar_mejor_salida
from planificacion.services.motor_batch_semanal.tipos import SitioMotor
from planificacion.services.prioridades_planificacion_diaria import (
    fecha_valida_para_prioridad, resolver_prioridades_batch,
    score_fecha_prioridad)

# ============================================================
# ESTADOS DEL BATCH DISPONIBLES PARA PLANIFICACIÓN DIARIA
# ============================================================

ESTADOS_BATCH_DISPONIBLES_DIARIO = {
    "disponible",
    "confirmado",
}


# ============================================================
# ESTADOS DEL SITIO PLANIFICADO QUE PUEDE TOMAR EL MOTOR
# ============================================================

ESTADOS_SITIO_PLANIFICACION_DISPONIBLES = {
    "listo_planificar",
    "planificado",
    "reprogramado",
}


# ============================================================
# SALIDAS EDITABLES POR EL MOTOR
# ============================================================

ESTADOS_SALIDA_EDITABLES = {
    "borrador",
    "lista_asignar",
}


# ============================================================
# ESTADOS DE PARTICIPACIÓN DIARIA EDITABLES
# ============================================================

ESTADOS_SITIO_SALIDA_EDITABLES = {
    "planificado",
    "listo_asignar",
}


# ============================================================
# ESTADOS DE PARTICIPACIÓN DIARIA COMPROMETIDOS
# ============================================================

ESTADOS_SITIO_SALIDA_COMPROMETIDOS = {
    "asignado",
    "en_ejecucion",
    "revision",
    "finalizado",
    "no_ejecutado",
    "reprogramado",
}


# ============================================================
# PARTICIPACIONES QUE OCUPAN ACTUALMENTE UN SITIO
# ============================================================

ESTADOS_SITIO_SALIDA_ACTIVOS = {
    "planificado",
    "listo_asignar",
    "asignado",
    "en_ejecucion",
    "revision",
}


# ============================================================
# OPERACIONES
# ============================================================

ESTADOS_OPERACIONES_FINALIZADOS = {
    "aprobado_supervisor",
    "finalizado",
}


ESTADOS_OPERACIONES_REVISION = {
    "en_revision_supervisor",
    "finalizado_trabajador",
}


ESTADOS_OPERACIONES_EJECUCION = {
    "en_progreso",
    "rechazado_supervisor",
}


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(
    valor,
):
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


def _normalizar_tipo_zona(
    valor,
):
    return str(valor or "").strip().lower()


def _sitio_batch_a_motor(
    item_batch,
):
    """
    Convierte SitioBatchSemanal en SitioMotor.
    """

    sitio_planificado = item_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    tipo_zona = _normalizar_tipo_zona(
        sitio.tipo_zona,
    )

    return SitioMotor(
        sitio_planificado_id=(sitio_planificado.pk),
        sitio_id=(sitio.pk),
        id_claro=(sitio.id_claro or sitio.id_sites or ""),
        nombre=(sitio.nombre or ""),
        comuna=(sitio.comuna or ""),
        tipo_zona=(sitio.tipo_zona or ""),
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
# SERIALIZAR PENDIENTE DE PROGRAMACIÓN
# ============================================================


def _serializar_pendiente_sin_salida(
    item_batch,
    *,
    motivo,
    codigo_motivo,
):
    sitio_planificado = item_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    return {
        "sitio_batch_id": (item_batch.pk),
        "sitio_planificado_id": (sitio_planificado.pk),
        "sitio_id": (sitio.pk),
        "id_claro": (sitio.id_claro or sitio.id_sites or ""),
        "nombre": (sitio.nombre or ""),
        "comuna": (sitio.comuna or ""),
        "tipo_zona": (sitio.tipo_zona or ""),
        "estado_permiso": (sitio_planificado.estado_permiso),
        "codigo_motivo": (codigo_motivo),
        "motivo": (motivo),
    }


# ============================================================
# DETERMINAR SI UNA SALIDA PUEDE SER RECALCULADA
# ============================================================


def salida_es_editable_por_motor(
    salida,
):
    if salida.estado not in ESTADOS_SALIDA_EDITABLES:
        return False

    if salida.bloqueada:
        return False

    sitios = list(salida.sitios.all())

    for sitio_salida in sitios:

        if sitio_salida.bloqueado:
            return False

        if sitio_salida.estado not in ESTADOS_SITIO_SALIDA_EDITABLES:
            return False

    return True


# ============================================================
# SALIDAS EDITABLES DEL BATCH
# ============================================================


def obtener_salidas_editables_batch(
    batch,
):
    candidatas = (
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
            estado__in=(ESTADOS_SALIDA_EDITABLES),
            bloqueada=False,
        )
        .prefetch_related(
            "sitios",
        )
        .order_by(
            "fecha",
            "orden",
            "id",
        )
    )

    return [salida for salida in candidatas if salida_es_editable_por_motor(salida)]


# ============================================================
# SALIDAS PROTEGIDAS / COMPROMETIDAS
# ============================================================


def obtener_salidas_protegidas_batch(
    batch,
):
    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
        )
        .prefetch_related(
            "sitios",
        )
        .order_by(
            "fecha",
            "orden",
            "id",
        )
    )

    return [salida for salida in salidas if not salida_es_editable_por_motor(salida)]


# ============================================================
# IDS PROTEGIDOS DEL BATCH
# ============================================================


def _ids_sitios_batch_protegidos(
    batch,
):
    ids = set()

    for salida in obtener_salidas_protegidas_batch(batch):

        for sitio_salida in salida.sitios.all():

            if sitio_salida.estado in {
                "retirado",
                "cancelado",
            }:
                continue

            ids.add(sitio_salida.sitio_batch_id)

    return ids


# ============================================================
# IDS QUE YA ESTÁN EN CUALQUIER SALIDA ACTIVA
# ============================================================


def _ids_sitios_batch_ya_programados(
    batch,
):
    return set(
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch__batch=batch,
            estado__in=(ESTADOS_SITIO_SALIDA_ACTIVOS),
        ).values_list(
            "sitio_batch_id",
            flat=True,
        )
    )


# ============================================================
# QUERY BASE DE SITIOS ELEGIBLES
# ============================================================


def _query_sitios_elegibles_planificacion_diaria(
    batch,
):
    return (
        SitioBatchSemanal.objects.filter(
            batch=batch,
            estado__in=(ESTADOS_BATCH_DISPONIBLES_DIARIO),
            sitio_planificado__activo_en_mes=True,
            sitio_planificado__estado_permiso__in=[
                "aprobado",
                "no_requiere",
            ],
            sitio_planificado__estado__in=(ESTADOS_SITIO_PLANIFICACION_DISPONIBLES),
        )
        .exclude(
            sitio_planificado__estado__in=[
                "completado",
                "cancelado",
                "bloqueado",
            ]
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


# ============================================================
# SITIOS DISPONIBLES PARA EL MOTOR DIARIO
# ============================================================


def obtener_sitios_disponibles_planificacion_diaria(
    batch,
):
    ids_protegidos = _ids_sitios_batch_protegidos(batch)

    items = _query_sitios_elegibles_planificacion_diaria(batch)

    resultado = []

    for item in items:

        if item.pk in ids_protegidos:
            continue

        resultado.append(item)

    return resultado


# ============================================================
# APROBADOS REALMENTE PENDIENTES DE PROGRAMAR
# ============================================================


def obtener_sitios_pendientes_planificacion_diaria(
    batch,
):
    ids_ya_programados = _ids_sitios_batch_ya_programados(batch)

    items = _query_sitios_elegibles_planificacion_diaria(batch)

    resultado = []

    for item in items:

        if item.pk in ids_ya_programados:
            continue

        resultado.append(item)

    return resultado


# ============================================================
# DISPONIBILIDADES DE CUADRILLAS
# ============================================================


def obtener_disponibilidades_planificacion_diaria(
    batch,
):
    if not batch.configuracion_semana_id:
        return []

    return list(
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


# ============================================================
# CONSTRUIR UNIVERSO MOTOR
# ============================================================


def construir_universo_diario(
    batch,
):
    items = obtener_sitios_disponibles_planificacion_diaria(batch)

    mapa_items = {}

    universo_motor = []

    sin_coordenadas = []

    for item in items:

        motor = _sitio_batch_a_motor(item)

        if motor.latitud is None or motor.longitud is None:

            sin_coordenadas.append(item)

            continue

        universo_motor.append(motor)

        mapa_items[motor.sitio_planificado_id] = item

    return {
        "items": (items),
        "universo_motor": (universo_motor),
        "mapa_items": (mapa_items),
        "sin_coordenadas": (sin_coordenadas),
    }


# ============================================================
# DÍAS POTENCIALES DE LA SEMANA
# ============================================================


def obtener_fechas_operacionales_batch(
    batch,
    *,
    incluir_hoy=False,
):
    """
    Devuelve los días operacionales utilizables por el motor.

    REGLA NORMAL
    ==========================================================

    Un batch dispone originalmente de:

        lunes
        martes
        miércoles
        jueves
        viernes
        sábado

    REGLA DURANTE LA SEMANA ACTUAL
    ==========================================================

    Si hoy pertenece al rango operacional del batch:

        días anteriores a hoy
            -> nunca disponibles

        hoy
            -> disponible solamente cuando incluir_hoy=True

        días posteriores
            -> disponibles normalmente

    SEMANAS FUTURAS
    ==========================================================

    Se devuelven completas.

    SEMANAS HISTÓRICAS
    ==========================================================

    Se conserva el comportamiento existente para no modificar
    retrospectivamente herramientas administrativas que puedan
    trabajar sobre semanas anteriores.
    """

    inicio = batch.fecha_inicio

    fechas = [
        inicio
        + timedelta(
            days=indice,
        )
        for indice in range(6)
    ]

    hoy = timezone.localdate()

    fecha_fin = inicio + timedelta(
        days=5,
    )

    # ========================================================
    # SOLO APLICAR RESTRICCIÓN EN LA SEMANA VIGENTE
    # ========================================================

    if not (inicio <= hoy <= fecha_fin):
        return fechas

    resultado = []

    for fecha in fechas:

        # ====================================================
        # PASADO
        # ====================================================

        if fecha < hoy:
            continue

        # ====================================================
        # HOY
        # ====================================================

        if fecha == hoy and not incluir_hoy:
            continue

        resultado.append(
            fecha,
        )

    return resultado


# ============================================================
# FECHAS VÁLIDAS PARA UNA CUADRILLA
# ============================================================


def _fechas_validas_disponibilidad(
    *,
    batch,
    disponibilidad,
    incluir_hoy=False,
):
    fechas = obtener_fechas_operacionales_batch(
        batch,
        incluir_hoy=incluir_hoy,
    )

    resultado = []

    for fecha in fechas:

        if fecha.weekday() == 6:
            continue

        if fecha.weekday() == 5 and not disponibilidad.trabaja_sabado:
            continue

        resultado.append(
            fecha,
        )

    return resultado


# ============================================================
# FECHAS YA OCUPADAS
# ============================================================


def _fechas_ocupadas_por_cuadrilla(
    batch,
):
    resultado = defaultdict(set)

    for salida in obtener_salidas_protegidas_batch(batch):

        if salida.estado == "cancelada":
            continue

        resultado[salida.disponibilidad_cuadrilla_id].add(salida.fecha)

    return resultado


# ============================================================
# SIGUIENTE FECHA LIBRE
# ============================================================


def _obtener_siguiente_fecha_libre(
    *,
    batch,
    disponibilidad,
    fechas_ocupadas,
    incluir_hoy=False,
):
    fechas_validas = _fechas_validas_disponibilidad(
        batch=batch,
        disponibilidad=disponibilidad,
        incluir_hoy=incluir_hoy,
    )

    ocupadas = fechas_ocupadas.get(
        disponibilidad.pk,
        set(),
    )

    for fecha in fechas_validas:

        if fecha in ocupadas:
            continue

        return fecha

    return None


# ============================================================
# FECHA PARA PRIORIDAD
# ============================================================


def _obtener_fecha_prioridad(
    *,
    batch,
    disponibilidad,
    prioridad,
    fechas_ocupadas,
    incluir_hoy=False,
):
    """
    Selecciona fecha para una prioridad.

    Respeta además la ventana temporal real del recálculo:

    - pasado de la semana actual: nunca;
    - hoy: solamente si incluir_hoy=True;
    - futuro: normalmente.
    """

    fechas_validas = _fechas_validas_disponibilidad(
        batch=batch,
        disponibilidad=disponibilidad,
        incluir_hoy=incluir_hoy,
    )

    ocupadas = fechas_ocupadas.get(
        disponibilidad.pk,
        set(),
    )

    candidatas = []

    for fecha in fechas_validas:

        if fecha in ocupadas:
            continue

        if not fecha_valida_para_prioridad(
            prioridad=prioridad,
            fecha=fecha,
        ):
            continue

        candidatas.append(
            fecha,
        )

    if not candidatas:
        return None

    candidatas.sort(
        key=lambda fecha: (
            -score_fecha_prioridad(
                prioridad=prioridad,
                fecha=fecha,
            ),
            fecha,
        )
    )

    return candidatas[0]


# ============================================================
# CONSTRUIR GRUPO MOTOR DE PRIORIDAD
# ============================================================


def _construir_grupo_motor_prioridad(
    propuesta,
):
    """
    Construye el grupo completo utilizado para calcular
    territorialmente una prioridad.

    Puede contener:

        ancla
        +
        sitios que ya estaban en la salida
        +
        nuevos acompañantes.

    El ancla se conserva siempre como primer elemento lógico.
    """

    ancla = propuesta["sitio_ancla"]

    items = [
        ancla,
    ]

    ids_agregados = {
        ancla.pk,
    }

    # ========================================================
    # SITIOS YA EXISTENTES
    # ========================================================

    for item in (
        propuesta.get(
            "sitios_existentes",
            [],
        )
        or []
    ):

        if item.pk in ids_agregados:
            continue

        items.append(item)

        ids_agregados.add(item.pk)

    # ========================================================
    # NUEVOS ACOMPAÑANTES
    # ========================================================

    for candidato in (
        propuesta.get(
            "acompanantes",
            [],
        )
        or []
    ):

        item = candidato.get("sitio_batch")

        if item is None:
            continue

        if item.pk in ids_agregados:
            continue

        items.append(item)

        ids_agregados.add(item.pk)

    motores = []

    for item in items:

        motor = _sitio_batch_a_motor(item)

        if motor.latitud is None or motor.longitud is None:

            return {
                "items": items,
                "motores": [],
                "valido": False,
            }

        motores.append(motor)

    return {
        "items": items,
        "motores": motores,
        "valido": bool(motores),
    }


# ============================================================
# CALCULAR MEJOR OPCIÓN PARA PRIORIDAD
# ============================================================


def _seleccionar_mejor_opcion_prioridad(
    *,
    batch,
    propuesta,
    fechas_ocupadas,
    incluir_hoy=False,
):
    """
    Selecciona fecha, cuadrilla y cálculo para una prioridad.

    Una salida protegida existente conserva exactamente su
    fecha y cuadrilla.

    Una prioridad que todavía debe programarse solamente puede
    utilizar fechas permitidas por la ventana temporal real.
    """

    prioridad = propuesta["prioridad"]

    grupo = _construir_grupo_motor_prioridad(
        propuesta,
    )

    if not grupo["valido"]:

        return {
            "opcion": None,
            "motivo": (
                f"{prioridad.id_claro}: "
                "no fue posible calcular la prioridad "
                "porque el ancla o alguno de sus "
                "acompañantes no posee coordenadas válidas."
            ),
        }

    # ========================================================
    # COMPLETAR SALIDA EXISTENTE
    # ========================================================

    if propuesta.get(
        "completar_salida_existente",
        False,
    ):

        salida_existente = propuesta.get(
            "salida_existente",
        )

        if salida_existente is None:

            return {
                "opcion": None,
                "motivo": (
                    f"{prioridad.id_claro}: "
                    "la prioridad indica que debe "
                    "completarse una salida existente, "
                    "pero no fue posible localizarla."
                ),
            }

        disponibilidad = salida_existente.disponibilidad_cuadrilla

        fecha = salida_existente.fecha

        configuracion = construir_configuracion_cuadrilla(
            disponibilidad,
        )

        calculo = encontrar_mejor_salida(
            sitios=grupo["motores"],
            configuracion_cuadrilla=configuracion,
        )

        if not calculo:

            return {
                "opcion": None,
                "motivo": (
                    f"{prioridad.id_claro}: "
                    "no fue posible calcular la ruta "
                    "de la salida protegida existente."
                ),
            }

        return {
            "opcion": {
                "prioridad": prioridad,
                "propuesta": propuesta,
                "disponibilidad": disponibilidad,
                "fecha": fecha,
                "calculo": calculo,
                "items": grupo["items"],
                "motores": grupo["motores"],
                "salida_existente": salida_existente,
                "completar_salida_existente": True,
            },
            "motivo": "",
        }

    # ========================================================
    # PRIORIDAD NORMAL
    # ========================================================

    disponibilidades_validas = (
        propuesta.get(
            "disponibilidades_validas",
            [],
        )
        or []
    )

    mejor = None

    mejor_clave = None

    for disponibilidad in disponibilidades_validas:

        fecha = _obtener_fecha_prioridad(
            batch=batch,
            disponibilidad=disponibilidad,
            prioridad=prioridad,
            fechas_ocupadas=fechas_ocupadas,
            incluir_hoy=incluir_hoy,
        )

        if fecha is None:
            continue

        configuracion = construir_configuracion_cuadrilla(
            disponibilidad,
        )

        calculo = encontrar_mejor_salida(
            sitios=grupo["motores"],
            configuracion_cuadrilla=configuracion,
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

        score_fecha = score_fecha_prioridad(
            prioridad=prioridad,
            fecha=fecha,
        )

        clave = (
            viable,
            not jornada_extendida,
            score_fecha,
            -minutos_total,
        )

        if mejor is None or clave > mejor_clave:

            mejor = {
                "prioridad": prioridad,
                "propuesta": propuesta,
                "disponibilidad": disponibilidad,
                "fecha": fecha,
                "calculo": calculo,
                "items": grupo["items"],
                "motores": grupo["motores"],
                "salida_existente": None,
                "completar_salida_existente": False,
            }

            mejor_clave = clave

    if mejor is None:

        if prioridad.fecha_es_obligatoria and prioridad.fecha_objetivo:

            motivo = (
                f"{prioridad.id_claro}: "
                "no existe una cuadrilla compatible "
                "con disponibilidad libre para la fecha "
                "obligatoria "
                f"{prioridad.fecha_objetivo:%d/%m/%Y}."
            )

        else:

            motivo = (
                f"{prioridad.id_claro}: "
                "no fue posible encontrar una fecha "
                "y cuadrilla disponibles para ejecutar "
                "la prioridad."
            )

        return {
            "opcion": None,
            "motivo": motivo,
        }

    return {
        "opcion": mejor,
        "motivo": "",
    }


# ============================================================
# CONVERTIR PRIORIDAD EN SALIDA DE RESULTADO
# ============================================================


def _construir_salida_resultado_prioridad(
    opcion,
):
    """
    Construye la salida final de una prioridad.

    REGLA CRÍTICA
    ==========================================================

    El sitio prioritario SIEMPRE se devuelve como orden 1.

    El cálculo de viaje puede haber encontrado internamente
    otra secuencia más eficiente para estimar distancia/tiempo,
    pero la jerarquía operacional de la planificación será:

        1. prioridad
        2. acompañante
        3. acompañante
    """

    prioridad = opcion["prioridad"]

    propuesta = opcion["propuesta"]

    disponibilidad = opcion["disponibilidad"]

    calculo = opcion["calculo"]

    items = list(opcion["items"])

    motores = list(opcion["motores"])

    mapa_motor = {motor.sitio_planificado_id: motor for motor in motores}

    # ========================================================
    # ANCLA SIEMPRE PRIMERO
    # ========================================================

    ancla = propuesta["sitio_ancla"]

    items_ordenados = [
        ancla,
    ]

    for item in items:

        if item.pk == ancla.pk:
            continue

        items_ordenados.append(item)

    sitios_salida = []

    for item in items_ordenados:

        motor = mapa_motor.get(item.sitio_planificado_id)

        if motor is None:
            continue

        sitios_salida.append(
            {
                "sitio_batch": item,
                "sitio_motor": motor,
            }
        )

    cantidad = len(sitios_salida)

    # ========================================================
    # IDS NUEVOS
    # ========================================================
    #
    # Cuando completamos una salida existente solamente
    # contabilizamos como nuevos los acompañantes.
    # ========================================================

    if opcion.get(
        "completar_salida_existente",
        False,
    ):

        sitios_nuevos_ids = {
            candidato["sitio_batch"].pk
            for candidato in (
                propuesta.get(
                    "acompanantes",
                    [],
                )
                or []
            )
        }

    else:

        sitios_nuevos_ids = {dato["sitio_batch"].pk for dato in sitios_salida}

    salida_existente = opcion.get("salida_existente")

    return {
        "fecha": opcion["fecha"],
        "disponibilidad": (disponibilidad),
        "cuadrilla": (disponibilidad.codigo_cuadrilla),
        "cuadrilla_nombre": (disponibilidad.nombre_cuadrilla),
        "cluster_id": (f"prioridad_{prioridad.pk}"),
        "sitios": sitios_salida,
        "cantidad_sitios": cantidad,
        "orden": list(
            range(
                1,
                cantidad + 1,
            )
        ),
        "minutos_viaje": int(
            calculo.get(
                "minutos_viaje",
                0,
            )
            or 0
        ),
        "minutos_trabajo": int(
            calculo.get(
                "minutos_trabajo",
                0,
            )
            or 0
        ),
        "minutos_total": int(
            calculo.get(
                "minutos_total",
                0,
            )
            or 0
        ),
        "distancia_directa_km": (calculo.get("distancia_directa_km")),
        "distancia_vial_estimada_km": (calculo.get("distancia_vial_estimada_km")),
        "jornada_extendida": bool(
            calculo.get(
                "jornada_extendida",
                False,
            )
        ),
        "exceso_jornada_minutos": int(
            calculo.get(
                "exceso_jornada_minutos",
                0,
            )
            or 0
        ),
        "puntaje_motor": (calculo.get("score_salida")),
        "es_prioridad": True,
        "prioridad_id": (prioridad.pk),
        "prioridad_codigo": (prioridad.prioridad),
        "prioridad_ancla_id": (prioridad.sitio_batch_id),
        "prioridad_motivo": (prioridad.motivo or ""),
        "completar_salida_existente": bool(
            opcion.get(
                "completar_salida_existente",
                False,
            )
        ),
        "salida_existente_id": (salida_existente.pk if salida_existente else None),
        "sitios_nuevos_ids": (sitios_nuevos_ids),
    }


# ============================================================
# PROCESAR PRIORIDADES PENDIENTES
# ============================================================


def _generar_salidas_prioritarias(
    *,
    batch,
    resolucion_prioridades,
    fechas_ocupadas,
    incluir_hoy=False,
):
    salidas = []

    ids_planificados = set()

    advertencias = []

    prioridades_pendientes_confirmacion = []

    for propuesta in resolucion_prioridades.get(
        "prioridades",
        [],
    ):

        prioridad = propuesta["prioridad"]

        if propuesta.get(
            "sin_cuadrilla_compatible",
            False,
        ):

            advertencias.append(
                (
                    f"{prioridad.id_claro}: "
                    "la prioridad no puede programarse "
                    "automáticamente porque ninguna "
                    "cuadrilla disponible es compatible."
                )
            )

            continue

        if propuesta.get(
            "requiere_confirmacion",
            False,
        ):

            prioridades_pendientes_confirmacion.append(
                propuesta,
            )

            advertencias.append(
                (
                    f"{prioridad.id_claro}: "
                    "la prioridad requiere confirmación "
                    "manual antes de completar o crear "
                    "la salida diaria."
                )
            )

            continue

        seleccion = _seleccionar_mejor_opcion_prioridad(
            batch=batch,
            propuesta=propuesta,
            fechas_ocupadas=fechas_ocupadas,
            incluir_hoy=incluir_hoy,
        )

        opcion = seleccion["opcion"]

        if opcion is None:

            if seleccion.get(
                "motivo",
            ):
                advertencias.append(
                    seleccion["motivo"],
                )

            continue

        calculo = opcion["calculo"]

        if not calculo.get(
            "viable",
            False,
        ):

            prioridades_pendientes_confirmacion.append(
                propuesta,
            )

            advertencias.append(
                (
                    f"{prioridad.id_claro}: "
                    "la combinación prioritaria excede "
                    "las condiciones operacionales "
                    "permitidas y requiere revisión manual."
                )
            )

            continue

        salida_resultado = _construir_salida_resultado_prioridad(
            opcion,
        )

        if not salida_resultado["sitios"]:
            continue

        salidas.append(
            salida_resultado,
        )

        for sitio_batch_id in salida_resultado.get(
            "sitios_nuevos_ids",
            set(),
        ):

            ids_planificados.add(
                sitio_batch_id,
            )

        fechas_ocupadas[opcion["disponibilidad"].pk].add(
            opcion["fecha"],
        )

    return {
        "salidas": salidas,
        "ids_planificados": ids_planificados,
        "advertencias": advertencias,
        "prioridades_pendientes_confirmacion": (prioridades_pendientes_confirmacion),
    }


# ============================================================
# CONSTRUIR PENDIENTES DEL RESULTADO
# ============================================================


def _construir_pendientes_resultado(
    *,
    items,
    ids_planificados,
    sin_coordenadas,
    ids_prioridad_reservados=None,
):
    ids_sin_coordenadas = {item.pk for item in sin_coordenadas}

    ids_prioridad_reservados = set(ids_prioridad_reservados or [])

    resultado = []

    for item in items:

        if item.pk in ids_planificados:
            continue

        if item.pk in ids_sin_coordenadas:

            motivo = (
                "El sitio no posee coordenadas válidas "
                "para construir una ruta operacional."
            )

            codigo = "sin_coordenadas"

        elif item.pk in ids_prioridad_reservados:

            motivo = (
                "El sitio forma parte de una prioridad "
                "diaria pendiente de resolución o "
                "confirmación operacional."
            )

            codigo = "prioridad_pendiente"

        else:

            motivo = (
                "Actualmente no existe una combinación "
                "operacional suficientemente conveniente "
                "para incorporarlo a una salida. "
                "El sitio permanece aprobado y pendiente "
                "para un próximo recálculo."
            )

            codigo = "sin_combinacion_operacional"

        resultado.append(
            _serializar_pendiente_sin_salida(
                item,
                motivo=motivo,
                codigo_motivo=codigo,
            )
        )

    return resultado


# ============================================================
# ADVERTENCIA DE PENDIENTES
# ============================================================


def _agregar_advertencia_pendientes(
    *,
    advertencias,
    pendientes,
):
    if not pendientes:
        return

    identificadores = [
        pendiente["id_claro"] for pendiente in pendientes if pendiente.get("id_claro")
    ]

    texto_ids = ", ".join(identificadores)

    if texto_ids:

        advertencias.append(
            (
                f"{len(pendientes)} sitio(s) "
                "aprobado(s) quedaron pendientes "
                "de programación diaria: "
                f"{texto_ids}."
            )
        )

    else:

        advertencias.append(
            (
                f"{len(pendientes)} sitio(s) "
                "aprobado(s) quedaron pendientes "
                "de programación diaria."
            )
        )


# ============================================================
# PLAN OPERATIVO VACÍO
# ============================================================


def _plan_operativo_vacio():
    return {
        "estrategia": (ESTRATEGIA_OPERATIVA),
        "objetivo": 0,
        "sitios": [],
        "sitio_ids": [],
        "cantidad_sitios": 0,
        "faltantes_objetivo": 0,
        "salidas": [],
        "total_salidas": 0,
        "salidas_3_sitios": 0,
        "salidas_2_sitios": 0,
        "salidas_1_sitio": 0,
        "salidas_jornada_extendida": 0,
        "minutos_extension_total": 0,
        "promedio_sitios_salida": 0,
        "minutos_viaje": 0,
        "minutos_total": 0,
        "distancia_directa_km": 0,
        "distancia_vial_estimada_km": 0,
        "score_cobertura": 0,
        "score_aprovechamiento": 0,
        "score_residuales": 0,
        "score_remanente": 0,
        "score_equidad_cuadrillas": 0,
        "score_operativo": 0,
        "clusters_utilizados": 0,
        "salidas_por_cuadrilla": {},
        "cupos_por_cuadrilla": {},
        "utilizacion_por_cuadrilla": {},
        "cuadrillas_sin_trabajo": [],
    }

# ============================================================
# SELECCIONAR FECHA / CUADRILLA MÁS TEMPRANA PARA SALIDA REGULAR
# ============================================================


def _seleccionar_asignacion_regular_mas_temprana(
    *,
    batch,
    salida_motor,
    disponibilidades,
    fechas_ocupadas,
    incluir_hoy=False,
):
    """
    Recibe una salida/grupo ya construido por el orquestador
    y decide dónde colocarla realmente en el calendario.

    La fecha más temprana disponible sigue siendo la regla
    principal, pero durante la semana vigente:

    - nunca se utilizan días anteriores;
    - hoy solamente puede utilizarse cuando incluir_hoy=True.
    """

    motores_originales = list(
        salida_motor.get(
            "sitios",
            [],
        )
        or []
    )

    if not motores_originales:

        return {
            "opcion": None,
            "motivo": (
                "La salida no contiene sitios "
                "para distribuir."
            ),
        }

    todos_urbanos = all(
        bool(
            getattr(
                sitio,
                "urbano",
                False,
            )
        )
        and not bool(
            getattr(
                sitio,
                "rural",
                False,
            )
        )
        for sitio in motores_originales
    )

    contiene_rural = any(
        bool(
            getattr(
                sitio,
                "rural",
                False,
            )
        )
        for sitio in motores_originales
    )

    mejor = None

    mejor_clave = None

    for disponibilidad in disponibilidades:

        fecha = _obtener_siguiente_fecha_libre(
            batch=batch,
            disponibilidad=disponibilidad,
            fechas_ocupadas=fechas_ocupadas,
            incluir_hoy=incluir_hoy,
        )

        if fecha is None:
            continue

        configuracion = construir_configuracion_cuadrilla(
            disponibilidad,
        )

        if not configuracion.get(
            "activa",
            False,
        ):
            continue

        calculo = encontrar_mejor_salida(
            sitios=motores_originales,
            configuracion_cuadrilla=configuracion,
        )

        if not calculo:
            continue

        if not calculo.get(
            "viable",
            False,
        ):
            continue

        permite_urbano = bool(
            configuracion.get(
                "permite_urbano",
                False,
            )
        )

        permite_rural = bool(
            configuracion.get(
                "permite_rural",
                False,
            )
        )

        if (
            todos_urbanos
            and permite_urbano
            and not permite_rural
        ):

            prioridad_especializacion = 0

        elif (
            contiene_rural
            and permite_rural
        ):

            prioridad_especializacion = 0

        else:

            prioridad_especializacion = 1

        orden_cuadrilla = (
            getattr(
                disponibilidad.cuadrilla_operativa,
                "orden",
                9999,
            )
            if disponibilidad.cuadrilla_operativa_id
            else 9999
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

        jornada_extendida = bool(
            calculo.get(
                "jornada_extendida",
                False,
            )
        )

        clave = (
            fecha,
            prioridad_especializacion,
            jornada_extendida,
            minutos_total,
            minutos_viaje,
            orden_cuadrilla,
            disponibilidad.pk,
        )

        if (
            mejor is None
            or clave < mejor_clave
        ):

            mejor = {
                "fecha": fecha,
                "disponibilidad": disponibilidad,
                "configuracion": configuracion,
                "calculo": calculo,
            }

            mejor_clave = clave

    if mejor is None:

        identificadores = [
            (
                getattr(
                    sitio,
                    "id_claro",
                    "",
                )
                or str(
                    getattr(
                        sitio,
                        "sitio_planificado_id",
                        "",
                    )
                )
            )
            for sitio in motores_originales
        ]

        texto = ", ".join(
            identificador
            for identificador in identificadores
            if identificador
        )

        return {
            "opcion": None,
            "motivo": (
                "No existe una cuadrilla compatible con un día "
                "operacional libre para la salida"
                + (
                    f" formada por: {texto}."
                    if texto
                    else "."
                )
            ),
        }

    return {
        "opcion": mejor,
        "motivo": "",
    }

# ============================================================
# ACTUALIZAR DISTRIBUCIÓN REAL DEL PLAN OPERATIVO
# ============================================================


def _actualizar_distribucion_real_plan_operativo(
    *,
    plan_operativo,
    salidas_resultado,
    disponibilidades,
):
    """
    Corrige las métricas de distribución del orquestador para
    que reflejen la cuadrilla y fecha REAL finalmente asignadas.

    Esto es necesario porque:

        orquestador.py
            decide agrupaciones

    mientras:

        planificacion_diaria.py
            decide finalmente fecha/cuadrilla.

    Después de esta etapa no queremos seguir mostrando:

        C1 -> 1
        C2 -> 3
        C3 -> 5

    si la distribución final terminó siendo, por ejemplo:

        C1 -> 3
        C2 -> 3
        C3 -> 3
    """

    if plan_operativo is None:
        return

    # ========================================================
    # CUPOS
    # ========================================================

    cupos = {}

    nombres = {}

    for disponibilidad in disponibilidades:

        codigo = disponibilidad.codigo_cuadrilla

        try:
            dias = int(disponibilidad.dias_disponibles or 0)

        except (
            TypeError,
            ValueError,
        ):
            dias = 0

        cupos[codigo] = max(
            dias,
            0,
        )

        nombres[codigo] = disponibilidad.nombre_cuadrilla

    # ========================================================
    # CONTAR SOLAMENTE SALIDAS REGULARES DEL MOTOR
    # ========================================================

    usadas = {codigo: 0 for codigo in cupos}

    for salida in salidas_resultado:

        if salida.get(
            "es_prioridad",
            False,
        ):
            continue

        codigo = salida.get(
            "cuadrilla",
        )

        if codigo not in usadas:
            usadas[codigo] = 0

        usadas[codigo] += 1

    # ========================================================
    # UTILIZACIÓN
    # ========================================================

    utilizacion = {}

    for codigo, cupo in cupos.items():

        cantidad = usadas.get(
            codigo,
            0,
        )

        if cupo:

            porcentaje = cantidad / cupo * 100

        else:

            porcentaje = 0.0

        utilizacion[codigo] = round(
            porcentaje,
            2,
        )

    # ========================================================
    # SIN TRABAJO
    # ========================================================

    cuadrillas_sin_trabajo = [
        codigo
        for codigo, cupo in cupos.items()
        if (
            cupo > 0
            and usadas.get(
                codigo,
                0,
            )
            == 0
        )
    ]

    # ========================================================
    # EQUIDAD DE DIAGNÓSTICO
    # ========================================================

    porcentajes = [utilizacion[codigo] for codigo, cupo in cupos.items() if cupo > 0]

    if porcentajes:

        diferencia = max(porcentajes) - min(porcentajes)

        score_equidad = max(
            100.0 - diferencia,
            0.0,
        )

    else:

        score_equidad = 0.0

    # ========================================================
    # ACTUALIZAR PLAN
    # ========================================================

    plan_operativo["salidas_por_cuadrilla"] = usadas

    plan_operativo["cupos_por_cuadrilla"] = cupos

    plan_operativo["utilizacion_por_cuadrilla"] = utilizacion

    plan_operativo["cuadrillas_sin_trabajo"] = cuadrillas_sin_trabajo

    plan_operativo["score_equidad_cuadrillas"] = round(
        score_equidad,
        2,
    )


# ============================================================
# PROPUESTA OPERACIONAL DIARIA
# ============================================================


def generar_plan_diario_batch(
    *,
    batch,
    estrategia=ESTRATEGIA_OPERATIVA,
    incluir_hoy=False,
):
    """
    Genera la propuesta diaria SIN guardar.

    ARQUITECTURA
    ==========================================================

    El motor trabaja ahora en dos niveles claramente separados.

    NIVEL 1 - AGRUPACIÓN
    ----------------------------------------------------------

    orquestador.py decide:

        qué sitios conviene ejecutar juntos.

    Ejemplo:

        05_761
        05_040
        05_098

    forman una buena terna.

    NIVEL 2 - CALENDARIO REAL
    ----------------------------------------------------------

    planificacion_diaria.py decide:

        qué cuadrilla;
        qué fecha.

    La cuadrilla elegida originalmente por el orquestador NO
    queda amarrada al grupo.

    JERARQUÍA DE CALENDARIO
    ==========================================================

    La regla principal es:

        utilizar primero el día operacional más temprano.

    Por ejemplo:

        C1 jueves disponible
        C3 viernes disponible

    si ambas cuadrillas pueden ejecutar la misma terna:

        gana C1 jueves.

    Nunca queremos:

        C1 jueves vacío
        C3 viernes con 3 sitios

    cuando esos 3 sitios son compatibles con C1.

    COMPACTACIÓN
    ==========================================================

    Las salidas del orquestador se procesan explícitamente:

        3 sitios
        2 sitios
        1 sitio

    en ese orden.

    De esta forma intentamos llenar primero jornadas completas
    antes de consumir nuevos vehículos/días con salidas
    parciales.

    TIEMPO
    ==========================================================

    Una terna puede exceder la jornada configurada.

    El exceso NO impide su programación.

    Se conserva:

        jornada_extendida
        exceso_jornada_minutos

    para mostrar la advertencia correspondiente al usuario.

    PRIORIDADES
    ==========================================================

    Las prioridades continúan procesándose antes del motor
    regular.

    Las prioridades ya satisfechas o protegidas continúan
    respetándose.

    IMPORTANTE
    ==========================================================

    Este flujo pertenece exclusivamente a PLANIFICACIÓN DIARIA.

    No modifica:

        selección mensual;
        batch semanal;
        Operaciones.
    """

    datos = construir_universo_diario(batch)

    items = datos["items"]

    sin_coordenadas = datos["sin_coordenadas"]

    disponibilidades = obtener_disponibilidades_planificacion_diaria(batch)

    total_disponibles = len(items)

    advertencias = []

    # ========================================================
    # SIN SITIOS ELEGIBLES
    # ========================================================

    if not items:

        return {
            "batch_id": batch.pk,
            "sitios_disponibles": 0,
            "sitios_planificados": 0,
            "faltantes": 0,
            "salidas": [],
            "pendientes_sin_salida": [],
            "advertencias": [
                (
                    "No existen sitios aprobados "
                    "disponibles para generar nuevas "
                    "salidas diarias."
                )
            ],
            "prioridades": {
                "pendientes": [],
                "satisfechas": [],
                "incumplidas": [],
                "pendientes_confirmacion": [],
                "sitios_reservados_ids": set(),
            },
            "plan_operativo": (_plan_operativo_vacio()),
        }

    # ========================================================
    # SIN CUADRILLAS
    # ========================================================

    if not disponibilidades:

        pendientes = _construir_pendientes_resultado(
            items=items,
            ids_planificados=set(),
            sin_coordenadas=sin_coordenadas,
        )

        advertencias.append(("No existen cuadrillas activas " "para esta semana."))

        _agregar_advertencia_pendientes(
            advertencias=advertencias,
            pendientes=pendientes,
        )

        return {
            "batch_id": batch.pk,
            "sitios_disponibles": total_disponibles,
            "sitios_planificados": 0,
            "faltantes": len(pendientes),
            "salidas": [],
            "pendientes_sin_salida": pendientes,
            "advertencias": advertencias,
            "prioridades": {
                "pendientes": [],
                "satisfechas": [],
                "incumplidas": [],
                "pendientes_confirmacion": [],
                "sitios_reservados_ids": set(),
            },
            "plan_operativo": (_plan_operativo_vacio()),
        }

    # ========================================================
    # FECHAS PROTEGIDAS / YA OCUPADAS
    # ========================================================

    fechas_ocupadas = _fechas_ocupadas_por_cuadrilla(batch)

    # ========================================================
    # RESOLVER PRIORIDADES
    # ========================================================

    resolucion_prioridades = resolver_prioridades_batch(
        batch=batch,
        items_disponibles=items,
        disponibilidades=disponibilidades,
    )

    advertencias.extend(
        resolucion_prioridades.get(
            "advertencias",
            [],
        )
    )

    prioridades_satisfechas = resolucion_prioridades.get(
        "prioridades_satisfechas",
        [],
    )

    prioridades_incumplidas = resolucion_prioridades.get(
        "prioridades_incumplidas",
        [],
    )

    # ========================================================
    # PRIORIDADES PROGRAMADAS PERO INCUMPLIDAS
    # ========================================================

    for dato in prioridades_incumplidas:

        prioridad = dato.get("prioridad")

        incumplimientos = dato.get(
            "incumplimientos",
            [],
        )

        if prioridad is None:
            continue

        if not incumplimientos:
            continue

        detalle = "; ".join(str(valor) for valor in incumplimientos)

        advertencias.append(
            (
                f"{prioridad.id_claro}: "
                "la prioridad posee una programación "
                "existente que no cumple completamente "
                f"su configuración: {detalle}."
            )
        )

    # ========================================================
    # GENERAR PRIORIDADES AUTOMÁTICAS
    # ========================================================

    resultado_prioridades = _generar_salidas_prioritarias(
        batch=batch,
        resolucion_prioridades=(resolucion_prioridades),
        fechas_ocupadas=(fechas_ocupadas),
        incluir_hoy=incluir_hoy,
    )

    salidas_resultado = list(resultado_prioridades["salidas"])

    ids_planificados = set(resultado_prioridades["ids_planificados"])

    advertencias.extend(resultado_prioridades["advertencias"])

    prioridades_pendientes_confirmacion = resultado_prioridades[
        "prioridades_pendientes_confirmacion"
    ]

    # ========================================================
    # RESERVADOS POR PRIORIDADES
    # ========================================================

    ids_reservados_prioridades = set(
        resolucion_prioridades.get(
            "sitios_reservados_ids",
            set(),
        )
        or set()
    )

    ids_reservados_no_planificados = ids_reservados_prioridades - ids_planificados

    # ========================================================
    # REMANENTE REGULAR
    # ========================================================

    items_regulares = [
        item
        for item in items
        if (
            item.pk not in ids_reservados_prioridades
            and item.pk not in ids_planificados
        )
    ]

    universo_regular = []

    mapa_items_regular = {}

    ids_sin_coordenadas = {item.pk for item in sin_coordenadas}

    for item in items_regulares:

        if item.pk in ids_sin_coordenadas:
            continue

        motor = _sitio_batch_a_motor(item)

        if motor.latitud is None or motor.longitud is None:
            continue

        universo_regular.append(motor)

        mapa_items_regular[motor.sitio_planificado_id] = item

    # ========================================================
    # MOTOR DE AGRUPACIÓN
    # ========================================================

    plan_operativo = _plan_operativo_vacio()

    if universo_regular:

        clusters = detectar_clusters(universo_regular)

        if clusters:

            plan_operativo = construir_plan_operativo_semana(
                clusters=clusters,
                disponibilidades=disponibilidades,
                objetivo=len(universo_regular),
                estrategia=estrategia,
            )

        else:

            advertencias.append(
                (
                    "No fue posible construir clusters "
                    "operacionales para los sitios "
                    "regulares restantes."
                )
            )

    # ========================================================
    # ORDENAR GRUPOS PARA CALENDARIO
    # ========================================================

    salidas_motor = list(
        plan_operativo.get(
            "salidas",
            [],
        )
        or []
    )

    salidas_motor.sort(
        key=lambda salida: (
            -int(
                salida.get(
                    "cantidad_sitios",
                    0,
                )
                or 0
            ),
            -float(
                salida.get(
                    "score_salida",
                    0,
                )
                or 0
            ),
            int(
                salida.get(
                    "minutos_viaje",
                    0,
                )
                or 0
            ),
            int(
                salida.get(
                    "minutos_total",
                    0,
                )
                or 0
            ),
        )
    )

    # ========================================================
    # DISTRIBUIR GRUPOS EN CALENDARIO REAL
    # ========================================================

    for salida_motor in salidas_motor:

        motores_disponibles = []

        for sitio_motor in salida_motor.get(
            "sitios",
            [],
        ):

            item_batch = mapa_items_regular.get(sitio_motor.sitio_planificado_id)

            if item_batch is None:
                continue

            if item_batch.pk in ids_planificados:
                continue

            if item_batch.pk in ids_reservados_prioridades:
                continue

            motores_disponibles.append(sitio_motor)

        if not motores_disponibles:
            continue

        cantidad_original = int(
            salida_motor.get(
                "cantidad_sitios",
                len(
                    salida_motor.get(
                        "sitios",
                        [],
                    )
                ),
            )
            or 0
        )

        if len(motores_disponibles) != cantidad_original:
            continue

        salida_para_distribuir = dict(salida_motor)

        salida_para_distribuir["sitios"] = motores_disponibles

        salida_para_distribuir["cantidad_sitios"] = len(motores_disponibles)

        seleccion = _seleccionar_asignacion_regular_mas_temprana(
            batch=batch,
            salida_motor=(salida_para_distribuir),
            disponibilidades=(disponibilidades),
            fechas_ocupadas=(fechas_ocupadas),
            incluir_hoy=incluir_hoy,
        )

        opcion = seleccion.get("opcion")

        if opcion is None:

            motivo = seleccion.get("motivo")

            if motivo:
                advertencias.append(motivo)

            continue

        fecha = opcion["fecha"]

        disponibilidad = opcion["disponibilidad"]

        calculo = opcion["calculo"]

        codigo_cuadrilla = disponibilidad.codigo_cuadrilla

        sitios_salida = []

        for sitio_motor in calculo.get(
            "sitios",
            [],
        ):

            item_batch = mapa_items_regular.get(sitio_motor.sitio_planificado_id)

            if item_batch is None:
                continue

            if item_batch.pk in ids_planificados:
                continue

            if item_batch.pk in ids_reservados_prioridades:
                continue

            sitios_salida.append(
                {
                    "sitio_batch": item_batch,
                    "sitio_motor": sitio_motor,
                }
            )

        if not sitios_salida:
            continue

        salidas_resultado.append(
            {
                "fecha": fecha,
                "disponibilidad": (disponibilidad),
                "cuadrilla": (codigo_cuadrilla),
                "cuadrilla_nombre": (disponibilidad.nombre_cuadrilla),
                "cluster_id": (
                    salida_motor.get(
                        "cluster_id",
                        "",
                    )
                ),
                "sitios": sitios_salida,
                "cantidad_sitios": len(sitios_salida),
                "orden": [
                    sitio.id_claro
                    for sitio in calculo.get(
                        "sitios",
                        [],
                    )
                ],
                "minutos_viaje": int(
                    calculo.get(
                        "minutos_viaje",
                        0,
                    )
                    or 0
                ),
                "minutos_trabajo": int(
                    calculo.get(
                        "minutos_trabajo",
                        0,
                    )
                    or 0
                ),
                "minutos_total": int(
                    calculo.get(
                        "minutos_total",
                        0,
                    )
                    or 0
                ),
                "distancia_directa_km": (calculo.get("distancia_directa_km")),
                "distancia_vial_estimada_km": (
                    calculo.get("distancia_vial_estimada_km")
                ),
                "jornada_extendida": bool(
                    calculo.get(
                        "jornada_extendida",
                        False,
                    )
                ),
                "exceso_jornada_minutos": int(
                    calculo.get(
                        "exceso_jornada_minutos",
                        0,
                    )
                    or 0
                ),
                "puntaje_motor": (salida_motor.get("score_salida")),
                "es_prioridad": False,
                "prioridad_id": None,
                "prioridad_codigo": "",
                "prioridad_ancla_id": None,
                "prioridad_motivo": "",
            }
        )

        for dato_sitio in sitios_salida:

            ids_planificados.add(dato_sitio["sitio_batch"].pk)

        fechas_ocupadas[disponibilidad.pk].add(fecha)

    # ========================================================
    # ORDEN FINAL
    # ========================================================

    salidas_resultado.sort(
        key=lambda salida: (
            salida["fecha"],
            (
                getattr(
                    salida["disponibilidad"].cuadrilla_operativa,
                    "orden",
                    9999,
                )
                if salida["disponibilidad"].cuadrilla_operativa_id
                else 9999
            ),
            salida["cuadrilla_nombre"],
        )
    )

    # ========================================================
    # ACTUALIZAR MÉTRICAS DE DISTRIBUCIÓN REAL
    # ========================================================

    _actualizar_distribucion_real_plan_operativo(
        plan_operativo=plan_operativo,
        salidas_resultado=salidas_resultado,
        disponibilidades=disponibilidades,
    )

    # ========================================================
    # PENDIENTES
    # ========================================================

    pendientes = _construir_pendientes_resultado(
        items=items,
        ids_planificados=(ids_planificados),
        sin_coordenadas=(sin_coordenadas),
        ids_prioridad_reservados=(ids_reservados_no_planificados),
    )

    _agregar_advertencia_pendientes(
        advertencias=advertencias,
        pendientes=pendientes,
    )

    cantidad_planificada = len(ids_planificados)

    return {
        "batch_id": batch.pk,
        "sitios_disponibles": (total_disponibles),
        "sitios_planificados": (cantidad_planificada),
        "faltantes": len(pendientes),
        "salidas": (salidas_resultado),
        "pendientes_sin_salida": (pendientes),
        "advertencias": (advertencias),
        "prioridades": {
            "pendientes": (
                resolucion_prioridades.get(
                    "prioridades",
                    [],
                )
            ),
            "satisfechas": (prioridades_satisfechas),
            "incumplidas": (prioridades_incumplidas),
            "pendientes_confirmacion": (prioridades_pendientes_confirmacion),
            "sitios_reservados_ids": (ids_reservados_prioridades),
        },
        "plan_operativo": (plan_operativo),
    }


# ============================================================
# RESTAURAR SITIO TRAS ELIMINAR PROPUESTA EDITABLE
# ============================================================


def _restaurar_sitio_planificado_si_corresponde(
    *,
    sitio_planificado,
    usuario=None,
):
    if sitio_planificado.estado in {
        "completado",
        "cancelado",
        "bloqueado",
    }:
        return

    existe_compromiso = (
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch__sitio_planificado=(sitio_planificado),
        )
        .exclude(
            estado__in=[
                "retirado",
                "cancelado",
                "reprogramado",
            ]
        )
        .exists()
    )

    if existe_compromiso:
        return

    sitio_planificado.fecha_planificada = None

    sitio_planificado.orden_dia = 0

    if sitio_planificado.estado_permiso in {
        "aprobado",
        "no_requiere",
    }:

        sitio_planificado.estado = "listo_planificar"

    sitio_planificado.actualizado_por = usuario

    sitio_planificado.save(
        update_fields=[
            "fecha_planificada",
            "orden_dia",
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )


# ============================================================
# BORRAR PROPUESTA EDITABLE ANTERIOR
# ============================================================


def _limpiar_salidas_editables_batch(
    *,
    batch,
    usuario=None,
):
    salidas = obtener_salidas_editables_batch(batch)

    sitios_planificados = {}

    for salida in salidas:

        for sitio_salida in salida.sitios.select_related(
            "sitio_batch__sitio_planificado"
        ).all():

            sitio_planificado = sitio_salida.sitio_batch.sitio_planificado

            sitios_planificados[sitio_planificado.pk] = sitio_planificado

    ids_salidas = [salida.pk for salida in salidas]

    if ids_salidas:

        SalidaPlanificacionDiaria.objects.filter(pk__in=ids_salidas).delete()

    for sitio_planificado in sitios_planificados.values():

        _restaurar_sitio_planificado_si_corresponde(
            sitio_planificado=(sitio_planificado),
            usuario=usuario,
        )

    return len(ids_salidas)


# ============================================================
# SINCRONIZAR ESTADO DEL BATCH DESDE PLANIFICACIÓN DIARIA
# ============================================================


def sincronizar_estado_batch_desde_planificacion_diaria(
    *,
    batch,
    usuario=None,
):
    """
    Sincroniza el estado general del batch cuando ya existe
    planificación diaria persistida.

    REGLA
    ==========================================================

    Si existe al menos una SalidaPlanificacionDiaria
    persistida, el batch ya alcanzó operacionalmente la etapa
    de planificación diaria y no puede permanecer en un estado
    semanal anterior.

    Estados que pueden avanzar:

        borrador
        propuesto
        gestion_permisos
        listo_planificar

    Estado destino:

        planificado

    PROTECCIONES
    ==========================================================

    Esta función:

    - NO modifica sitios del batch;
    - NO modifica SitioPlanificado;
    - NO modifica permisos;
    - NO modifica salidas;
    - NO reconstruye la semana;
    - NO recupera sitios trasladados;
    - NO modifica Operaciones;
    - NO retrocede estados posteriores;
    - NO modifica batches cerrados;
    - NO modifica batches cancelados.

    El batch debe encontrarse bloqueado previamente mediante
    select_for_update() cuando esta función forme parte de una
    operación concurrente de escritura.
    """

    estados_que_pueden_avanzar = {
        "borrador",
        "propuesto",
        "gestion_permisos",
        "listo_planificar",
    }

    if batch.estado not in estados_que_pueden_avanzar:
        return False

    existe_planificacion_diaria = SalidaPlanificacionDiaria.objects.filter(
        batch=batch,
    ).exists()

    if not existe_planificacion_diaria:
        return False

    batch.estado = "planificado"

    if usuario is not None:
        batch.actualizado_por = usuario

    batch.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return True


# ============================================================
# GUARDAR PLAN DIARIO
# ============================================================


@transaction.atomic
def guardar_plan_diario_batch(
    *,
    batch,
    usuario,
    estrategia=ESTRATEGIA_OPERATIVA,
    incluir_hoy=False,
):
    """
    Recalcula y guarda la planificación diaria.

    REGLA DE SALIDAS PROTEGIDAS PRIORITARIAS
    ==========================================================

    Una salida manual/protegida puede mantenerse intacta y
    al mismo tiempo recibir acompañantes del motor.

    Ejemplo:

        lunes
        C1

        1. 05_067 PRIORIDAD

    después del recálculo:

        1. 05_067 PRIORIDAD
        2. 05_366
        3. 05_098

    Conservamos:

        salida original
        fecha original
        cuadrilla original
        bloqueo original
        origen original

    Recalculamos:

        viaje
        trabajo
        jornada
        distancia
        jornada extendida

    El ancla siempre queda como orden 1.

    BLOQUEOS SQL
    ==========================================================

    Las filas principales se bloquean con select_for_update()
    SIN combinarlas con select_related() sobre relaciones que
    puedan ser nullable.

    Esto evita el error PostgreSQL:

        FOR UPDATE cannot be applied to the nullable side
        of an outer join
    """

    # ========================================================
    # BLOQUEAR EXCLUSIVAMENTE EL BATCH
    # ========================================================

    batch = BatchPlanificacionSemanal.objects.select_for_update().get(
        pk=batch.pk,
    )

    # ========================================================
    # CARGAR CONFIGURACIÓN FUERA DEL BLOQUEO PRINCIPAL
    # ========================================================

    if batch.configuracion_semana_id:
        batch.configuracion_semana

    # ========================================================
    # GENERAR PROPUESTA
    # ========================================================

    resultado = generar_plan_diario_batch(
        batch=batch,
        estrategia=estrategia,
        incluir_hoy=incluir_hoy,
    )

    # ========================================================
    # SIN NUEVAS SALIDAS NI COMPLETACIONES
    # ========================================================

    if not resultado.get("salidas"):

        resultado["salidas_creadas"] = 0
        resultado["salidas_actualizadas"] = 0
        resultado["salida_ids"] = []
        resultado["salidas_eliminadas"] = 0
        resultado["propuesta_anterior_conservada"] = True

        # ====================================================
        # SINCRONIZAR BATCH SI YA EXISTE PLANIFICACIÓN
        # ====================================================
        #
        # Este caso es importante para semanas que ya poseen
        # salidas persistidas pero cuyo recálculo actual no
        # necesita crear ni completar nuevas salidas.
        # ====================================================

        sincronizar_estado_batch_desde_planificacion_diaria(
            batch=batch,
            usuario=usuario,
        )

        return resultado

    # ========================================================
    # LIMPIAR PROPUESTA AUTOMÁTICA EDITABLE
    # ========================================================

    salidas_eliminadas = _limpiar_salidas_editables_batch(
        batch=batch,
        usuario=usuario,
    )

    creadas = []
    actualizadas = []
    salidas_resultado_ids = []

    # ========================================================
    # PROCESAR RESULTADOS
    # ========================================================

    for salida_data in resultado["salidas"]:

        es_prioridad = bool(
            salida_data.get(
                "es_prioridad",
                False,
            )
        )

        completar_existente = bool(
            salida_data.get(
                "completar_salida_existente",
                False,
            )
        )

        salida_existente_id = salida_data.get(
            "salida_existente_id",
        )

        # ====================================================
        # COMPLETAR SALIDA PROTEGIDA EXISTENTE
        # ====================================================

        if completar_existente and salida_existente_id:

            salida = SalidaPlanificacionDiaria.objects.select_for_update().get(
                pk=salida_existente_id,
                batch=batch,
            )

            if salida.disponibilidad_cuadrilla_id:
                salida.disponibilidad_cuadrilla

            participaciones_existentes = list(
                salida.sitios.exclude(
                    estado__in=[
                        "retirado",
                        "reprogramado",
                        "cancelado",
                    ],
                )
                .select_for_update()
                .order_by(
                    "orden",
                    "id",
                )
            )

            for participacion in participaciones_existentes:
                participacion.sitio_batch
                participacion.sitio_batch.sitio_planificado

            mapa_participaciones = {
                participacion.sitio_batch_id: participacion
                for participacion in participaciones_existentes
            }

            for indice, dato_sitio in enumerate(
                salida_data["sitios"],
                start=1,
            ):

                item_batch = dato_sitio["sitio_batch"]

                participacion = mapa_participaciones.get(
                    item_batch.pk,
                )

                es_ancla_prioridad = bool(
                    salida_data.get(
                        "prioridad_ancla_id",
                    )
                    == item_batch.pk
                )

                if participacion is not None:

                    campos = []

                    if participacion.orden != indice:
                        participacion.orden = indice
                        campos.append("orden")

                    if participacion.estado == "planificado":
                        participacion.estado = "listo_asignar"
                        campos.append("estado")

                    participacion.actualizado_por = usuario
                    campos.append("actualizado_por")

                    participacion.save(
                        update_fields=[
                            *dict.fromkeys(campos),
                            "actualizado_en",
                        ]
                    )

                else:

                    if es_ancla_prioridad:
                        motivo_sitio = (
                            "Sitio ancla de una prioridad " "de planificación diaria."
                        )
                    else:
                        motivo_sitio = (
                            "Sitio incorporado por el motor "
                            "para completar una salida "
                            "prioritaria protegida."
                        )

                    participacion = SitioSalidaPlanificacionDiaria.objects.create(
                        salida=salida,
                        sitio_batch=item_batch,
                        orden=indice,
                        estado="listo_asignar",
                        origen="motor",
                        puntaje_motor=(
                            salida_data.get(
                                "puntaje_motor",
                            )
                        ),
                        motivo_motor=motivo_sitio,
                        creado_por=usuario,
                        actualizado_por=usuario,
                    )

                    mapa_participaciones[item_batch.pk] = participacion

                sitio_planificado = item_batch.sitio_planificado

                sitio_planificado.fecha_planificada = salida.fecha
                sitio_planificado.orden_dia = indice

                if sitio_planificado.estado not in {
                    "completado",
                    "cancelado",
                    "bloqueado",
                }:
                    sitio_planificado.estado = "planificado"

                sitio_planificado.actualizado_por = usuario

                sitio_planificado.save(
                    update_fields=[
                        "fecha_planificada",
                        "orden_dia",
                        "estado",
                        "actualizado_por",
                        "actualizado_en",
                    ]
                )

            salida.minutos_viaje_estimados = salida_data["minutos_viaje"]
            salida.minutos_trabajo_estimados = salida_data["minutos_trabajo"]
            salida.minutos_total_estimados = salida_data["minutos_total"]
            salida.distancia_directa_km = salida_data["distancia_directa_km"]

            salida.distancia_vial_estimada_km = salida_data[
                "distancia_vial_estimada_km"
            ]

            salida.jornada_extendida = salida_data["jornada_extendida"]

            salida.exceso_jornada_minutos = salida_data["exceso_jornada_minutos"]

            salida.puntaje_motor = salida_data.get(
                "puntaje_motor",
            )

            if salida.estado == "borrador":
                salida.estado = "lista_asignar"

            salida.actualizado_por = usuario

            salida.save(
                update_fields=[
                    "estado",
                    "minutos_viaje_estimados",
                    "minutos_trabajo_estimados",
                    "minutos_total_estimados",
                    "distancia_directa_km",
                    "distancia_vial_estimada_km",
                    "jornada_extendida",
                    "exceso_jornada_minutos",
                    "puntaje_motor",
                    "actualizado_por",
                    "actualizado_en",
                ]
            )

            actualizadas.append(
                salida,
            )

            salidas_resultado_ids.append(
                salida.pk,
            )

            continue

        # ====================================================
        # CREAR SALIDA NUEVA
        # ====================================================

        motivo_motor = (
            (
                "Salida diaria prioritaria generada "
                "por el motor utilizando un sitio "
                "ancla."
            )
            if es_prioridad
            else ("Salida diaria generada por " "el motor operacional.")
        )

        salida = SalidaPlanificacionDiaria.objects.create(
            batch=batch,
            disponibilidad_cuadrilla=(salida_data["disponibilidad"]),
            fecha=salida_data["fecha"],
            orden=0,
            estado="lista_asignar",
            origen="motor",
            minutos_viaje_estimados=(salida_data["minutos_viaje"]),
            minutos_trabajo_estimados=(salida_data["minutos_trabajo"]),
            minutos_total_estimados=(salida_data["minutos_total"]),
            distancia_directa_km=(salida_data["distancia_directa_km"]),
            distancia_vial_estimada_km=(salida_data["distancia_vial_estimada_km"]),
            jornada_extendida=(salida_data["jornada_extendida"]),
            exceso_jornada_minutos=(salida_data["exceso_jornada_minutos"]),
            puntaje_motor=(
                salida_data.get(
                    "puntaje_motor",
                )
            ),
            motivo_motor=motivo_motor,
            creado_por=usuario,
            actualizado_por=usuario,
        )

        # ====================================================
        # SITIOS DE NUEVA SALIDA
        # ====================================================

        for indice, dato_sitio in enumerate(
            salida_data["sitios"],
            start=1,
        ):

            item_batch = dato_sitio["sitio_batch"]

            es_ancla_prioridad = bool(
                es_prioridad
                and salida_data.get(
                    "prioridad_ancla_id",
                )
                == item_batch.pk
            )

            if es_ancla_prioridad:
                motivo_sitio = (
                    "Sitio ancla de una prioridad " "de planificación diaria."
                )

            elif es_prioridad:
                motivo_sitio = (
                    "Sitio incorporado como acompañante " "de una prioridad diaria."
                )

            else:
                motivo_sitio = "Incluido por el motor dentro " "de la salida diaria."

            SitioSalidaPlanificacionDiaria.objects.create(
                salida=salida,
                sitio_batch=item_batch,
                orden=indice,
                estado="listo_asignar",
                origen="motor",
                puntaje_motor=(
                    salida_data.get(
                        "puntaje_motor",
                    )
                ),
                motivo_motor=motivo_sitio,
                creado_por=usuario,
                actualizado_por=usuario,
            )

            sitio_planificado = item_batch.sitio_planificado

            sitio_planificado.fecha_planificada = salida.fecha
            sitio_planificado.orden_dia = indice

            if sitio_planificado.estado not in {
                "completado",
                "cancelado",
                "bloqueado",
            }:
                sitio_planificado.estado = "planificado"

            sitio_planificado.actualizado_por = usuario

            sitio_planificado.save(
                update_fields=[
                    "fecha_planificada",
                    "orden_dia",
                    "estado",
                    "actualizado_por",
                    "actualizado_en",
                ]
            )

        creadas.append(
            salida,
        )

        salidas_resultado_ids.append(
            salida.pk,
        )

    # ========================================================
    # SINCRONIZAR ESTADO GENERAL DEL BATCH
    # ========================================================

    sincronizar_estado_batch_desde_planificacion_diaria(
        batch=batch,
        usuario=usuario,
    )

    # ========================================================
    # RESULTADO
    # ========================================================

    resultado["salidas_eliminadas"] = salidas_eliminadas
    resultado["salidas_creadas"] = len(creadas)
    resultado["salidas_actualizadas"] = len(actualizadas)
    resultado["salida_ids"] = salidas_resultado_ids
    resultado["propuesta_anterior_conservada"] = False

    return resultado





# ============================================================
# BUSCAR SERVICIO OPERACIONAL DE ESTA EJECUCIÓN
# ============================================================


def obtener_servicio_operacional_sitio(
    sitio_planificado,
):
    """
    Obtiene exclusivamente el ServicioCotizado correspondiente
    a esta ejecución mensual concreta de SitioPlanificado.

    ARQUITECTURA
    ==========================================================

    Un mismo SitioMovil puede aparecer múltiples veces a lo
    largo del tiempo.

    Ejemplo:

        05_750
            Octubre 2025
            Agosto 2026
            Diciembre 2026
            Marzo 2027

    Cada una de esas apariciones representa una ejecución
    operacional diferente.

    Por lo tanto NO es válido buscar Operaciones mediante:

        id_claro

    ni mediante:

        order_by("-id").first()

    porque el último ServicioCotizado creado podría pertenecer
    a otro mes, otro año o incluso a otra ejecución histórica.

    RELACIÓN CORRECTA
    ==========================================================

        SitioPlanificado
            ->
        ServicioCotizado.sitio_planificado

    Esto permite que la planificación diaria consulte
    exclusivamente el servicio perteneciente a su propia
    ejecución mensual.

    SIN FALLBACK
    ==========================================================

    No existe fallback por:

        id_claro
        id_new
        fecha_creacion
        último DU

    Si todavía no existe un ServicioCotizado vinculado a este
    SitioPlanificado, se devuelve None.

    Esto es intencional.

    Es preferible mostrar:

        Sin servicio operacional

    antes que tomar accidentalmente un servicio histórico de
    otra ejecución y marcar el sitio como:

        asignado
        en ejecución
        revisión
        finalizado

    incorrectamente.

    INTEGRIDAD
    ==========================================================

    En condiciones normales debe existir como máximo un
    ServicioCotizado operacional asociado a una ejecución
    mensual.

    Si por datos históricos existieran varios, utilizamos el
    más reciente únicamente DENTRO DEL MISMO SitioPlanificado.

    Esto es seguro porque todos ellos pertenecerían a la misma
    ejecución mensual y nunca a otro mes/año.
    """

    # ========================================================
    # SIN SITIO PLANIFICADO
    # ========================================================

    if sitio_planificado is None:
        return None

    # ========================================================
    # INSTANCIA TODAVÍA NO GUARDADA
    # ========================================================

    if not getattr(
        sitio_planificado,
        "pk",
        None,
    ):
        return None

    # ========================================================
    # SERVICIO VINCULADO A ESTA EJECUCIÓN
    # ========================================================

    return (
        ServicioCotizado.objects.filter(
            sitio_planificado_id=(sitio_planificado.pk),
        )
        .order_by(
            "-id",
        )
        .first()
    )


# ============================================================
# ESTADO OPERACIONAL PARA PLANIFICACIÓN
# ============================================================


def obtener_estado_operacional_sitio(
    sitio_planificado,
):
    servicio = obtener_servicio_operacional_sitio(sitio_planificado)

    if servicio is None:

        return {
            "servicio": None,
            "servicio_id": None,
            "du": None,
            "estado_operaciones": None,
            "estado_operaciones_display": ("Sin servicio operacional"),
            "estado_planificacion": ("sin_servicio"),
            "puede_asignar": False,
            "finalizado": False,
        }

    estado = servicio.estado

    if estado in ESTADOS_OPERACIONES_FINALIZADOS:

        estado_planificacion = "finalizado"

    elif estado in ESTADOS_OPERACIONES_REVISION:

        estado_planificacion = "revision"

    elif estado in ESTADOS_OPERACIONES_EJECUCION:

        estado_planificacion = "en_ejecucion"

    elif estado == "asignado":

        estado_planificacion = "asignado"

    elif estado == "aprobado_pendiente":

        estado_planificacion = "listo_asignar"

    else:

        estado_planificacion = "no_disponible"

    try:

        estado_display = servicio.get_estado_display()

    except Exception:

        estado_display = estado or "Sin estado"

    return {
        "servicio": (servicio),
        "servicio_id": (servicio.pk),
        "du": (servicio.du),
        "estado_operaciones": (estado),
        "estado_operaciones_display": (estado_display),
        "estado_planificacion": (estado_planificacion),
        "puede_asignar": (estado == "aprobado_pendiente"),
        "finalizado": (estado_planificacion == "finalizado"),
    }


# ============================================================
# SINCRONIZAR ESTADO DESDE OPERACIONES
# ============================================================


@transaction.atomic
def sincronizar_estado_sitio_salida(
    *,
    sitio_salida,
    usuario=None,
):
    sitio_salida = (
        SitioSalidaPlanificacionDiaria.objects.select_for_update()
        .select_related(
            "salida",
            ("sitio_batch__" "sitio_planificado__" "sitio"),
        )
        .get(pk=sitio_salida.pk)
    )

    sitio_planificado = sitio_salida.sitio_batch.sitio_planificado

    estado = obtener_estado_operacional_sitio(sitio_planificado)

    estado_planificacion = estado["estado_planificacion"]

    mapa_estado_salida = {
        "listo_asignar": ("listo_asignar"),
        "asignado": ("asignado"),
        "en_ejecucion": ("en_ejecucion"),
        "revision": ("revision"),
        "finalizado": ("finalizado"),
    }

    nuevo_estado = mapa_estado_salida.get(estado_planificacion)

    if nuevo_estado and sitio_salida.estado != nuevo_estado:

        sitio_salida.estado = nuevo_estado

        sitio_salida.actualizado_por = usuario

        sitio_salida.save(
            update_fields=[
                "estado",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    nuevo_estado_sitio = None

    if estado_planificacion in {
        "listo_asignar",
        "asignado",
    }:

        nuevo_estado_sitio = "planificado"

    elif estado_planificacion == "en_ejecucion":

        nuevo_estado_sitio = "en_ejecucion"

    elif estado_planificacion == "revision":

        nuevo_estado_sitio = "en_ejecucion"

    elif estado_planificacion == "finalizado":

        nuevo_estado_sitio = "completado"

    if nuevo_estado_sitio and sitio_planificado.estado != nuevo_estado_sitio:

        sitio_planificado.estado = nuevo_estado_sitio

        sitio_planificado.actualizado_por = usuario

        sitio_planificado.save(
            update_fields=[
                "estado",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    return estado


# ============================================================
# SINCRONIZAR SALIDA COMPLETA
# ============================================================


@transaction.atomic
def sincronizar_estado_salida(
    *,
    salida,
    usuario=None,
):
    salida = SalidaPlanificacionDiaria.objects.select_for_update().get(pk=salida.pk)

    sitios = list(
        salida.sitios.select_related(
            ("sitio_batch__" "sitio_planificado__" "sitio"),
        ).all()
    )

    for sitio_salida in sitios:

        sincronizar_estado_sitio_salida(
            sitio_salida=(sitio_salida),
            usuario=usuario,
        )

    estados = list(
        salida.sitios.exclude(
            estado__in=[
                "retirado",
                "cancelado",
            ]
        ).values_list(
            "estado",
            flat=True,
        )
    )

    if not estados:
        return salida

    if all(estado == "finalizado" for estado in estados):

        nuevo_estado = "finalizada"

    elif any(estado == "finalizado" for estado in estados) and any(
        estado != "finalizado" for estado in estados
    ):

        nuevo_estado = "parcial"

    elif any(
        estado
        in {
            "en_ejecucion",
            "revision",
        }
        for estado in estados
    ):

        nuevo_estado = "en_ejecucion"

    elif any(estado == "asignado" for estado in estados):

        nuevo_estado = "asignada"

    elif all(
        estado
        in {
            "planificado",
            "listo_asignar",
        }
        for estado in estados
    ):

        nuevo_estado = "lista_asignar"

    else:

        nuevo_estado = salida.estado

    if salida.estado != nuevo_estado:

        salida.estado = nuevo_estado

        salida.actualizado_por = usuario

        salida.save(
            update_fields=[
                "estado",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    return salida


# ============================================================
# SINCRONIZAR TODO EL BATCH
# ============================================================


def sincronizar_estado_planificacion_diaria_batch(
    *,
    batch,
    usuario=None,
):
    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
        )
        .exclude(
            estado="cancelada",
        )
        .order_by(
            "fecha",
            "orden",
            "id",
        )
    )

    for salida in salidas:

        sincronizar_estado_salida(
            salida=salida,
            usuario=usuario,
        )

    return len(salidas)


# ============================================================
# RESUMEN PARA LA VISTA DIARIA
# ============================================================


def obtener_resumen_planificacion_diaria(
    batch,
):
    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
        )
        .select_related(
            "disponibilidad_cuadrilla",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        )
        .prefetch_related(("sitios__" "sitio_batch__" "sitio_planificado__" "sitio"))
        .order_by(
            "fecha",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa__orden"),
            "orden",
            "id",
        )
    )

    total_sitios = 0

    por_estado = defaultdict(int)

    salidas_por_estado = defaultdict(int)

    for salida in salidas:

        salidas_por_estado[salida.estado] += 1

        for sitio_salida in salida.sitios.all():

            if sitio_salida.estado in {
                "retirado",
                "cancelado",
            }:
                continue

            total_sitios += 1

            por_estado[sitio_salida.estado] += 1

    pendientes_reales = obtener_sitios_pendientes_planificacion_diaria(batch)

    return {
        "salidas": (salidas),
        "total_salidas": (len(salidas)),
        "total_sitios": (total_sitios),
        "disponibles_sin_planificar": (len(pendientes_reales)),
        "planificados": (por_estado["planificado"] + por_estado["listo_asignar"]),
        "asignados": (por_estado["asignado"]),
        "en_ejecucion": (por_estado["en_ejecucion"]),
        "revision": (por_estado["revision"]),
        "finalizados": (por_estado["finalizado"]),
        "no_ejecutados": (por_estado["no_ejecutado"]),
        "reprogramados": (por_estado["reprogramado"]),
        "salidas_borrador": (salidas_por_estado["borrador"]),
        "salidas_listas": (salidas_por_estado["lista_asignar"]),
        "salidas_asignadas": (salidas_por_estado["asignada"]),
        "salidas_en_ejecucion": (salidas_por_estado["en_ejecucion"]),
        "salidas_parciales": (salidas_por_estado["parcial"]),
        "salidas_finalizadas": (salidas_por_estado["finalizada"]),
    }

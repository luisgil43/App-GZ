from collections import Counter
from math import asin, cos, radians, sin, sqrt

from django.db import transaction

from planificacion.modelos import (CuadrillaOperativa,
                                   DisponibilidadCuadrillaSemana)
from planificacion.models import (BatchPlanificacionSemanal,
                                  ConfiguracionSemana, SitioBatchSemanal,
                                  SitioPlanificado)

# ============================================================
# ESTADOS QUE CONSIDERAMOS COMO PARTICIPACIÓN ACTIVA EN BATCH
# ============================================================

ESTADOS_BATCH_ACTIVOS_SITIO = [
    "candidato",
    "seleccionado",
    "gestion_permiso",
    "disponible",
    "confirmado",
]


# ============================================================
# UTILIDADES GEOGRÁFICAS
# ============================================================


def calcular_distancia_km(
    lat1,
    lon1,
    lat2,
    lon2,
):
    """
    Calcula distancia aproximada en línea recta
    utilizando Haversine.

    Esta distancia NO reemplaza Google Routes.

    Sirve para:
    - cercanía preliminar;
    - agrupación;
    - detección de aislamiento;
    - scoring inicial.
    """

    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)

    except (
        TypeError,
        ValueError,
    ):
        return None

    radio_tierra_km = 6371.0

    lat1 = radians(lat1)
    lon1 = radians(lon1)

    lat2 = radians(lat2)
    lon2 = radians(lon2)

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2

    c = 2 * asin(sqrt(a))

    return radio_tierra_km * c


# ============================================================
# LEGACY
# ============================================================


def _codigo_legacy_cuadrilla(
    cuadrilla_operativa,
):
    """
    Devuelve el código histórico únicamente para C1/C2/C3.

    Las nuevas cuadrillas C4, C5, etc. utilizan exclusivamente
    cuadrilla_operativa y dejan el campo legacy vacío.
    """

    if not cuadrilla_operativa:
        return ""

    codigo = (cuadrilla_operativa.codigo or "").strip().lower()

    mapeo = {
        "cuadrilla_c1": "cuadrilla_1",
        "cuadrilla_c2": "cuadrilla_2",
        "cuadrilla_c3": "cuadrilla_3",
    }

    return mapeo.get(
        codigo,
        "",
    )


# ============================================================
# DISPONIBILIDAD DE CUADRILLAS
# ============================================================


@transaction.atomic
def guardar_disponibilidad_cuadrilla(
    *,
    configuracion,
    cuadrilla_operativa,
    modalidad,
    activa,
    capacidad_diaria,
    usuario=None,
):
    """
    Crea o actualiza la disponibilidad semanal
    utilizando CuadrillaOperativa.

    Este método ya no depende de tres códigos fijos.

    Cualquier nueva cuadrilla creada en el catálogo puede
    utilizarse sin modificar código.
    """

    if cuadrilla_operativa is None:
        raise ValueError("Debe indicar una cuadrilla operativa.")

    if not isinstance(
        cuadrilla_operativa,
        CuadrillaOperativa,
    ):
        try:
            cuadrilla_operativa = CuadrillaOperativa.objects.get(
                pk=cuadrilla_operativa,
            )

        except (
            CuadrillaOperativa.DoesNotExist,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError("La cuadrilla operativa indicada " "no existe.") from exc

    modalidades_validas = {
        valor for valor, _ in DisponibilidadCuadrillaSemana.MODALIDADES
    }

    if modalidad not in modalidades_validas:
        raise ValueError("Modalidad semanal inválida.")

    try:
        capacidad_diaria = int(capacidad_diaria)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("La capacidad diaria debe " "ser un número entero.") from exc

    if capacidad_diaria <= 0:
        raise ValueError("La capacidad diaria debe " "ser mayor que cero.")

    codigo_legacy = _codigo_legacy_cuadrilla(cuadrilla_operativa)

    # --------------------------------------------------------
    # BUSCAMOS PRIMERO POR LA FK NUEVA
    # --------------------------------------------------------

    disponibilidad = DisponibilidadCuadrillaSemana.objects.filter(
        configuracion_semana=configuracion,
        cuadrilla_operativa=(cuadrilla_operativa),
    ).first()

    # --------------------------------------------------------
    # COMPATIBILIDAD CON REGISTROS ANTIGUOS
    # --------------------------------------------------------

    if disponibilidad is None and codigo_legacy:
        disponibilidad = DisponibilidadCuadrillaSemana.objects.filter(
            configuracion_semana=configuracion,
            cuadrilla=codigo_legacy,
        ).first()

    creada = False

    # --------------------------------------------------------
    # CREAR
    # --------------------------------------------------------

    if disponibilidad is None:

        disponibilidad = DisponibilidadCuadrillaSemana.objects.create(
            configuracion_semana=configuracion,
            cuadrilla_operativa=(cuadrilla_operativa),
            cuadrilla=codigo_legacy,
            modalidad=modalidad,
            activa=bool(activa),
            capacidad_diaria_objetivo=(capacidad_diaria),
            creado_por=usuario,
            actualizado_por=usuario,
        )

        creada = True

    # --------------------------------------------------------
    # ACTUALIZAR
    # --------------------------------------------------------

    else:

        disponibilidad.cuadrilla_operativa = cuadrilla_operativa

        if codigo_legacy:
            disponibilidad.cuadrilla = codigo_legacy

        disponibilidad.modalidad = modalidad

        disponibilidad.activa = bool(activa)

        disponibilidad.capacidad_diaria_objetivo = capacidad_diaria

        disponibilidad.actualizado_por = usuario

        disponibilidad.save(
            update_fields=[
                "cuadrilla_operativa",
                "cuadrilla",
                "modalidad",
                "activa",
                "capacidad_diaria_objetivo",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    return disponibilidad


def obtener_disponibilidades_semana(
    configuracion,
):
    """
    Devuelve las disponibilidades de la semana.

    Prioriza el orden definido en CuadrillaOperativa.
    """

    return (
        DisponibilidadCuadrillaSemana.objects.filter(
            configuracion_semana=configuracion,
        )
        .select_related(
            "cuadrilla_operativa",
        )
        .order_by(
            "cuadrilla_operativa__orden",
            "cuadrilla_operativa__nombre",
            "cuadrilla",
            "id",
        )
    )


# ============================================================
# CAPACIDAD SEMANAL
# ============================================================


def calcular_capacidad_configuracion(
    configuracion,
):
    """
    Construye la capacidad operacional de la semana.

    Sigue exponiendo la capacidad nominal porque distintas
    vistas actuales la utilizan.

    Además entrega al motor la información real de cada
    cuadrilla:

    - identidad;
    - base efectiva;
    - vehículo;
    - urbano/rural;
    - jornada;
    - trabajo por sitio;
    - sábado;
    - capacidad nominal.

    Esto permite que la siguiente capa del motor calcule
    posteriormente:

    base -> sitio -> sitios -> base
    """

    disponibilidades = obtener_disponibilidades_semana(configuracion).filter(
        activa=True,
    )

    capacidad_lv = 0
    capacidad_sabado = 0

    detalle = []

    for disponibilidad in disponibilidades:

        capacidad_diaria = disponibilidad.capacidad_diaria_objetivo

        capacidad_cuadrilla_lv = capacidad_diaria * 5

        capacidad_cuadrilla_sabado = 0

        capacidad_lv += capacidad_cuadrilla_lv

        if disponibilidad.trabaja_sabado:

            capacidad_cuadrilla_sabado = capacidad_diaria

            capacidad_sabado += capacidad_diaria

        cuadrilla_operativa = disponibilidad.cuadrilla_operativa

        detalle.append(
            {
                # ============================================
                # IDENTIDAD
                # ============================================
                "disponibilidad_id": (disponibilidad.pk),
                "cuadrilla_operativa_id": (
                    cuadrilla_operativa.pk if cuadrilla_operativa else None
                ),
                "cuadrilla": (disponibilidad.codigo_cuadrilla),
                "codigo": (disponibilidad.codigo_cuadrilla),
                "cuadrilla_display": (disponibilidad.nombre_cuadrilla),
                "nombre": (disponibilidad.nombre_cuadrilla),
                # ============================================
                # DISPONIBILIDAD
                # ============================================
                "activa": (disponibilidad.activa),
                "modalidad": (disponibilidad.modalidad),
                "modalidad_display": (disponibilidad.get_modalidad_display()),
                "trabaja_sabado": (disponibilidad.trabaja_sabado),
                "dias_disponibles": (disponibilidad.dias_disponibles),
                # ============================================
                # VEHÍCULO / TERRITORIO
                # ============================================
                "vehiculo": (disponibilidad.tipo_vehiculo),
                "permite_urbano": (disponibilidad.permite_urbano),
                "permite_rural": (disponibilidad.permite_rural),
                # ============================================
                # BASE REAL
                # ============================================
                "base_nombre": (disponibilidad.base_nombre_efectiva),
                "base_latitud": (disponibilidad.base_latitud_efectiva),
                "base_longitud": (disponibilidad.base_longitud_efectiva),
                "tiene_base_operacional": (disponibilidad.tiene_base_operacional),
                # ============================================
                # TIEMPOS
                # ============================================
                "minutos_jornada": (disponibilidad.minutos_jornada_efectivos),
                "minutos_trabajo_sitio": (
                    disponibilidad.minutos_trabajo_sitio_efectivos
                ),
                # ============================================
                # CAPACIDAD NOMINAL
                # ============================================
                "capacidad_diaria": (capacidad_diaria),
                "capacidad_lv": (capacidad_cuadrilla_lv),
                "capacidad_sabado": (capacidad_cuadrilla_sabado),
                "capacidad_total": (
                    capacidad_cuadrilla_lv + capacidad_cuadrilla_sabado
                ),
            }
        )

    return {
        "lunes_viernes": capacidad_lv,
        "sabado": capacidad_sabado,
        "total": (capacidad_lv + capacidad_sabado),
        "detalle": detalle,
    }


# ============================================================
# CREACIÓN DEL BATCH SEMANAL
# ============================================================


@transaction.atomic
def crear_batch_semanal(
    *,
    planificacion,
    fecha_inicio,
    objetivo_sitios,
    nombre="",
    observaciones="",
    disponibilidades=None,
    usuario=None,
):
    """
    Crea una semana operacional GLOBAL.

    REGLAS
    ==========================================================

    - fecha_inicio identifica globalmente la semana;
    - solo puede existir un BatchPlanificacionSemanal por fecha;
    - solo puede existir una ConfiguracionSemana por fecha;
    - planificacion se conserva como origen legacy;
    - planificaciones_origen contiene los meses que alimentan
      realmente la semana.
    """

    if fecha_inicio.weekday() != 0:
        raise ValueError("La fecha de inicio debe corresponder a un lunes.")

    try:
        objetivo_sitios = int(objetivo_sitios)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("El objetivo de sitios debe ser un número entero.") from exc

    if objetivo_sitios <= 0:
        raise ValueError("El objetivo de sitios debe ser mayor que cero.")

    disponibilidades = list(disponibilidades or [])

    if not disponibilidades:
        raise ValueError("Debe configurar al menos una cuadrilla para la semana.")

    if not any(bool(datos.get("activa")) for datos in disponibilidades):
        raise ValueError("Debe existir al menos una cuadrilla activa.")

    # ========================================================
    # UNICIDAD GLOBAL DEL BATCH
    # ========================================================

    if BatchPlanificacionSemanal.objects.filter(
        fecha_inicio=fecha_inicio,
    ).exists():

        raise ValueError(
            "Ya existe una planificación semanal " "para esta semana operacional."
        )

    # ========================================================
    # CONFIGURACIÓN GLOBAL
    # ========================================================
    #
    # IMPORTANTE:
    #
    # NO usamos:
    #
    #     planificacion=planificacion
    #
    # dentro de la clave del get_or_create.
    #
    # La identidad real es fecha_inicio.
    # ========================================================

    configuracion, configuracion_creada = ConfiguracionSemana.objects.get_or_create(
        fecha_inicio=fecha_inicio,
        defaults={
            "planificacion": planificacion,
            "trabaja_sabado": False,
            "capacidad_diaria_objetivo": 3,
            "observaciones": "",
            "actualizado_por": usuario,
        },
    )

    # ========================================================
    # COMPATIBILIDAD LEGACY
    # ========================================================

    if (
        not configuracion_creada
        and configuracion.planificacion_id is None
        and planificacion is not None
    ):

        configuracion.planificacion = planificacion

        configuracion.actualizado_por = usuario

        configuracion.save(
            update_fields=[
                "planificacion",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    # ========================================================
    # SEGURIDAD ONE-TO-ONE
    # ========================================================

    if BatchPlanificacionSemanal.objects.filter(
        configuracion_semana=configuracion,
    ).exists():

        raise ValueError(
            "La configuración de esta semana ya está " "vinculada a otro batch."
        )

    # ========================================================
    # DISPONIBILIDADES
    # ========================================================

    alguna_trabaja_sabado = False

    for datos in disponibilidades:

        cuadrilla_operativa = datos.get("cuadrilla_operativa")

        if cuadrilla_operativa is None:
            raise ValueError("Existe una disponibilidad sin cuadrilla operativa.")

        modalidad = datos.get(
            "modalidad",
            DisponibilidadCuadrillaSemana.LUNES_VIERNES,
        )

        activa = bool(
            datos.get(
                "activa",
                True,
            )
        )

        try:
            capacidad_diaria = int(
                datos.get(
                    "capacidad_diaria",
                    3,
                )
            )

        except (
            TypeError,
            ValueError,
        ) as exc:

            raise ValueError("La capacidad diaria debe ser un número entero.") from exc

        guardar_disponibilidad_cuadrilla(
            configuracion=configuracion,
            cuadrilla_operativa=cuadrilla_operativa,
            modalidad=modalidad,
            activa=activa,
            capacidad_diaria=capacidad_diaria,
            usuario=usuario,
        )

        if activa and modalidad == DisponibilidadCuadrillaSemana.LUNES_SABADO:
            alguna_trabaja_sabado = True

    # ========================================================
    # CONFIGURACIÓN GENERAL
    # ========================================================

    configuracion.trabaja_sabado = alguna_trabaja_sabado

    configuracion.actualizado_por = usuario

    configuracion.save(
        update_fields=[
            "trabaja_sabado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    # ========================================================
    # CREAR BATCH
    # ========================================================

    batch = BatchPlanificacionSemanal.objects.create(
        planificacion=planificacion,
        configuracion_semana=configuracion,
        fecha_inicio=fecha_inicio,
        estado="borrador",
        nombre=nombre,
        objetivo_sitios=objetivo_sitios,
        generado_por_motor=False,
        observaciones=observaciones,
        creado_por=usuario,
        actualizado_por=usuario,
    )

    # ========================================================
    # PRIMER ORIGEN MENSUAL
    # ========================================================

    if planificacion is not None:

        batch.planificaciones_origen.add(
            planificacion,
        )

    return batch


# ============================================================
# SITIOS YA COMPROMETIDOS EN OTROS BATCHES
# ============================================================


def ids_sitios_comprometidos_en_otros_batches(
    batch,
):
    return (
        SitioBatchSemanal.objects.filter(
            estado__in=(ESTADOS_BATCH_ACTIVOS_SITIO),
        )
        .exclude(
            batch=batch,
        )
        .values_list(
            "sitio_planificado_id",
            flat=True,
        )
    )


# ============================================================
# CANDIDATOS DE LA SEMANA
# ============================================================


def obtener_candidatos_batch(
    batch,
):
    """
    Devuelve el universo disponible de una semana operacional.

    Una semana puede recibir sitios de múltiples meses.

    La fuente real de meses es:

        batch.planificaciones_origen

    El campo batch.planificacion se utiliza únicamente como
    respaldo legacy.

    También evitamos utilizar dos SitioPlanificado distintos
    que representen el mismo SitioMovil físico.
    """

    # ========================================================
    # PLANIFICACIONES PARTICIPANTES
    # ========================================================

    planificacion_ids = set(
        batch.planificaciones_origen.values_list(
            "id",
            flat=True,
        )
    )

    if batch.planificacion_id:
        planificacion_ids.add(batch.planificacion_id)

    if not planificacion_ids:

        return SitioPlanificado.objects.none()

    # ========================================================
    # SITIOS FÍSICOS COMPROMETIDOS EN OTROS BATCHES
    # ========================================================

    sitios_fisicos_comprometidos = (
        SitioBatchSemanal.objects.filter(
            estado__in=ESTADOS_BATCH_ACTIVOS_SITIO,
        )
        .exclude(
            batch=batch,
        )
        .values_list(
            "sitio_planificado__sitio_id",
            flat=True,
        )
    )

    # ========================================================
    # ITEMS YA PERTENECIENTES AL BATCH ACTUAL
    # ========================================================

    ids_planificados_mismo_batch = set(
        SitioBatchSemanal.objects.filter(
            batch=batch,
        ).values_list(
            "sitio_planificado_id",
            flat=True,
        )
    )

    # ========================================================
    # UNIVERSO MULTIMES
    # ========================================================

    candidatos = list(
        SitioPlanificado.objects.filter(
            planificacion_id__in=planificacion_ids,
            activo_en_mes=True,
        )
        .exclude(
            sitio_id__in=sitios_fisicos_comprometidos,
        )
        .exclude(
            estado__in=[
                "completado",
                "cancelado",
            ],
        )
        .select_related(
            "sitio",
            "planificacion",
        )
        .order_by(
            "planificacion__anio",
            "planificacion__mes",
            "sitio__comuna",
            "sitio__id_claro",
            "id",
        )
    )

    # ========================================================
    # DEDUPLICACIÓN FÍSICA
    # ========================================================
    #
    # Prioridad:
    #
    # 1. conservar el SitioPlanificado que YA pertenece
    #    al batch;
    #
    # 2. para nuevos candidatos, conservar una sola
    #    representación del mismo SitioMovil.
    #
    # El orden año/mes ascendente favorece conservar la
    # instancia histórica más antigua cuando un mismo sitio
    # aparece en dos meses consecutivos.
    # ========================================================

    resultado_ids = []

    sitios_fisicos_usados = set()

    # ========================================================
    # PRIMERO LOS YA PRESENTES
    # ========================================================

    for candidato in candidatos:

        if candidato.pk not in ids_planificados_mismo_batch:
            continue

        if candidato.sitio_id in sitios_fisicos_usados:
            continue

        resultado_ids.append(candidato.pk)

        sitios_fisicos_usados.add(candidato.sitio_id)

    # ========================================================
    # DESPUÉS LOS NUEVOS
    # ========================================================

    for candidato in candidatos:

        if candidato.pk in ids_planificados_mismo_batch:
            continue

        if candidato.sitio_id in sitios_fisicos_usados:
            continue

        resultado_ids.append(candidato.pk)

        sitios_fisicos_usados.add(candidato.sitio_id)

    return (
        SitioPlanificado.objects.filter(
            pk__in=resultado_ids,
        )
        .select_related(
            "sitio",
            "planificacion",
        )
        .order_by(
            "sitio__comuna",
            "sitio__id_claro",
            "planificacion__anio",
            "planificacion__mes",
            "id",
        )
    )


# ============================================================
# CENTRO GEOGRÁFICO DEL BATCH
# ============================================================


def obtener_centro_geografico_batch(
    batch,
):
    items = SitioBatchSemanal.objects.filter(
        batch=batch,
        estado__in=(ESTADOS_BATCH_ACTIVOS_SITIO),
    ).select_related(
        "sitio_planificado__sitio",
    )

    coordenadas = []

    for item in items:

        sitio = item.sitio_planificado.sitio

        if sitio.latitud is None or sitio.longitud is None:
            continue

        try:
            latitud = float(sitio.latitud)

            longitud = float(sitio.longitud)

        except (
            TypeError,
            ValueError,
        ):
            continue

        coordenadas.append(
            (
                latitud,
                longitud,
            )
        )

    if not coordenadas:
        return None

    promedio_latitud = sum(coordenada[0] for coordenada in coordenadas) / len(
        coordenadas
    )

    promedio_longitud = sum(coordenada[1] for coordenada in coordenadas) / len(
        coordenadas
    )

    return (
        promedio_latitud,
        promedio_longitud,
    )


# ============================================================
# COMUNAS PRESENTES EN EL BATCH
# ============================================================


def obtener_comunas_batch(
    batch,
):
    comunas = SitioBatchSemanal.objects.filter(
        batch=batch,
        estado__in=(ESTADOS_BATCH_ACTIVOS_SITIO),
    ).values_list(
        "sitio_planificado__sitio__comuna",
        flat=True,
    )

    return Counter(comuna for comuna in comunas if comuna)


# ============================================================
# PUNTAJE PRELIMINAR
# ============================================================


def puntuar_candidato_batch(
    *,
    batch,
    sitio_planificado,
):
    sitio = sitio_planificado.sitio

    puntaje = 0.0
    motivos = []
    distancia_centro = None

    centro = obtener_centro_geografico_batch(batch)

    if centro and sitio.latitud is not None and sitio.longitud is not None:

        distancia_centro = calcular_distancia_km(
            centro[0],
            centro[1],
            sitio.latitud,
            sitio.longitud,
        )

        if distancia_centro is not None:

            if distancia_centro <= 10:

                puntaje += 35

                motivos.append("Muy cercano al grupo actual")

            elif distancia_centro <= 25:

                puntaje += 25

                motivos.append("Cercano al grupo actual")

            elif distancia_centro <= 50:

                puntaje += 12

                motivos.append("Distancia razonable al grupo")

            elif distancia_centro >= 100:

                puntaje -= 20

                motivos.append("Alejado del grupo actual")

    comunas_batch = obtener_comunas_batch(batch)

    if sitio.comuna and sitio.comuna in comunas_batch:

        puntaje += 25

        motivos.append("Comuna ya presente en el batch")

    if sitio_planificado.estado_permiso == "aprobado":

        puntaje += 15

        motivos.append("Permiso aprobado")

    elif sitio_planificado.estado_permiso == "no_requiere":

        puntaje += 15

        motivos.append("No requiere permiso")

    if sitio_planificado.prioridad == "critica":

        puntaje += 20

        motivos.append("Prioridad crítica")

    elif sitio_planificado.prioridad == "alta":

        puntaje += 10

        motivos.append("Prioridad alta")

    condiciones_acceso = (sitio.condiciones_acceso or "").strip()

    if condiciones_acceso and "libre" in condiciones_acceso.lower():

        puntaje += 10

        motivos.append("Libre acceso")

    return {
        "puntaje": round(
            puntaje,
            2,
        ),
        "motivo": "; ".join(motivos),
        "distancia_centro_km": (
            round(
                distancia_centro,
                2,
            )
            if distancia_centro is not None
            else None
        ),
    }


# ============================================================
# AGREGAR SITIOS AL BATCH
# ============================================================


@transaction.atomic
def agregar_sitios_al_batch(
    *,
    batch,
    sitio_ids,
    usuario,
    es_reserva=False,
):
    candidatos = obtener_candidatos_batch(batch).filter(
        id__in=sitio_ids,
    )

    cantidad = 0

    for sitio_planificado in candidatos:

        evaluacion = puntuar_candidato_batch(
            batch=batch,
            sitio_planificado=(sitio_planificado),
        )

        item_existente = SitioBatchSemanal.objects.filter(
            batch=batch,
            sitio_planificado=(sitio_planificado),
        ).first()

        if item_existente:

            if item_existente.estado not in [
                "excluido",
                "reemplazado",
            ]:
                continue

            item_existente.estado = "seleccionado"

            item_existente.origen = "manual"

            item_existente.puntaje_motor = evaluacion["puntaje"]

            item_existente.motivo_recomendacion = evaluacion["motivo"]

            item_existente.motivo_exclusion = ""

            item_existente.agregado_manualmente = True

            item_existente.bloqueado_en_batch = False

            item_existente.es_reserva = es_reserva

            item_existente.agregado_por = usuario

            item_existente.save(
                update_fields=[
                    "estado",
                    "origen",
                    "puntaje_motor",
                    "motivo_recomendacion",
                    "motivo_exclusion",
                    "agregado_manualmente",
                    "bloqueado_en_batch",
                    "es_reserva",
                    "agregado_por",
                    "actualizado_en",
                ]
            )

            cantidad += 1

            continue

        SitioBatchSemanal.objects.create(
            batch=batch,
            sitio_planificado=(sitio_planificado),
            estado="seleccionado",
            origen="manual",
            puntaje_motor=(evaluacion["puntaje"]),
            motivo_recomendacion=(evaluacion["motivo"]),
            agregado_manualmente=True,
            bloqueado_en_batch=False,
            es_reserva=es_reserva,
            agregado_por=usuario,
        )

        cantidad += 1

    return cantidad


# ============================================================
# RETIRAR SITIO
# ============================================================


@transaction.atomic
def quitar_sitio_del_batch(
    *,
    item_batch,
    usuario=None,
    motivo="",
):
    item_batch.estado = "excluido"

    item_batch.motivo_exclusion = motivo

    item_batch.save(
        update_fields=[
            "estado",
            "motivo_exclusion",
            "actualizado_en",
        ]
    )

    return item_batch


# ============================================================
# CAMBIAR PRINCIPAL / RESERVA
# ============================================================


@transaction.atomic
def cambiar_reserva_item(
    *,
    item,
    es_reserva,
):
    item.es_reserva = bool(es_reserva)

    item.save(
        update_fields=[
            "es_reserva",
            "actualizado_en",
        ]
    )

    return item


# ============================================================
# CERRAR PROPUESTA
# ============================================================


@transaction.atomic
def cerrar_propuesta_batch(
    *,
    batch,
    usuario,
):
    if batch.estado != "borrador":
        raise ValueError("Solo un batch en borrador " "puede cerrarse como propuesta.")

    cantidad = batch.sitios.filter(
        estado="seleccionado",
    ).count()

    if cantidad == 0:
        raise ValueError("El batch no tiene " "sitios seleccionados.")

    batch.estado = "propuesto"

    batch.actualizado_por = usuario

    batch.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return batch


# ============================================================
# MARCAR GESTIÓN DE PERMISOS ENVIADA
# ============================================================


@transaction.atomic
def marcar_gestion_permisos_enviada(
    *,
    batch,
    usuario,
):
    if batch.estado != "propuesto":
        raise ValueError("El batch debe estar " "en estado Propuesto.")

    items = batch.sitios.filter(
        estado="seleccionado",
    ).select_related(
        "sitio_planificado",
    )

    for item in items:

        sitio_planificado = item.sitio_planificado

        if sitio_planificado.estado_permiso in [
            "aprobado",
            "no_requiere",
        ]:

            item.estado = "disponible"

            sitio_planificado.estado = "listo_planificar"

        else:

            item.estado = "gestion_permiso"

            if sitio_planificado.estado_permiso in [
                "sin_gestion",
                "por_solicitar",
            ]:

                sitio_planificado.estado_permiso = "solicitado"

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

        item.save(
            update_fields=[
                "estado",
                "actualizado_en",
            ]
        )

    batch.estado = "gestion_permisos"

    batch.actualizado_por = usuario

    batch.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return batch


# ============================================================
# ACTUALIZAR PERMISO
# ============================================================


@transaction.atomic
def actualizar_permiso_desde_batch(
    *,
    item,
    nuevo_permiso,
    usuario,
):
    estados_validos = {valor for valor, _ in SitioPlanificado.ESTADOS_PERMISO}

    if nuevo_permiso not in estados_validos:
        raise ValueError("Estado de permiso inválido.")

    sitio_planificado = item.sitio_planificado

    sitio_planificado.estado_permiso = nuevo_permiso

    if nuevo_permiso in [
        "aprobado",
        "no_requiere",
    ]:

        sitio_planificado.estado = "listo_planificar"

        item.estado = "disponible"

    elif nuevo_permiso == "rechazado":

        sitio_planificado.estado = "bloqueado"

        item.estado = "rechazado"

    elif nuevo_permiso in [
        "solicitado",
        "en_espera",
        "por_solicitar",
    ]:

        sitio_planificado.estado = "gestionando_permiso"

        item.estado = "gestion_permiso"

    else:

        sitio_planificado.estado = "pendiente"

        if item.estado not in [
            "excluido",
            "reemplazado",
        ]:
            item.estado = "seleccionado"

    sitio_planificado.actualizado_por = usuario

    sitio_planificado.save(
        update_fields=[
            "estado_permiso",
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    item.save(
        update_fields=[
            "estado",
            "actualizado_en",
        ]
    )

    return item


# ============================================================
# REGISTRAR REEMPLAZO
# ============================================================


@transaction.atomic
def agregar_reemplazo_batch(
    *,
    batch,
    sitio_planificado,
    item_reemplazado,
    usuario,
    es_reserva=False,
):
    """
    Incorpora un reemplazo asegurando que el mismo SitioMovil
    físico no esté comprometido en otro batch mediante otra
    PlanificacionMensual.
    """

    existe_compromiso_fisico = (
        SitioBatchSemanal.objects.filter(
            sitio_planificado__sitio_id=(sitio_planificado.sitio_id),
            estado__in=ESTADOS_BATCH_ACTIVOS_SITIO,
        )
        .exclude(
            batch=batch,
        )
        .exists()
    )

    if existe_compromiso_fisico:

        raise ValueError(
            "El sitio físico ya se encuentra " "comprometido en otro batch activo."
        )

    evaluacion = puntuar_candidato_batch(
        batch=batch,
        sitio_planificado=sitio_planificado,
    )

    # ========================================================
    # EVITAR DUPLICAR EL MISMO SITIO FÍSICO EN EL BATCH
    # ========================================================

    item_fisico_existente = (
        SitioBatchSemanal.objects.filter(
            batch=batch,
            sitio_planificado__sitio_id=(sitio_planificado.sitio_id),
        )
        .select_related(
            "sitio_planificado",
        )
        .first()
    )

    if item_fisico_existente is not None:

        raise ValueError("El sitio físico ya pertenece a este batch.")

    item = SitioBatchSemanal.objects.create(
        batch=batch,
        sitio_planificado=sitio_planificado,
        estado="seleccionado",
        origen="reemplazo",
        puntaje_motor=evaluacion["puntaje"],
        motivo_recomendacion=(
            f"Reemplazo de "
            f"{item_reemplazado.sitio_planificado.sitio.id_claro}. "
            f"{evaluacion['motivo']}"
        ).strip(),
        agregado_manualmente=True,
        bloqueado_en_batch=False,
        es_reserva=es_reserva,
        agregado_por=usuario,
    )

    if item_reemplazado.estado not in [
        "rechazado",
        "excluido",
    ]:

        item_reemplazado.estado = "reemplazado"

        item_reemplazado.save(
            update_fields=[
                "estado",
                "actualizado_en",
            ]
        )

    return item


# ============================================================
# CONFIRMAR PARA PLANIFICACIÓN DIARIA
# ============================================================


@transaction.atomic
def confirmar_sitios_para_planificacion(
    *,
    batch,
    item_ids,
    usuario,
):
    items = batch.sitios.filter(
        id__in=item_ids,
        estado="disponible",
    ).select_related(
        "sitio_planificado",
    )

    cantidad = items.count()

    if cantidad == 0:
        raise ValueError("No existen sitios disponibles " "para confirmar.")

    for item in items:

        item.estado = "confirmado"

        item.save(
            update_fields=[
                "estado",
                "actualizado_en",
            ]
        )

        sitio_planificado = item.sitio_planificado

        sitio_planificado.estado = "listo_planificar"

        sitio_planificado.actualizado_por = usuario

        sitio_planificado.save(
            update_fields=[
                "estado",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    batch.estado = "listo_planificar"

    batch.actualizado_por = usuario

    batch.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return cantidad


# ============================================================
# RESUMEN COMPLETO DEL BATCH
# ============================================================


def obtener_resumen_batch(
    batch,
):
    items = batch.sitios.select_related(
        "sitio_planificado__sitio",
    ).all()

    contador_estados = Counter()
    contador_permisos = Counter()
    contador_comunas = Counter()

    principales = 0
    reservas = 0

    urbanos = 0
    rurales = 0
    sin_tipo_zona = 0

    total_activos = 0

    for item in items:

        contador_estados[item.estado] += 1

        if item.estado in [
            "excluido",
            "reemplazado",
        ]:
            continue

        total_activos += 1

        if item.es_reserva:
            reservas += 1
        else:
            principales += 1

        sitio_planificado = item.sitio_planificado

        sitio = sitio_planificado.sitio

        contador_permisos[sitio_planificado.estado_permiso] += 1

        if sitio.comuna:
            contador_comunas[sitio.comuna] += 1

        tipo_zona = (sitio.tipo_zona or "").strip().lower()

        if "rural" in tipo_zona:
            rurales += 1

        elif "urb" in tipo_zona:
            urbanos += 1

        else:
            sin_tipo_zona += 1

    capacidad = {
        "lunes_viernes": 0,
        "sabado": 0,
        "total": 0,
        "detalle": [],
    }

    if batch.configuracion_semana_id:

        capacidad = calcular_capacidad_configuracion(batch.configuracion_semana)

    objetivo = batch.objetivo_sitios

    diferencia_objetivo = principales - objetivo

    return {
        "objetivo": objetivo,
        "total_activos": total_activos,
        "principales": principales,
        "reservas": reservas,
        "candidatos": (contador_estados["candidato"]),
        "seleccionados": (contador_estados["seleccionado"]),
        "gestion_permiso": (contador_estados["gestion_permiso"]),
        "disponibles": (contador_estados["disponible"]),
        "rechazados": (contador_estados["rechazado"]),
        "sin_respuesta": (contador_estados["sin_respuesta"]),
        "excluidos": (contador_estados["excluido"]),
        "reemplazados": (contador_estados["reemplazado"]),
        "confirmados": (contador_estados["confirmado"]),
        "urbanos": urbanos,
        "rurales": rurales,
        "sin_tipo_zona": (sin_tipo_zona),
        "comunas": (contador_comunas.most_common()),
        "permisos": dict(contador_permisos),
        "capacidad": capacidad,
        "diferencia_objetivo": (diferencia_objetivo),
        "faltan_para_objetivo": max(
            objetivo - principales,
            0,
        ),
        "sobre_objetivo": max(
            principales - objetivo,
            0,
        ),
    }


# ============================================================
# RESUMEN GENERAL DEL MES
# ============================================================


def obtener_resumen_planificacion_mensual(
    planificacion,
):
    """
    Resume una planificación mensual respetando la nueva
    arquitectura de semanas multimes.

    La ocupación semanal se determina por SitioMovil físico,
    no exclusivamente por SitioPlanificado.
    """

    sitios_mes = SitioPlanificado.objects.filter(
        planificacion=planificacion,
        activo_en_mes=True,
    )

    total_mes = sitios_mes.count()

    completados = sitios_mes.filter(
        estado="completado",
    ).count()

    bloqueados = sitios_mes.filter(
        estado="bloqueado",
    ).count()

    aprobados = sitios_mes.filter(
        estado_permiso="aprobado",
    ).count()

    no_requiere = sitios_mes.filter(
        estado_permiso="no_requiere",
    ).count()

    en_gestion = sitios_mes.filter(
        estado_permiso__in=[
            "solicitado",
            "en_espera",
        ],
    ).count()

    # ========================================================
    # SITIOS FÍSICOS COMPROMETIDOS EN CUALQUIER BATCH
    # ========================================================

    sitios_fisicos_comprometidos = (
        SitioBatchSemanal.objects.filter(
            estado__in=ESTADOS_BATCH_ACTIVOS_SITIO,
        )
        .values_list(
            "sitio_planificado__sitio_id",
            flat=True,
        )
        .distinct()
    )

    # ========================================================
    # CUÁNTOS SITIOS DE ESTE MES ESTÁN COMPROMETIDOS
    # ========================================================

    comprometidos = (
        sitios_mes.filter(
            sitio_id__in=sitios_fisicos_comprometidos,
        )
        .values("sitio_id")
        .distinct()
        .count()
    )

    # ========================================================
    # DISPONIBLES REALES
    # ========================================================

    disponibles_nuevo_batch = (
        sitios_mes.exclude(
            estado__in=[
                "completado",
                "cancelado",
            ],
        )
        .exclude(
            sitio_id__in=sitios_fisicos_comprometidos,
        )
        .count()
    )

    return {
        "total_mes": total_mes,
        "completados": completados,
        "bloqueados": bloqueados,
        "aprobados": aprobados,
        "no_requiere": no_requiere,
        "en_gestion": en_gestion,
        "comprometidos_batches": comprometidos,
        "disponibles_nuevo_batch": (disponibles_nuevo_batch),
    }

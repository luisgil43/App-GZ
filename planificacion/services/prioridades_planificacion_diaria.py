# planificacion/services/prioridades_planificacion_diaria.py

from math import asin, cos, radians, sin, sqrt

from planificacion.modelos import (PrioridadPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
from planificacion.services.motor_batch_semanal.cuadrillas import (
    construir_configuracion_cuadrilla, cuadrilla_puede_ejecutar_sitio)

# ============================================================
# CONSTANTES
# ============================================================

ORDEN_PRIORIDAD = {
    "critica": 0,
    "alta": 1,
    "normal": 2,
}


ESTADOS_PARTICIPACION_NO_ACTIVA = {
    "retirado",
    "reprogramado",
    "cancelado",
}


# ============================================================
# CONVERSIÓN A SITIO MOTOR
# ============================================================


def _sitio_batch_a_motor(
    item_batch,
):
    """
    Import diferido para evitar dependencia circular entre:

        planificacion_diaria
            ->
        prioridades_planificacion_diaria
            ->
        planificacion_diaria

    La conversión continúa utilizando exactamente el helper
    oficial del motor diario. No duplicamos su lógica.
    """

    from planificacion.services.planificacion_diaria import \
        _sitio_batch_a_motor as convertir_sitio_batch_a_motor

    return convertir_sitio_batch_a_motor(
        item_batch,
    )


# ============================================================
# DETERMINAR SI UNA PROGRAMACIÓN SERÁ RECALCULADA
# ============================================================


def _participacion_pertenece_a_salida_recalculable(
    participacion,
):
    """
    Determina si una participación existente pertenece a una
    salida AUTOMÁTICA que será sustituida durante el próximo
    recálculo del motor diario.

    REGLA FUNDAMENTAL
    ==========================================================

    Una prioridad dentro de una salida automática editable:

        origen = motor
        estado = borrador / lista_asignar
        no bloqueada
        sitios no bloqueados

    NO puede considerarse definitivamente satisfecha.

    ¿Por qué?

    Porque guardar_plan_diario_batch() posteriormente elimina
    las salidas automáticas editables para reemplazarlas por
    una nueva propuesta.

    Si la consideráramos satisfecha antes de esa limpieza:

        1. no volveríamos a generar la prioridad;
        2. eliminaríamos su salida antigua;
        3. el sitio prioritario desaparecería de la propuesta.

    En cambio:

    - salida manual;
    - salida bloqueada;
    - sitio bloqueado;
    - salida asignada;
    - salida en ejecución;
    - salida parcial;
    - salida finalizada;

    representan decisiones o compromisos que deben conservarse.

    El import es diferido para evitar dependencia circular.
    """

    if participacion is None:
        return False

    salida = participacion.salida

    # ========================================================
    # UNA DECISIÓN MANUAL NO SE REINTERPRETA COMO PROPUESTA
    # AUTOMÁTICA AUNQUE POR CONFIGURACIÓN ESTUVIESE DESBLOQUEADA.
    # ========================================================

    if salida.origen != "motor":
        return False

    # ========================================================
    # UTILIZAR LA MISMA REGLA OFICIAL DEL MOTOR DIARIO
    # ========================================================

    from planificacion.services.planificacion_diaria import \
        salida_es_editable_por_motor

    return salida_es_editable_por_motor(
        salida,
    )


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


def _distancia_haversine_km(
    latitud_1,
    longitud_1,
    latitud_2,
    longitud_2,
):
    """
    Distancia geográfica aproximada entre dos puntos.

    Esta distancia NO sustituye la distancia vial.

    Se utiliza solamente para ordenar candidatos alrededor
    del sitio ancla antes de ejecutar el cálculo operacional.
    """

    latitud_1 = _float_seguro(latitud_1)
    longitud_1 = _float_seguro(longitud_1)
    latitud_2 = _float_seguro(latitud_2)
    longitud_2 = _float_seguro(longitud_2)

    if None in {
        latitud_1,
        longitud_1,
        latitud_2,
        longitud_2,
    }:
        return None

    radio_tierra_km = 6371.0

    latitud_1_rad = radians(latitud_1)
    latitud_2_rad = radians(latitud_2)

    delta_latitud = radians(latitud_2 - latitud_1)

    delta_longitud = radians(longitud_2 - longitud_1)

    a = (
        sin(delta_latitud / 2) ** 2
        + cos(latitud_1_rad) * cos(latitud_2_rad) * sin(delta_longitud / 2) ** 2
    )

    c = 2 * asin(sqrt(a))

    return radio_tierra_km * c


# ============================================================
# PRIORIDADES ACTIVAS
# ============================================================


def obtener_prioridades_activas_batch(
    batch,
):
    """
    Devuelve las prioridades activas del batch.

    Solamente devuelve sitios todavía vigentes dentro del batch.
    """

    prioridades = list(
        PrioridadPlanificacionDiaria.objects.filter(
            sitio_batch__batch=batch,
            estado="activa",
        )
        .exclude(
            sitio_batch__estado__in=[
                "excluido",
                "reemplazado",
            ]
        )
        .select_related(
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
            "cuadrilla_obligatoria",
        )
        .order_by(
            "id",
        )
    )

    prioridades.sort(
        key=lambda prioridad: (
            ORDEN_PRIORIDAD.get(
                prioridad.prioridad,
                99,
            ),
            prioridad.fecha_objetivo is None,
            prioridad.fecha_objetivo,
            prioridad.pk,
        )
    )

    return prioridades


# ============================================================
# MAPA DE PRIORIDADES
# ============================================================


def construir_mapa_prioridades_batch(
    batch,
):
    return {
        prioridad.sitio_batch_id: prioridad
        for prioridad in obtener_prioridades_activas_batch(batch)
    }


# ============================================================
# CUADRILLA OBLIGATORIA
# ============================================================


def disponibilidad_cumple_prioridad(
    *,
    disponibilidad,
    prioridad,
):
    """
    Determina si una disponibilidad semanal puede ejecutar
    el sitio prioritario.

    Reglas:

    1. Si existe cuadrilla obligatoria:
       debe coincidir exactamente.

    2. Aunque coincida la cuadrilla:
       debe seguir siendo compatible con urbano/rural.
    """

    if prioridad.cuadrilla_obligatoria_id:

        if disponibilidad.cuadrilla_operativa_id != prioridad.cuadrilla_obligatoria_id:
            return False

    sitio_motor = _sitio_batch_a_motor(
        prioridad.sitio_batch,
    )

    configuracion = construir_configuracion_cuadrilla(
        disponibilidad,
    )

    return cuadrilla_puede_ejecutar_sitio(
        configuracion,
        sitio_motor,
    )


# ============================================================
# DISPONIBILIDADES COMPATIBLES CON PRIORIDAD
# ============================================================


def obtener_disponibilidades_prioridad(
    *,
    prioridad,
    disponibilidades,
):
    resultado = []

    for disponibilidad in disponibilidades:

        if not disponibilidad.activa:
            continue

        if disponibilidad_cumple_prioridad(
            disponibilidad=disponibilidad,
            prioridad=prioridad,
        ):
            resultado.append(disponibilidad)

    return resultado


# ============================================================
# FECHA DE PRIORIDAD
# ============================================================


def fecha_valida_para_prioridad(
    *,
    prioridad,
    fecha,
):
    """
    Si la fecha es obligatoria, solamente esa fecha es válida.

    Si existe fecha objetivo no obligatoria:
    la fecha objetivo se considera preferida, pero otras fechas
    siguen siendo permitidas.

    Si no hay fecha:
    cualquier fecha operacional puede ser utilizada.
    """

    if prioridad.fecha_es_obligatoria and prioridad.fecha_objetivo:
        return fecha == prioridad.fecha_objetivo

    return True


# ============================================================
# SCORE DE FECHA PARA PRIORIDAD
# ============================================================


def score_fecha_prioridad(
    *,
    prioridad,
    fecha,
):
    """
    Mayor score = fecha más deseable.
    """

    if prioridad.fecha_objetivo is None:
        return 50

    if fecha == prioridad.fecha_objetivo:
        return 100

    diferencia = abs((fecha - prioridad.fecha_objetivo).days)

    return max(
        100 - diferencia * 20,
        0,
    )


# ============================================================
# PARTICIPACIÓN DIARIA ACTIVA DE UNA PRIORIDAD
# ============================================================


def obtener_participacion_activa_prioridad(
    prioridad,
):
    """
    Busca si el sitio prioritario ya se encuentra programado
    dentro de una salida diaria activa.

    No consideramos como programación vigente:

        retirado
        reprogramado
        cancelado

    Esto permite distinguir correctamente:

        prioridad pendiente
        prioridad ya satisfecha
        prioridad programada pero incumplida
    """

    return (
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch=prioridad.sitio_batch,
        )
        .exclude(
            estado__in=ESTADOS_PARTICIPACION_NO_ACTIVA,
        )
        .select_related(
            "salida",
            "salida__batch",
            "salida__disponibilidad_cuadrilla",
            ("salida__" "disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        )
        .prefetch_related(
            "salida__sitios",
        )
        .order_by(
            "-salida__fecha",
            "-id",
        )
        .first()
    )


# ============================================================
# EVALUAR PRIORIDAD YA PROGRAMADA
# ============================================================


def evaluar_prioridad_programada(
    *,
    prioridad,
    participacion,
):
    """
    Evalúa si una programación diaria existente satisface
    realmente la prioridad.

    IMPORTANTE
    ==========================================================

    Si existe una programación manual o protegida previa,
    NO la movemos aquí.

    Solamente informamos si:

        satisface la prioridad

    o si:

        incumple alguna condición obligatoria.

    Las condiciones obligatorias actuales son:

    - pertenecer al mismo batch;
    - respetar fecha obligatoria;
    - respetar cuadrilla obligatoria;
    - seguir siendo territorialmente compatible.
    """

    salida = participacion.salida

    disponibilidad = salida.disponibilidad_cuadrilla

    incumplimientos = []

    advertencias = []

    # ========================================================
    # BATCH
    # ========================================================

    if salida.batch_id != prioridad.sitio_batch.batch_id:

        incumplimientos.append(
            ("La salida existente pertenece " "a otro batch semanal.")
        )

    # ========================================================
    # FECHA OBLIGATORIA
    # ========================================================

    if (
        prioridad.fecha_es_obligatoria
        and prioridad.fecha_objetivo
        and salida.fecha != prioridad.fecha_objetivo
    ):

        incumplimientos.append(
            (
                "La prioridad exige ejecución el "
                f"{prioridad.fecha_objetivo:%d/%m/%Y}, "
                "pero actualmente está programada para "
                f"{salida.fecha:%d/%m/%Y}."
            )
        )

    # ========================================================
    # FECHA PREFERIDA NO OBLIGATORIA
    # ========================================================

    elif (
        prioridad.fecha_objetivo
        and not prioridad.fecha_es_obligatoria
        and salida.fecha != prioridad.fecha_objetivo
    ):

        advertencias.append(
            (
                "La fecha objetivo preferida era "
                f"{prioridad.fecha_objetivo:%d/%m/%Y}, "
                "pero actualmente está programada para "
                f"{salida.fecha:%d/%m/%Y}."
            )
        )

    # ========================================================
    # CUADRILLA OBLIGATORIA
    # ========================================================

    if prioridad.cuadrilla_obligatoria_id:

        if disponibilidad.cuadrilla_operativa_id != prioridad.cuadrilla_obligatoria_id:

            incumplimientos.append(
                (
                    "La prioridad exige la cuadrilla "
                    f"{prioridad.cuadrilla_obligatoria}, "
                    "pero actualmente está programada con "
                    f"{disponibilidad.nombre_cuadrilla}."
                )
            )

    # ========================================================
    # COMPATIBILIDAD TERRITORIAL
    # ========================================================

    try:

        compatible = disponibilidad_cumple_prioridad(
            disponibilidad=disponibilidad,
            prioridad=prioridad,
        )

    except Exception:

        compatible = False

    if not compatible:

        incumplimientos.append(
            (
                f"{disponibilidad.nombre_cuadrilla} "
                "no cumple actualmente las condiciones "
                "territoriales configuradas para el sitio."
            )
        )

    satisfecha = not incumplimientos

    return {
        "prioridad": prioridad,
        "participacion": participacion,
        "salida": salida,
        "disponibilidad": disponibilidad,
        "fecha": salida.fecha,
        "cuadrilla_codigo": (disponibilidad.codigo_cuadrilla),
        "cuadrilla_nombre": (disponibilidad.nombre_cuadrilla),
        "estado_participacion": (participacion.estado),
        "estado_salida": (salida.estado),
        "origen_participacion": (participacion.origen),
        "origen_salida": (salida.origen),
        "bloqueado_sitio": (participacion.bloqueado),
        "bloqueada_salida": (salida.bloqueada),
        "satisfecha": satisfecha,
        "incumplimientos": incumplimientos,
        "advertencias": advertencias,
    }


# ============================================================
# CANDIDATOS ALREDEDOR DEL ANCLA
# ============================================================


def obtener_candidatos_alrededor_prioridad(
    *,
    prioridad,
    items_disponibles,
):
    """
    Devuelve sitios disponibles ordenados por cercanía al ancla.

    El sitio prioritario NO se incluye como candidato porque
    ya funciona como ancla.

    Cada elemento contiene:

        sitio_batch
        distancia_ancla_km
        dentro_preferida
        dentro_maxima
    """

    sitio_ancla = prioridad.sitio_batch.sitio_planificado.sitio

    latitud_ancla = _float_seguro(sitio_ancla.latitud)

    longitud_ancla = _float_seguro(sitio_ancla.longitud)

    distancia_preferida = float(prioridad.distancia_preferida_km or 0)

    distancia_maxima = float(prioridad.distancia_maxima_km or 0)

    resultado = []

    for item in items_disponibles:

        if item.pk == prioridad.sitio_batch_id:
            continue

        sitio = item.sitio_planificado.sitio

        distancia = _distancia_haversine_km(
            latitud_ancla,
            longitud_ancla,
            sitio.latitud,
            sitio.longitud,
        )

        if distancia is None:
            continue

        resultado.append(
            {
                "sitio_batch": item,
                "distancia_ancla_km": round(
                    distancia,
                    2,
                ),
                "dentro_preferida": (distancia <= distancia_preferida),
                "dentro_maxima": (distancia <= distancia_maxima),
            }
        )

    resultado.sort(
        key=lambda dato: (
            not dato["dentro_preferida"],
            not dato["dentro_maxima"],
            dato["distancia_ancla_km"],
            dato["sitio_batch"].pk,
        )
    )

    return resultado


# ============================================================
# SEPARAR CANDIDATOS POR ZONA
# ============================================================


def clasificar_candidatos_prioridad(
    *,
    prioridad,
    items_disponibles,
):
    candidatos = obtener_candidatos_alrededor_prioridad(
        prioridad=prioridad,
        items_disponibles=items_disponibles,
    )

    preferidos = []

    tolerables = []

    excepcion = []

    for candidato in candidatos:

        if candidato["dentro_preferida"]:

            preferidos.append(candidato)

        elif candidato["dentro_maxima"]:

            tolerables.append(candidato)

        else:

            excepcion.append(candidato)

    return {
        "preferidos": preferidos,
        "tolerables": tolerables,
        "excepcion": excepcion,
        "todos": candidatos,
    }


# ============================================================
# NECESIDAD DE CONFIRMACIÓN
# ============================================================


def prioridad_requiere_confirmacion_por_candidatos(
    *,
    prioridad,
    candidatos_seleccionados,
):
    """
    Decide si la selección alrededor del ancla requiere
    confirmación humana.

    Casos:

    - 3 sitios dentro de radio preferido:
        NO.

    - usa sitio fuera del radio preferido pero dentro máximo:
        depende de requiere_confirmacion_excepcion.

    - usa sitio fuera del máximo:
        SIEMPRE.

    - queda con 1 sitio:
        SIEMPRE salvo configuración expresa.

    - queda con 2 sitios:
        depende de configuración.
    """

    total_salida = len(candidatos_seleccionados) + 1

    # ========================================================
    # SOLO EL ANCLA
    # ========================================================

    if total_salida == 1:

        if not prioridad.permitir_salida_1_sitio:
            return True

        return bool(prioridad.requiere_confirmacion_excepcion)

    # ========================================================
    # DOS SITIOS
    # ========================================================

    if total_salida == 2:

        if not prioridad.permitir_salida_2_sitios:
            return True

    # ========================================================
    # DISTANCIAS
    # ========================================================

    for candidato in candidatos_seleccionados:

        if not candidato["dentro_maxima"]:
            return True

        if (
            not candidato["dentro_preferida"]
            and prioridad.requiere_confirmacion_excepcion
        ):
            return True

    return False


# ============================================================
# CONSTRUIR PROPUESTA BASE DE UNA PRIORIDAD
# ============================================================


def construir_propuesta_base_prioridad(
    *,
    prioridad,
    items_disponibles,
    participacion_existente=None,
):
    """
    Construye la intención territorial de una prioridad.

    Puede trabajar en dos escenarios:

    1. PRIORIDAD TODAVÍA SIN SALIDA PROTEGIDA

       El ancla todavía debe ser colocada por el motor.

       Ejemplo:

           ancla
           + acompañante
           + acompañante

    2. PRIORIDAD YA UBICADA EN UNA SALIDA PROTEGIDA

       Ejemplo:

           lunes
           C1
           05_067

       En este caso NO movemos:

           fecha
           cuadrilla
           salida
           ancla

       Únicamente intentamos completar la salida hasta el
       objetivo configurado.

       Ejemplo:

           1. 05_067  ← prioridad
           2. 05_366
           3. 05_098

    IMPORTANTE
    ==========================================================

    El sitio prioritario continúa siendo siempre el ANCLA.

    Los acompañantes se seleccionan alrededor de él.

    Esta función todavía NO guarda nada.
    """

    # ========================================================
    # CLASIFICAR CANDIDATOS ALREDEDOR DEL ANCLA
    # ========================================================

    clasificados = clasificar_candidatos_prioridad(
        prioridad=prioridad,
        items_disponibles=items_disponibles,
    )

    # ========================================================
    # OBJETIVO
    # ========================================================

    try:
        objetivo_total = int(prioridad.objetivo_sitios_salida or 3)

    except (
        TypeError,
        ValueError,
    ):
        objetivo_total = 3

    objetivo_total = max(
        1,
        min(
            objetivo_total,
            3,
        ),
    )

    # ========================================================
    # SITIOS YA EXISTENTES EN LA SALIDA
    # ========================================================

    sitios_existentes = []

    salida_existente = None

    completar_salida_existente = False

    if participacion_existente is not None:

        salida_existente = participacion_existente.salida

        completar_salida_existente = True

        participaciones_activas = (
            salida_existente.sitios.exclude(
                estado__in=ESTADOS_PARTICIPACION_NO_ACTIVA,
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

        sitios_existentes = [
            participacion.sitio_batch for participacion in participaciones_activas
        ]

    # ========================================================
    # ASEGURAR QUE EL ANCLA ESTÉ PRIMERO
    # ========================================================

    mapa_existentes = {item.pk: item for item in sitios_existentes}

    ancla = prioridad.sitio_batch

    sitios_existentes_ordenados = [
        ancla,
    ]

    for item in sitios_existentes:

        if item.pk == ancla.pk:
            continue

        sitios_existentes_ordenados.append(item)

    sitios_existentes = sitios_existentes_ordenados

    ids_existentes = {item.pk for item in sitios_existentes}

    # ========================================================
    # CUÁNTOS ACOMPAÑANTES FALTAN
    # ========================================================

    cantidad_actual = len(ids_existentes)

    cantidad_acompanantes = max(
        objetivo_total - cantidad_actual,
        0,
    )

    seleccionados = []

    # ========================================================
    # PRIMERO PREFERIDOS
    # ========================================================

    for candidato in clasificados["preferidos"]:

        item = candidato["sitio_batch"]

        if item.pk in ids_existentes:
            continue

        if len(seleccionados) >= cantidad_acompanantes:
            break

        seleccionados.append(candidato)

    # ========================================================
    # DESPUÉS TOLERABLES
    # ========================================================

    if len(seleccionados) < cantidad_acompanantes:

        for candidato in clasificados["tolerables"]:

            item = candidato["sitio_batch"]

            if item.pk in ids_existentes:
                continue

            if len(seleccionados) >= cantidad_acompanantes:
                break

            seleccionados.append(candidato)

    # ========================================================
    # TOTAL RESULTANTE
    # ========================================================

    total_resultante = cantidad_actual + len(seleccionados)

    # ========================================================
    # CONFIRMACIÓN
    # ========================================================
    #
    # La función original calcula la salida pensando en:
    #
    #   ancla + candidatos seleccionados
    #
    # Cuando ya existen otros sitios dentro de la salida,
    # solamente debemos evaluar como excepción los nuevos
    # candidatos incorporados.
    # ========================================================

    if total_resultante >= objetivo_total:

        requiere_confirmacion = False

        for candidato in seleccionados:

            if not candidato["dentro_maxima"]:

                requiere_confirmacion = True

                break

            if (
                not candidato["dentro_preferida"]
                and prioridad.requiere_confirmacion_excepcion
            ):

                requiere_confirmacion = True

                break

    else:

        # ====================================================
        # SALIDA INCOMPLETA
        # ====================================================

        if total_resultante == 1:

            if not prioridad.permitir_salida_1_sitio:

                requiere_confirmacion = True

            else:

                requiere_confirmacion = bool(prioridad.requiere_confirmacion_excepcion)

        elif total_resultante == 2:

            if not prioridad.permitir_salida_2_sitios:

                requiere_confirmacion = True

            else:

                requiere_confirmacion = False

                for candidato in seleccionados:

                    if not candidato["dentro_maxima"]:

                        requiere_confirmacion = True

                        break

                    if (
                        not candidato["dentro_preferida"]
                        and prioridad.requiere_confirmacion_excepcion
                    ):

                        requiere_confirmacion = True

                        break

        else:

            requiere_confirmacion = False

    # ========================================================
    # ADVERTENCIAS
    # ========================================================

    advertencias = []

    if total_resultante < objetivo_total:

        faltantes = objetivo_total - total_resultante

        advertencias.append(
            (
                f"{prioridad.id_claro}: "
                "no existen suficientes sitios "
                "dentro de la distancia automática "
                f"configurada. Faltan {faltantes} "
                "sitio(s) para completar el objetivo "
                f"de {objetivo_total}."
            )
        )

        if clasificados["excepcion"]:

            candidatos_excepcion = clasificados["excepcion"]

            distancias = [
                candidato["distancia_ancla_km"]
                for candidato in candidatos_excepcion[:3]
            ]

            advertencias.append(
                (
                    "Existen sitios más alejados que "
                    "podrían evaluarse manualmente. "
                    "Distancias directas aproximadas "
                    "desde el ancla: "
                    + ", ".join(f"{distancia:.1f} km" for distancia in distancias)
                    + "."
                )
            )

    # ========================================================
    # UTILIZA TOLERABLES
    # ========================================================

    if any(not candidato["dentro_preferida"] for candidato in seleccionados):

        advertencias.append(
            (
                f"{prioridad.id_claro}: "
                "para completar la salida fue "
                "necesario utilizar al menos un "
                "sitio fuera del radio preferido."
            )
        )

    # ========================================================
    # SALIDA EXISTENTE
    # ========================================================

    if completar_salida_existente and seleccionados:

        advertencias.append(
            (
                f"{prioridad.id_claro}: "
                "la salida protegida existente será "
                f"completada con {len(seleccionados)} "
                "sitio(s) adicional(es), conservando "
                "su fecha y cuadrilla actuales."
            )
        )

    return {
        "prioridad": prioridad,
        "sitio_ancla": ancla,
        "acompanantes": seleccionados,
        "sitios_existentes": sitios_existentes,
        "cantidad_existente": cantidad_actual,
        "cantidad_total": total_resultante,
        "objetivo_total": objetivo_total,
        "requiere_confirmacion": (requiere_confirmacion),
        "advertencias": advertencias,
        "candidatos_preferidos": (clasificados["preferidos"]),
        "candidatos_tolerables": (clasificados["tolerables"]),
        "candidatos_excepcion": (clasificados["excepcion"]),
        "completar_salida_existente": (completar_salida_existente),
        "salida_existente": salida_existente,
    }


# ============================================================
# RESOLVER PRIORIDADES DEL BATCH
# ============================================================


def resolver_prioridades_batch(
    *,
    batch,
    items_disponibles,
    disponibilidades,
):
    """
    Resuelve prioridades del motor diario.

    REGLA NUEVA
    ==========================================================

    Una prioridad dentro de una salida protegida NO se
    considera completamente satisfecha solamente porque:

        fecha correcta
        +
        cuadrilla correcta.

    También evaluamos si la salida alcanzó su objetivo
    operacional.

    Ejemplo:

        objetivo = 3

        lunes C1:
            1. 05_067 prioritario

    La prioridad está correctamente ubicada, pero la salida
    todavía puede completarse.

    Resultado:

        conservar lunes
        conservar C1
        conservar 05_067 como sitio 1

        +
        buscar hasta 2 acompañantes.

    Cuando ya existen 3 sitios:

        prioridad satisfecha
        no modificar.
    """

    prioridades = obtener_prioridades_activas_batch(batch)

    if not prioridades:

        return {
            "prioridades": [],
            "prioridades_satisfechas": [],
            "prioridades_incumplidas": [],
            "sitios_reservados_ids": set(),
            "advertencias": [],
        }

    disponibles_por_id = {item.pk: item for item in items_disponibles}

    sitios_reservados_ids = set()

    propuestas = []

    prioridades_satisfechas = []

    prioridades_incumplidas = []

    advertencias = []

    # ========================================================
    # RECORRER PRIORIDADES
    # ========================================================

    for prioridad in prioridades:

        sitio_batch_id = prioridad.sitio_batch_id

        # ====================================================
        # PROGRAMACIÓN EXISTENTE
        # ====================================================

        participacion_existente = obtener_participacion_activa_prioridad(prioridad)

        # ====================================================
        # ¿ES UNA PROPUESTA AUTOMÁTICA RECALCULABLE?
        # ====================================================

        programacion_recalculable = False

        if participacion_existente is not None:

            programacion_recalculable = _participacion_pertenece_a_salida_recalculable(
                participacion_existente
            )

        # ====================================================
        # PROGRAMACIÓN PROTEGIDA
        # ========================================================

        if participacion_existente is not None and not programacion_recalculable:

            evaluacion = evaluar_prioridad_programada(
                prioridad=prioridad,
                participacion=(participacion_existente),
            )

            # =================================================
            # PROGRAMACIÓN INCUMPLIDA
            # =================================================

            if not evaluacion["satisfecha"]:

                salida_existente = participacion_existente.salida

                participaciones_activas = salida_existente.sitios.exclude(
                    estado__in=(ESTADOS_PARTICIPACION_NO_ACTIVA),
                )

                for participacion in participaciones_activas:

                    sitios_reservados_ids.add(participacion.sitio_batch_id)

                prioridades_incumplidas.append(evaluacion)

                for incumplimiento in evaluacion["incumplimientos"]:

                    advertencias.append(
                        (
                            f"{prioridad.id_claro}: "
                            "prioridad programada pero "
                            f"incumplida. {incumplimiento}"
                        )
                    )

                for advertencia in evaluacion["advertencias"]:

                    advertencias.append((f"{prioridad.id_claro}: " f"{advertencia}"))

                continue

            # =================================================
            # PROGRAMACIÓN CORRECTA
            # =================================================

            salida_existente = participacion_existente.salida

            participaciones_activas = list(
                salida_existente.sitios.exclude(
                    estado__in=(ESTADOS_PARTICIPACION_NO_ACTIVA),
                )
                .select_related(
                    "sitio_batch",
                    ("sitio_batch__" "sitio_planificado"),
                    ("sitio_batch__" "sitio_planificado__" "sitio"),
                )
                .order_by(
                    "orden",
                    "id",
                )
            )

            # =================================================
            # RESERVAR LO QUE YA EXISTE
            # =================================================

            for participacion in participaciones_activas:

                sitios_reservados_ids.add(participacion.sitio_batch_id)

            # =================================================
            # OBJETIVO
            # =================================================

            try:

                objetivo_total = int(prioridad.objetivo_sitios_salida or 3)

            except (
                TypeError,
                ValueError,
            ):

                objetivo_total = 3

            objetivo_total = max(
                1,
                min(
                    objetivo_total,
                    3,
                ),
            )

            cantidad_actual = len(participaciones_activas)

            # =================================================
            # YA ESTÁ COMPLETA
            # =================================================

            if cantidad_actual >= objetivo_total:

                prioridades_satisfechas.append(evaluacion)

                for advertencia in evaluacion["advertencias"]:

                    advertencias.append((f"{prioridad.id_claro}: " f"{advertencia}"))

                continue

            # =================================================
            # SALIDA PROTEGIDA PERO INCOMPLETA
            # =================================================
            #
            # Conservamos:
            #
            #   fecha
            #   cuadrilla
            #   salida
            #   ancla
            #
            # y buscamos acompañantes.
            # =================================================

            items_libres = [
                item
                for item in items_disponibles
                if (item.pk not in sitios_reservados_ids)
            ]

            propuesta = construir_propuesta_base_prioridad(
                prioridad=prioridad,
                items_disponibles=items_libres,
                participacion_existente=(participacion_existente),
            )

            disponibilidad_existente = salida_existente.disponibilidad_cuadrilla

            propuesta["disponibilidades_validas"] = [
                disponibilidad_existente,
            ]

            propuesta["sin_cuadrilla_compatible"] = False

            propuesta["estado_resolucion"] = "completar_existente"

            propuesta["programacion_existente"] = True

            propuesta["programacion_recalculable"] = False

            propuesta["participacion_reemplazada"] = None

            propuesta["salida_reemplazada"] = None

            propuesta["participacion_existente"] = participacion_existente

            # =================================================
            # RESERVAR ACOMPAÑANTES
            # =================================================

            for candidato in propuesta["acompanantes"]:

                sitios_reservados_ids.add(candidato["sitio_batch"].pk)

            propuestas.append(propuesta)

            advertencias.extend(propuesta["advertencias"])

            continue

        # ====================================================
        # PRIORIDAD PENDIENTE NORMAL
        # ====================================================

        sitio_batch = disponibles_por_id.get(sitio_batch_id)

        if sitio_batch is None:

            if programacion_recalculable:

                advertencias.append(
                    (
                        f"{prioridad.id_claro}: "
                        "la prioridad se encuentra dentro "
                        "de una salida automática editable, "
                        "pero el sitio no apareció en el "
                        "universo actual del recálculo."
                    )
                )

            else:

                advertencias.append(
                    (
                        f"{prioridad.id_claro}: posee "
                        "prioridad activa, no tiene una "
                        "programación diaria protegida y "
                        "actualmente no está disponible "
                        "para planificación diaria."
                    )
                )

            continue

        if sitio_batch.pk in sitios_reservados_ids:
            continue

        # ====================================================
        # UNIVERSO LIBRE
        # ====================================================

        items_libres = [
            item for item in items_disponibles if (item.pk not in sitios_reservados_ids)
        ]

        # ====================================================
        # PROPUESTA
        # ====================================================

        propuesta = construir_propuesta_base_prioridad(
            prioridad=prioridad,
            items_disponibles=items_libres,
        )

        # ====================================================
        # CUADRILLAS
        # ====================================================

        disponibilidades_validas = obtener_disponibilidades_prioridad(
            prioridad=prioridad,
            disponibilidades=(disponibilidades),
        )

        if not disponibilidades_validas:

            advertencias.append(
                (
                    f"{prioridad.id_claro}: "
                    "ninguna cuadrilla activa de "
                    "esta semana puede ejecutar "
                    "la prioridad configurada."
                )
            )

            propuesta["disponibilidades_validas"] = []

            propuesta["sin_cuadrilla_compatible"] = True

        else:

            propuesta["disponibilidades_validas"] = disponibilidades_validas

            propuesta["sin_cuadrilla_compatible"] = False

        # ====================================================
        # ESTADO
        # ====================================================

        propuesta["estado_resolucion"] = "pendiente"

        propuesta["programacion_existente"] = participacion_existente is not None

        propuesta["programacion_recalculable"] = programacion_recalculable

        if programacion_recalculable:

            propuesta["participacion_reemplazada"] = participacion_existente

            propuesta["salida_reemplazada"] = participacion_existente.salida

        else:

            propuesta["participacion_reemplazada"] = None

            propuesta["salida_reemplazada"] = None

        propuesta["participacion_existente"] = participacion_existente

        # ====================================================
        # RESERVAR ANCLA
        # ====================================================

        sitios_reservados_ids.add(sitio_batch.pk)

        # ====================================================
        # RESERVAR ACOMPAÑANTES
        # ====================================================

        for candidato in propuesta["acompanantes"]:

            sitios_reservados_ids.add(candidato["sitio_batch"].pk)

        propuestas.append(propuesta)

        advertencias.extend(propuesta["advertencias"])

    return {
        "prioridades": propuestas,
        "prioridades_satisfechas": (prioridades_satisfechas),
        "prioridades_incumplidas": (prioridades_incumplidas),
        "sitios_reservados_ids": (sitios_reservados_ids),
        "advertencias": advertencias,
    }

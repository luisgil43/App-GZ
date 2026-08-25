# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

MINUTOS_JORNADA_REFERENCIA = 9 * 60
MINUTOS_TRABAJO_SITIO_REFERENCIA = 165

CAPACIDAD_DIARIA_REFERENCIA = 3


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(valor):
    try:
        return float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _entero_positivo(
    valor,
    defecto,
):
    try:
        valor = int(valor)

    except (
        TypeError,
        ValueError,
    ):
        return defecto

    if valor <= 0:
        return defecto

    return valor


# ============================================================
# CONFIGURACIÓN OPERACIONAL
# ============================================================


def construir_configuracion_cuadrilla(
    disponibilidad,
):
    """
    Convierte DisponibilidadCuadrillaSemana en una estructura
    independiente de Django para el motor.

    PRIORIDAD DE BASE:

    1. override semanal;
    2. base habitual de CuadrillaOperativa.

    PRIORIDAD DE TIEMPOS:

    1. override semanal;
    2. valor habitual de CuadrillaOperativa;
    3. referencia general.

    La capacidad diaria continúa siendo una referencia
    operacional máxima para esta cuadrilla.

    No significa que el motor tenga que ejecutar esa cantidad.

    Ejemplo:

    capacidad diaria = 3

    El motor puede concluir:

    - 3 sitios si caben;
    - 2 sitios si el traslado es mayor;
    - 1 sitio si la distancia obliga a ello.
    """

    # ========================================================
    # BASE EFECTIVA
    # ========================================================

    base_latitud = _float_seguro(disponibilidad.base_latitud_efectiva)

    base_longitud = _float_seguro(disponibilidad.base_longitud_efectiva)

    # ========================================================
    # TIEMPOS EFECTIVOS
    # ========================================================

    minutos_jornada = _entero_positivo(
        disponibilidad.minutos_jornada_efectivos,
        MINUTOS_JORNADA_REFERENCIA,
    )

    minutos_sitio = _entero_positivo(
        disponibilidad.minutos_trabajo_sitio_efectivos,
        MINUTOS_TRABAJO_SITIO_REFERENCIA,
    )

    # ========================================================
    # CAPACIDAD NOMINAL
    # ========================================================

    capacidad_diaria = _entero_positivo(
        disponibilidad.capacidad_diaria_objetivo,
        CAPACIDAD_DIARIA_REFERENCIA,
    )

    # ========================================================
    # CUADRILLA MAESTRA
    # ========================================================

    cuadrilla_operativa = (
        disponibilidad.cuadrilla_operativa
        if disponibilidad.cuadrilla_operativa_id
        else None
    )

    return {
        # ====================================================
        # IDENTIDAD
        # ====================================================
        "disponibilidad_id": (disponibilidad.id),
        "cuadrilla_operativa_id": (disponibilidad.cuadrilla_operativa_id),
        "cuadrilla": (disponibilidad.codigo_cuadrilla),
        "cuadrilla_display": (disponibilidad.nombre_cuadrilla),
        # ====================================================
        # DISPONIBILIDAD SEMANAL
        # ====================================================
        "activa": bool(disponibilidad.activa),
        "modalidad": (disponibilidad.modalidad),
        "modalidad_display": (disponibilidad.get_modalidad_display()),
        "trabaja_sabado": (disponibilidad.trabaja_sabado),
        "dias_disponibles": (disponibilidad.dias_disponibles),
        # ====================================================
        # VEHÍCULO / COBERTURA
        # ====================================================
        "tipo_vehiculo": (disponibilidad.tipo_vehiculo),
        "permite_urbano": bool(disponibilidad.permite_urbano),
        "permite_rural": bool(disponibilidad.permite_rural),
        # ====================================================
        # BASE
        # ====================================================
        "base_nombre": (disponibilidad.base_nombre_efectiva),
        "base_latitud": (base_latitud),
        "base_longitud": (base_longitud),
        "tiene_base": (base_latitud is not None and base_longitud is not None),
        # ====================================================
        # TIEMPOS
        # ====================================================
        "minutos_jornada": (minutos_jornada),
        "minutos_trabajo_sitio": (minutos_sitio),
        # ====================================================
        # CAPACIDAD NOMINAL
        # ====================================================
        "capacidad_diaria": (capacidad_diaria),
        # ====================================================
        # INFORMACIÓN MAESTRA ADICIONAL
        # ====================================================
        "direccion_base": (
            (cuadrilla_operativa.direccion_base if cuadrilla_operativa else "") or ""
        ),
    }


# ============================================================
# COMPATIBILIDAD SITIO / CUADRILLA
# ============================================================


def cuadrilla_puede_ejecutar_sitio(
    configuracion,
    sitio,
):
    """
    Evalúa solamente compatibilidad territorial básica.

    La viabilidad de tiempo se calcula posteriormente
    dentro del motor de salidas.
    """

    if not configuracion.get(
        "activa",
        False,
    ):
        return False

    if sitio.rural:
        return bool(
            configuracion.get(
                "permite_rural",
                False,
            )
        )

    if sitio.urbano:
        return bool(
            configuracion.get(
                "permite_urbano",
                True,
            )
        )

    # ========================================================
    # ZONA SIN CLASIFICAR
    # ========================================================
    #
    # No bloqueamos automáticamente el sitio.
    #
    # Puede continuar hacia el análisis operacional,
    # pero posteriormente podremos generar una advertencia.
    # ========================================================

    return True


def cuadrilla_puede_ejecutar_grupo(
    configuracion,
    sitios,
):
    """
    Todos los sitios de una misma salida deben ser
    compatibles con la cuadrilla.
    """

    return all(
        cuadrilla_puede_ejecutar_sitio(
            configuracion,
            sitio,
        )
        for sitio in sitios
    )

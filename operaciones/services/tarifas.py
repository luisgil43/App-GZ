import re
from decimal import ROUND_HALF_UP, Decimal

from operaciones.models import (MontoTecnicoServicio, TarifaActividadTecnico,
                                TarifaTecnico)

INICIO_TARIFAS_INDIVIDUALES = "2026-10"

ESTADOS_AJUSTES = {
    "ajuste_bono",
    "ajuste_adelanto",
    "ajuste_descuento",
}


class TarifaNoConfigurada(Exception):
    pass


# ============================================================
# NORMALIZACIÓN DEL MES DE PRODUCCIÓN
# ============================================================


def normalizar_mes_produccion(valor):
    """
    Convierte diferentes representaciones del mes
    al formato YYYY-MM.
    """

    texto = str(valor or "").strip().lower()

    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", texto)

    if match:
        anio = int(match.group(1))
        mes = int(match.group(2))

        if 1 <= mes <= 12:
            return f"{anio:04d}-{mes:02d}"

    meses = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    match = re.fullmatch(
        r"([a-záéíóú]+)\s+(?:de\s+)?(\d{4})",
        texto,
    )

    if match:
        nombre = match.group(1)
        anio = int(match.group(2))

        if nombre in meses:
            return f"{anio:04d}-{meses[nombre]:02d}"

    return None


# ============================================================
# TARIFAS INDIVIDUALES — SISTEMA ANTERIOR
# ============================================================


def obtener_tarifa_tecnico(tecnico, mes):
    """
    Obtiene la última tarifa individual registrada
    cuya vigencia sea igual o anterior al mes solicitado.

    Se conserva el comportamiento anterior para
    mantener compatibilidad con producción y pagos.
    """

    tarifa = (
        TarifaTecnico.objects.filter(
            tecnico=tecnico,
            mes_vigencia__lte=mes,
        )
        .order_by(
            "-mes_vigencia",
            "-fecha_registro",
            "-id",
        )
        .first()
    )

    if tarifa is None:
        raise TarifaNoConfigurada(
            f"No existe una tarifa configurada para " f"{tecnico} en el período {mes}."
        )

    return tarifa.monto


# ============================================================
# TARIFAS POR ACTIVIDAD — SISTEMA NUEVO
# ============================================================


def obtener_tarifa_actividad_tecnico(
    actividad,
    tecnico,
    mes,
):
    """
    Obtiene la tarifa por actividad correspondiente
    a un trabajador y un mes de producción.

    REGLAS:

    1. La actividad debe estar activa.

    2. Se selecciona la tarifa más reciente cuya
       vigencia sea igual o anterior al mes solicitado.

    3. La selección incluye inicialmente tarifas
       activas y desactivadas.

    4. Si la tarifa seleccionada está desactivada,
       se bloquea su utilización.

    5. Nunca se recupera automáticamente una tarifa
       histórica anterior cuando la seleccionada
       está desactivada.

    6. No modifica servicios, pagos ni registros
       históricos.
    """

    mes_normalizado = normalizar_mes_produccion(mes)

    if mes_normalizado is None:
        raise ValueError("El mes de producción no es válido.")

    if actividad is None:
        raise TarifaNoConfigurada("El servicio no tiene una actividad configurada.")

    if not actividad.activa:
        raise TarifaNoConfigurada(
            f"La actividad '{actividad.nombre}' " "está desactivada."
        )

    tarifa = (
        TarifaActividadTecnico.objects.filter(
            actividad=actividad,
            tecnico=tecnico,
            mes_vigencia__lte=mes_normalizado,
        )
        .order_by(
            "-mes_vigencia",
            "-fecha_registro",
            "-id",
        )
        .first()
    )

    if tarifa is None:
        raise TarifaNoConfigurada(
            f"No existe una tarifa por actividad "
            f"configurada para {tecnico} "
            f"en el período {mes_normalizado}."
        )

    # ========================================================
    # PROTECCIÓN DE TARIFAS DESACTIVADAS
    # ========================================================

    if not tarifa.activa:
        raise TarifaNoConfigurada(
            f"La tarifa de {tecnico} para la actividad "
            f"'{actividad.nombre}' está desactivada. "
            "Debe activarse antes de utilizarla."
        )

    return tarifa.monto


# ============================================================
# CÁLCULO DE IMPORTE INDIVIDUAL DEL TÉCNICO
# ============================================================


def obtener_monto_tecnico(servicio, tecnico):
    """
    Calcula el importe individual correspondiente a un técnico.

    REGLAS:

    1. SERVICIOS HISTÓRICOS:
       Antes de octubre de 2026 se conserva el
       prorrateo histórico de la MMOO.

    2. AJUSTES:
       Bonos, adelantos y descuentos mantienen
       el comportamiento anterior.

    3. TARIFA POR ACTIVIDAD:
       Desde octubre de 2026 utiliza exclusivamente
       el monto individual guardado en
       MontoTecnicoServicio.

    4. MMOO DEFINIDA EN COTIZACIÓN:
       Desde octubre de 2026 utiliza exclusivamente
       el monto individual distribuido y guardado
       en MontoTecnicoServicio.

    5. SERVICIOS SIN MODALIDAD:
       Conservan el cálculo anterior utilizando
       TarifaTecnico.

    6. PROTECCIÓN:
       Nunca recalcula tarifas actuales para
       servicios que ya tienen una modalidad nueva.

    7. Esta función solamente consulta información.
       No modifica servicios, tarifas ni pagos.
    """

    # ========================================================
    # NORMALIZAR MES
    # ========================================================

    mes = normalizar_mes_produccion(servicio.mes_produccion)

    # ========================================================
    # DATOS GENERALES
    # ========================================================

    monto_total = Decimal(str(servicio.monto_mmoo or 0))

    cantidad_tecnicos = servicio.trabajadores_asignados.count() or 1

    # ========================================================
    # 1. AJUSTES Y SERVICIOS HISTÓRICOS
    # ========================================================

    if servicio.estado in ESTADOS_AJUSTES or (
        mes is not None and mes < INICIO_TARIFAS_INDIVIDUALES
    ):

        return (monto_total / Decimal(cantidad_tecnicos)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    # ========================================================
    # 2. VALIDAR MES DE PRODUCCIÓN
    # ========================================================

    if mes is None:

        raise ValueError(
            "No se pudo determinar el mes de producción " f"del servicio {servicio.pk}."
        )

    # ========================================================
    # 3. IDENTIFICAR MODALIDAD DE PAGO
    # ========================================================

    modalidad = (
        getattr(
            servicio,
            "modalidad_pago_tecnicos",
            None,
        )
        or ""
    ).strip()

    # ========================================================
    # 4. MODALIDADES NUEVAS
    # ========================================================

    if modalidad in (
        "tarifa_actividad",
        "mmoo_cotizacion",
    ):

        # ----------------------------------------------------
        # CONSULTAR MONTO INDIVIDUAL REGISTRADO
        # ----------------------------------------------------

        monto_registrado = MontoTecnicoServicio.objects.filter(
            servicio=servicio,
            tecnico=tecnico,
        ).first()

        # ----------------------------------------------------
        # SI EXISTE, DEVOLVER EL MONTO CONGELADO
        # ----------------------------------------------------

        if monto_registrado is not None:

            monto = Decimal(str(monto_registrado.monto or 0))

            return monto.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        # ----------------------------------------------------
        # SI NO EXISTE, NO INVENTAR UN IMPORTE
        # ----------------------------------------------------
        #
        # No consultar tarifas actuales.
        # No dividir la MMOO total.
        #
        # El importe debe haberse registrado
        # durante la asignación.
        # ----------------------------------------------------

        raise ValueError(
            "El servicio "
            f"DU{str(servicio.du).zfill(8)} "
            "utiliza la modalidad "
            f"'{modalidad}', pero no tiene "
            "un monto individual registrado "
            f"para el técnico {tecnico.pk}. "
            "Debe regularizarse la asignación "
            "antes de calcular su producción."
        )

    # ========================================================
    # 5. MODALIDAD ANTERIOR
    # ========================================================
    #
    # Servicios sin modalidad nueva.
    #
    # Conserva exactamente el cálculo anterior.
    # ========================================================

    monto = obtener_tarifa_tecnico(
        tecnico,
        mes,
    )

    return Decimal(str(monto)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

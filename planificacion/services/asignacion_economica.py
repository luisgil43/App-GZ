from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from operaciones.models import (MonthlyPayment, MontoTecnicoServicio,
                                ServicioCotizado)
from operaciones.services.tarifas import (TarifaNoConfigurada,
                                          normalizar_mes_produccion,
                                          obtener_tarifa_tecnico)
from usuarios.models import CustomUser

CENTAVO = Decimal("0.01")

ESTADOS_PAGO_PROTEGIDOS = {
    "approved_user",
    "pending_payment",
    "paid",
}


class ErrorAsignacionEconomica(ValueError):
    """
    Error de validación económica.

    Impide confirmar una asignación cuando existe
    riesgo de alterar importes o pagos protegidos.
    """


def _decimal_monto(valor):
    return Decimal(str(valor)).quantize(
        CENTAVO,
        rounding=ROUND_HALF_UP,
    )


@transaction.atomic
def aplicar_asignacion_economica(
    *,
    servicio,
    tecnicos_ids,
    tecnicos_anteriores_ids=None,
):
    """
    Aplica las reglas económicas existentes de
    operaciones.views.asignar_trabajadores.

    Debe ejecutarse dentro de la misma transacción
    que confirma la asignación de técnicos.

    REGLAS:

    - No modifica tarifas históricas anteriores a octubre 2026.
    - Protege pagos del período.
    - Conserva montos individuales registrados.
    - Calcula tarifas por actividad únicamente para técnicos
      que todavía no poseen un monto congelado.
    - No redistribuye automáticamente MMOO de cotización.
    - Protege montos aprobados.
    - Actualiza MMOO y registros individuales.
    """

    from operaciones.services.tarifas import obtener_tarifa_actividad_tecnico

    if servicio is None or servicio.pk is None:
        raise ErrorAsignacionEconomica(
            "El servicio es obligatorio."
        )

    # ========================================================
    # BLOQUEAR SERVICIO
    # ========================================================

    servicio = ServicioCotizado.objects.select_for_update().get(
        pk=servicio.pk,
    )

    final_ids = set(tecnicos_ids)

    if tecnicos_anteriores_ids is None:

        old_ids = set(
            servicio.trabajadores_asignados.values_list(
                "id",
                flat=True,
            )
        )

    else:

        old_ids = set(tecnicos_anteriores_ids)

    # ========================================================
    # MES DE PRODUCCIÓN
    # ========================================================

    mes_normalizado = normalizar_mes_produccion(
        servicio.mes_produccion,
    )

    if mes_normalizado is None:

        raise ErrorAsignacionEconomica(
            "No se pudo determinar el mes de producción "
            "del servicio. No se modificó la asignación."
        )

    # ========================================================
    # COMPATIBILIDAD HISTÓRICA
    # ========================================================

    if mes_normalizado < "2026-10":

        return {
            "servicio": servicio,
            "mes": mes_normalizado,
            "modalidad": "historica",
            "monto_mmoo": servicio.monto_mmoo,
            "montos_individuales": {},
        }

    # ========================================================
    # MODALIDAD
    # ========================================================

    modalidad = (
        servicio.modalidad_pago_tecnicos or ""
    ).strip()

    usa_tarifa_actividad = modalidad == "tarifa_actividad"

    usa_mmoo_cotizacion = modalidad == "mmoo_cotizacion"

    usa_tarifa_legacy = not (
        usa_tarifa_actividad or usa_mmoo_cotizacion
    )

    usa_modalidad_nueva = (
        usa_tarifa_actividad or usa_mmoo_cotizacion
    )

    # ========================================================
    # PROTEGER PAGOS
    # ========================================================

    involucrados = old_ids | final_ids

    if involucrados:

        pagos_protegidos = MonthlyPayment.objects.filter(
            technician_id__in=involucrados,
            month=mes_normalizado,
            status__in=ESTADOS_PAGO_PROTEGIDOS,
        )

        if pagos_protegidos.exists():

            raise ErrorAsignacionEconomica(
                "No se puede modificar la asignación porque "
                "uno o más técnicos tienen pagos aprobados, "
                "pendientes de pago o pagados para este mes."
            )

    # ========================================================
    # CARGAR MONTOS CONGELADOS
    # ========================================================

    montos_existentes = {}

    if usa_modalidad_nueva:

        registros = MontoTecnicoServicio.objects.filter(
            servicio=servicio,
        )

        montos_existentes = {
            registro.tecnico_id: registro
            for registro in registros
        }

    montos_individuales = {}

    nuevo_monto_mmoo = Decimal("0.00")

    # ========================================================
    # TARIFA POR ACTIVIDAD
    # ========================================================

    if usa_tarifa_actividad:

        if servicio.actividad_id is None:

            raise ErrorAsignacionEconomica(
                "Este servicio utiliza tarifas por actividad, "
                "pero no tiene una actividad configurada. "
                "No se modificó la asignación."
            )

        if (
            servicio.proyecto_id
            and servicio.actividad.proyecto_id != servicio.proyecto_id
        ):

            raise ErrorAsignacionEconomica(
                "La actividad configurada no pertenece "
                "al proyecto de este servicio. "
                "No se modificó la asignación."
            )

        tecnicos_finales = list(
            CustomUser.objects.filter(
                id__in=final_ids,
            )
        )

        if len(tecnicos_finales) != len(final_ids):

            raise ErrorAsignacionEconomica(
                "Uno o más técnicos seleccionados no existen."
            )

        for tecnico in tecnicos_finales:

            monto_existente = montos_existentes.get(
                tecnico.pk,
            )

            if monto_existente is not None:

                monto_individual = _decimal_monto(
                    monto_existente.monto,
                )

            else:

                try:

                    tarifa = obtener_tarifa_actividad_tecnico(
                        servicio.actividad,
                        tecnico,
                        mes_normalizado,
                    )

                except TarifaNoConfigurada as exc:

                    raise ErrorAsignacionEconomica(
                        str(exc)
                    ) from exc

                monto_individual = _decimal_monto(
                    tarifa,
                )

            montos_individuales[tecnico.pk] = monto_individual

            nuevo_monto_mmoo += monto_individual

        nuevo_monto_mmoo = _decimal_monto(
            nuevo_monto_mmoo,
        )

    # ========================================================
    # MMOO DE COTIZACIÓN
    # ========================================================

    elif usa_mmoo_cotizacion:

        monto_original = getattr(
            servicio,
            "monto_mmoo_cotizacion",
            None,
        )

        if monto_original is None:

            raise ErrorAsignacionEconomica(
                "Este servicio utiliza la MMOO de cotización, "
                "pero no tiene un monto original registrado. "
                "No se modificó la asignación."
            )

        nuevo_monto_mmoo = _decimal_monto(
            monto_original,
        )

        if final_ids:

            tecnicos_ordenados = sorted(
                final_ids,
            )

            ids_con_monto = {
                tecnico_id
                for tecnico_id in final_ids
                if tecnico_id in montos_existentes
            }

            # ====================================================
            # PRIMERA DISTRIBUCIÓN
            # ====================================================

            if not ids_con_monto:

                cantidad_tecnicos = len(
                    tecnicos_ordenados,
                )

                total_centavos = int(
                    nuevo_monto_mmoo * Decimal("100")
                )

                centavos_base, centavos_restantes = divmod(
                    total_centavos,
                    cantidad_tecnicos,
                )

                for indice, tecnico_id in enumerate(
                    tecnicos_ordenados
                ):

                    centavos_tecnico = centavos_base

                    if indice < centavos_restantes:
                        centavos_tecnico += 1

                    montos_individuales[tecnico_id] = (
                        Decimal(centavos_tecnico) / Decimal("100")
                    )

            # ====================================================
            # CONSERVAR DISTRIBUCIÓN EXISTENTE
            # ====================================================

            else:

                for tecnico_id in ids_con_monto:

                    registro = montos_existentes[
                        tecnico_id
                    ]

                    montos_individuales[tecnico_id] = (
                        _decimal_monto(registro.monto)
                    )

                ids_sin_monto = final_ids - ids_con_monto

                total_registrado = sum(
                    montos_individuales.values(),
                    Decimal("0.00"),
                )

                if ids_sin_monto:

                    raise ErrorAsignacionEconomica(
                        "Este servicio tiene una MMOO definida "
                        "en cotización y ya existen importes "
                        "individuales registrados. "
                        "Agregar o reemplazar técnicos requiere "
                        "redistribuir la MMOO original. "
                        "La operación se bloqueó para evitar "
                        "modificar importes previamente acordados."
                    )

                if total_registrado != nuevo_monto_mmoo:

                    raise ErrorAsignacionEconomica(
                        "La suma de los importes individuales "
                        "registrados no coincide con la MMOO "
                        "original de la cotización. "
                        "No se modificó la asignación."
                    )

    # ========================================================
    # MODALIDAD LEGACY
    # ========================================================

    elif usa_tarifa_legacy:

        tecnicos_finales = list(
            CustomUser.objects.filter(
                id__in=final_ids,
            )
        )

        if len(tecnicos_finales) != len(final_ids):

            raise ErrorAsignacionEconomica(
                "Uno o más técnicos seleccionados no existen."
            )

        for tecnico in tecnicos_finales:

            try:

                tarifa = obtener_tarifa_tecnico(
                    tecnico,
                    mes_normalizado,
                )

            except TarifaNoConfigurada as exc:

                raise ErrorAsignacionEconomica(
                    str(exc)
                ) from exc

            nuevo_monto_mmoo += Decimal(
                str(tarifa)
            )

        nuevo_monto_mmoo = _decimal_monto(
            nuevo_monto_mmoo,
        )

    # ========================================================
    # PROTEGER MONTOS APROBADOS
    # ========================================================

    if usa_modalidad_nueva:

        for registro in montos_existentes.values():

            if not registro.aprobado:
                continue

            nuevo_monto = montos_individuales.get(
                registro.tecnico_id,
            )

            if (
                nuevo_monto is None
                or Decimal(str(registro.monto)) != nuevo_monto
            ):

                raise ErrorAsignacionEconomica(
                    "No se puede modificar la asignación porque "
                    "existen montos individuales aprobados "
                    "que serían alterados."
                )

    # ========================================================
    # REGISTRAR MONTOS
    # ========================================================

    if usa_modalidad_nueva:

        MontoTecnicoServicio.objects.filter(
            servicio=servicio,
            aprobado=False,
        ).exclude(
            tecnico_id__in=final_ids,
        ).delete()

        for tecnico_id, monto_individual in (
            montos_individuales.items()
        ):

            if tecnico_id not in montos_existentes:

                MontoTecnicoServicio.objects.create(
                    servicio=servicio,
                    tecnico_id=tecnico_id,
                    monto=monto_individual,
                )

    # ========================================================
    # ACTUALIZAR MMOO
    # ========================================================

    servicio.monto_mmoo = nuevo_monto_mmoo

    servicio.save(
        update_fields=[
            "monto_mmoo",
        ]
    )

    return {
        "servicio": servicio,
        "mes": mes_normalizado,
        "modalidad": modalidad,
        "monto_mmoo": nuevo_monto_mmoo,
        "montos_individuales": montos_individuales,
    }
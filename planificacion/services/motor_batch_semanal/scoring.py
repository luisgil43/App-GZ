# ============================================================
# PESOS DEL MOTOR SEMANAL
# ============================================================
#
# La propuesta semanal debe privilegiar:
#
# 1. Que los sitios formen grupos geográficos razonables.
# 2. Que las cuadrillas disponibles puedan ejecutarlos.
# 3. Que no destruyamos el equilibrio del resto del mes.
#
# La capacidad ya no representa únicamente cantidad nominal.
#
# Incluye:
# - base;
# - cobertura territorial;
# - jornada;
# - tiempo por sitio;
# - viabilidad preliminar de desplazamiento.
# ============================================================

PESO_GEOGRAFIA = 0.30
PESO_CAPACIDAD = 0.30
PESO_ACCESO = 0.10
PESO_BALANCE_MENSUAL = 0.20
PESO_RESPALDO = 0.10


def score_total_propuesta(
    *,
    geografico,
    capacidad,
    acceso,
    balance_mensual,
    respaldo,
):
    total = (
        geografico * PESO_GEOGRAFIA
        + capacidad * PESO_CAPACIDAD
        + acceso * PESO_ACCESO
        + balance_mensual * PESO_BALANCE_MENSUAL
        + respaldo * PESO_RESPALDO
    )

    return round(
        total,
        2,
    )

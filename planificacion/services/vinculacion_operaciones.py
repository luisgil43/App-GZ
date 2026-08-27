# planificacion/services/vinculacion_operaciones.py

from calendar import monthrange
from datetime import date, timedelta

from django.db import transaction

from operaciones.models import ServicioCotizado
from planificacion.models import SitioBatchSemanal

# ============================================================
# MESES SOPORTADOS
# ============================================================
#
# Incluimos español e inglés porque en producción existen
# valores históricos como:
#
#     Agosto 2026
#     June 2025
#
# ============================================================

MESES = {
    # Español
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    # Inglés
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


# ============================================================
# ESTADOS SEMANALES QUE NO REPRESENTAN PARTICIPACIÓN ACTIVA
# ============================================================

ESTADOS_BATCH_SITIO_INACTIVOS = {
    "excluido",
    "reemplazado",
}


# ============================================================
# INTERPRETAR MES DE PRODUCCIÓN
# ============================================================


def interpretar_periodo_produccion(
    valor,
):
    """
    Convierte mes_produccion en:

        (año, mes)

    Soporta:

        Agosto 2026
        June 2025
        08/2026
        2026-08
    """

    texto = str(valor or "").strip().lower()

    if not texto:
        return None

    # ========================================================
    # YYYY-MM
    # ========================================================

    if len(texto) == 7 and texto[4] == "-":

        try:

            anio = int(texto[:4])

            mes = int(texto[5:7])

            if anio >= 2000 and 1 <= mes <= 12:
                return (
                    anio,
                    mes,
                )

        except ValueError:
            pass

    # ========================================================
    # MM/YYYY
    # ========================================================

    if "/" in texto:

        partes = [parte.strip() for parte in texto.split("/")]

        if len(partes) == 2:

            try:

                mes = int(partes[0])

                anio = int(partes[1])

                if anio >= 2000 and 1 <= mes <= 12:
                    return (
                        anio,
                        mes,
                    )

            except ValueError:
                pass

    # ========================================================
    # NOMBRE MES + AÑO
    # ========================================================

    partes = texto.replace(
        "-",
        " ",
    ).split()

    if len(partes) >= 2:

        nombre_mes = partes[0]

        mes = MESES.get(nombre_mes)

        try:

            anio = int(partes[-1])

        except ValueError:

            anio = None

        if mes and anio and anio >= 2000:

            return (
                anio,
                mes,
            )

    return None


# ============================================================
# PERÍODO DEL MES
# ============================================================


def obtener_limites_periodo(
    anio,
    mes,
):
    primer_dia = date(
        anio,
        mes,
        1,
    )

    ultimo_dia = date(
        anio,
        mes,
        monthrange(
            anio,
            mes,
        )[1],
    )

    return (
        primer_dia,
        ultimo_dia,
    )


# ============================================================
# RANGO OPERACIONAL DE UN BATCH
# ============================================================


def obtener_rango_batch(
    batch,
):
    """
    La planificación diaria trabaja:

        lunes -> sábado

    por tanto el período operacional del batch son seis días.
    """

    inicio = batch.fecha_inicio

    fin = inicio + timedelta(
        days=5,
    )

    return (
        inicio,
        fin,
    )


# ============================================================
# DETERMINAR SI BATCH PERTENECE AL PERÍODO OPERACIONAL
# ============================================================


def batch_intersecta_periodo(
    batch,
    *,
    anio,
    mes,
):
    """
    Determina si la semana operacional toca el mes de
    producción.

    Esto cubre correctamente semanas que cruzan de un mes
    hacia otro.

    Ejemplo:

        W36
        31 agosto -> 5 septiembre

    puede operacionalmente intersectar ambos meses.
    """

    inicio_mes, fin_mes = obtener_limites_periodo(
        anio,
        mes,
    )

    inicio_batch, fin_batch = obtener_rango_batch(
        batch,
    )

    return inicio_batch <= fin_mes and fin_batch >= inicio_mes


# ============================================================
# OBTENER PARTICIPACIONES REALES PARA UN SERVICIO
# ============================================================


def obtener_candidatos_planificacion_para_servicio(
    servicio,
):
    """
    Busca SitioPlanificado candidatos usando:

        ID Claro
        +
        período operacional real del batch

    NO utiliza PlanificacionMensual como criterio temporal.
    """

    id_claro = (servicio.id_claro or "").strip()

    if not id_claro:
        return []

    periodo = interpretar_periodo_produccion(
        servicio.mes_produccion,
    )

    if periodo is None:
        return []

    anio, mes = periodo

    participaciones = (
        SitioBatchSemanal.objects.filter(
            sitio_planificado__sitio__id_claro=id_claro,
        )
        .exclude(
            estado__in=ESTADOS_BATCH_SITIO_INACTIVOS,
        )
        .select_related(
            "batch",
            "sitio_planificado",
            "sitio_planificado__sitio",
            "sitio_planificado__planificacion",
        )
        .order_by(
            "-batch__fecha_inicio",
            "-id",
        )
    )

    candidatos = {}

    for participacion in participaciones:

        if not batch_intersecta_periodo(
            participacion.batch,
            anio=anio,
            mes=mes,
        ):
            continue

        sitio_planificado = participacion.sitio_planificado

        candidatos[sitio_planificado.pk] = sitio_planificado

    return list(candidatos.values())


# ============================================================
# VINCULAR SERVICIO EXISTENTE DESDE SU EJECUCIÓN SEMANAL
# ============================================================


@transaction.atomic
def vincular_servicio_a_planificacion_real(
    servicio,
):
    """
    Vincula ServicioCotizado con su SitioPlanificado real.

    REGLA
    ==========================================================

    Solamente se vincula automáticamente cuando existe:

        EXACTAMENTE 1 SitioPlanificado candidato.

    Si hay:

        0 candidatos
            -> no hacemos nada

        >1 candidatos
            -> no elegimos arbitrariamente

    De esta manera nunca contaminamos una ejecución con otra.
    """

    if servicio is None:
        return {
            "estado": "sin_servicio",
            "servicio": None,
            "sitio_planificado": None,
            "candidatos": 0,
        }

    servicio = ServicioCotizado.objects.select_for_update().get(
        pk=servicio.pk,
    )

    candidatos = obtener_candidatos_planificacion_para_servicio(
        servicio,
    )

    # ========================================================
    # SIN CANDIDATO
    # ========================================================

    if not candidatos:

        return {
            "estado": "sin_coincidencia",
            "servicio": servicio,
            "sitio_planificado": None,
            "candidatos": 0,
        }

    # ========================================================
    # AMBIGUO
    # ========================================================

    if len(candidatos) != 1:

        return {
            "estado": "ambiguo",
            "servicio": servicio,
            "sitio_planificado": None,
            "candidatos": len(candidatos),
        }

    sitio_planificado = candidatos[0]

    # ========================================================
    # YA ESTÁ BIEN VINCULADO
    # ========================================================

    if servicio.sitio_planificado_id == sitio_planificado.pk:

        return {
            "estado": "ya_vinculado",
            "servicio": servicio,
            "sitio_planificado": sitio_planificado,
            "candidatos": 1,
        }

    # ========================================================
    # VINCULAR / CORREGIR VÍNCULO
    # ========================================================

    ServicioCotizado.objects.filter(
        pk=servicio.pk,
    ).update(
        sitio_planificado=sitio_planificado,
    )

    servicio.sitio_planificado_id = sitio_planificado.pk

    return {
        "estado": "vinculado",
        "servicio": servicio,
        "sitio_planificado": sitio_planificado,
        "candidatos": 1,
    }


# ============================================================
# BUSCAR SERVICIO PARA UN SITIO AL ENTRAR/MOVERSE DE SEMANA
# ============================================================


@transaction.atomic
def vincular_sitio_planificado_con_servicio_del_batch(
    *,
    sitio_planificado,
    batch,
):
    """
    Se ejecuta cuando un SitioPlanificado:

        - entra por primera vez a una semana;
        - cambia de semana.

    Busca ServiciosCotizados del mismo ID Claro cuyo
    mes_produccion corresponda al período operacional real
    del batch destino.

    Solo vincula cuando existe exactamente un candidato.
    """

    if sitio_planificado is None:
        return {
            "estado": "sin_sitio",
            "servicio": None,
            "sitio_planificado": None,
            "candidatos": 0,
        }

    if batch is None:
        return {
            "estado": "sin_batch",
            "servicio": None,
            "sitio_planificado": sitio_planificado,
            "candidatos": 0,
        }

    sitio = sitio_planificado.sitio

    id_claro = (sitio.id_claro or "").strip()

    if not id_claro:

        return {
            "estado": "sin_id_claro",
            "servicio": None,
            "sitio_planificado": sitio_planificado,
            "candidatos": 0,
        }

    servicios = list(
        ServicioCotizado.objects.select_for_update()
        .filter(
            id_claro=id_claro,
        )
        .order_by(
            "-id",
        )
    )

    candidatos = []

    for servicio in servicios:

        periodo = interpretar_periodo_produccion(
            servicio.mes_produccion,
        )

        if periodo is None:
            continue

        anio, mes = periodo

        if not batch_intersecta_periodo(
            batch,
            anio=anio,
            mes=mes,
        ):
            continue

        candidatos.append(servicio)

    # ========================================================
    # SIN SERVICIO
    # ========================================================

    if not candidatos:

        return {
            "estado": "sin_coincidencia",
            "servicio": None,
            "sitio_planificado": sitio_planificado,
            "candidatos": 0,
        }

    # ========================================================
    # MÁS DE UN SERVICIO DEL MISMO PERÍODO
    # ========================================================

    if len(candidatos) != 1:

        return {
            "estado": "ambiguo",
            "servicio": None,
            "sitio_planificado": sitio_planificado,
            "candidatos": len(candidatos),
        }

    servicio = candidatos[0]

    # ========================================================
    # YA ESTÁ CORRECTO
    # ========================================================

    if servicio.sitio_planificado_id == sitio_planificado.pk:

        return {
            "estado": "ya_vinculado",
            "servicio": servicio,
            "sitio_planificado": sitio_planificado,
            "candidatos": 1,
        }

    # ========================================================
    # VINCULAR
    # ========================================================

    ServicioCotizado.objects.filter(
        pk=servicio.pk,
    ).update(
        sitio_planificado=sitio_planificado,
    )

    servicio.sitio_planificado_id = sitio_planificado.pk

    return {
        "estado": "vinculado",
        "servicio": servicio,
        "sitio_planificado": sitio_planificado,
        "candidatos": 1,
    }

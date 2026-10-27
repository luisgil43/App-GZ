"""
Retiro seguro de asignación operacional desde Planificación Diaria.

La función de este módulo no retira por sí sola el sitio de una jornada.
Su responsabilidad es validar y liberar únicamente el ServicioCotizado
asociado a una ejecución mensual concreta.

Todas las escrituras deben ejecutarse dentro de la misma transacción
que retire la participación de Planificación Diaria.
"""

from decimal import Decimal

from django.db import transaction

from operaciones.models import (
    MonthlyPayment,
    MontoTecnicoServicio,
    ServicioCotizado,
)
from operaciones.services.tarifas import normalizar_mes_produccion
from planificacion.modelos.planificacion_diaria import (
    SitioSalidaPlanificacionDiaria,
)


ESTADOS_REASIGNABLES = frozenset(
    {
        "asignado",
        "en_progreso",
        "en_revision_supervisor",
    }
)

ESTADOS_FINALIZADOS = frozenset(
    {
        "aprobado_supervisor",
        "finalizado",
    }
)

ESTADOS_PAGO_PROTEGIDOS = frozenset(
    {
        "approved_user",
        "pending_payment",
        "paid",
    }
)


class ErrorRetiroAsignacionOperacional(Exception):
    """Bloquea el retiro sin modificar la asignación."""


@transaction.atomic
def retirar_asignacion_operacional(
    *,
    sitio_planificado,
    usuario,
    participacion_retirada=None,
):
    """
    Devuelve a pendiente por asignar el servicio exacto del mes indicado.

    Retorna:
        (servicio, modificacion_realizada)

    Si el servicio ya estaba pendiente, no altera su estado.

    Ante cualquier ambigüedad o protección económica, genera una
    excepción para que la transacción de Planificación se revierta.
    """

    if sitio_planificado is None or not sitio_planificado.pk:
        raise ErrorRetiroAsignacionOperacional(
            "No se identificó una ejecución mensual válida."
        )

    if participacion_retirada is None or not participacion_retirada.pk:
        raise ErrorRetiroAsignacionOperacional(
            "No se identificó la participación concreta que "
            "se pretende retirar."
        )

    if (
        participacion_retirada.sitio_batch.sitio_planificado_id
        != sitio_planificado.pk
    ):
        raise ErrorRetiroAsignacionOperacional(
            "La participación no corresponde al sitio mensual indicado."
        )

    planificacion = sitio_planificado.planificacion

    mes_esperado = (
        f"{planificacion.anio:04d}-{planificacion.mes:02d}"
    )

    # Seleccionar únicamente servicios de esta ejecución mensual.
    # Otros meses no deben provocar una desasignación cruzada.
    servicios_vinculados = (
        ServicioCotizado.objects.select_for_update()
        .filter(sitio_planificado_id=sitio_planificado.pk)
        .order_by("pk")
    )

    servicios_mes = []
    existen_otros_meses = False

    for candidato in servicios_vinculados:
        mes_candidato = normalizar_mes_produccion(
            candidato.mes_produccion
        )

        if mes_candidato == mes_esperado:
            servicios_mes.append(candidato)
        else:
            existen_otros_meses = True

    if len(servicios_mes) > 1:
        raise ErrorRetiroAsignacionOperacional(
            "Existen varios servicios del mismo mes vinculados "
            "a esta planificación. Se requiere revisión."
        )

    if not servicios_mes:
        if existen_otros_meses:
            raise ErrorRetiroAsignacionOperacional(
                "Los servicios vinculados pertenecen a otro mes "
                "o tienen un mes de producción no válido."
            )
        return None, False

    servicio = servicios_mes[0]

    if servicio.estado in ESTADOS_FINALIZADOS:
        raise ErrorRetiroAsignacionOperacional(
            "Este servicio ya está finalizado o aprobado por el "
            "supervisor. No puede retirarse desde Planificación."
        )

    if servicio.estado == "aprobado_pendiente":
        if servicio.trabajadores_asignados.exists():
            raise ErrorRetiroAsignacionOperacional(
                "El servicio figura pendiente, pero todavía tiene "
                "técnicos asignados. Se requiere revisión."
            )

        return servicio, False

    if servicio.estado not in ESTADOS_REASIGNABLES:
        raise ErrorRetiroAsignacionOperacional(
            "El estado actual del servicio no permite retirar "
            "su asignación desde Planificación."
        )

    # ========================================================
    # PROTEGER OTRAS JORNADAS ACTIVAS
    # ========================================================
    #
    # Nunca liberar técnicos si el mismo SitioPlanificado
    # permanece comprometido en otra jornada.
    #
    # La búsqueda utiliza el vínculo mensual exacto.
    # No utiliza id_claro, DU ni identificadores históricos.
    # ========================================================

    otras_jornadas = (
        SitioSalidaPlanificacionDiaria.objects.select_for_update()
        .filter(
            sitio_batch__sitio_planificado_id=sitio_planificado.pk,
        )
        .exclude(
            estado__in={
                "retirado",
                "cancelado",
                "reprogramado",
            },
        )
    )

    if participacion_retirada is not None:
        otras_jornadas = otras_jornadas.exclude(
            pk=participacion_retirada.pk,
        )

    if otras_jornadas.exists():
        return servicio, False

    tecnicos_ids = set(
        servicio.trabajadores_asignados.values_list(
            "pk",
            flat=True,
        )
    )

    if tecnicos_ids:
        pagos_protegidos = MonthlyPayment.objects.filter(
            technician_id__in=tecnicos_ids,
            month=mes_esperado,
            status__in=ESTADOS_PAGO_PROTEGIDOS,
        )

        if pagos_protegidos.exists():
            raise ErrorRetiroAsignacionOperacional(
                "No se puede retirar la asignación porque alguno de "
                "los técnicos tiene un pago mensual aprobado, "
                "pendiente de pago o pagado."
            )

    montos = MontoTecnicoServicio.objects.select_for_update().filter(
        servicio=servicio,
    )

    if montos.filter(aprobado=True).exists():
        raise ErrorRetiroAsignacionOperacional(
            "No se puede retirar la asignación porque existen "
            "importes individuales aprobados para este servicio."
        )

    modalidad = (
        getattr(servicio, "modalidad_pago_tecnicos", None) or ""
    ).strip()

    servicio.trabajadores_asignados.clear()

    montos.filter(aprobado=False).delete()

    servicio.estado = "aprobado_pendiente"
    servicio.supervisor_asigna = usuario
    servicio.tecnico_aceptado = None
    servicio.tecnico_finalizo = None
    servicio.supervisor_aprobo = None
    servicio.supervisor_rechazo = None

    campos = [
        "estado",
        "supervisor_asigna",
        "tecnico_aceptado",
        "tecnico_finalizo",
        "supervisor_aprobo",
        "supervisor_rechazo",
    ]

    if modalidad == "tarifa_actividad" and mes_esperado >= "2026-10":
        servicio.monto_mmoo = Decimal("0.00")
        campos.append("monto_mmoo")

    if hasattr(servicio, "fecha_aprobacion_supervisor"):
        servicio.fecha_aprobacion_supervisor = None
        campos.append("fecha_aprobacion_supervisor")

    servicio.save(update_fields=campos)

    # ========================================================
    # CONSERVAR EVIDENCIAS Y RESTABLECER ESTADO OPERATIVO
    # ========================================================
    #
    # No llamar a _get_or_create_sesion() después de limpiar
    # trabajadores_asignados: esa función elimina asignaciones
    # fotográficas que ya no estén entre los técnicos actuales.
    #
    # Conservamos:
    # - SesionFotos;
    # - SesionFotoTecnico;
    # - RequisitoFoto;
    # - fotografías;
    # - registros históricos.
    #
    # Solo se restablecen indicadores de ejecución.
    # ========================================================

    # Las asignaciones fotográficas existentes se conservan
    # íntegramente. No se borran ni se reinician estados,
    # fechas o indicadores históricos desde Planificación.
    #
    # La preparación de nuevas asignaciones fotográficas
    # corresponde al flujo de Operaciones.
    #
    # No ejecutar aquí _get_or_create_sesion(), porque podría
    # eliminar asignaciones fotográficas anteriores.

    return servicio, True

import logging

from django.db import transaction
from django.urls import reverse

from notificaciones.services import notificar_asignacion_servicio_tecnicos
from operaciones.models import (RequisitoFoto, ServicioCotizado,
                                SesionFotoTecnico)
from operaciones.views_fotos import _get_or_create_sesion
from planificacion.services.asignacion_economica import \
    aplicar_asignacion_economica
from usuarios.models import CustomUser
from usuarios.utils import crear_notificacion

logger = logging.getLogger(__name__)


# ============================================================
# RESET DE ASIGNACIÓN INDIVIDUAL
# ============================================================


def _reset_asignacion_tecnico(
    asignacion,
):
    """
    Devuelve la asignación individual del técnico al estado
    'asignado' para forzar una nueva aceptación.
    """

    asignacion.estado = "asignado"

    update_fields = [
        "estado",
    ]

    if hasattr(
        asignacion,
        "aceptado_en",
    ):
        asignacion.aceptado_en = None

        update_fields.append(
            "aceptado_en",
        )

    if hasattr(
        asignacion,
        "finalizado_en",
    ):
        asignacion.finalizado_en = None

        update_fields.append(
            "finalizado_en",
        )

    if hasattr(
        asignacion,
        "reintento_habilitado",
    ):
        asignacion.reintento_habilitado = False

        update_fields.append(
            "reintento_habilitado",
        )

    asignacion.save(
        update_fields=update_fields,
    )


# ============================================================
# CLONAR REQUISITOS
# ============================================================


def _clonar_requisitos_a_asignacion(
    origen_asignacion,
    destino_asignacion,
):
    """
    Copia requisitos activos desde una asignación existente
    hacia una nueva.
    """

    if not origen_asignacion or not destino_asignacion:
        return

    requisitos = RequisitoFoto.objects.filter(
        tecnico_sesion=origen_asignacion,
        activo=True,
    ).order_by(
        "orden",
        "id",
    )

    for req in requisitos:

        RequisitoFoto.objects.get_or_create(
            tecnico_sesion=destino_asignacion,
            titulo=req.titulo,
            defaults={
                "descripcion": req.descripcion,
                "obligatorio": req.obligatorio,
                "orden": req.orden,
                "activo": req.activo,
            },
        )


# ============================================================
# SINCRONIZAR SESIÓN DE FOTOS
# ============================================================


def sincronizar_asignaciones_sesion(
    servicio,
    tecnicos_actuales_ids,
    reset_para_ids=None,
):
    """
    Asegura que exista una SesionFotoTecnico por cada técnico
    actualmente asignado.

    Los técnicos incluidos en reset_para_ids vuelven a estado
    'asignado' para requerir una nueva aceptación.

    La lista recibida representa la lista definitiva de
    responsables del servicio.
    """

    if reset_para_ids is None:
        reset_para_ids = set()

    tecnicos_actuales_ids = set(
        tecnicos_actuales_ids,
    )

    reset_para_ids = set(
        reset_para_ids,
    )

    sesion = _get_or_create_sesion(
        servicio,
    )

    existentes = {
        asignacion.tecnico_id: asignacion
        for asignacion in (
            sesion.asignaciones.select_related(
                "tecnico",
            ).all()
        )
    }

    asignacion_base = None

    for asignacion in existentes.values():
        asignacion_base = asignacion
        break

    # ========================================================
    # CREAR / REACTIVAR TÉCNICOS ACTUALES
    # ========================================================

    for tecnico_id in tecnicos_actuales_ids:

        asignacion = existentes.get(
            tecnico_id,
        )

        if asignacion is None:

            asignacion = SesionFotoTecnico.objects.create(
                sesion=sesion,
                tecnico_id=tecnico_id,
                estado="asignado",
            )

            existentes[tecnico_id] = asignacion

            if asignacion_base:

                _clonar_requisitos_a_asignacion(
                    asignacion_base,
                    asignacion,
                )

        if tecnico_id in reset_para_ids:

            _reset_asignacion_tecnico(
                asignacion,
            )

    # ========================================================
    # QUITAR TÉCNICOS QUE YA NO PERTENECEN AL SERVICIO
    # ========================================================

    sesion.asignaciones.exclude(
        tecnico_id__in=tecnicos_actuales_ids,
    ).delete()

    return (
        sesion,
        existentes,
    )


# ============================================================
# ASIGNACIÓN REUTILIZABLE DE SERVICIO
# ============================================================


@transaction.atomic
def asignar_tecnicos_servicio(
    *,
    servicio,
    tecnicos,
    actor,
    request=None,
    exigir_pendiente=True,
    enviar_notificaciones=True,
):
    """
    Asigna un conjunto exacto de técnicos a un ServicioCotizado.

    Conserva las reglas operativas existentes de Planificación:

    - Validación de estado.
    - Integrantes activos de cuadrilla.
    - No exige rol usuario.
    - Reinicio de aceptaciones.
    - Sincronización fotográfica.
    - Notificaciones internas y Telegram.

    NUEVO:
    ==========================================================

    Antes de confirmar la asignación ejecuta las mismas
    reglas económicas utilizadas por Servicios Disponibles.

    La operación económica y la asignación de técnicos
    se ejecutan dentro de una misma transacción.

    Si falla una validación económica, no se confirma
    ningún cambio en el servicio.
    """

    if servicio is None:

        raise ValueError(
            "El servicio es obligatorio.",
        )

    # ========================================================
    # BLOQUEAR SERVICIO
    # ========================================================

    servicio = ServicioCotizado.objects.select_for_update().get(
        pk=servicio.pk,
    )

    # ========================================================
    # VALIDAR ESTADO
    # ========================================================

    if exigir_pendiente and servicio.estado != "aprobado_pendiente":

        raise ValueError(
            (
                f"DU{str(servicio.du).zfill(8)} "
                "ya no se encuentra pendiente por asignar."
            )
        )

    # ========================================================
    # IDS RECIBIDOS
    # ========================================================

    if hasattr(
        tecnicos,
        "values_list",
    ):

        tecnicos_ids = set(
            tecnicos.values_list(
                "id",
                flat=True,
            )
        )

    else:

        tecnicos_ids = {
            tecnico.pk
            for tecnico in tecnicos
            if getattr(
                tecnico,
                "pk",
                None,
            )
        }

    if not tecnicos_ids:

        raise ValueError(
            "Debes indicar al menos un técnico.",
        )

    # ========================================================
    # VALIDAR INTEGRANTES ACTIVOS
    # ========================================================

    tecnicos_validos = list(
        CustomUser.objects.filter(
            pk__in=tecnicos_ids,
            is_active=True,
        )
        .distinct()
        .order_by(
            "first_name",
            "last_name",
            "username",
            "id",
        )
    )

    tecnicos_validos_ids = {tecnico.pk for tecnico in tecnicos_validos}

    if tecnicos_validos_ids != tecnicos_ids:

        ids_invalidos = tecnicos_ids - tecnicos_validos_ids

        raise ValueError(
            (
                "Uno o más integrantes configurados "
                "en la cuadrilla no existen o están "
                "inactivos. "
                f"IDs inválidos: "
                f"{', '.join(str(pk) for pk in sorted(ids_invalidos))}."
            )
        )

    # ========================================================
    # ASIGNACIÓN ANTERIOR
    # ========================================================

    old_ids = set(
        servicio.trabajadores_asignados.values_list(
            "id",
            flat=True,
        )
    )

    # ========================================================
    # APLICAR REGLAS ECONÓMICAS COMPARTIDAS
    # ========================================================
    #
    # IMPORTANTE:
    #
    # Si falta una tarifa, existe un pago protegido
    # o hay un conflicto con montos aprobados,
    # la función genera una excepción.
    #
    # La transacción revierte todos los cambios.
    #
    # No se modifica la asignación de técnicos.
    # ========================================================

    resultado_economico = aplicar_asignacion_economica(
        servicio=servicio,
        tecnicos_ids=tecnicos_validos_ids,
        tecnicos_anteriores_ids=old_ids,
    )

    servicio = resultado_economico["servicio"]

    # ========================================================
    # ASIGNAR LISTA EXACTA
    # ========================================================

    servicio.trabajadores_asignados.set(
        tecnicos_validos_ids,
    )

    servicio.supervisor_asigna = actor

    servicio.estado = "asignado"

    servicio.tecnico_aceptado = None

    servicio.tecnico_finalizo = None

    servicio.supervisor_aprobo = None

    servicio.supervisor_rechazo = None

    update_fields = [
        "supervisor_asigna",
        "estado",
        "tecnico_aceptado",
        "tecnico_finalizo",
        "supervisor_aprobo",
        "supervisor_rechazo",
    ]

    if hasattr(
        servicio,
        "fecha_aprobacion_supervisor",
    ):

        servicio.fecha_aprobacion_supervisor = None

        update_fields.append(
            "fecha_aprobacion_supervisor",
        )

    servicio.save(
        update_fields=update_fields,
    )

    # ========================================================
    # SESIÓN / REQUISITOS FOTOGRÁFICOS
    # ========================================================

    sincronizar_asignaciones_sesion(
        servicio,
        tecnicos_actuales_ids=tecnicos_validos_ids,
        reset_para_ids=tecnicos_validos_ids,
    )

    # ========================================================
    # TÉCNICOS QUE DEBEN RECIBIR AVISO
    # ========================================================

    if not old_ids:

        notificar_ids = set(
            tecnicos_validos_ids,
        )

    else:

        notificar_ids = tecnicos_validos_ids - old_ids

        if exigir_pendiente:

            notificar_ids = set(
                tecnicos_validos_ids,
            )

    usuarios_a_notificar = [
        tecnico for tecnico in tecnicos_validos if tecnico.pk in notificar_ids
    ]

    # ========================================================
    # NOTIFICACIONES
    # ========================================================
    #
    # Se ejecutan después de confirmar la transacción
    # completa, evitando avisar por una asignación
    # que posteriormente pueda revertirse.
    # ========================================================

    if enviar_notificaciones and usuarios_a_notificar:

        def enviar_avisos():

            for trabajador in usuarios_a_notificar:

                crear_notificacion(
                    usuario=trabajador,
                    mensaje=(
                        "Se te ha asignado una nueva tarea: "
                        f"DU{str(servicio.du).zfill(8)}."
                    ),
                    url=reverse(
                        "operaciones:mis_servicios_tecnico",
                    ),
                )

            # ================================================
            # TELEGRAM
            # ================================================

            if request is None:
                return

            try:

                enlace_app = request.build_absolute_uri(
                    reverse(
                        "operaciones:mis_servicios_tecnico",
                    )
                )

                logs = notificar_asignacion_servicio_tecnicos(
                    servicio=servicio,
                    actor=actor,
                    url=enlace_app,
                    extra={
                        "du": servicio.du,
                        "id_claro": servicio.id_claro,
                    },
                )

                for log in logs:

                    logger.info(
                        (
                            "Telegram asignación servicio DU%s "
                            "-> usuario_id=%s "
                            "status=%s error=%s"
                        ),
                        str(servicio.du).zfill(8),
                        log.usuario_id,
                        log.status,
                        getattr(
                            log,
                            "error",
                            "",
                        ),
                    )

            except Exception:

                logger.exception(
                    "Error enviando notificación Telegram " "de asignación"
                )

        transaction.on_commit(
            enviar_avisos,
        )

    return {
        "servicio": servicio,
        "tecnicos": tecnicos_validos,
        "tecnicos_ids": tecnicos_validos_ids,
        "notificados": usuarios_a_notificar,
    }

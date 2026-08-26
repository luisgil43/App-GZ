# planificacion/services/mover_semana.py

from django.core.exceptions import ValidationError
from django.db import transaction

from operaciones.models import ServicioCotizado, SesionFotos
from planificacion.modelos import SitioSalidaPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal

# ============================================================
# ESTADOS OPERACIONALES QUE YA NO PUEDEN MOVERSE DE SEMANA
# ============================================================

ESTADOS_OPERACIONES_BLOQUEADOS_MOVIMIENTO = {
    "en_revision_supervisor",
    "rechazado_supervisor",
    "aprobado_supervisor",
}


# ============================================================
# ESTADOS OPERACIONALES QUE DEBEN REINICIARSE AL MOVER
# ============================================================

ESTADOS_OPERACIONES_REINICIAR = {
    "asignado",
    "en_progreso",
}


# ============================================================
# IDENTIFICAR SITIO
# ============================================================


def _identificador_sitio(
    sitio_planificado,
):
    sitio = sitio_planificado.sitio

    return sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"


# ============================================================
# OBTENER SERVICIO OPERACIONAL
# ============================================================


def _obtener_servicio(
    sitio_planificado,
    *,
    bloquear=False,
):
    """
    Busca el ServicioCotizado vigente mediante ID Claro.

    Se utiliza la misma relación actual utilizada por
    Planificación y Operaciones.

    Cuando bloquear=True:

        se bloquea exclusivamente ServicioCotizado.

    No utilizamos select_related() junto con select_for_update()
    para evitar que PostgreSQL intente bloquear relaciones
    nullable mediante OUTER JOIN.
    """

    sitio = sitio_planificado.sitio

    id_claro = (sitio.id_claro or "").strip()

    if not id_claro:
        return None

    queryset = ServicioCotizado.objects.filter(
        id_claro=id_claro,
    ).order_by(
        "-id",
    )

    if bloquear:

        queryset = queryset.select_for_update()

    return queryset.first()


# ============================================================
# VALIDAR ESTADO OPERACIONAL
# ============================================================


def _validar_servicio_movible(
    servicio,
):
    """
    Impide mover un servicio que ya entró al proceso de
    revisión del supervisor o superó esa etapa.
    """

    if servicio is None:
        return

    if servicio.estado not in ESTADOS_OPERACIONES_BLOQUEADOS_MOVIMIENTO:
        return

    try:

        estado_display = servicio.get_estado_display()

    except Exception:

        estado_display = servicio.estado

    raise ValidationError(
        (
            f"DU{str(servicio.du).zfill(8)} no puede cambiar "
            "de semana porque ya alcanzó una etapa protegida "
            f"de Operaciones: {estado_display}."
        )
    )


# ============================================================
# BORRAR ARCHIVOS FÍSICOS DE EVIDENCIAS
# ============================================================


def _eliminar_archivos_evidencias(
    sesion,
):
    """
    Elimina del storage los archivos asociados a las
    evidencias antes de eliminar la sesión.

    Esto evita dejar archivos huérfanos en Wasabi cuando
    una ejecución se reinicia completamente.
    """

    asignaciones = sesion.asignaciones.all()

    for asignacion in asignaciones:

        evidencias = asignacion.evidencias.all()

        for evidencia in evidencias:

            if evidencia.imagen:

                try:

                    evidencia.imagen.delete(
                        save=False,
                    )

                except Exception:

                    # El registro igualmente será eliminado.
                    #
                    # No dejamos que un problema aislado del
                    # storage rompa el movimiento completo.
                    pass


# ============================================================
# REINICIAR OPERACIONES
# ============================================================


def _reiniciar_servicio_para_nueva_semana(
    servicio,
):
    """
    Devuelve un ServicioCotizado asignado o en progreso a:

        aprobado_pendiente

    y elimina toda memoria correspondiente a la ejecución
    anterior.

    Se ejecuta exclusivamente antes de mover el sitio a otra
    semana.
    """

    if servicio is None:
        return

    # ========================================================
    # SOLO HAY QUE DESMONTAR SI EXISTÍA EJECUCIÓN
    # ========================================================

    if servicio.estado not in ESTADOS_OPERACIONES_REINICIAR:
        return

    # ========================================================
    # SESIÓN FOTOGRÁFICA
    # ========================================================
    #
    # SesionFotos es OneToOne.
    #
    # Su eliminación también elimina mediante CASCADE:
    #
    # - SesionFotoTecnico
    # - RequisitoFoto
    # - EvidenciaFoto
    #
    # Antes eliminamos los archivos físicos de evidencia.
    # ========================================================

    sesion = (
        SesionFotos.objects.select_for_update()
        .filter(
            servicio=servicio,
        )
        .first()
    )

    if sesion is not None:

        _eliminar_archivos_evidencias(
            sesion,
        )

        sesion.delete()

    # ========================================================
    # TÉCNICOS
    # ========================================================

    servicio.trabajadores_asignados.clear()

    # ========================================================
    # RESTABLECER SERVICIO
    # ========================================================

    servicio.estado = "aprobado_pendiente"

    servicio.supervisor_asigna = None

    servicio.tecnico_aceptado = None

    servicio.tecnico_finalizo = None

    servicio.supervisor_aprobo = None

    servicio.supervisor_rechazo = None

    servicio.fecha_aprobacion_supervisor = None

    servicio.motivo_rechazo = None

    servicio.save(
        update_fields=[
            "estado",
            "supervisor_asigna",
            "tecnico_aceptado",
            "tecnico_finalizo",
            "supervisor_aprobo",
            "supervisor_rechazo",
            "fecha_aprobacion_supervisor",
            "motivo_rechazo",
        ]
    )


# ============================================================
# ELIMINAR PARTICIPACIONES DIARIAS ANTERIORES
# ============================================================


def _eliminar_planificacion_diaria_anterior(
    sitio_batch,
):
    """
    Elimina completamente la memoria diaria perteneciente
    al SitioBatchSemanal antes de moverlo de semana.

    Si una salida queda vacía, también se elimina.

    Si conserva otros sitios:

    - se mantienen;
    - se reordenan;
    - NO se fuerza el estado de la salida;
    - NO se desbloquea la salida;
    - NO se modifica Operaciones de los demás sitios.

    El estado real de esa salida podrá sincronizarse
    posteriormente mediante sincronizar_estado_salida().
    """

    # ========================================================
    # IMPORTANTE
    # ========================================================
    #
    # Bloqueamos exclusivamente las participaciones.
    #
    # NO hacemos select_related("salida") dentro del mismo
    # queryset bloqueado.
    #
    # De esta manera evitamos que PostgreSQL extienda el
    # FOR UPDATE hacia una relación mediante JOIN.
    # ========================================================

    participaciones = list(
        SitioSalidaPlanificacionDiaria.objects.select_for_update()
        .filter(
            sitio_batch=sitio_batch,
        )
        .order_by(
            "id",
        )
    )

    salidas_afectadas = {}

    # ========================================================
    # ELIMINAR PARTICIPACIONES DEL SITIO QUE SE MUEVE
    # ========================================================

    for participacion in participaciones:

        salida = participacion.salida

        salidas_afectadas[salida.pk] = salida

        participacion.delete()

    # ========================================================
    # LIMPIAR SALIDAS AFECTADAS
    # ========================================================

    for salida in salidas_afectadas.values():

        restantes = list(
            salida.sitios.exclude(
                estado__in=[
                    "retirado",
                    "cancelado",
                    "reprogramado",
                ]
            ).order_by(
                "orden",
                "id",
            )
        )

        # ====================================================
        # SALIDA VACÍA
        # ====================================================

        if not restantes:

            salida.delete()

            continue

        # ====================================================
        # REORDENAR LOS SITIOS QUE PERMANECEN
        # ====================================================

        for nuevo_orden, participacion in enumerate(
            restantes,
            start=1,
        ):

            if participacion.orden == nuevo_orden:
                continue

            participacion.orden = nuevo_orden

            participacion.save(
                update_fields=[
                    "orden",
                ]
            )


# ============================================================
# LIMPIAR MEMORIA DEL SITIO PLANIFICADO
# ============================================================


def _limpiar_sitio_planificado(
    sitio_planificado,
    *,
    usuario,
):
    """
    Deja el SitioPlanificado nuevamente disponible para que
    el motor de la nueva semana lo procese desde cero.

    SE CONSERVA
    ==========================================================

    - planificación mensual original;
    - permiso;
    - prioridad mensual;
    - información del sitio;
    - gestiones de acceso/contacto.

    SE ELIMINA
    ==========================================================

    Únicamente la memoria correspondiente a su posición
    diaria/semanal anterior.

    IMPORTANTE
    ==========================================================

    Mover un sitio de semana NO cambia su mes de origen.

    Ejemplo:

        sitio perteneciente a julio

    puede ser movido a:

        W38 de septiembre

    y continuará teniendo julio como planificación mensual
    de origen histórica.
    """

    sitio_planificado.fecha_planificada = None

    sitio_planificado.orden_dia = 0

    sitio_planificado.estado = "listo_planificar"

    sitio_planificado.bloqueado_motor = False

    sitio_planificado.planificado_manualmente = False

    sitio_planificado.motivo_bloqueo = ""

    sitio_planificado.alerta_motor = ""

    sitio_planificado.actualizado_por = usuario

    sitio_planificado.save(
        update_fields=[
            "fecha_planificada",
            "orden_dia",
            "estado",
            "bloqueado_motor",
            "planificado_manualmente",
            "motivo_bloqueo",
            "alerta_motor",
            "actualizado_por",
            "actualizado_en",
        ]
    )


# ============================================================
# LIMPIAR Y MOVER SITIO BATCH
# ============================================================


def _mover_sitio_batch(
    sitio_batch,
    *,
    batch_destino,
    usuario,
):
    """
    Mueve el MISMO SitioBatchSemanal a la semana destino.

    No se crea un registro duplicado.

    Toda memoria calculada del batch anterior se elimina.
    """

    sitio_batch.batch = batch_destino

    sitio_batch.estado = "confirmado"

    sitio_batch.origen = "manual"

    sitio_batch.puntaje_motor = None

    sitio_batch.motivo_recomendacion = ""

    sitio_batch.motivo_exclusion = ""

    sitio_batch.agregado_manualmente = True

    sitio_batch.bloqueado_en_batch = False

    sitio_batch.es_reserva = False

    sitio_batch.cluster_codigo = ""

    sitio_batch.agregado_por = usuario

    sitio_batch.save(
        update_fields=[
            "batch",
            "estado",
            "origen",
            "puntaje_motor",
            "motivo_recomendacion",
            "motivo_exclusion",
            "agregado_manualmente",
            "bloqueado_en_batch",
            "es_reserva",
            "cluster_codigo",
            "agregado_por",
            "actualizado_en",
        ]
    )


# ============================================================
# MOVIMIENTO DEFINITIVO ENTRE SEMANAS
# ============================================================


@transaction.atomic
def mover_sitio_a_semana(
    *,
    sitio_batch_id,
    batch_destino_id,
    usuario,
):
    """
    Mueve un sitio desde CUALQUIER semana hacia CUALQUIER
    otra semana operacional global existente.

    REGLA GLOBAL
    ==========================================================

    El mes de origen del sitio NO restringe el movimiento.

    Son válidos casos como:

        Julio -> Agosto
        Julio -> Septiembre
        Agosto -> Septiembre
        Septiembre -> Agosto
        Octubre -> Septiembre

    siempre que la semana destino:

        - exista;
        - no esté cancelada;
        - sea distinta de la actual.

    La semana operacional es GLOBAL.

    Por tanto NO se exige:

        - mismo mes;
        - misma PlanificacionMensual;
        - que la semana intersecte el mes del sitio;
        - que batch.planificacion coincida con el origen.

    El SitioPlanificado conserva su PlanificacionMensual
    histórica original.

    La planificación mensual de origen se agrega además a:

        batch_destino.planificaciones_origen

    para mantener correctamente la trazabilidad del batch
    global.

    MEMORIA OPERACIONAL
    ==========================================================

    El sitio NO conserva memoria de la semana anterior.

    Puede moverse aunque esté:

        aprobado_pendiente
        asignado
        en_progreso

    Cuando está:

        asignado
        en_progreso

    Operaciones se reinicia completamente a:

        aprobado_pendiente

    NO puede moverse cuando ServicioCotizado está en:

        en_revision_supervisor
        rechazado_supervisor
        aprobado_supervisor

    porque ya alcanzó una etapa protegida de revisión.

    La salida diaria anterior solamente pierde este sitio.

    Los demás sitios de esa salida conservan su estado
    operacional y de planificación.

    BLOQUEOS SQL
    ==========================================================

    select_for_update() se aplica únicamente sobre tablas
    concretas.

    NO se combina con select_related() sobre relaciones
    nullable.

    Esto evita el error PostgreSQL:

        FOR UPDATE cannot be applied to the nullable side
        of an outer join
    """

    # ========================================================
    # 1. BLOQUEAR SITIO BATCH ORIGEN
    # ========================================================
    #
    # MUY IMPORTANTE:
    #
    # No utilizamos:
    #
    #     select_for_update().select_related(...)
    #
    # porque algunas relaciones relacionadas pueden ser
    # nullable y Django genera LEFT OUTER JOIN.
    #
    # PostgreSQL no permite FOR UPDATE sobre el lado nullable
    # de ese JOIN.
    #
    # Bloqueamos solamente SitioBatchSemanal.
    # ========================================================

    sitio_batch = SitioBatchSemanal.objects.select_for_update().get(
        pk=sitio_batch_id,
    )

    # ========================================================
    # 2. RELACIONES DEL SITIO
    # ========================================================
    #
    # Se cargan DESPUÉS del bloqueo principal mediante
    # consultas normales independientes.
    #
    # No forman parte del FOR UPDATE anterior.
    # ========================================================

    batch_origen = sitio_batch.batch

    sitio_planificado = sitio_batch.sitio_planificado

    # Forzamos la carga de estas relaciones fuera del JOIN
    # bloqueado.
    sitio = sitio_planificado.sitio

    planificacion_origen = (
        sitio_planificado.planificacion if sitio_planificado.planificacion_id else None
    )

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    # ========================================================
    # 3. BLOQUEAR BATCH DESTINO
    # ========================================================
    #
    # Tampoco hacemos select_related("configuracion_semana")
    # aquí porque ConfiguracionSemana puede ser NULL.
    # ========================================================

    batch_destino = BatchPlanificacionSemanal.objects.select_for_update().get(
        pk=batch_destino_id,
    )

    # ========================================================
    # 4. MISMA SEMANA
    # ========================================================

    if batch_origen.pk == batch_destino.pk:

        raise ValidationError(
            (f"{identificador} ya pertenece a " f"{batch_destino.codigo_semana}.")
        )

    # ========================================================
    # 5. BATCH DESTINO CANCELADO
    # ========================================================

    if batch_destino.estado == "cancelado":

        raise ValidationError("No puedes mover sitios hacia una semana cancelada.")

    # ========================================================
    # 6. NO HAY RESTRICCIÓN POR MES
    # ========================================================
    #
    # NO validar:
    #
    #     sitio_planificado.planificacion
    #         ==
    #     batch_destino.planificacion
    #
    # NO validar:
    #
    #     mes del sitio
    #         ==
    #     mes de la semana
    #
    # NO validar:
    #
    #     intersección de la semana con el mes original.
    #
    # La semana es global.
    # ========================================================

    # ========================================================
    # 7. EVITAR DUPLICADO EN DESTINO
    # ========================================================
    #
    # Para exists() no necesitamos select_for_update().
    #
    # sitio_batch ya está bloqueado y el destino también.
    # ========================================================

    duplicado = (
        SitioBatchSemanal.objects.filter(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
        )
        .exclude(
            pk=sitio_batch.pk,
        )
        .exists()
    )

    if duplicado:

        raise ValidationError(
            (f"{identificador} ya existe dentro de " f"{batch_destino.codigo_semana}.")
        )

    # ========================================================
    # 8. SERVICIO OPERACIONAL
    # ========================================================

    servicio = _obtener_servicio(
        sitio_planificado,
        bloquear=True,
    )

    # ========================================================
    # 9. VALIDAR SI TODAVÍA PUEDE MOVERSE
    # ========================================================

    _validar_servicio_movible(
        servicio,
    )

    # ========================================================
    # 10. RECORDAR SI REALMENTE REQUERÍA REINICIO
    # ========================================================
    #
    # Esto debe calcularse ANTES de reiniciar el servicio.
    #
    # Si estaba:
    #
    #     asignado
    #     en_progreso
    #
    # entonces servicio_reiniciado será True.
    #
    # Si ya estaba aprobado_pendiente será False.
    # ========================================================

    servicio_requeria_reinicio = bool(
        servicio and servicio.estado in ESTADOS_OPERACIONES_REINICIAR
    )

    # ========================================================
    # 11. DESMONTAR EJECUCIÓN OPERACIONAL ANTERIOR
    # ========================================================

    _reiniciar_servicio_para_nueva_semana(
        servicio,
    )

    # ========================================================
    # 12. ELIMINAR PLANIFICACIÓN DIARIA ANTERIOR
    # ========================================================

    _eliminar_planificacion_diaria_anterior(
        sitio_batch,
    )

    # ========================================================
    # 13. REINICIAR POSICIÓN DEL SITIO
    # ========================================================

    _limpiar_sitio_planificado(
        sitio_planificado,
        usuario=usuario,
    )

    # ========================================================
    # 14. CAMBIAR DE SEMANA
    # ========================================================

    _mover_sitio_batch(
        sitio_batch,
        batch_destino=batch_destino,
        usuario=usuario,
    )

    # ========================================================
    # 15. REGISTRAR MES DE ORIGEN EN LA SEMANA DESTINO
    # ========================================================
    #
    # Esto es precisamente lo que permite que una semana
    # global contenga sitios provenientes de varios meses.
    #
    # Ejemplo:
    #
    #     W38
    #
    # puede contener simultáneamente:
    #
    #     julio
    #     agosto
    #     septiembre
    #
    # sin cambiar el PlanificacionMensual original de cada
    # SitioPlanificado.
    # ========================================================

    if planificacion_origen is not None:

        batch_destino.planificaciones_origen.add(
            planificacion_origen,
        )

    # ========================================================
    # 16. RESULTADO
    # ========================================================

    return {
        "sitio_batch": sitio_batch,
        "sitio_planificado": sitio_planificado,
        "servicio": servicio,
        "batch_origen": batch_origen,
        "batch_destino": batch_destino,
        "identificador": identificador,
        "servicio_reiniciado": (servicio_requeria_reinicio),
    }

# planificacion/services/mover_semana.py

from django.core.exceptions import ValidationError
from django.db import transaction

from operaciones.models import ServicioCotizado, SesionFotos
from planificacion.modelos import SitioSalidaPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal
from planificacion.services.vinculacion_operaciones import \
    vincular_sitio_planificado_con_servicio_del_batch

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
    Obtiene exclusivamente el ServicioCotizado perteneciente
    a esta ejecución mensual de SitioPlanificado.

    REGLA CRÍTICA
    ==========================================================

    Un mismo SitioMovil puede ejecutarse múltiples veces:

        05_750 - Octubre 2025
        05_750 - Agosto 2026
        05_750 - Diciembre 2026

    Por lo tanto NO se busca por ID Claro.

    La relación correcta es:

        ServicioCotizado.sitio_planificado
            ==
        sitio_planificado

    Cuando bloquear=True se aplica select_for_update()
    exclusivamente sobre ServicioCotizado.
    """

    if sitio_planificado is None:
        return None

    if not sitio_planificado.pk:
        return None

    queryset = ServicioCotizado.objects.filter(
        sitio_planificado=sitio_planificado,
    ).order_by(
        "-id",
    )

    if bloquear:
        queryset = queryset.select_for_update()

    return queryset.first()


# ============================================================
# VALIDAR ESTADO OPERACIONAL
# ============================================================
# ============================================================
# VALIDAR ESTADO OPERACIONAL
# ============================================================


def _validar_servicio_movible(
    servicio,
):
    """
    VALIDACIÓN TEMPORAL DESACTIVADA.

    Permite mover servicios aunque ya se encuentren en:

        en_revision_supervisor
        rechazado_supervisor
        aprobado_supervisor

    IMPORTANTE:

    Esta modificación es únicamente para realizar una
    reorganización manual de semanas.

    NO modifica el estado de Operaciones.
    NO reinicia servicios aprobados/rechazados/en revisión.
    NO elimina evidencias de esos estados.

    Cuando termine la reorganización debe restaurarse
    inmediatamente la validación original.
    """

    return


"""
def _validar_servicio_movible(
    servicio,
):
   

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
"""

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
# ASIGNAR O MOVER SITIO PLANIFICADO A UNA SEMANA
# ============================================================


@transaction.atomic
def asignar_o_mover_sitio_planificado_a_semana(
    *,
    sitio_planificado_id,
    batch_destino_id,
    usuario,
):
    """
    Entrada general para enviar un SitioPlanificado hacia una
    semana operacional global.

    SOPORTA DOS ESCENARIOS
    ==========================================================

    1. SITIO SIN SEMANA ACTIVA

        SitioPlanificado
            ->
        no existe SitioBatchSemanal activo

    En este caso se crea directamente su participación dentro
    de la semana destino.

    NO se exige pertenecer previamente a otra semana.

    Las participaciones históricas con estado:

        excluido
        reemplazado

    NO cuentan como pertenencia semanal activa.

    2. SITIO QUE YA PERTENECE A UNA SEMANA ACTIVA

        SitioPlanificado
            ->
        SitioBatchSemanal activo

    En este caso se utiliza mover_sitio_a_semana() para
    conservar toda la lógica existente de:

        - desmontar planificación diaria anterior;
        - reiniciar Operaciones cuando corresponde;
        - limpiar memoria semanal;
        - mover el mismo SitioBatchSemanal;
        - conservar trazabilidad mensual;
        - reconciliar el ServicioCotizado correspondiente
          al período operacional real.

    REGLA TEMPORAL
    ==========================================================

    PlanificacionMensual NO determina el mes operacional real.

    Un sitio puede tener:

        PlanificacionMensual = Julio 2026

    y ser ejecutado realmente dentro de:

        W35 de Agosto 2026

    Por tanto la vinculación con ServicioCotizado se determina
    utilizando:

        ID Claro
        +
        período operacional real del batch semanal

    y NO mediante el mes original de PlanificacionMensual.
    """

    # ========================================================
    # 1. IMPORT LOCAL PARA EVITAR CICLOS
    # ========================================================

    from planificacion.models import SitioPlanificado

    # ========================================================
    # 2. BLOQUEAR SITIO PLANIFICADO
    # ========================================================

    sitio_planificado = SitioPlanificado.objects.select_for_update().get(
        pk=sitio_planificado_id,
    )

    sitio = sitio_planificado.sitio

    planificacion_origen = (
        sitio_planificado.planificacion if sitio_planificado.planificacion_id else None
    )

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    # ========================================================
    # 3. BLOQUEAR BATCH DESTINO
    # ========================================================

    batch_destino = BatchPlanificacionSemanal.objects.select_for_update().get(
        pk=batch_destino_id,
    )

    # ========================================================
    # 4. VALIDAR DESTINO
    # ========================================================

    if batch_destino.estado == "cancelado":

        raise ValidationError("No puedes asignar sitios hacia una semana cancelada.")

    # ========================================================
    # 5. BUSCAR SOLO PARTICIPACIONES SEMANALES ACTIVAS
    # ========================================================
    #
    # excluido / reemplazado son memoria histórica.
    #
    # No deben impedir una nueva asignación.
    # ========================================================

    participaciones_activas = list(
        SitioBatchSemanal.objects.select_for_update()
        .filter(
            sitio_planificado=sitio_planificado,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .order_by(
            "id",
        )
    )

    # ========================================================
    # 6. INCONSISTENCIA: MÁS DE UNA SEMANA ACTIVA
    # ========================================================

    if len(participaciones_activas) > 1:

        semanas = ", ".join(
            participacion.batch.codigo_semana
            for participacion in participaciones_activas
        )

        raise ValidationError(
            (
                f"{identificador} aparece actualmente en más de "
                f"una semana activa ({semanas}). "
                "Debe revisarse antes de moverlo."
            )
        )

    # ========================================================
    # 7. YA PERTENECE A UNA SEMANA ACTIVA
    # ========================================================

    if len(participaciones_activas) == 1:

        sitio_batch = participaciones_activas[0]

        if sitio_batch.batch_id == batch_destino.pk:

            # =================================================
            # YA ESTÁ EN LA SEMANA, PERO RECONCILIAMOS
            # OPERACIONES POR SI EL VÍNCULO TODAVÍA FALTA
            # =================================================

            resultado_vinculacion = vincular_sitio_planificado_con_servicio_del_batch(
                sitio_planificado=sitio_planificado,
                batch=batch_destino,
            )

            raise ValidationError(
                (f"{identificador} ya pertenece a " f"{batch_destino.codigo_semana}.")
            )

        return mover_sitio_a_semana(
            sitio_batch_id=sitio_batch.pk,
            batch_destino_id=batch_destino.pk,
            usuario=usuario,
        )

    # ========================================================
    # 8. SITIO SIN SEMANA ACTIVA
    # ========================================================

    duplicado_destino_activo = (
        SitioBatchSemanal.objects.filter(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .exists()
    )

    if duplicado_destino_activo:

        raise ValidationError(
            (f"{identificador} ya existe dentro de " f"{batch_destino.codigo_semana}.")
        )

    # ========================================================
    # 9. LIMPIAR POSICIÓN DE PLANIFICACIÓN
    # ========================================================

    sitio_planificado.fecha_planificada = None

    sitio_planificado.orden_dia = 0

    if sitio_planificado.estado not in {
        "completado",
        "cancelado",
        "bloqueado",
    }:

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

    # ========================================================
    # 10. REUTILIZAR PARTICIPACIÓN HISTÓRICA EN DESTINO
    # ========================================================

    sitio_batch_historico_destino = (
        SitioBatchSemanal.objects.select_for_update()
        .filter(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .order_by(
            "-id",
        )
        .first()
    )

    if sitio_batch_historico_destino is not None:

        sitio_batch = sitio_batch_historico_destino

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

    else:

        # ====================================================
        # 11. CREAR PRIMERA PARTICIPACIÓN SEMANAL
        # ====================================================

        sitio_batch = SitioBatchSemanal.objects.create(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
            estado="confirmado",
            origen="manual",
            puntaje_motor=None,
            motivo_recomendacion="",
            motivo_exclusion="",
            agregado_manualmente=True,
            bloqueado_en_batch=False,
            es_reserva=False,
            agregado_por=usuario,
            cluster_codigo="",
        )

    # ========================================================
    # 12. TRAZABILIDAD DEL MES DE ORIGEN
    # ========================================================

    if planificacion_origen is not None:

        batch_destino.planificaciones_origen.add(
            planificacion_origen,
        )

    # ========================================================
    # 13. VINCULAR OPERACIONES POR EJECUCIÓN REAL
    # ========================================================
    #
    # Este paso es el que corrige definitivamente el problema:
    #
    # Planificación mensual:
    #     Julio 2026
    #
    # Semana real:
    #     W35 Agosto 2026
    #
    # Servicio:
    #     Agosto 2026
    #
    # Debe quedar:
    #
    # ServicioCotizado.sitio_planificado = sitio_planificado
    # ========================================================

    resultado_vinculacion = vincular_sitio_planificado_con_servicio_del_batch(
        sitio_planificado=sitio_planificado,
        batch=batch_destino,
    )

    servicio = resultado_vinculacion.get("servicio")

    # ========================================================
    # 14. RESULTADO
    # ========================================================

    return {
        "sitio_batch": sitio_batch,
        "sitio_planificado": sitio_planificado,
        "servicio": servicio,
        "batch_origen": None,
        "batch_destino": batch_destino,
        "identificador": identificador,
        "servicio_reiniciado": False,
        "era_primera_asignacion_semanal": True,
        "vinculacion_operaciones": resultado_vinculacion,
    }


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

    El SitioPlanificado conserva su PlanificacionMensual
    histórica original.

    OPERACIONES
    ==========================================================

    El ServicioCotizado correcto NO se determina mediante:

        PlanificacionMensual

    sino mediante:

        ID Claro
        +
        período operacional real del batch destino.

    Esto permite correctamente:

        PlanificacionMensual = Julio 2026
        Batch destino = W35 Agosto 2026
        ServicioCotizado = Agosto 2026

    sin romper la relación.
    """

    # ========================================================
    # 1. BLOQUEAR SITIO BATCH ORIGEN
    # ========================================================

    sitio_batch = SitioBatchSemanal.objects.select_for_update().get(
        pk=sitio_batch_id,
    )

    # ========================================================
    # 2. RELACIONES DEL SITIO
    # ========================================================

    batch_origen = sitio_batch.batch

    sitio_planificado = sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    planificacion_origen = (
        sitio_planificado.planificacion if sitio_planificado.planificacion_id else None
    )

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    # ========================================================
    # 3. BLOQUEAR BATCH DESTINO
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
    # 6. EVITAR DUPLICADO EN DESTINO
    # ========================================================

    duplicado = (
        SitioBatchSemanal.objects.filter(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
        )
        .exclude(
            pk=sitio_batch.pk,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .exists()
    )

    if duplicado:

        raise ValidationError(
            (f"{identificador} ya existe dentro de " f"{batch_destino.codigo_semana}.")
        )

    # ========================================================
    # 7. SERVICIO OPERACIONAL ACTUAL
    # ========================================================

    servicio = _obtener_servicio(
        sitio_planificado,
        bloquear=True,
    )

    # ========================================================
    # 8. VALIDAR SI TODAVÍA PUEDE MOVERSE
    # ========================================================

    _validar_servicio_movible(
        servicio,
    )

    # ========================================================
    # 9. RECORDAR SI REQUERÍA REINICIO
    # ========================================================

    servicio_requeria_reinicio = bool(
        servicio and servicio.estado in ESTADOS_OPERACIONES_REINICIAR
    )

    # ========================================================
    # 10. DESMONTAR EJECUCIÓN OPERACIONAL ANTERIOR
    # ========================================================

    _reiniciar_servicio_para_nueva_semana(
        servicio,
    )

    # ========================================================
    # 11. ELIMINAR PLANIFICACIÓN DIARIA ANTERIOR
    # ========================================================

    _eliminar_planificacion_diaria_anterior(
        sitio_batch,
    )

    # ========================================================
    # 12. REINICIAR POSICIÓN DEL SITIO
    # ========================================================

    _limpiar_sitio_planificado(
        sitio_planificado,
        usuario=usuario,
    )

    # ========================================================
    # 13. ¿EXISTE REGISTRO HISTÓRICO EN DESTINO?
    # ========================================================
    #
    # Puede existir un SitioBatchSemanal antiguo excluido o
    # reemplazado para este mismo sitio y destino.
    #
    # Si existe, debemos reutilizarlo para respetar cualquier
    # constraint único batch + sitio_planificado.
    # ========================================================

    sitio_batch_historico_destino = (
        SitioBatchSemanal.objects.select_for_update()
        .filter(
            batch=batch_destino,
            sitio_planificado=sitio_planificado,
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .exclude(
            pk=sitio_batch.pk,
        )
        .order_by(
            "-id",
        )
        .first()
    )

    if sitio_batch_historico_destino is not None:

        # ====================================================
        # REACTIVAR REGISTRO HISTÓRICO DESTINO
        # ====================================================

        sitio_batch_destino = sitio_batch_historico_destino

        sitio_batch_destino.estado = "confirmado"

        sitio_batch_destino.origen = "manual"

        sitio_batch_destino.puntaje_motor = None

        sitio_batch_destino.motivo_recomendacion = ""

        sitio_batch_destino.motivo_exclusion = ""

        sitio_batch_destino.agregado_manualmente = True

        sitio_batch_destino.bloqueado_en_batch = False

        sitio_batch_destino.es_reserva = False

        sitio_batch_destino.cluster_codigo = ""

        sitio_batch_destino.agregado_por = usuario

        sitio_batch_destino.save(
            update_fields=[
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

        # ====================================================
        # EL REGISTRO ORIGEN PASA A HISTÓRICO
        # ====================================================

        sitio_batch.estado = "reemplazado"

        sitio_batch.motivo_exclusion = (
            f"Movido manualmente hacia " f"{batch_destino.codigo_semana}."
        )

        sitio_batch.bloqueado_en_batch = False

        sitio_batch.es_reserva = False

        sitio_batch.agregado_por = usuario

        sitio_batch.save(
            update_fields=[
                "estado",
                "motivo_exclusion",
                "bloqueado_en_batch",
                "es_reserva",
                "agregado_por",
                "actualizado_en",
            ]
        )

        sitio_batch = sitio_batch_destino

    else:

        # ====================================================
        # MOVER EL MISMO REGISTRO AL DESTINO
        # ====================================================

        _mover_sitio_batch(
            sitio_batch,
            batch_destino=batch_destino,
            usuario=usuario,
        )

    # ========================================================
    # 14. TRAZABILIDAD DEL MES DE ORIGEN
    # ========================================================

    if planificacion_origen is not None:

        batch_destino.planificaciones_origen.add(
            planificacion_origen,
        )

    # ========================================================
    # 15. RECONCILIAR OPERACIONES CON LA SEMANA DESTINO
    # ========================================================
    #
    # No asumimos que el ServicioCotizado actual siga siendo
    # necesariamente el correspondiente al nuevo período.
    #
    # Ejemplo:
    #
    # antes:
    #     servicio Julio
    #
    # después:
    #     sitio movido a Agosto
    #
    # Si existe exactamente un ServicioCotizado de Agosto para
    # esta ejecución, ese es el que debe quedar asociado.
    # ========================================================

    resultado_vinculacion = vincular_sitio_planificado_con_servicio_del_batch(
        sitio_planificado=sitio_planificado,
        batch=batch_destino,
    )

    servicio_vinculado = resultado_vinculacion.get("servicio")

    if servicio_vinculado is not None:
        servicio = servicio_vinculado

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
        "era_primera_asignacion_semanal": False,
        "vinculacion_operaciones": resultado_vinculacion,
    }

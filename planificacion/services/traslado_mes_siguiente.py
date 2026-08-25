from django.core.exceptions import ValidationError
from django.db import transaction

from planificacion.models import PlanificacionMensual, SitioPlanificado

# ============================================================
# OBTENER AÑO / MES SIGUIENTE
# ============================================================


def obtener_anio_mes_siguiente(
    *,
    anio,
    mes,
):
    anio = int(anio)

    mes = int(mes)

    if mes == 12:

        return {
            "anio": anio + 1,
            "mes": 1,
        }

    return {
        "anio": anio,
        "mes": mes + 1,
    }


# ============================================================
# OBTENER PLANIFICACIÓN DESTINO
# ============================================================


def obtener_planificacion_mes_siguiente(
    planificacion_origen,
):
    siguiente = obtener_anio_mes_siguiente(
        anio=planificacion_origen.anio,
        mes=planificacion_origen.mes,
    )

    return (
        PlanificacionMensual.objects.filter(
            anio=siguiente["anio"],
            mes=siguiente["mes"],
        )
        .order_by(
            "id",
        )
        .first()
    )


# ============================================================
# VALIDAR SITIO TRASLADABLE
# ============================================================


def validar_sitio_trasladable(
    sitio_planificado,
):
    """
    Valida que un sitio pueda abandonar el mes actual.

    El traslado está pensado para sitios que:

        - continúan pendientes;
        - no fueron ejecutados;
        - poseen autorización operacional;
        - deben competir nuevamente dentro del mes siguiente.

    IMPORTANTE
    ==========================================================

    Esta validación solamente determina si podemos retirarlo
    del mes de origen.

    No introduce ninguna prioridad especial en el mes destino.
    """

    if not sitio_planificado.activo_en_mes:

        raise ValidationError(
            "El sitio ya no se encuentra activo "
            "en la planificación mensual de origen."
        )

    if sitio_planificado.estado in {
        "completado",
        "en_ejecucion",
        "cancelado",
    }:

        raise ValidationError(
            "El sitio no puede trasladarse porque su estado "
            "actual ya representa una ejecución o cierre."
        )

    if sitio_planificado.estado_permiso not in {
        "aprobado",
        "no_requiere",
    }:

        raise ValidationError(
            "El sitio no puede trasladarse porque todavía "
            "no está habilitado operacionalmente."
        )


# ============================================================
# VALIDAR REGISTRO EXISTENTE EN DESTINO
# ============================================================


def _validar_sitio_destino_existente(
    sitio_destino,
):
    """
    Si el mismo sitio ya existe en el mes destino, protegemos
    cualquier ejecución o planificación realmente comprometida.

    No queremos que un traslado desde el mes anterior pueda
    sobrescribir trabajo ya efectuado dentro del nuevo mes.
    """

    if sitio_destino.estado in {
        "en_ejecucion",
        "completado",
    }:

        raise ValidationError(
            "El sitio ya existe en la planificación del mes "
            "siguiente y posee una ejecución activa o "
            "finalizada. No puede ser sobrescrito mediante "
            "el traslado mensual."
        )


# ============================================================
# NORMALIZAR SITIO DESTINO
# ============================================================


def _normalizar_sitio_destino(
    *,
    sitio_destino,
    sitio_origen,
    usuario=None,
):
    """
    Deja el sitio del mes destino completamente neutral para
    el motor.

    REGLA FUNDAMENTAL
    ==========================================================

    El motor del nuevo mes NO debe saber que este sitio vino
    del mes anterior.

    Por tanto NO copiamos:

        - prioridad del mes anterior;
        - fecha planificada anterior;
        - orden anterior;
        - bloqueo del motor;
        - planificación manual;
        - observaciones de traslado;
        - alertas del motor;
        - fecha mínima de ejecución anterior;
        - tipo de ruta decidido anteriormente;
        - importación de origen del mes anterior.

    Sí conservamos únicamente información operacional todavía
    válida:

        - estado de permiso;
        - datos de contacto ya confirmados.

    El resultado debe comportarse exactamente igual que un
    sitio normal disponible dentro del nuevo mes.
    """

    _validar_sitio_destino_existente(
        sitio_destino,
    )

    sitio_destino.activo_en_mes = True

    sitio_destino.fecha_planificada = None

    sitio_destino.orden_dia = 0

    sitio_destino.estado = "listo_planificar"

    sitio_destino.estado_permiso = sitio_origen.estado_permiso

    # ========================================================
    # SIN MEMORIA TERRITORIAL / OPERACIONAL DEL MES ANTERIOR
    # ========================================================

    sitio_destino.tipo_ruta = ""

    sitio_destino.prioridad = "normal"

    sitio_destino.fecha_minima_ejecucion = None

    sitio_destino.bloqueado_motor = False

    sitio_destino.planificado_manualmente = False

    sitio_destino.motivo_bloqueo = ""

    sitio_destino.observacion_planificacion = ""

    sitio_destino.alerta_motor = ""

    # ========================================================
    # CONTACTO
    # ========================================================
    #
    # Estos datos representan conocimiento operacional del
    # sitio y no una preferencia del motor mensual.
    # ========================================================

    sitio_destino.requiere_contacto = sitio_origen.requiere_contacto

    sitio_destino.contacto_confirmado = sitio_origen.contacto_confirmado

    sitio_destino.fecha_contacto_confirmado = sitio_origen.fecha_contacto_confirmado

    # ========================================================
    # AUDITORÍA
    # ========================================================

    sitio_destino.actualizado_por = usuario

    sitio_destino.save(
        update_fields=[
            "activo_en_mes",
            "fecha_planificada",
            "orden_dia",
            "estado",
            "estado_permiso",
            "tipo_ruta",
            "prioridad",
            "requiere_contacto",
            "contacto_confirmado",
            "fecha_contacto_confirmado",
            "fecha_minima_ejecucion",
            "bloqueado_motor",
            "planificado_manualmente",
            "motivo_bloqueo",
            "observacion_planificacion",
            "alerta_motor",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return sitio_destino


# ============================================================
# MARCAR SITIO COMO TRASLADADO EN MES ORIGEN
# ============================================================


def _marcar_sitio_origen_como_trasladado(
    *,
    sitio_origen,
    planificacion_destino,
    usuario=None,
):
    """
    Conserva la trazabilidad SOLAMENTE en el registro del
    mes de origen.

    Ejemplo:

        Agosto
            05_350
            estado = reprogramado
            activo_en_mes = False

    Septiembre
            05_350
            estado = listo_planificar
            prioridad = normal
            sin memoria del traslado

    De esta forma podemos auditar qué ocurrió en agosto sin
    contaminar las decisiones del motor de septiembre.
    """

    observacion_existente = (sitio_origen.observacion_planificacion or "").strip()

    texto_traslado = (
        "Trasladado al mes "
        f"{planificacion_destino.mes:02d}/"
        f"{planificacion_destino.anio} "
        "para continuar su planificación operacional."
    )

    if observacion_existente:

        nueva_observacion = f"{observacion_existente}\n\n" f"{texto_traslado}"

    else:

        nueva_observacion = texto_traslado

    sitio_origen.activo_en_mes = False

    sitio_origen.fecha_planificada = None

    sitio_origen.orden_dia = 0

    sitio_origen.estado = "reprogramado"

    sitio_origen.bloqueado_motor = True

    sitio_origen.motivo_bloqueo = "Trasladado al mes siguiente."

    sitio_origen.observacion_planificacion = nueva_observacion

    sitio_origen.actualizado_por = usuario

    sitio_origen.save(
        update_fields=[
            "activo_en_mes",
            "fecha_planificada",
            "orden_dia",
            "estado",
            "bloqueado_motor",
            "motivo_bloqueo",
            "observacion_planificacion",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return sitio_origen


# ============================================================
# TRASLADAR UN SITIO AL MES SIGUIENTE
# ============================================================


@transaction.atomic
def trasladar_sitio_mes_siguiente(
    *,
    sitio_planificado,
    usuario=None,
):
    """
    Traslada administrativamente un sitio al mes siguiente.

    ARQUITECTURA
    ==========================================================

    MES ORIGEN
    ----------------------------------------------------------

    Conserva trazabilidad de que el sitio salió del mes:

        activo_en_mes = False
        estado = reprogramado
        bloqueado_motor = True

    MES DESTINO
    ----------------------------------------------------------

    El sitio entra completamente neutral:

        activo_en_mes = True
        estado = listo_planificar
        prioridad = normal
        fecha_planificada = None
        orden_dia = 0
        bloqueado_motor = False
        planificado_manualmente = False
        tipo_ruta = ""
        fecha_minima_ejecucion = None
        observacion_planificacion = ""
        alerta_motor = ""

    REGLA FUNDAMENTAL
    ==========================================================

    El motor del mes destino no recibe ninguna señal que
    indique que el sitio perteneció previamente al mes
    anterior.

    Por ejemplo:

        Agosto:
            3 sitios sin ejecutar

        Septiembre:
            130 sitios propios

    después del traslado:

        Septiembre:
            133 sitios

    El motor analiza los 133 como un único universo y decide
    desde cero:

        - semana;
        - zona;
        - cluster;
        - cuadrilla;
        - agrupación;
        - ruta.

    Los tres trasladados no reciben prioridad ni tratamiento
    especial.
    """

    # ========================================================
    # BLOQUEAR REGISTRO ORIGEN
    # ========================================================

    sitio_origen = (
        SitioPlanificado.objects.select_for_update()
        .select_related(
            "planificacion",
            "sitio",
        )
        .get(
            pk=sitio_planificado.pk,
        )
    )

    # ========================================================
    # VALIDAR ORIGEN
    # ========================================================

    validar_sitio_trasladable(
        sitio_origen,
    )

    planificacion_origen = sitio_origen.planificacion

    # ========================================================
    # OBTENER DESTINO
    # ========================================================

    planificacion_destino = obtener_planificacion_mes_siguiente(planificacion_origen)

    if planificacion_destino is None:

        siguiente = obtener_anio_mes_siguiente(
            anio=planificacion_origen.anio,
            mes=planificacion_origen.mes,
        )

        raise ValidationError(
            "No existe una planificación mensual creada para "
            f"{siguiente['mes']:02d}/"
            f"{siguiente['anio']}."
        )

    # ========================================================
    # EVITAR AUTOTRASLADO
    # ========================================================

    if planificacion_destino.pk == planificacion_origen.pk:

        raise ValidationError(
            "La planificación de destino no puede ser "
            "la misma planificación de origen."
        )

    # ========================================================
    # BUSCAR DESTINO EXISTENTE CON BLOQUEO
    # ========================================================
    #
    # No usamos directamente get_or_create porque queremos
    # bloquear correctamente un registro ya existente antes
    # de normalizarlo.
    # ========================================================

    sitio_destino = (
        SitioPlanificado.objects.select_for_update()
        .filter(
            planificacion=planificacion_destino,
            sitio=sitio_origen.sitio,
        )
        .first()
    )

    creado = False

    # ========================================================
    # CREAR DESTINO
    # ========================================================

    if sitio_destino is None:

        sitio_destino = SitioPlanificado.objects.create(
            planificacion=(planificacion_destino),
            sitio=(sitio_origen.sitio),
            # ============================================
            # NO COPIAR IMPORTACIÓN DEL MES ANTERIOR
            # ============================================
            importacion_origen=None,
            # ============================================
            # ESTADO NORMAL DEL NUEVO MES
            # ============================================
            activo_en_mes=True,
            fecha_planificada=None,
            orden_dia=0,
            estado="listo_planificar",
            estado_permiso=(sitio_origen.estado_permiso),
            # ============================================
            # SIN MEMORIA PARA EL MOTOR
            # ============================================
            tipo_ruta="",
            prioridad="normal",
            fecha_minima_ejecucion=None,
            bloqueado_motor=False,
            planificado_manualmente=False,
            motivo_bloqueo="",
            observacion_planificacion="",
            alerta_motor="",
            # ============================================
            # INFORMACIÓN OPERACIONAL VÁLIDA
            # ============================================
            requiere_contacto=(sitio_origen.requiere_contacto),
            contacto_confirmado=(sitio_origen.contacto_confirmado),
            fecha_contacto_confirmado=(sitio_origen.fecha_contacto_confirmado),
            # ============================================
            # AUDITORÍA
            # ============================================
            creado_por=usuario,
            actualizado_por=usuario,
        )

        creado = True

    # ========================================================
    # DESTINO YA EXISTENTE
    # ========================================================

    else:

        sitio_destino = _normalizar_sitio_destino(
            sitio_destino=sitio_destino,
            sitio_origen=sitio_origen,
            usuario=usuario,
        )

    # ========================================================
    # DESACTIVAR ORIGEN
    # ========================================================

    sitio_origen = _marcar_sitio_origen_como_trasladado(
        sitio_origen=sitio_origen,
        planificacion_destino=(planificacion_destino),
        usuario=usuario,
    )

    # ========================================================
    # RESULTADO
    # ========================================================

    return {
        "sitio_origen": sitio_origen,
        "sitio_destino": sitio_destino,
        "planificacion_origen": (planificacion_origen),
        "planificacion_destino": (planificacion_destino),
        "creado_destino": creado,
    }

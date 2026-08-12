from __future__ import annotations

import logging
import uuid
from typing import Iterable, Optional

from notificaciones.models import NotificationLog
from notificaciones.services import enviar_telegram
from usuarios.models import CustomUser

logger = logging.getLogger(__name__)


# ============================================================
# USUARIOS DISPONIBLES PARA COMUNICADOS
# ============================================================


def _usuarios_con_telegram_activo():
    """
    Devuelve únicamente usuarios que realmente pueden recibir
    comunicados por Telegram.

    Requisitos:
    - Usuario activo.
    - telegram_activo=True.
    - telegram_chat_id existente.
    - telegram_chat_id no vacío.

    IMPORTANTE:
    Usuarios con is_active=False nunca aparecen ni pueden
    recibir comunicados.
    """
    return (
        CustomUser.objects.filter(
            is_active=True,
            telegram_activo=True,
            telegram_chat_id__isnull=False,
        )
        .exclude(telegram_chat_id="")
        .prefetch_related("roles")
        .order_by(
            "first_name",
            "last_name",
            "username",
            "id",
        )
        .distinct()
    )


# ============================================================
# SEPARACIÓN TÉCNICOS / ADMINISTRATIVOS
# ============================================================


def obtener_destinatarios_comunicados() -> dict:
    """
    Separa usuarios con Telegram activo en dos grupos:

    TÉCNICOS:
    - Usuarios que poseen el rol 'usuario'.

    ADMINISTRATIVOS:
    - Superusuarios.
    - Usuarios que NO poseen el rol 'usuario'.

    Cada persona aparece una sola vez.
    """

    usuarios = list(_usuarios_con_telegram_activo())

    tecnicos = []
    administrativos = []

    for usuario in usuarios:

        roles = {(rol.nombre or "").strip().lower() for rol in usuario.roles.all()}

        # Superusuario siempre se considera administrativo.
        if usuario.is_superuser:
            administrativos.append(usuario)
            continue

        # Técnico.
        if "usuario" in roles:
            tecnicos.append(usuario)

        # Cualquier otro rol se considera administrativo.
        else:
            administrativos.append(usuario)

    return {
        "tecnicos": tecnicos,
        "administrativos": administrativos,
        "total": len(tecnicos) + len(administrativos),
    }


# ============================================================
# CONSTRUCCIÓN DEL MENSAJE
# ============================================================


def construir_mensaje_comunicado(
    *,
    titulo: str,
    mensaje: str,
) -> str:
    """
    Construye el mensaje corporativo final que recibirá
    el usuario por Telegram.

    Se mantiene como texto plano para ser compatible con
    el sistema general de notificaciones de Telegram.
    """

    titulo = (titulo or "").strip()
    mensaje = (mensaje or "").strip()

    # Evita exceso de espacios si el usuario escribe
    # varias líneas vacías accidentalmente.
    lineas_mensaje = [linea.rstrip() for linea in mensaje.splitlines()]

    mensaje_limpio = "\n".join(lineas_mensaje).strip()

    partes = [
        "📢  COMUNICADO MILTEL",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🔷 {titulo.upper()}",
        "",
        mensaje_limpio,
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🏢 GRUPO GZS",
    ]

    return "\n".join(partes)


# ============================================================
# ENVÍO DEL COMUNICADO
# ============================================================


def enviar_comunicado_telegram_usuarios(
    *,
    usuarios: Iterable[CustomUser],
    titulo: str,
    mensaje: str,
    actor: Optional[CustomUser] = None,
) -> dict:
    """
    Envía un comunicado a los usuarios indicados.

    Seguridad:
    Aunque llegue una lista de usuarios desde otra parte del sistema,
    antes de enviar se vuelve a comprobar:

    - is_active=True
    - telegram_activo=True
    - telegram_chat_id presente

    Cada envío general genera un comunicado_id único.
    Ese mismo comunicado_id se guarda en todos los NotificationLog
    correspondientes a los destinatarios del mismo comunicado.

    Esto permite que posteriormente el historial muestre:

    - 1 fila por comunicado
    - cantidad total de destinatarios
    - cantidad enviada correctamente
    - cantidad con error
    - usuario que realizó el envío
    - título
    - mensaje original

    Retorna:

    {
        "total": ...,
        "enviados": ...,
        "errores": ...,
        "logs": [...],
        "comunicado_id": ...
    }
    """

    import uuid

    # ========================================================
    # LIMPIAR DATOS DEL COMUNICADO
    # ========================================================

    titulo = (titulo or "").strip()
    mensaje = (mensaje or "").strip()

    if not titulo:
        raise ValueError("El título del comunicado es obligatorio.")

    if not mensaje:
        raise ValueError("El mensaje del comunicado es obligatorio.")

    # ========================================================
    # LIMPIAR IDS RECIBIDOS
    # ========================================================

    usuario_ids = []

    for usuario in usuarios or []:

        try:
            uid = int(usuario.pk)

        except (
            TypeError,
            ValueError,
            AttributeError,
        ):
            continue

        if uid not in usuario_ids:
            usuario_ids.append(uid)

    # ========================================================
    # SIN DESTINATARIOS
    # ========================================================

    if not usuario_ids:
        return {
            "total": 0,
            "enviados": 0,
            "errores": 0,
            "logs": [],
            "comunicado_id": None,
        }

    # ========================================================
    # VALIDACIÓN FINAL DE SEGURIDAD
    # ========================================================
    #
    # Aunque desde el formulario lleguen IDs manipulados,
    # aquí únicamente se permiten usuarios que:
    #
    # - estén activos
    # - tengan Telegram habilitado
    # - tengan telegram_chat_id
    #
    # ========================================================

    destinatarios = list(
        CustomUser.objects.filter(
            id__in=usuario_ids,
            is_active=True,
            telegram_activo=True,
            telegram_chat_id__isnull=False,
        )
        .exclude(telegram_chat_id="")
        .order_by(
            "first_name",
            "last_name",
            "username",
            "id",
        )
        .distinct()
    )

    # ========================================================
    # SI DESPUÉS DE VALIDAR NO QUEDA NADIE
    # ========================================================

    if not destinatarios:
        return {
            "total": 0,
            "enviados": 0,
            "errores": 0,
            "logs": [],
            "comunicado_id": None,
        }

    # ========================================================
    # ID ÚNICO DEL COMUNICADO
    # ========================================================
    #
    # Todos los logs creados en esta ejecución compartirán
    # este mismo identificador.
    #
    # Ejemplo:
    #
    # comunicado_id =
    # "820d7cca-caf2-45f3-ae89-a30f65ea53ad"
    #
    # Si se envía a 20 personas, tendremos 20 NotificationLog
    # pero todos pertenecerán al mismo comunicado.
    #
    # ========================================================

    comunicado_id = str(uuid.uuid4())

    # ========================================================
    # CONSTRUIR MENSAJE FINAL TELEGRAM
    # ========================================================

    mensaje_final = construir_mensaje_comunicado(
        titulo=titulo,
        mensaje=mensaje,
    )

    # ========================================================
    # CONTADORES
    # ========================================================

    logs = []

    enviados = 0
    errores = 0

    # ========================================================
    # ENVÍO INDIVIDUAL
    # ========================================================

    for usuario in destinatarios:

        try:

            log = enviar_telegram(
                usuario=usuario,
                actor=actor,
                tipo="comunicado_general",
                titulo=titulo,
                mensaje=mensaje_final,
                url="",
                servicio=None,
                extra={
                    # Tipo interno.
                    "tipo": "comunicado_general",
                    # Identificador compartido de este envío.
                    "comunicado_id": comunicado_id,
                    # Datos originales para historial.
                    "titulo": titulo,
                    "mensaje_original": mensaje,
                    # Datos útiles para auditoría.
                    "destinatario_id": usuario.id,
                    "destinatario_nombre": (
                        usuario.get_full_name()
                        or usuario.username
                        or f"Usuario {usuario.id}"
                    ),
                    # Quién realizó el comunicado.
                    "actor_id": (actor.id if actor else None),
                    "actor_nombre": (actor.get_full_name() if actor else "Sistema"),
                },
            )

            logs.append(log)

            # =================================================
            # TELEGRAM ACEPTÓ EL ENVÍO
            # =================================================

            if log.status == NotificationLog.STATUS_SENT:
                enviados += 1

            # =================================================
            # TELEGRAM NO LO ENVIÓ
            # =================================================

            else:
                errores += 1

                logger.warning(
                    (
                        "Comunicado Telegram no enviado "
                        "comunicado_id=%s "
                        "usuario_id=%s "
                        "status=%s"
                    ),
                    comunicado_id,
                    usuario.id,
                    log.status,
                )

        except Exception:

            errores += 1

            logger.exception(
                (
                    "Error enviando comunicado Telegram "
                    "comunicado_id=%s "
                    "usuario_id=%s"
                ),
                comunicado_id,
                usuario.id,
            )

    # ========================================================
    # RESULTADO FINAL
    # ========================================================

    logger.info(
        (
            "Comunicado Telegram finalizado "
            "comunicado_id=%s "
            "total=%s "
            "enviados=%s "
            "errores=%s "
            "actor_id=%s"
        ),
        comunicado_id,
        len(destinatarios),
        enviados,
        errores,
        getattr(
            actor,
            "id",
            None,
        ),
    )

    return {
        "total": len(destinatarios),
        "enviados": enviados,
        "errores": errores,
        "logs": logs,
        "comunicado_id": comunicado_id,
    }

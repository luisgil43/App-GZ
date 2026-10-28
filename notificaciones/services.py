import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import requests
from django.conf import settings

from usuarios.models import CustomUser

from .models import NotificationLog

logger = logging.getLogger(__name__)


def _clean_info(value: Any) -> str:
    """
    Normaliza textos que vienen desde la BD (SitioMovil, etc.).
    Convierte 'nan', 'None', 'null', '-' y cadenas vacías en ''.
    """
    if value is None:
        return ""
    txt = str(value).strip()
    if txt.lower() in ("nan", "none", "null", "-", ""):
        return ""
    return txt


# ========== Helpers de permisos de canal ==========

def can_notify_telegram(user: CustomUser) -> bool:
    """
    Solo permite Telegram si el usuario:
    - está activo
    - tiene chat_id
    - tiene telegram_activo = True
    """
    if not user.is_active:
        return False
    if not getattr(user, "telegram_activo", False):
        return False
    chat_id = getattr(user, "telegram_chat_id", None)
    return bool(chat_id)


def can_notify_email(user: CustomUser) -> bool:
    """
    Solo permite correo si el usuario:
    - está activo
    - tiene email_notificaciones_activo = True
    - tiene email definido
    """
    if not user.is_active:
        return False
    if not getattr(user, "email_notificaciones_activo", True):
        return False
    email = (user.email or "").strip()
    return bool(email)


def _get_bot_token() -> Optional[str]:
    """
    Lee el token del bot desde settings.
    Puedes usar TELEGRAM_BOT_TOKEN_GZ o TELEGRAM_BOT_TOKEN.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN_GZ", None) or getattr(
        settings, "TELEGRAM_BOT_TOKEN", None
    )
    if not token:
        logger.warning(
            "Telegram: no se encontró TELEGRAM_BOT_TOKEN_GZ ni TELEGRAM_BOT_TOKEN en settings."
        )
    return token


# ========== Log genérico ==========

def crear_log_notificacion(
    *,
    usuario: CustomUser,
    actor: Optional[CustomUser],
    canal: str,
    tipo: str,
    titulo: str,
    mensaje: str,
    url: str = "",
    servicio: Any = None,
    du: str = "",
    extra_dict: Optional[dict] = None,
) -> NotificationLog:
    """
    Crea el log base. 'extra_dict' se guarda como JSON string.
    """
    extra = ""
    if extra_dict is not None:
        try:
            extra = json.dumps(extra_dict, ensure_ascii=False)
        except Exception:
            extra = str(extra_dict)

    log = NotificationLog.objects.create(
        usuario=usuario,
        actor=actor,
        canal=canal,
        tipo=tipo,
        titulo=titulo[:255] if titulo else "",
        mensaje=mensaje,
        url=url or "",
        servicio=servicio if servicio is not None else None,
        du=du or (getattr(servicio, "du", "") if servicio is not None else ""),
        extra=extra,
        status=NotificationLog.STATUS_PENDING,
    )
    return log


# ========== Envío Telegram genérico ==========

def enviar_telegram(
    *,
    usuario: CustomUser,
    actor: Optional[CustomUser],
    tipo: str,
    titulo: str,
    mensaje: str,
    url: str = "",
    servicio: Any = None,
    extra: Optional[dict] = None,
) -> NotificationLog:
    """
    Envía (o intenta enviar) una notificación por Telegram usando la API real.
    Registra el resultado en NotificationLog.
    """

    log = crear_log_notificacion(
        usuario=usuario,
        actor=actor,
        canal=NotificationLog.CANAL_TELEGRAM,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
        url=url,
        servicio=servicio,
        extra_dict=extra,
    )

    # Reglas para NO enviar
    if not can_notify_telegram(usuario):
        log.mark_error("Telegram desactivado, sin chat_id o usuario inactivo.")
        return log

    token = _get_bot_token()
    if not token:
        log.mark_error("No hay token configurado para el bot de Telegram.")
        return log

    chat_id = getattr(usuario, "telegram_chat_id", None)
    if not chat_id:
        log.mark_error("El usuario no tiene telegram_chat_id configurado.")
        return log

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    # TELEGRAM_FULL_MESSAGE_V1
    # Conservar íntegramente el contenido, sin abreviar ni eliminar texto.
    # Telegram admite hasta 4096 caracteres por mensaje.
    limite = 4000
    texto_completo = str(mensaje)

    partes = [
        texto_completo[i:i + limite]
        for i in range(0, len(texto_completo), limite)
    ] or [""]

    try:
        for numero, parte in enumerate(partes, start=1):
            payload = {
                "chat_id": chat_id,
                "text": parte,
                # Sin Markdown para evitar errores de entidades.
                "disable_web_page_preview": True,
            }

            resp = requests.post(
                api_url,
                json=payload,
                timeout=10,
            )

            try:
                data = resp.json()
            except Exception:
                data = None

            if not (
                resp.status_code == 200
                and isinstance(data, dict)
                and data.get("ok")
            ):
                description = ""
                if isinstance(data, dict):
                    description = data.get("description") or ""
                if not description:
                    description = resp.text[:500]

                log.mark_error(
                    f"Telegram rechazó la parte "
                    f"{numero}/{len(partes)} "
                    f"({resp.status_code}): {description}"
                )
                break
        else:
            log.mark_sent()

        logger.info(
            "Telegram notificación tipo=%s usuario_id=%s servicio_id=%s status=%s",
            tipo,
            usuario.id,
            getattr(servicio, "id", None),
            log.status,
        )

    except Exception as e:
        logger.exception("Error enviando mensaje a Telegram")
        log.mark_error(f"Excepción al contactar API Telegram: {e}")

    return log


# ========== Envío Email genérico (placeholder) ==========

def enviar_email_notificacion(
    *,
    usuario: CustomUser,
    actor: Optional[CustomUser],
    tipo: str,
    titulo: str,
    mensaje: str,
    url: str = "",
    servicio: Any = None,
    extra: Optional[dict] = None,
) -> NotificationLog:
    """
    Enviar notificación por correo.
    TODO: integrar send_mail real. De momento solo genera el log.
    """

    log = crear_log_notificacion(
        usuario=usuario,
        actor=actor,
        canal=NotificationLog.CANAL_EMAIL,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
        url=url,
        servicio=servicio,
        extra_dict=extra,
    )

    if not can_notify_email(usuario):
        log.mark_error("Correo desactivado o sin email configurado.")
        return log

    # Aquí luego integramos send_mail real
    log.mark_sent()
    return log


# ========== Helper específico: asignación de servicio a técnicos ==========


def _build_mensaje_asignacion(
    servicio: Any,
    tecnico: CustomUser,
    actor: Optional[CustomUser],
) -> tuple[str, str]:
    """
    Construye título y mensaje para la asignación de un servicio.
    Intenta agregar nombre/dirección del sitio, Construcción, Altura,
    datos de acceso (Candado BT, Acceso, Claves, Llaves, Cantidad Llaves),
    link a Google Maps, sin incluir importes económicos.
    """
    du_raw = getattr(servicio, "du", None)
    du_txt = f"DU{str(du_raw).zfill(8)}" if du_raw else "Sin DU"

    id_claro = _clean_info(getattr(servicio, "id_claro", "")) or "Sin ID Claro"
    region = _clean_info(getattr(servicio, "region", "")) or "Sin región"
    detalle = _clean_info(getattr(servicio, "detalle_tarea", "")) or "Sin detalle"

    asignador_txt = actor.get_full_name() if actor else "Sistema"

    # ==========================================================
    # Buscar SitioMovil + Google Maps + accesos
    # ==========================================================
    sitio_nombre = ""
    sitio_direccion = ""

    # NUEVO
    tipo_construccion = ""
    altura = ""

    google_link = ""

    candado_bt = ""
    acceso = ""
    claves = ""
    llaves = ""
    cantidad_llaves = ""

    try:
        from operaciones.models import SitioMovil

        sitio = None

        id_claro_val = getattr(servicio, "id_claro", None)
        id_new_val = getattr(servicio, "id_new", None)

        qs = SitioMovil.objects.all()

        if id_claro_val:
            sitio = qs.filter(id_claro=id_claro_val).first()

        if not sitio and id_new_val:
            sitio = qs.filter(id_sites_new=id_new_val).first()

        if sitio:
            sitio_nombre = _clean_info(sitio.nombre)

            sitio_direccion = _clean_info(sitio.direccion)

            # ==================================================
            # NUEVO: Construcción + Altura
            # ==================================================
            tipo_construccion = _clean_info(getattr(sitio, "tipo_construccion", ""))

            altura = _clean_info(getattr(sitio, "altura", ""))

            # ==================================================
            # Datos de acceso / seguridad
            # ==================================================
            candado_bt = _clean_info(getattr(sitio, "candado_bt", ""))

            acceso = _clean_info(getattr(sitio, "condiciones_acceso", ""))

            claves = _clean_info(getattr(sitio, "claves", ""))

            llaves = _clean_info(getattr(sitio, "llaves", ""))

            cantidad_llaves = _clean_info(getattr(sitio, "cantidad_llaves", ""))

            # ==================================================
            # Link Google Maps
            # ==================================================
            if sitio.latitud is not None and sitio.longitud is not None:
                lat = str(sitio.latitud).replace(",", ".")

                lng = str(sitio.longitud).replace(",", ".")

                google_link = (
                    "https://www.google.com/maps/search/" f"?api=1&query={lat},{lng}"
                )

    except Exception:
        google_link = ""

    # ==========================================================
    # Título
    # ==========================================================
    titulo = f"Asignación de servicio {du_txt}"

    # ==========================================================
    # Mensaje principal
    # ==========================================================
    mensaje = (
        "🔔 Nueva asignación de servicio\n\n"
        f"{du_txt}\n"
        f"ID Claro: {id_claro}\n"
        f"Región: {region}\n"
        f"Detalle: {detalle}\n"
    )

    # ==========================================================
    # Bloque info de sitio
    # ==========================================================
    if sitio_nombre or sitio_direccion or tipo_construccion or altura:
        mensaje += "\n📍 Sitio:\n"

        if sitio_nombre:
            mensaje += f"{sitio_nombre}\n"

        if sitio_direccion:
            mensaje += f"{sitio_direccion}\n"

        if tipo_construccion:
            mensaje += f"- Construcción: {tipo_construccion}\n"

        if altura:
            mensaje += f"- Altura: {altura}\n"

    # ==========================================================
    # Bloque accesos / seguridad
    # ==========================================================
    if any(
        [
            candado_bt,
            acceso,
            claves,
            llaves,
            cantidad_llaves,
        ]
    ):
        mensaje += "\n� Accesos / Seguridad:\n"

        if candado_bt:
            mensaje += f"- Candado BT: {candado_bt}\n"

        if acceso:
            mensaje += f"- Acceso: {acceso}\n"

        if claves:
            mensaje += f"- Claves: {claves}\n"

        if llaves:
            mensaje += f"- Llaves: {llaves}\n"

        if cantidad_llaves:
            mensaje += f"- Cantidad llaves: {cantidad_llaves}\n"

    # ==========================================================
    # Google Maps
    # ==========================================================
    if google_link:
        mensaje += f"\n🌐 Google Maps:\n" f"{google_link}\n"

    # ==========================================================
    # Footer
    # ==========================================================
    mensaje += (
        "\n"
        f"👷 Técnico: {tecnico.get_full_name() or tecnico.username}\n"
        f"👤 Asignado por: {asignador_txt}\n\n"
        "✅ Revisa el servicio en la app de GZ Services."
    )

    return titulo, mensaje


def notificar_asignacion_servicio_tecnicos(
    servicio: Any,
    actor: Optional[CustomUser] = None,
    url: str = "",
    extra: Optional[dict] = None,
):
    """
    Envía notificación de ASIGNACIÓN DE SERVICIO por Telegram
    a TODOS los técnicos en servicio.trabajadores_asignados.

    Devuelve una lista con los NotificationLog creados.
    """
    logs = []
    extra = extra.copy() if extra else {}

    # Guardamos también info del servicio en el extra
    extra.setdefault("servicio_id", getattr(servicio, "id", None))
    extra.setdefault("servicio_du", getattr(servicio, "du", None))

    for tecnico in servicio.trabajadores_asignados.all():
        titulo, mensaje = _build_mensaje_asignacion(servicio, tecnico, actor)

        log = enviar_telegram(
            usuario=tecnico,
            actor=actor,
            tipo=NotificationLog.TIPO_SERVICIO_ASIGNADO,
            titulo=titulo,
            mensaje=mensaje,
            url=url,
            servicio=servicio,
            extra=extra,
        )
        logs.append(log)

    return logs

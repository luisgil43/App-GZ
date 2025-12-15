# bot_gz/services.py

import logging
import re
import unicodedata
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento
from liquidaciones.models import Liquidacion
from operaciones.models import ServicioCotizado, SitioMovil
from rrhh.models import ContratoTrabajo, CronogramaPago
from usuarios.models import CustomUser

from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample

logger = logging.getLogger(__name__)


def _get_bot_token() -> Optional[str]:
    """
    Lee el token del bot desde settings.

    Usamos el mismo criterio que en notificaciones:
    TELEGRAM_BOT_TOKEN_GZ o TELEGRAM_BOT_TOKEN.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN_GZ", None) or getattr(
        settings, "TELEGRAM_BOT_TOKEN", None
    )
    if not token:
        logger.warning(
            "Telegram bot: no se encontró TELEGRAM_BOT_TOKEN_GZ ni TELEGRAM_BOT_TOKEN en settings."
        )
    return token


# ===================== Helpers de normalización =====================

_STOPWORDS = {
    "yo", "mi", "mis", "de", "del", "la", "el", "los", "las",
    "un", "una", "por", "para", "que", "en", "a", "al",
    "este", "este", "mes", "hoy", "ayer",
    "quiero", "necesito", "dime", "decime", "muéstrame", "muestrame",
    "pásame", "pasame", "ayudame", "ayúdame",
    "porfa", "porfavor", "por", "favor",
}


def _unaccent(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = _unaccent(t)
    # dejar solo letras/números/espacios
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _tokenize(text: str):
    norm = _normalize_text(text)
    tokens = norm.split()
    return [t for t in tokens if t not in _STOPWORDS]


# ===================== Parsing de mes/año =====================

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _parse_mes_anio_desde_texto(texto: str) -> Optional[Tuple[int, int]]:
    """
    Intenta extraer mes/año desde texto libre:
    - nombres de mes (enero, febrero, ...)
    - número de mes (1..12, 01..12)
    - año (2024, 2025, ...) si viene; si no, usa año actual.
    """
    tokens = _tokenize(texto)
    if not tokens:
        return None

    # año explícito
    anio = None
    for t in tokens:
        m = re.match(r"20\d{2}$", t)
        if m:
            anio = int(m.group())
            break
    if not anio:
        anio = timezone.localdate().year

    # mes por nombre
    for t in tokens:
        if t in _MESES:
            return _MESES[t], anio

    # mes numérico simple
    for t in tokens:
        if t.isdigit():
            val = int(t)
            if 1 <= val <= 12:
                return val, anio

    # intentos tipo 07-2025 o 07/2025
    m2 = re.search(r"(0?[1-9]|1[0-2])[-/](20\d{2})", _normalize_text(texto))
    if m2:
        mes = int(m2.group(1))
        anio = int(m2.group(2))
        return mes, anio

    return None


# ===================== Sesiones y usuarios =====================

def get_or_create_session(chat_id: str, from_user: Optional[dict] = None) -> Tuple[BotSession, Optional[CustomUser]]:
    """
    Obtiene o crea la sesión del bot para este chat.
    Intenta vincularla con un CustomUser que tenga ese telegram_chat_id.
    """
    chat_id_str = str(chat_id)

    usuario = CustomUser.objects.filter(
        telegram_chat_id=chat_id_str,
        telegram_activo=True,
    ).first()

    defaults = {
        "usuario": usuario,
        "contexto": "tecnico",
        "activa": True,
        "ultima_interaccion": timezone.now(),
    }

    sesion, created = BotSession.objects.get_or_create(
        chat_id=chat_id_str,
        defaults=defaults,
    )

    # Si luego encontramos usuario y la sesión no lo tenía, actualizamos
    if not created and usuario and sesion.usuario_id != usuario.id:
        sesion.usuario = usuario
        sesion.save(update_fields=["usuario"])

    return sesion, sesion.usuario


# ===================== Detección de intent =====================

def detect_intent_from_text(texto: str, scope: Optional[str] = None) -> Tuple[Optional[BotIntent], float]:
    """
    Detección simple de intent basada en overlap de tokens con ejemplos de entrenamiento.
    Devuelve (intent, confianza). Si no encuentra nada sólido, intent = None.
    """
    user_tokens = set(_tokenize(texto))
    if not user_tokens:
        return None, 0.0

    examples_qs = BotTrainingExample.objects.filter(activo=True).select_related("intent")

    if scope:
        examples_qs = examples_qs.filter(intent__scope__in=[scope, "global"])

    best_score = 0.0
    best_intent: Optional[BotIntent] = None

    for ex in examples_qs:
        ex_tokens = set(_tokenize(ex.texto))
        if not ex_tokens:
            continue
        inter = user_tokens & ex_tokens
        if not inter:
            continue

        score = len(inter) / len(ex_tokens)
        if score > best_score:
            best_score = score
            best_intent = ex.intent

    # Umbral mínimo para aceptar un intent
    if best_score < 0.3:
        return None, best_score

    return best_intent, float(best_score)


# ===================== Envío de mensajes a Telegram + log =====================

def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    sesion: Optional[BotSession] = None,
    usuario: Optional[CustomUser] = None,
    intent: Optional[BotIntent] = None,
    meta: Optional[dict] = None,
    marcar_para_entrenamiento: bool = False,
) -> BotMessageLog:
    """
    Envía un mensaje a Telegram y registra el BotMessageLog (salida).
    """
    token = _get_bot_token()
    chat_id_str = str(chat_id)

    meta = meta or {}

    log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id_str,
        direccion="out",
        texto=text,
        intent_detectado=intent,
        status="ok",
        marcar_para_entrenamiento=marcar_para_entrenamiento,
        meta=meta,
    )

    if not token:
        log.status = "error"
        log.meta = {**meta, "error": "No hay token de Telegram configurado"}
        log.save(update_fields=["status", "meta"])
        logger.error("No se puede enviar mensaje Telegram: falta token.")
        return log

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id_str,
        "text": text,
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        try:
            data = resp.json()
        except Exception:
            data = None

        if resp.status_code == 200 and isinstance(data, dict) and data.get("ok"):
            # ok
            logger.info("Mensaje bot OUT ok chat_id=%s", chat_id_str)
        else:
            desc = ""
            if isinstance(data, dict):
                desc = data.get("description") or ""
            if not desc:
                desc = resp.text[:500]

            log.status = "error"
            meta_error = dict(meta)
            meta_error["telegram_error"] = {
                "status_code": resp.status_code,
                "description": desc,
            }
            log.meta = meta_error
            log.save(update_fields=["status", "meta"])
            logger.error("Error enviando mensaje bot OUT a Telegram: %s", desc)

    except Exception as e:
        log.status = "error"
        meta_error = dict(meta)
        meta_error["exception"] = str(e)
        log.meta = meta_error
        log.save(update_fields=["status", "meta"])
        logger.exception("Excepción enviando mensaje bot OUT a Telegram")

    return log


# ===================== Handlers de intents (respuestas) =====================

def _respuesta_sin_usuario(chat_id: str) -> str:
    return (
        "👋 Hola! Aún no tengo vinculado este chat de Telegram con un usuario de GZ Services.\n\n"
        "Por favor pídele al administrador que configure tu `telegram_chat_id` en tu ficha "
        "de usuario dentro del sistema para que pueda ayudarte con tus datos personales."
    )


def _handler_cronograma_produccion(usuario: CustomUser) -> str:
    obj = CronogramaPago.objects.first()
    if not obj:
        return (
            "Por ahora no tengo un cronograma de pagos/corte de producción configurado "
            "en el sistema. Consulta con administración o finanzas."
        )

    hoy = timezone.localdate()
    mes = hoy.month

    mapping = {
        1: ("enero_texto", "enero_fecha", "Enero"),
        2: ("febrero_texto", "febrero_fecha", "Febrero"),
        3: ("marzo_texto", "marzo_fecha", "Marzo"),
        4: ("abril_texto", "abril_fecha", "Abril"),
        5: ("mayo_texto", "mayo_fecha", "Mayo"),
        6: ("junio_texto", "junio_fecha", "Junio"),
        7: ("julio_texto", "julio_fecha", "Julio"),
        8: ("agosto_texto", "agosto_fecha", "Agosto"),
        9: ("septiembre_texto", "septiembre_fecha", "Septiembre"),
        10: ("octubre_texto", "octubre_fecha", "Octubre"),
        11: ("noviembre_texto", "noviembre_fecha", "Noviembre"),
        12: ("diciembre_texto", "diciembre_fecha", "Diciembre"),
    }

    texto_field, fecha_field, nombre_mes = mapping[mes]
    texto_mes = getattr(obj, texto_field)
    fecha_mes = getattr(obj, fecha_field)

    msg = "📆 *Corte de producción*\n\n"
    if fecha_mes:
        msg += f"Para *{nombre_mes}* el corte está definido el *{fecha_mes.strftime('%d-%m-%Y')}*.\n"
    else:
        msg += f"Para *{nombre_mes}* no tengo una fecha de corte configurada.\n"

    if texto_mes:
        msg += f"\n📝 Nota: {texto_mes}"

    return msg


def _handler_mis_liquidaciones(usuario: CustomUser, texto_usuario: str) -> str:
    qs = Liquidacion.objects.filter(tecnico=usuario)

    if not qs.exists():
        return "Por ahora no tengo liquidaciones de sueldo cargadas a tu nombre en el sistema."

    objetivo = None
    parsed = _parse_mes_anio_desde_texto(texto_usuario)
    if parsed:
        mes, anio = parsed
        objetivo = qs.filter(mes=mes, año=anio).first()
        if not objetivo:
            return (
                f"No encontré una liquidación para {mes:02d}/{anio}.\n"
                "Revisa si ya fue cargada en el sistema o intenta con otro mes/año."
            )
    else:
        # Si el usuario no especificó, le mando la última
        objetivo = qs.order_by("-año", "-mes").first()

    if not objetivo:
        return "No encontré liquidaciones que coincidan con tu consulta."

    # Preferimos el PDF firmado si existe
    url = None
    if objetivo.pdf_firmado:
        url = objetivo.pdf_firmado.url
    elif objetivo.archivo_pdf_liquidacion:
        url = objetivo.archivo_pdf_liquidacion.url

    if not url:
        return (
            f"Tengo registrada tu liquidación de {objetivo.mes:02d}/{objetivo.año}, "
            "pero aún no tiene un archivo PDF asociado."
        )

    return (
        f"🧾 Tu liquidación de sueldo {objetivo.mes:02d}/{objetivo.año}:\n\n"
        f"{url}\n\n"
        "Puedes abrir ese enlace para descargarla."
    )


def _handler_mi_contrato(usuario: CustomUser) -> str:
    contrato = (
        ContratoTrabajo.objects.filter(tecnico=usuario)
        .order_by("-fecha_inicio")
        .first()
    )
    if not contrato:
        return "No tengo registrado ningún contrato de trabajo asociado a tu usuario."

    url = contrato.archivo.url if contrato.archivo else None
    estado = contrato.status_label

    msg = "📄 *Tu contrato de trabajo*\n\n"
    msg += f"• Estado: *{estado}*\n"
    msg += f"• Fecha de inicio: {contrato.fecha_inicio.strftime('%d-%m-%Y')}\n"
    if contrato.fecha_termino:
        msg += f"• Fecha de término: {contrato.fecha_termino.strftime('%d-%m-%Y')}\n"

    if url:
        msg += f"\n🔗 Archivo del contrato:\n{url}"
    else:
        msg += "\n(No tengo un archivo PDF/subido para este contrato)."

    return msg


def _handler_info_sitio_id_claro(texto_usuario: str) -> str:
    # Buscar algo tipo MA5694, CL1234, etc.
    match = re.search(r"\b[A-Za-z]{1,3}\d{3,6}\b", texto_usuario)
    if not match:
        return (
            "Para ayudarte con la información del sitio necesito que me indiques el *ID Claro*, "
            "por ejemplo: `MA5694`."
        )

    id_claro = match.group(0).upper()

    sitio = (
        SitioMovil.objects.filter(id_claro__iexact=id_claro).first()
        or SitioMovil.objects.filter(id_sites__iexact=id_claro).first()
        or SitioMovil.objects.filter(id_sites_new__iexact=id_claro).first()
    )

    if not sitio:
        return f"No encontré un sitio con ID Claro `{id_claro}` en el sistema."

    msg = f"📡 *Sitio {id_claro}*\n\n"
    if sitio.nombre:
        msg += f"• Nombre: {sitio.nombre}\n"
    if sitio.direccion:
        msg += f"• Dirección: {sitio.direccion}\n"
    if sitio.comuna:
        msg += f"• Comuna: {sitio.comuna}\n"
    if sitio.region:
        msg += f"• Región: {sitio.region}\n"

    # Seguridad / acceso
    detalles = []
    if sitio.candado_bt:
        detalles.append(f"Candado BT: {sitio.candado_bt}")
    if sitio.condiciones_acceso:
        detalles.append(f"Acceso: {sitio.condiciones_acceso}")
    if sitio.claves:
        detalles.append(f"Claves: {sitio.claves}")
    if sitio.llaves:
        detalles.append(f"Llaves: {sitio.llaves}")
    if sitio.cantidad_llaves:
        detalles.append(f"Cantidad de llaves: {sitio.cantidad_llaves}")

    if detalles:
        msg += "\n🔐 *Acceso / Seguridad:*\n"
        for d in detalles:
            msg += f"• {d}\n"

    if sitio.latitud is not None and sitio.longitud is not None:
        lat = str(sitio.latitud).replace(",", ".")
        lng = str(sitio.longitud).replace(",", ".")
        google_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        msg += f"\n📍 Google Maps:\n{google_link}"

    return msg


def _handler_mis_proyectos_pendientes(usuario: CustomUser) -> str:
    estados = ["asignado", "en_progreso", "en_revision_supervisor"]

    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado__in=estados,
    ).order_by("fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos/servicios pendientes en este momento. ✅"

    msg = f"Tienes *{total}* proyectos pendientes.\n\n"
    msg += "Te muestro los últimos asignados:\n"

    for s in qs[:10]:
        du = s.du or "—"
        id_claro = s.id_claro or "Sin ID Claro"
        detalle = (s.detalle_tarea or "").strip()
        if len(detalle) > 80:
            detalle = detalle[:77] + "…"
        msg += f"• DU {du} / {id_claro}: {detalle}\n"

    return msg


def _handler_mis_proyectos_rechazados(usuario: CustomUser) -> str:
    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado="rechazado_supervisor",
    ).order_by("-fecha_aprobacion_supervisor", "-fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos rechazados actualmente. ✅"

    msg = f"Tienes *{total}* proyectos rechazados por supervisor.\n\n"
    for s in qs[:10]:
        du = s.du or "—"
        id_claro = s.id_claro or "Sin ID Claro"
        motivo = (s.motivo_rechazo or "").strip()
        if len(motivo) > 80:
            motivo = motivo[:77] + "…"
        msg += f"• DU {du} / {id_claro} – Motivo: {motivo or 'Sin detalle'}\n"

    return msg


def _handler_mis_rendiciones_pendientes(usuario: CustomUser) -> str:
    estados_pendientes = [
        "pendiente_abono_usuario",
        "pendiente_supervisor",
        "aprobado_supervisor",
        "aprobado_pm",
    ]

    qs = CartolaMovimiento.objects.filter(
        usuario=usuario,
        status__in=estados_pendientes,
    )

    total = qs.count()
    if total == 0:
        return "No tienes rendiciones de gastos pendientes por aprobación. ✅"

    suma_cargos = qs.aggregate(total=Sum("cargos"))["total"] or 0

    msg = (
        f"Tienes *{total}* rendiciones pendientes en el flujo de aprobación.\n"
        f"Monto total declarado (cargos): ${suma_cargos:,.0f}\n\n"
        "Ejemplos recientes:\n"
    )

    for m in qs.order_by("-fecha")[:5]:
        proyecto = m.proyecto.nombre if m.proyecto else "Sin proyecto"
        tipo = m.tipo.nombre if m.tipo else "Sin tipo"
        msg += f"• {m.fecha.strftime('%d-%m-%Y')} – {proyecto} – {tipo} – ${m.cargos:,.0f}\n"

    msg += "\nSi quieres más detalle, puedes verlo en la sección de rendiciones de la app web."
    return msg


# ===================== Router principal de intents =====================

def run_intent(
    intent: Optional[BotIntent],
    texto_usuario: str,
    sesion: BotSession,
    usuario: Optional[CustomUser],
    inbound_log: BotMessageLog,
) -> str:
    """
    Dado un intent detectado (o None), decide qué responder.
    """
    chat_id = sesion.chat_id

    if usuario is None:
        # No hay usuario vinculado -> mensaje estándar
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
        return _respuesta_sin_usuario(chat_id)

    # Si no se detectó intent, devolvemos un fallback amable
    if not intent:
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        return (
            "Por ahora no entendí bien tu mensaje 🤔.\n\n"
            "Puedes probar con frases como:\n"
            "• \"pásame mi liquidación de este mes\"\n"
            "• \"pásame mi contrato de trabajo\"\n"
            "• \"qué proyectos tengo pendientes\"\n"
            "• \"cuántas declaraciones tengo pendientes por aprobación\""
        )

    # Marcamos el intent y confianza como OK
    inbound_log.status = "ok"
    inbound_log.marcar_para_entrenamiento = intent.requiere_revision_humana
    inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

    slug = intent.slug

    # === Router por slug ===
    if slug == "cronograma_produccion_corte":
        return _handler_cronograma_produccion(usuario)

    if slug == "mis_liquidaciones":
        return _handler_mis_liquidaciones(usuario, texto_usuario)

    if slug == "mi_contrato_vigente":
        return _handler_mi_contrato(usuario)

    if slug == "info_sitio_id_claro":
        return _handler_info_sitio_id_claro(texto_usuario)

    if slug == "mis_proyectos_pendientes":
        return _handler_mis_proyectos_pendientes(usuario)

    if slug == "mis_proyectos_rechazados":
        return _handler_mis_proyectos_rechazados(usuario)

    if slug == "mis_rendiciones_pendientes":
        return _handler_mis_rendiciones_pendientes(usuario)

    # Otros intents que todavía no implementamos bien:
    return (
        f"Recibí tu consulta y la reconocí como '{intent.nombre}', "
        "pero esta funcionalidad aún se está terminando de implementar en el bot.\n\n"
        "Mientras tanto, puedes revisar esa información directamente en la app web."
    )


# ===================== Entry point: manejar update de Telegram =====================

def handle_telegram_update(update: dict) -> None:
    """
    Punto de entrada para el webhook de Telegram.
    - Lee el mensaje
    - Crea/actualiza sesión
    - Detecta intent
    - Genera respuesta
    - Envía respuesta y registra logs
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        logger.info("Update de Telegram sin 'message': %s", update)
        return

    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    text = message.get("text") or ""

    if not text.strip():
        # Por ahora ignoramos mensajes sin texto (stickers, fotos, etc.)
        logger.info("Mensaje sin texto recibido (chat_id=%s)", chat.get("id"))
        return

    chat_id = str(chat.get("id"))

    sesion, usuario = get_or_create_session(chat_id, from_user)
    sesion.ultima_interaccion = timezone.now()
    sesion.save(update_fields=["ultima_interaccion"])

    # Log IN
    inbound_log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="in",
        texto=text,
        status="ok",
        meta={"update_id": update.get("update_id")},
    )

    # Detect intent
    intent, confianza = detect_intent_from_text(text, scope=sesion.contexto)
    if intent:
        sesion.ultimo_intent = intent
        sesion.save(update_fields=["ultimo_intent"])

    inbound_log.intent_detectado = intent
    inbound_log.confianza = confianza
    inbound_log.save(update_fields=["intent_detectado", "confianza"])

    # Ejecutar intent
    reply_text = run_intent(intent, text, sesion, usuario, inbound_log)

    # Enviar OUT
    marcar_train_out = (intent is None) or intent.requiere_revision_humana
    send_telegram_message(
        chat_id,
        reply_text,
        sesion=sesion,
        usuario=usuario,
        intent=intent,
        meta={"from_update_id": update.get("update_id")},
        marcar_para_entrenamiento=marcar_train_out,
    )
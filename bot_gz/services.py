# bot_gz/services.py

import logging
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import FieldError
from django.core.files.base import ContentFile
from django.db.models import Count, Q, Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento, Proyecto, TipoGasto
from liquidaciones.models import Liquidacion
from operaciones.models import ServicioCotizado, SitioMovil
from rrhh.models import ContratoTrabajo, CronogramaPago, DocumentoTrabajador
from usuarios.models import CustomUser

from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample
from .services_tecnico import \
    responder_direccion_basura as _responder_direccion_basura
from .services_tecnico import \
    responder_produccion_hasta_hoy as _responder_produccion_hasta_hoy

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
    "yo",
    "mi",
    "mis",
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "por",
    "para",
    "que",
    "en",
    "a",
    "al",
    "quiero",
    "necesito",
    "dime",
    "decime",
    "muéstrame",
    "muestrame",
    "pásame",
    "pasame",
    "ayudame",
    "ayúdame",
    "porfa",
    "porfavor",
    "favor",
    "hola",
    "buenos",
    "dias",
    "días",
    "tardes",
    "noches",
    "y",
}


def _unaccent(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = _unaccent(t)
    # dejar solo letras/números/espacios
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _tokenize(text: str):
    norm = _normalize(text)
    tokens = norm.split()
    return [t for t in tokens if t not in _STOPWORDS]


def _es_saludo(texto: str) -> bool:
    """
    Detecta saludos simples tipo 'hola', 'buenas', 'buenos días', etc.
    Sin usar STOPWORDS, para no perder la señal.
    """
    norm = _normalize(texto)
    if not norm:
        return False

    tokens = norm.split()
    if not tokens:
        return False

    primeras = {"hola", "buenas", "buenos"}
    if tokens[0] in primeras:
        return True

    frases = {
        "hola",
        "hola bot",
        "buenas",
        "buenas tardes",
        "buenas noches",
        "buenos dias",
        "buen dia",
    }
    return norm in frases


def _menciona_otra_persona(texto_original: str, usuario: CustomUser) -> bool:
    """
    Intenta detectar si el mensaje habla de otra persona distinta al usuario.
    Ej: 'contrato de Edgardo', 'liquidación de juan', etc.

    ✅ ROBUSTO:
    - Mantiene detección por palabras capitalizadas (como antes).
    - Agrega detección por patrón "X de <nombre>" aunque el nombre venga en minúsculas.
    - Solo se usa para mostrar mensajes de privacidad (no para buscar info de terceros).
    """
    texto_original = texto_original or ""
    norm = _normalize(texto_original)
    if not norm:
        return False

    NO_NOMBRES = {
        "cual",
        "que",
        "quien",
        "cuando",
        "donde",
        "como",
        "necesito",
        "quiero",
        "dime",
        "decime",
        "ayudame",
        "pasame",
        "muestrame",
        "hola",
        "buenas",
        "buenos",
        "gracias",
        "por",
        "favor",
        "mi",
        "mis",
        "mio",
        "mia",
        "contrato",
        "liquidacion",
        "liquidaciones",
        "produccion",
        "rendicion",
        "rendiciones",
        "proyecto",
        "proyectos",
        "servicio",
        "servicios",
        "sueldo",
        "finiquito",
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "setiembre",
        "octubre",
        "noviembre",
        "diciembre",
        "este",
        "esta",
        "estos",
        "estas",
        "hoy",
        "ayer",
        "manana",
        "mañana",
        "actual",
        "pasado",
        "anterior",
        "vigente",
        "trabajo",
        "laboral",
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "un",
        "una",
        "para",
        "por",
        "al",
        "a",
        "en",
    }

    nombres_usuario = set()
    for campo in [
        usuario.first_name,
        usuario.last_name,
        getattr(usuario, "full_name", ""),
        usuario.get_full_name(),
    ]:
        if campo:
            for trozo in _normalize(str(campo)).split():
                if trozo:
                    nombres_usuario.add(trozo)

    # 1) Detección por Mayúsculas (como antes)
    candidatos = []
    for m in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b", texto_original):
        raw = m.group(0).strip()
        n_norm = _normalize(raw)
        if not n_norm:
            continue
        if n_norm in NO_NOMBRES:
            continue
        candidatos.append(n_norm)

    for n_norm in candidatos:
        if n_norm and n_norm not in nombres_usuario:
            return True

    # 2) Heurística "de <nombre>" aunque esté en minúsculas
    tokens = norm.split()
    tokens_set = set(tokens)

    triggers = {
        "liquidacion",
        "liquidaciones",
        "contrato",
        "contratos",
        "produccion",
        "rendicion",
        "rendiciones",
        "sueldo",
        "finiquito",
    }
    if not (tokens_set & triggers):
        return False

    preps = {"de", "del", "para", "por"}
    skip_after_prep = {"la", "el", "los", "las", "un", "una", "mi", "mis"}

    for i, tok in enumerate(tokens[:-1]):
        if tok not in preps:
            continue

        j = i + 1
        while j < len(tokens) and tokens[j] in skip_after_prep:
            j += 1
        if j >= len(tokens):
            continue

        cand1 = tokens[j]
        cand2 = tokens[j + 1] if (j + 1) < len(tokens) else None

        if not cand1.isalpha():
            continue
        if cand1 in NO_NOMBRES or cand1 in _STOPWORDS:
            continue
        if cand1 in nombres_usuario:
            continue

        if cand2 and cand2.isalpha() and (cand2 not in NO_NOMBRES) and (cand2 not in _STOPWORDS):
            if cand2 not in nombres_usuario:
                return True

        return True

    return False


# ===================== Parsing de mes/año =====================

_MESES = {
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
}

_NUM_PALABRAS = {
    "una": 1,
    "un": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
}


def _parse_ultimas_n_desde_texto(texto: str) -> Optional[int]:
    """
    Detecta "ultimas 3 liquidaciones", "ultimos tres meses", "últimas dos", etc.
    Devuelve N o None.
    """
    norm = _normalize(texto)

    if not re.search(r"\bultim[oa]s?\b", norm):
        return None

    m = re.search(r"\bultim[oa]s?\s+(\d{1,2})\b", norm)
    if m:
        try:
            n = int(m.group(1))
            return max(1, min(n, 12))
        except Exception:
            return None

    m2 = re.search(r"\bultim[oa]s?\s+([a-z]+)\b", norm)
    if m2:
        palabra = m2.group(1)
        if palabra in _NUM_PALABRAS:
            n = _NUM_PALABRAS[palabra]
            return max(1, min(n, 12))

    if re.search(r"\bultim[oa]s?\b.*\bliquidacion", norm) or re.search(
        r"\bultim[oa]s?\b.*\bmes", norm
    ):
        return 3

    return None


def _parse_meses_multi(texto: str) -> list[int]:
    """
    Extrae TODOS los meses mencionados en el texto (por nombre).
    Ej: "julio y septiembre" -> [7, 9]
    """
    tokens = set(_tokenize(texto))
    meses = []
    for t in tokens:
        if t in _MESES:
            meses.append(_MESES[t])
    return sorted(set(meses))


def _parse_anio_explicito(texto: str) -> Optional[int]:
    tokens = _tokenize(texto)
    for t in tokens:
        if re.match(r"20\d{2}$", t):
            return int(t)
    return None


def _get_liquidacion_pdf_url(liq: Liquidacion) -> Optional[str]:
    if getattr(liq, "pdf_firmado", None):
        return liq.pdf_firmado.url
    if getattr(liq, "archivo_pdf_liquidacion", None):
        return liq.archivo_pdf_liquidacion.url
    return None


def _fmt_liq_line(liq: Liquidacion) -> str:
    estado = "firmada ✅" if liq.firmada else "pendiente ✍️"
    url = _get_liquidacion_pdf_url(liq)
    if url:
        return f"• {liq.mes:02d}/{liq.año} – {estado}\n{url}"
    return f"• {liq.mes:02d}/{liq.año} – {estado}\n(Sin PDF asociado aún)"


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

    anio = None
    for t in tokens:
        m = re.match(r"20\d{2}$", t)
        if m:
            anio = int(m.group())
            break
    if not anio:
        anio = timezone.localdate().year

    for t in tokens:
        if t in _MESES:
            return _MESES[t], anio

    for t in tokens:
        if t.isdigit():
            val = int(t)
            if 1 <= val <= 12:
                return val, anio

    m2 = re.search(r"(0?[1-9]|1[0-2])[-/](20\d{2})", _normalize(texto))
    if m2:
        mes = int(m2.group(1))
        anio = int(m2.group(2))
        return mes, anio

    return None


# ===================== Sesiones y usuarios =====================

def get_or_create_session(
    chat_id: str, from_user: Optional[dict] = None
) -> Tuple[BotSession, Optional[CustomUser]]:
    """
    Obtiene o crea la sesión del bot para este chat.
    Intenta vincularla con un CustomUser que tenga ese telegram_chat_id.

    ✅ FIX:
    - Si el usuario ya no calza (porque se reseteó chat_id o se desactivó telegram_activo),
      limpiamos sesion.usuario para evitar que quede "pegado".
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

    # (Opcional sano) si existía sesión pero estaba desactivada, re-activarla
    if not created and sesion.activa is False:
        sesion.activa = True
        sesion.save(update_fields=["activa"])

    # ✅ Si encontramos usuario válido y no coincide con el actual, actualizamos
    if usuario and sesion.usuario_id != usuario.id:
        sesion.usuario = usuario
        sesion.save(update_fields=["usuario"])
        return sesion, sesion.usuario

    # ✅ FIX CLAVE: si NO hay usuario válido pero la sesión tenía uno, lo limpiamos
    if usuario is None and sesion.usuario_id is not None:
        sesion.usuario = None
        sesion.save(update_fields=["usuario"])
        return sesion, None

    return sesion, sesion.usuario

def _ik_main_menu() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🧾 Liquidaciones", "callback_data": "liquidaciones"},
                {"text": "📄 Contrato", "callback_data": "contrato"},
            ],
            [
                {"text": "🧭 Asignación", "callback_data": "asignacion"},
                {"text": "📌 Proyectos", "callback_data": "proyectos"},
            ],
            [
                {"text": "🧾 Rendiciones", "callback_data": "rendiciones"},
                {"text": "📊 Producción", "callback_data": "produccion"},
            ],
            [
                {"text": "📡 Sitio (ID)", "callback_data": "sitio"},
                {"text": "❓ Ayuda / Menú", "callback_data": "menu"},
            ],
        ]
    }


def _ik_produccion_menu() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Este mes", "callback_data": "mi produccion de este mes"},
                {"text": "📅 Mes anterior", "callback_data": "mi produccion del mes anterior"},
            ],
            [
                {"text": "🗓 Agosto 2025", "callback_data": "mi produccion de agosto 2025"},
                {"text": "🗓 Septiembre 2025", "callback_data": "mi produccion de septiembre 2025"},
            ],
            [
                {"text": "⬅️ Menú", "callback_data": "menu"},
            ],
        ]
    }


# ===================== Detección de intent =====================

def detect_intent_from_text(
    texto: str, scope: Optional[str] = None
) -> Tuple[Optional[BotIntent], float]:
    """
    Detección de intent basada en:
    1) overlap de tokens con ejemplos de entrenamiento
    2) refuerzo por palabras clave (liquidación, contrato, rendiciones, etc.)
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

    keyword_intent: Optional[BotIntent] = None
    keyword_score: float = 0.0

    def add_keyword_candidate(slug: str, score: float):
        nonlocal keyword_intent, keyword_score
        if score <= keyword_score:
            return
        try:
            intent = BotIntent.objects.get(slug=slug, activo=True)
        except BotIntent.DoesNotExist:
            return
        keyword_intent = intent
        keyword_score = score

    if {"liquidacion", "liquidaciones"} & user_tokens:
        add_keyword_candidate("mis_liquidaciones", 0.9)

    if "contrato" in user_tokens or "contratos" in user_tokens:
        add_keyword_candidate("mi_contrato_vigente", 0.9)

    if "produccion" in user_tokens:
        add_keyword_candidate("mi_produccion_hasta_hoy", 0.8)

    if "proyectos" in user_tokens or "servicios" in user_tokens:
        if {"pendientes", "pendiente", "asignados"} & user_tokens:
            add_keyword_candidate("mis_proyectos_pendientes", 0.8)

    if {"asignacion", "asignaciones", "asignado", "asignados", "asignada", "asignadas"} & user_tokens:
        add_keyword_candidate("mi_asignacion", 0.95)

    if {"proyectos", "proyecto", "servicios", "servicio"} & user_tokens:
        if not ({"rechazados", "rechazado", "rechazadas", "rechazada"} & user_tokens):
            add_keyword_candidate("mis_proyectos_pendientes", 0.75)

    if {"rechazados", "rechazado", "rechazadas", "rechazada"} & user_tokens:
        add_keyword_candidate("mis_proyectos_rechazados", 0.8)

    if {"rendicion", "rendiciones", "gasto", "gastos"} & user_tokens:
        add_keyword_candidate("mis_rendiciones_pendientes", 0.85)

    if {"basura", "residuos", "desechos"} & user_tokens:
        add_keyword_candidate("direccion_basura", 0.9)

    if {"pago", "pagan", "pagar", "corte", "cronograma"} & user_tokens:
        add_keyword_candidate("cronograma_produccion_corte", 0.7)

    if "sitio" in user_tokens or "site" in user_tokens:
        add_keyword_candidate("info_sitio_id_claro", 0.7)

    txt_up = (texto or "").strip().upper()
    if (
        re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
        or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)
        or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)
    ):
        add_keyword_candidate("info_sitio_id_claro", 0.72)

    final_intent = best_intent
    final_score = best_score

    if keyword_intent and keyword_score > final_score:
        final_intent = keyword_intent
        final_score = keyword_score

    if final_score < 0.3:
        return None, float(final_score)

    return final_intent, float(final_score)


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
    reply_markup: Optional[dict] = None,
) -> BotMessageLog:
    """
    Versión PRO:
    - parse_mode=HTML
    - A) Texto ya en HTML => se envía tal cual
    - B) Texto "markdownish" (*bold*, `code`) => se convierte a HTML seguro
    - Split automático por límite Telegram
    - Fallback a texto plano si falla HTML
    """
    import html as _html  # stdlib

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

    def _split_text(raw: str, limit: int = 3800) -> list[str]:
        raw = (raw or "").replace("\r\n", "\n").strip()
        if not raw:
            return [""]

        if len(raw) <= limit:
            return [raw]

        parts: list[str] = []
        buf: list[str] = []

        for block in raw.split("\n\n"):
            candidate = ("\n\n".join(buf + [block])).strip()
            if len(candidate) <= limit:
                buf.append(block)
                continue

            if buf:
                parts.append("\n\n".join(buf).strip())
                buf = []

            if len(block) > limit:
                line_buf = ""
                for line in block.split("\n"):
                    cand2 = (line_buf + ("\n" if line_buf else "") + line).strip()
                    if len(cand2) <= limit:
                        line_buf = cand2
                    else:
                        if line_buf:
                            parts.append(line_buf)
                        if len(line) > limit:
                            start = 0
                            while start < len(line):
                                parts.append(line[start:start + limit])
                                start += limit
                            line_buf = ""
                        else:
                            line_buf = line
                if line_buf:
                    parts.append(line_buf)
            else:
                buf = [block]

        if buf:
            parts.append("\n\n".join(buf).strip())

        return [p for p in parts if p is not None and p != ""]

    _HTML_TAG_RE = re.compile(
        r"</?(b|strong|i|em|u|s|strike|code|pre|a)(\s|>)", re.IGNORECASE
    )

    def _looks_like_html(raw: str) -> bool:
        if not raw:
            return False
        return bool(_HTML_TAG_RE.search(raw))

    def _bold_to_html(escaped_text: str) -> str:
        return re.sub(r"\*(.+?)\*", r"<b>\1</b>", escaped_text)

    def _markdownish_to_html(raw: str) -> str:
        raw = (raw or "").replace("\r\n", "\n")

        if raw.count("`") % 2 == 1:
            return _bold_to_html(_html.escape(raw))

        parts = raw.split("`")
        out: list[str] = []
        for i, seg in enumerate(parts):
            if i % 2 == 1:
                out.append(f"<code>{_html.escape(seg)}</code>")
            else:
                out.append(_bold_to_html(_html.escape(seg)))
        return "".join(out)

    def _to_html(raw: str) -> str:
        if _looks_like_html(raw):
            return (raw or "").replace("\r\n", "\n")
        return _markdownish_to_html(raw)

    chunks = _split_text(text, limit=3800)

    results = []
    any_error = False

    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id_str,
            "text": _to_html(chunk),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        if reply_markup and idx == 0:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(api_url, json=payload, timeout=10)
            try:
                data = resp.json()
            except Exception:
                data = None

            ok = (resp.status_code == 200 and isinstance(data, dict) and data.get("ok"))
            if ok:
                results.append({"chunk": idx, "ok": True})
                continue

            desc = ""
            if isinstance(data, dict):
                desc = data.get("description") or ""
            if not desc:
                desc = resp.text[:500]

            payload_plain = dict(payload)
            payload_plain.pop("parse_mode", None)
            payload_plain["text"] = chunk

            resp2 = requests.post(api_url, json=payload_plain, timeout=10)
            try:
                data2 = resp2.json()
            except Exception:
                data2 = None

            ok2 = (resp2.status_code == 200 and isinstance(data2, dict) and data2.get("ok"))
            if ok2:
                logger.warning(
                    "Telegram HTML falló, enviado como texto plano (chat_id=%s, chunk=%s): %s",
                    chat_id_str, idx, desc
                )
                results.append({"chunk": idx, "ok": True, "fallback_plain": True, "first_error": desc})
                continue

            desc2 = ""
            if isinstance(data2, dict):
                desc2 = data2.get("description") or ""
            if not desc2:
                desc2 = resp2.text[:500]

            any_error = True
            results.append({"chunk": idx, "ok": False, "first_error": desc, "fallback_error": desc2})
            logger.error(
                "Error enviando mensaje bot OUT a Telegram (chunk=%s): %s | fallback: %s",
                idx, desc, desc2
            )

        except Exception as e:
            any_error = True
            results.append({"chunk": idx, "ok": False, "exception": str(e)})
            logger.exception("Excepción enviando mensaje bot OUT a Telegram (chunk=%s)", idx)

    log.meta = {**meta, "telegram_send_results": results}

    if any_error:
        log.status = "error"
        log.save(update_fields=["status", "meta"])
    else:
        log.save(update_fields=["meta"])

    return log


# ===================== Handlers de intents (respuestas) =====================

def _respuesta_sin_usuario(chat_id: str) -> str:
    return (
        "👋 Hola. Todavía no tengo vinculado este chat de Telegram con tu usuario de GZ Services.\n\n"
        "Pídele al administrador que configure tu `telegram_chat_id` en tu ficha de usuario "
        "para que pueda mostrarte tu información personal (liquidaciones, contratos, proyectos, etc.)."
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
    texto_mes = getattr(obj, texto_field, "") or ""
    fecha_mes = getattr(obj, fecha_field, None)

    msg = "📆 *Corte de producción*\n\n"
    if fecha_mes:
        msg += f"Para *{nombre_mes}* el corte está definido el *{fecha_mes.strftime('%d-%m-%Y')}*.\n"
    else:
        msg += f"Para *{nombre_mes}* no tengo una fecha de corte configurada.\n"

    if texto_mes:
        msg += f"\n📝 Nota: {texto_mes}"

    return msg


def _handler_mis_liquidaciones(usuario: CustomUser, texto_usuario: str) -> str:
    """
    Maneja consultas de liquidaciones (PRO).
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tus propias* liquidaciones de sueldo.\n"
            "Si un compañero necesita la suya, debe pedirla directamente a RRHH o entrar con su usuario."
        )

    qs = Liquidacion.objects.filter(tecnico=usuario).order_by("-año", "-mes")

    if not qs.exists():
        return "Por ahora no tengo liquidaciones de sueldo cargadas a tu nombre en el sistema."

    n = _parse_ultimas_n_desde_texto(texto_usuario)
    if n:
        liqs = list(qs[:n])
        if not liqs:
            return "No encontré liquidaciones recientes a tu nombre."

        lineas = [f"🧾 Tus últimas {len(liqs)} liquidaciones (más recientes):", ""]
        for liq in liqs:
            lineas.append(_fmt_liq_line(liq))
            lineas.append("")
        return "\n".join(lineas).strip()

    meses_multi = _parse_meses_multi(texto_usuario)
    anio_explicito = _parse_anio_explicito(texto_usuario)

    if meses_multi:
        if not anio_explicito:
            ambiguos = {}
            for mes in meses_multi:
                years = list(
                    qs.filter(mes=mes)
                    .values_list("año", flat=True)
                    .distinct()
                    .order_by("-año")
                )
                if len(years) > 1:
                    ambiguos[mes] = years

            if ambiguos:
                parts = ["📌 Tengo esas liquidaciones en más de un año. ¿De qué año las necesitas?\n"]
                for mes, years in ambiguos.items():
                    nombre_mes = [k for k, v in _MESES.items() if v == mes][0]
                    parts.append(f"• {nombre_mes.capitalize()}: {', '.join(str(y) for y in years)}")
                parts.append("\nEjemplo: `liquidaciones de julio y septiembre 2025`")
                return "\n".join(parts).strip()

        resultados = []
        faltantes = []

        for mes in meses_multi:
            if anio_explicito:
                liq = qs.filter(mes=mes, año=anio_explicito).first()
            else:
                years = list(
                    qs.filter(mes=mes)
                    .values_list("año", flat=True)
                    .distinct()
                    .order_by("-año")
                )
                liq = qs.filter(mes=mes, año=years[0]).first() if len(years) == 1 else None

            if liq:
                resultados.append(liq)
            else:
                faltantes.append(mes)

        if not resultados:
            if anio_explicito:
                return f"No encontré liquidaciones para esos meses en el año {anio_explicito}."
            return "No encontré liquidaciones para esos meses."

        resultados.sort(key=lambda x: (x.año, x.mes), reverse=True)

        lineas = ["🧾 Aquí tienes tus liquidaciones solicitadas:", ""]
        for liq in resultados:
            lineas.append(_fmt_liq_line(liq))
            lineas.append("")

        if faltantes:
            nombres = []
            inv_me = {v: k for k, v in _MESES.items()}
            for m in faltantes:
                nombres.append(inv_me.get(m, str(m)).capitalize())
            lineas.append("⚠️ No encontré: " + ", ".join(nombres))

        return "\n".join(lineas).strip()

    tokens = _tokenize(texto_usuario)

    mes = None
    for t in tokens:
        if t in _MESES:
            mes = _MESES[t]
            break

    if mes is None:
        mnum = re.search(r"\b(0?[1-9]|1[0-2])\b", _normalize(texto_usuario))
        if mnum:
            mes = int(mnum.group(1))

    m_my = re.search(r"(0?[1-9]|1[0-2])[-/](20\d{2})", _normalize(texto_usuario))
    if m_my:
        mes = int(m_my.group(1))
        anio_explicito = int(m_my.group(2))

    if mes is not None:
        if not anio_explicito:
            years = list(
                qs.filter(mes=mes)
                .values_list("año", flat=True)
                .distinct()
                .order_by("-año")
            )
            if not years:
                return "No encontré una liquidación para ese mes en ningún año."
            if len(years) > 1:
                inv_me = {v: k for k, v in _MESES.items()}
                nombre_mes = inv_me.get(mes, str(mes)).capitalize()
                return (
                    f"📌 Tengo {nombre_mes} en más de un año: {', '.join(str(y) for y in years)}.\n"
                    f"¿De cuál año la necesitas?\n\n"
                    f"Ejemplo: `liquidación de {nombre_mes} {years[0]}`"
                )
            anio_explicito = years[0]

        objetivo = qs.filter(mes=mes, año=anio_explicito).first()
        if not objetivo:
            return f"No encontré una liquidación para {mes:02d}/{anio_explicito}."

        url = _get_liquidacion_pdf_url(objetivo)
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

    lineas = []
    lineas.append("🧾 Liquidaciones registradas a tu nombre (más recientes):")
    lineas.append("")
    for liq in qs[:12]:
        estado = "firmada ✅" if liq.firmada else "pendiente ✍️"
        lineas.append(f"• {liq.mes:02d}/{liq.año} – {estado}")
    if qs.count() > 12:
        lineas.append("")
        lineas.append("Mostrando solo las 12 más recientes.")

    lineas.append("")
    lineas.append("Pídemelas así y te las mando en un solo mensaje:")
    lineas.append("• `mis últimas 3 liquidaciones`")
    lineas.append("• `mis últimas 4 liquidaciones`")
    lineas.append("• `liquidaciones de julio y septiembre 2025`")
    lineas.append("• `liquidación de noviembre 2025`")
    return "\n".join(lineas)


def _get_contrato_actual_y_extensiones(qs):
    """
    qs: ContratoTrabajo queryset order_by('-fecha_inicio')
    Retorna (contrato_actual, extensiones_vencidas, otros_activos)
    """
    hoy = timezone.localdate()

    contratos = list(qs)
    if not contratos:
        return None, [], []

    activos = []
    vencidos = []

    for c in contratos:
        code = getattr(c, "status_code", None) or c.status_code
        if code in ("indefinido", "vigente", "por_vencer"):
            activos.append(c)
        else:
            if c.fecha_termino and c.fecha_termino < hoy:
                vencidos.append(c)
            else:
                vencidos.append(c)

    contrato_actual = activos[0] if activos else contratos[0]

    extensiones = [
        c
        for c in contratos
        if c.id != contrato_actual.id and c.fecha_inicio <= contrato_actual.fecha_inicio
    ]
    extensiones_vencidas = [c for c in extensiones if c.status_code == "vencido"]

    otros_activos = [c for c in activos if c.id != contrato_actual.id]

    return contrato_actual, extensiones_vencidas, otros_activos


def _buscar_anexos_rrhh(usuario: CustomUser):
    """
    Anexos como 'DocumentoTrabajador' (RRHH).
    Heurística: tipo_documento.nombre contiene 'anexo' o 'extension'.
    """
    return DocumentoTrabajador.objects.filter(
        trabajador=usuario,
        tipo_documento__nombre__iregex=r"(anex|extens)"
    ).select_related("tipo_documento").order_by("-creado")


def _handler_mi_contrato(usuario: CustomUser, texto_usuario: str) -> str:
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tu propio contrato de trabajo*.\n"
            "No tengo permiso para mostrar contratos de otros compañeros."
        )

    tokens = set(_tokenize(texto_usuario))

    quiere_extensiones = bool({"extension", "extensiones", "anteriores", "vencidos", "historial"} & tokens)
    quiere_anexos = bool({"anexo", "anexos"} & tokens)
    quiere_todos = bool({"todos", "todas"} & tokens) or ("contratos" in tokens)

    qs = ContratoTrabajo.objects.filter(tecnico=usuario).order_by("-fecha_inicio")
    if not qs.exists():
        return "No tengo registrado ningún contrato de trabajo asociado a tu usuario."

    if quiere_todos:
        contratos = list(qs[:20])
        msg = "📄 Tus contratos registrados:\n\n"
        for c in contratos:
            termino = "Indefinido" if not c.fecha_termino else c.fecha_termino.strftime("%d-%m-%Y")
            msg += f"• Inicio: {c.fecha_inicio.strftime('%d-%m-%Y')} | Término: {termino} | Estado: {c.status_label}\n"
            if c.archivo:
                msg += f"  {c.archivo.url}\n"
            else:
                msg += "  (Sin PDF asociado)\n"

        if qs.count() > len(contratos):
            msg += f"\nMostrando {len(contratos)} de {qs.count()}."
        msg += (
            "\n\nSi quieres solo el vigente, dime: `mi contrato vigente`.\n"
            "Si quieres el vigente + extensiones vencidas: `mi contrato y sus extensiones`."
        )
        return msg

    contrato_actual, extensiones_vencidas, otros_activos = _get_contrato_actual_y_extensiones(qs)

    termino = "Indefinido" if not contrato_actual.fecha_termino else contrato_actual.fecha_termino.strftime("%d-%m-%Y")

    msg = "📄 Tu contrato más reciente (vigente):\n\n"
    msg += f"• Estado: {contrato_actual.status_label}\n"
    msg += f"• Fecha de inicio: {contrato_actual.fecha_inicio.strftime('%d-%m-%Y')}\n"
    msg += f"• Fecha de término: {termino}\n"

    if contrato_actual.archivo:
        msg += f"\n🔗 Archivo del contrato:\n{contrato_actual.archivo.url}"
    else:
        msg += "\n(No tengo un archivo PDF/subido para este contrato)."

    if otros_activos:
        msg += "\n\n⚠️ Nota: veo más de un contrato marcado como vigente/activo en el sistema."

    if quiere_extensiones or ({"extension", "extensiones"} & tokens):
        if not extensiones_vencidas:
            msg += (
                "\n\n📎 Extensiones / contratos anteriores:\n"
                "No tienes extensiones vencidas registradas como contratos separados en el sistema."
            )
        else:
            msg += "\n\n📎 Extensiones / contratos anteriores (vencidos):\n"
            for c in extensiones_vencidas[:10]:
                termino2 = "Indefinido" if not c.fecha_termino else c.fecha_termino.strftime("%d-%m-%Y")
                msg += f"• Inicio: {c.fecha_inicio.strftime('%d-%m-%Y')} | Término: {termino2} | Estado: {c.status_label}\n"
                if c.archivo:
                    msg += f"  {c.archivo.url}\n"
                else:
                    msg += "  (Sin PDF asociado)\n"

    if quiere_anexos:
        anexos = list(_buscar_anexos_rrhh(usuario)[:10])
        if not anexos:
            msg += (
                "\n\n📎 Anexos:\n"
                "Usted no posee anexos cargados en el sistema.\n"
                "Comunícate con *Recursos Humanos* para que puedan cargarte tus anexos.\n"
                "Te estoy compartiendo el contrato más reciente."
            )
        else:
            msg += "\n\n📎 Anexos (RRHH):\n"
            for a in anexos:
                nombre = a.tipo_documento.nombre if a.tipo_documento else "Anexo"
                msg += f"• {nombre}\n"
                msg += f"  {a.archivo.url}\n"

    if (not quiere_extensiones) and (not quiere_anexos) and extensiones_vencidas:
        msg += (
            f"\n\nTengo además *{len(extensiones_vencidas)}* contrato(s) anterior(es) vencido(s) (extensiones).\n"
            "¿Quieres que te los envíe también?\n"
            "Ejemplo: `mi contrato y sus extensiones`"
        )

    return msg


def _month_start_end(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _parse_rango_fechas(texto: str):
    """
    Acepta:
    - 2025-08-01 a 2025-08-31
    - 2025/08/01 al 2025/08/31
    - 01-08-2025 a 31-08-2025
    - 01/08/2025 hasta 31/08/2025
    Devuelve (date_start, date_end) o None
    """
    raw = _unaccent((texto or "").strip().lower())
    if not raw:
        return None

    raw = raw.replace("–", "-").replace("—", "-")

    ymd = re.findall(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if len(ymd) >= 2:
        try:
            y1, m1, d1 = map(int, ymd[0])
            y2, m2, d2 = map(int, ymd[1])
            return date(y1, m1, d1), date(y2, m2, d2)
        except ValueError:
            return None

    dmy = re.findall(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b", raw)
    if len(dmy) >= 2:
        try:
            d1, m1, y1 = map(int, dmy[0])
            d2, m2, y2 = map(int, dmy[1])
            return date(y1, m1, d1), date(y2, m2, d2)
        except ValueError:
            return None

    return None


def _parse_mes_produccion(texto: str):
    parsed = _parse_mes_anio_desde_texto(texto)
    if parsed:
        mes, anio = parsed
        return mes, anio
    return None


def _parse_flags_estados_produccion(tokens: set[str]) -> dict:
    return {
        "incluye_asignados": bool({"asignado", "asignados"} & tokens),
        "incluye_ejecucion": bool({"ejecucion", "ejecución", "en_progreso", "progreso"} & tokens),
        "incluye_pendiente_supervisor": bool({"pendiente", "pendientes", "supervisor"} & tokens),
        "incluye_finalizados": bool({"finalizado", "finalizados"} & tokens),
        "incluye_todo": bool({"todo", "todos", "completo"} & tokens),
    }


def responder_produccion_rango(usuario, date_from, date_to, *, incluir_estados=None):
    return (
        f"📊 *Producción estimada*\n"
        f"Rango: {date_from.strftime('%d-%m-%Y')} al {date_to.strftime('%d-%m-%Y')}\n\n"
        "⚙️ (Pendiente: aplicar filtro por fechas en el cálculo)\n"
        "Si quieres, dime qué fecha del servicio usar: creación, aprobación supervisor o finalización."
    )


def _handler_mi_produccion(usuario: CustomUser, texto_usuario: str) -> str:
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tu propia producción*.\n"
            "No tengo permiso para entregar información de producción de otros compañeros."
        )

    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()
    norm = _normalize(texto_usuario)

    flags = _parse_flags_estados_produccion(tokens)

    rango = _parse_rango_fechas(texto_usuario)
    if rango:
        d1, d2 = rango
        if d2 < d1:
            d1, d2 = d2, d1
        return responder_produccion_rango(usuario, d1, d2, incluir_estados=flags)

    if {"hoy", "ahora", "fecha"} & tokens or ("hasta hoy" in norm) or ("a la fecha" in norm):
        return _responder_produccion_hasta_hoy(usuario)

    if ("este mes" in norm) or ("mes actual" in norm) or ("mes" in tokens and ({"este", "actual"} & tokens)):
        start, _end = _month_start_end(hoy.year, hoy.month)
        return responder_produccion_rango(usuario, start, hoy, incluir_estados=flags)

    if ("mes anterior" in norm) or ("mes pasado" in norm) or ("mes" in tokens and ({"anterior", "pasado"} & tokens)):
        year = hoy.year
        month = hoy.month - 1
        if month == 0:
            month = 12
            year -= 1
        start, end = _month_start_end(year, month)
        return responder_produccion_rango(usuario, start, end, incluir_estados=flags)

    parsed_mes = _parse_mes_produccion(texto_usuario)
    if parsed_mes:
        mes, anio = parsed_mes
        start, end = _month_start_end(anio, mes)
        return responder_produccion_rango(usuario, start, end, incluir_estados=flags)

    return (
        "📊 ¿Qué producción necesitas?\n\n"
        "Puedo ayudarte con:\n"
        "• *Este mes hasta hoy*: `mi producción de este mes`\n"
        "• *Mes anterior completo*: `mi producción del mes anterior`\n"
        "• *Un mes específico*: `mi producción de agosto 2025` (o `agosto 2025`)\n"
        "• *Rango de fechas*: `mi producción 2025-08-01 a 2025-08-31`\n\n"
        "También puedo darte un *estimado ampliado* según estados (si lo pides así):\n"
        "• `mi producción incluyendo asignados + en ejecución + pendientes + finalizados`"
    )


def _extract_site_key(texto: str) -> Optional[Tuple[str, str]]:
    """
    Detecta IDs de sitio en el texto y devuelve:
    - ("id_claro", "13_094")
    - ("cl_code",  "CL-13-00421-05" / "CL-13-SN-00421-05" / "CL-13-ÑÑ-01837-11")
    """
    raw = (texto or "").strip()
    if not raw:
        return None

    m = re.search(r"\b(\d{2})[_\-\s](\d{3})\b", raw)
    if m:
        return ("id_claro", f"{m.group(1)}_{m.group(2)}")

    m2 = re.search(
        r"\b(CL-\d{2}-(?:[A-ZÑ]{2,3}-)?\d{5}-\d{2})\b",
        raw,
        flags=re.IGNORECASE,
    )
    if m2:
        return ("cl_code", m2.group(1).upper())

    return None


def _is_only_site_id(texto: str, kind: str, value: str) -> bool:
    cleaned = (texto or "").strip().strip("`'\"").upper().replace(" ", "_")

    if kind == "id_claro":
        return cleaned == value.upper() or cleaned == value.replace("_", "-").upper()

    return cleaned == value.upper()


def _find_sitio_by_any_id(kind: str, value: str) -> Tuple[Optional[SitioMovil], str]:
    qs = SitioMovil.objects.all()

    if kind == "id_claro":
        variants = [value, value.replace("_", "-")]
        for v in variants:
            sm = qs.filter(id_claro__iexact=v).first()
            if sm:
                return sm, "id_claro"
        return None, "id_claro"

    sm = qs.filter(id_sites__iexact=value).first()
    if sm:
        return sm, "id_sites"

    sm = qs.filter(id_sites_new__iexact=value).first()
    if sm:
        return sm, "id_sites_new"

    sm = qs.filter(id_claro__iexact=value).first()
    if sm:
        return sm, "id_claro"

    return None, "id_sites_new"


def _handler_info_sitio_id_claro(texto_usuario: str) -> str:
    key = _extract_site_key(texto_usuario)

    if not key:
        return (
            "Para darte información del sitio, indícame uno de estos IDs:\n"
            "• *ID CLARO*: `13_094`\n"
            "• *ID SITES*: `CL-13-00421-05`\n"
            "• *ID NEW*: `CL-13-SN-00421-05`\n"
        )

    kind, value = key
    sitio, matched_field = _find_sitio_by_any_id(kind, value)

    if not sitio:
        if kind == "id_claro":
            return (
                f"No encontré un sitio con *ID CLARO* `{value}`.\n\n"
                "Si no lo tienes a mano, dime el *ID SITES* (ej: `CL-13-00421-05`) "
                "o el *ID NEW* (ej: `CL-13-SN-00421-05`)."
            )
        return (
            f"No encontré un sitio con ese ID `{value}`.\n\n"
            "Prueba enviándome el *ID CLARO* (ej: `13_094`). "
            "Si no lo tienes, dime el *ID SITES* o el *ID NEW*."
        )

    msg = "📡 *Información del Sitio*\n\n"

    msg += f"• ID Sites: `{sitio.id_sites or '—'}`\n"
    msg += f"• ID Claro: `{sitio.id_claro or '—'}`\n"
    msg += f"• ID New: `{sitio.id_sites_new or '—'}`\n"
    msg += f"• Región: {sitio.region or '—'}\n"

    if sitio.nombre:
        msg += f"• Nombre: {sitio.nombre}\n"
    if sitio.direccion:
        msg += f"• Dirección: {sitio.direccion}\n"
    if sitio.comuna:
        msg += f"• Comuna: {sitio.comuna}\n"

    detalles = []
    if sitio.candado_bt:
        detalles.append(f"Candado BT: {sitio.candado_bt}")
    if sitio.condiciones_acceso:
        detalles.append(f"Condiciones acceso: {sitio.condiciones_acceso}")
    if sitio.claves:
        detalles.append(f"Claves: {sitio.claves}")
    if sitio.llaves:
        detalles.append(f"Llaves: {sitio.llaves}")
    if sitio.cantidad_llaves:
        detalles.append(f"Cantidad llaves: {sitio.cantidad_llaves}")

    if detalles:
        msg += "\n🔐 *Acceso / Seguridad*\n"
        for d in detalles:
            msg += f"• {d}\n"

    if sitio.latitud is not None and sitio.longitud is not None:
        lat = str(sitio.latitud).replace(",", ".")
        lng = str(sitio.longitud).replace(",", ".")
        msg += f"\n📍 Google Maps:\nhttps://www.google.com/maps/search/?api=1&query={lat},{lng}"

    return msg


# ===================== PROYECTOS (PRO) =====================

_PROJ_BUCKETS = {
    "asignados": {"asignado"},
    "en_ejecucion": {"en_progreso"},
    "revision_supervisor": {"en_revision_supervisor"},
    "aprobado_supervisor": {"aprobado_supervisor"},
    "finalizados": {"finalizado_trabajador", "informe_subido", "finalizado"},
    "rechazados": {"rechazado_supervisor"},
}

_PROJ_BUCKET_LABEL = {
    "asignados": "Asignados",
    "en_ejecucion": "En ejecución",
    "revision_supervisor": "En revisión supervisor",
    "aprobado_supervisor": "Aprobado por supervisor",
    "finalizados": "Finalizados",
    "rechazados": "Rechazados",
}


def _fmt_clp(val) -> str:
    try:
        n = float(val or 0)
    except Exception:
        n = 0
    entero = int(round(n, 0))
    return "$" + f"{entero:,}".replace(",", ".")


def _mmoo_share_for_user(s: ServicioCotizado, usuario: CustomUser) -> Decimal:
    total = Decimal(str(getattr(s, "monto_mmoo", None) or 0))

    try:
        through = ServicioCotizado.trabajadores_asignados.through
        field_names = {f.name for f in through._meta.get_fields() if hasattr(f, "name")}

        candidate_amount_fields = [
            "monto_mmoo_tecnico",
            "monto_mano_obra_tecnico",
            "monto_mmoo",
            "monto_mano_obra",
            "monto_asignado",
            "mmoo",
            "monto",
        ]
        amount_field = next((f for f in candidate_amount_fields if f in field_names), None)

        if amount_field:
            svc_fk = next(
                (
                    f.name
                    for f in through._meta.fields
                    if getattr(f, "is_relation", False)
                    and getattr(f, "related_model", None) == ServicioCotizado
                ),
                None,
            )
            usr_fk = next(
                (
                    f.name
                    for f in through._meta.fields
                    if getattr(f, "is_relation", False)
                    and getattr(f, "related_model", None) == CustomUser
                ),
                None,
            )

            if svc_fk and usr_fk:
                val = (
                    through.objects
                    .filter(**{svc_fk: s, usr_fk: usuario})
                    .values_list(amount_field, flat=True)
                    .first()
                )
                if val is not None:
                    return Decimal(str(val or 0))
    except Exception:
        pass

    n = getattr(s, "n_tecs", None)
    if not n:
        try:
            n = s.trabajadores_asignados.count()
        except Exception:
            n = 1
    n = int(n or 1)

    return (total / Decimal(n)) if n else total


def _extract_project_id(texto: str) -> Optional[str]:
    t = (texto or "").strip()

    m = re.search(r"\b\d{2}_\d{3,5}\b", t)
    if m:
        return m.group(0)

    m2 = re.search(r"\bCL-\d{2}-\d{4,6}-\d{2}\b", t, flags=re.IGNORECASE)
    if m2:
        return m2.group(0)

    m3 = re.search(r"\bCL-\d{2}-[A-ZÑ]{1,3}-\d{4,6}-\d{2}\b", t, flags=re.IGNORECASE)
    if m3:
        return m3.group(0)

    return None


def _project_month_filter(texto_usuario: str):
    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()

    if ("mes" in tokens and ({"este", "actual"} & tokens)):
        start, end = _month_start_end(hoy.year, hoy.month)
        return start, end, f"{hoy.month:02d}/{hoy.year}"

    if ("mes" in tokens and ({"pasado", "anterior"} & tokens)):
        year = hoy.year
        month = hoy.month - 1
        if month == 0:
            month = 12
            year -= 1
        start, end = _month_start_end(year, month)
        return start, end, f"{month:02d}/{year}"

    parsed = _parse_mes_anio_desde_texto(texto_usuario)
    if parsed:
        mes, anio = parsed
        start, end = _month_start_end(anio, mes)
        return start, end, f"{mes:02d}/{anio}"

    return None


def _pick_project_buckets(tokens: set[str]) -> list[str]:
    wants = []
    if {"asignado", "asignados"} & tokens:
        wants.append("asignados")
    if {"ejecucion", "ejecución", "progreso", "ejecutando"} & tokens:
        wants.append("en_ejecucion")
    if {"revision", "revisión"} & tokens:
        wants.append("revision_supervisor")
    if {"aprobado", "aprobados", "aprobada", "aprobadas", "aprobacion", "aprobación"} & tokens and "supervisor" in tokens:
        wants.append("aprobado_supervisor")
    if {"finalizado", "finalizados", "terminado", "terminados"} & tokens:
        wants.append("finalizados")
    if {"rechazado", "rechazados", "rechazada", "rechazadas"} & tokens:
        wants.append("rechazados")

    return wants or ["asignados", "en_ejecucion", "revision_supervisor", "aprobado_supervisor", "finalizados", "rechazados"]


def _build_maps_link_for_services(servicios: list[ServicioCotizado]) -> tuple[Optional[str], list[str]]:
    id_claros = [s.id_claro for s in servicios if s.id_claro]
    id_news = [getattr(s, "id_new", None) for s in servicios if getattr(s, "id_new", None)]

    sitios = list(
        SitioMovil.objects.filter(
            Q(id_claro__in=id_claros) | Q(id_sites_new__in=id_news)
        ).only("id_claro", "id_sites_new", "nombre", "direccion", "latitud", "longitud")
    )

    by_id = {}
    for sm in sitios:
        if sm.id_claro:
            by_id[str(sm.id_claro).strip()] = sm
        if sm.id_sites_new:
            by_id[str(sm.id_sites_new).strip()] = sm

    puntos = []
    links_individuales = []

    for s in servicios:
        key = (s.id_claro or "").strip() or (getattr(s, "id_new", None) or "").strip()
        sm = by_id.get(key)
        if not sm:
            continue
        if sm.latitud is None or sm.longitud is None:
            continue

        lat = str(sm.latitud).replace(",", ".")
        lng = str(sm.longitud).replace(",", ".")
        name = (sm.nombre or key or "Sitio").strip()

        links_individuales.append(f"• {name}: https://www.google.com/maps/search/?api=1&query={lat},{lng}")
        puntos.append(f"{lat},{lng}")

    if not puntos:
        return None, links_individuales

    puntos = puntos[:10]

    if len(puntos) == 1:
        return None, links_individuales

    origin = "Current+Location"
    destination = puntos[-1]
    waypoints = "|".join(puntos[:-1])

    ruta = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&waypoints={waypoints}&travelmode=driving"
    return ruta, links_individuales


def _handler_mis_proyectos(usuario: CustomUser, texto_usuario: str) -> str:
    tokens = set(_tokenize(texto_usuario))

    base = (
        ServicioCotizado.objects
        .filter(trabajadores_asignados=usuario)
        .annotate(n_tecs=Count("trabajadores_asignados", distinct=True))
    )

    mf = _project_month_filter(texto_usuario)
    mes_label = None
    if mf:
        start, end, mes_label = mf
        base = base.filter(fecha_creacion__date__gte=start, fecha_creacion__date__lte=end)

    pid = _extract_project_id(texto_usuario)
    if pid:
        pid_u = pid.strip().upper()
        s = (
            base.filter(id_claro__iexact=pid_u).first()
            or base.filter(id_new__iexact=pid_u).first()
            or base.filter(du__iexact=pid_u).first()
        )
        if not s:
            return (
                f"No encontré un proyecto asignado a ti con identificador `{pid_u}`.\n\n"
                "Puedes mandarme:\n"
                "• `13_094` (ID Claro)\n"
                "• `CL-13-00421-05` (ID Sites)\n"
                "• `CL-13-SN-00421-05` (ID New)\n"
                "• o el `DU 00000131`"
            )

        estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))
        estado_legible = estados_dict.get(s.estado, s.estado)

        msg = "🧾 *Detalle de proyecto*\n\n"
        msg += f"• DU: `{s.du or '—'}`\n"
        msg += f"• ID Claro: `{s.id_claro or '—'}`\n"
        msg += f"• ID New: `{getattr(s, 'id_new', None) or '—'}`\n"
        msg += f"• Estado: {estado_legible}\n"

        tu_mmoo = _mmoo_share_for_user(s, usuario)
        msg += f"• Tu MMOO: {_fmt_clp(tu_mmoo)}\n"

        if s.monto_mmoo is not None:
            n = int(getattr(s, "n_tecs", None) or 1)
            msg += f"• MMOO total: {_fmt_clp(s.monto_mmoo)} (técnicos: {n})\n"

        if s.detalle_tarea:
            det = s.detalle_tarea.strip()
            msg += f"\n🛠️ Tarea:\n{det}\n"
        msg += "\nSi quieres, dime: `mapa de mis proyectos` o `total monto proyectos`."
        return msg

    if {"mapa", "maps", "google", "ubicacion", "ubicación"} & tokens:
        bucket_keys = _pick_project_buckets(tokens)
        estados = set().union(*(_PROJ_BUCKETS[k] for k in bucket_keys if k in _PROJ_BUCKETS))
        qs = base.filter(estado__in=list(estados)).order_by("-fecha_creacion")[:20]
        servicios = list(qs)

        if not servicios:
            extra = f" en {mes_label}" if mes_label else ""
            return f"No encontré proyectos para mapear{extra}."

        ruta, links = _build_maps_link_for_services(servicios)
        msg = "🗺️ *Mapa de tus proyectos*\n\n"
        if mes_label:
            msg += f"Filtro mes: *{mes_label}*\n\n"

        if ruta:
            msg += f"Ruta sugerida (usa tu ubicación actual):\n{ruta}\n\n"
        else:
            msg += "No pude armar una ruta única (puede faltar coordenadas), pero aquí van links individuales:\n\n"

        if links:
            msg += "Links (algunos sitios pueden no tener coordenadas cargadas):\n"
            msg += "\n".join(links[:12])
            if len(links) > 12:
                msg += f"\n… y {len(links)-12} más."
        else:
            msg += "No encontré coordenadas en SitioMovil para tus proyectos."

        msg += "\n\nTip: si quieres solo `asignados este mes` escribe: `mapa proyectos asignados este mes`."
        return msg

    if {"monto", "montos", "total", "suma", "sumo", "cuanto", "cuánto"} & tokens:
        bucket_keys = _pick_project_buckets(tokens)
        estados = set().union(*(_PROJ_BUCKETS[k] for k in bucket_keys if k in _PROJ_BUCKETS))
        qs = base.filter(estado__in=list(estados)).order_by("-fecha_creacion")

        servicios = list(qs)
        total_proy = len(servicios)

        total_mmoo = Decimal("0")
        for s in servicios:
            total_mmoo += _mmoo_share_for_user(s, usuario)

        extra = f" (mes {mes_label})" if mes_label else ""
        labels = ", ".join(_PROJ_BUCKET_LABEL[k] for k in bucket_keys)

        return (
            f"💰 *Total proyectos / montos*{extra}\n\n"
            f"Grupos: *{labels}*\n"
            f"• Proyectos: *{total_proy}*\n"
            f"• Tu MMOO total: *{_fmt_clp(total_mmoo)}*\n\n"
            "Si quieres el detalle de uno, dime: `monto proyecto 13_913` (o pega el DU / ID NEW)."
        )

    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))
    bucket_keys = ["asignados", "en_ejecucion", "revision_supervisor", "aprobado_supervisor", "finalizados", "rechazados"]

    msg = "📌 *Resumen de tus proyectos*\n"
    if mes_label:
        msg += f"Filtro mes: *{mes_label}*\n"
    msg += "\n"

    total_general = 0
    total_mmoo_general = Decimal("0")

    for key in bucket_keys:
        estados = _PROJ_BUCKETS[key]
        qs = base.filter(estado__in=list(estados)).order_by("-fecha_creacion")
        servicios = list(qs)

        c = len(servicios)
        m = Decimal("0")
        for s in servicios:
            m += _mmoo_share_for_user(s, usuario)

        total_general += c
        total_mmoo_general += m

        msg += f"• {_PROJ_BUCKET_LABEL[key]}: *{c}* (tu MMOO {_fmt_clp(m)})\n"

    msg += f"\n✅ Total: *{total_general}* proyectos (tu MMOO {_fmt_clp(total_mmoo_general)})\n\n"

    ejemplos = list(base.order_by("-fecha_creacion")[:8])
    if ejemplos:
        msg += "Últimos proyectos (recientes):\n"
        for s in ejemplos:
            du = s.du or "—"
            idc = s.id_claro or (getattr(s, "id_new", None) or "—")
            est = estados_dict.get(s.estado, s.estado)
            det = (s.detalle_tarea or "").strip()
            if len(det) > 60:
                det = det[:57] + "…"
            msg += f"• DU {du} / {idc} – {est} – {det}\n"

    msg += (
        "\n📲 Pídemelo así (PRO):\n"
        "• `proyectos aprobados por el supervisor`\n"
        "• `proyectos asignados este mes`\n"
        "• `proyectos en ejecución`\n"
        "• `proyectos finalizados mes pasado`\n"
        "• `total monto proyectos (asignados + ejecución + revisión)`\n"
        "• `monto proyecto 13_913`\n"
        "• `mapa de mis proyectos asignados`"
    )
    return msg


_ASIGNACION_ESTADOS_ACTIVOS = ["asignado", "en_progreso", "en_revision_supervisor"]


def _svc_assignment_date_field() -> str:
    candidatos = ["fecha_asignacion", "fecha_asignado", "fecha_creacion", "created_at", "creado"]
    nombres = {f.name for f in ServicioCotizado._meta.get_fields() if hasattr(f, "name")}
    for c in candidatos:
        if c in nombres:
            return c
    return "fecha_creacion"


def _filter_by_day(qs, field_name: str, day):
    try:
        return qs.filter(**{f"{field_name}__date": day})
    except FieldError:
        return qs.filter(**{field_name: day})


def _fmt_servicio_asignacion_line(s: ServicioCotizado, estados_dict: dict) -> str:
    du = s.du or "—"
    idref = s.id_claro or (getattr(s, "id_new", None) or "—")
    est = estados_dict.get(s.estado, s.estado)
    det = (s.detalle_tarea or "").strip()
    if len(det) > 90:
        det = det[:87] + "…"
    return f"• DU `{du}` / `{idref}` – {est} – {det}"


def _handler_asignacion(usuario: CustomUser, texto_usuario: str) -> str:
    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()

    es_hoy = bool({"hoy"} & tokens) or ("de hoy" in _normalize(texto_usuario)) or ("para hoy" in _normalize(texto_usuario))

    base = ServicioCotizado.objects.filter(trabajadores_asignados=usuario)
    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

    activos = base.filter(estado__in=_ASIGNACION_ESTADOS_ACTIVOS).order_by("fecha_creacion")

    date_field = _svc_assignment_date_field()

    if es_hoy:
        asignados_hoy = _filter_by_day(activos, date_field, hoy).order_by("fecha_creacion")

        if asignados_hoy.exists():
            lista = list(asignados_hoy[:15])
            msg = f"📌 *Tu asignación de hoy* ({hoy.strftime('%d-%m-%Y')})\n"
            msg += f"Campo usado: *{date_field}*\n\n"
            msg += f"Total: *{asignados_hoy.count()}*\n\n"
            for s in lista:
                msg += _fmt_servicio_asignacion_line(s, estados_dict) + "\n"

            if asignados_hoy.count() > len(lista):
                msg += f"\nMostrando {len(lista)} de {asignados_hoy.count()}."

            return msg.strip()

        pendientes = activos.order_by("fecha_creacion")
        total_pend = pendientes.count()

        msg = f"✅ No tienes asignación *creada/asignada hoy* ({hoy.strftime('%d-%m-%Y')}).\n"
        msg += f"(Campo usado para “hoy”: *{date_field}*)\n\n"

        if total_pend == 0:
            msg += "También estás sin pendientes activos. 👌"
            return msg.strip()

        msg += f"Pero tienes *{total_pend}* pendiente(s) activo(s):\n\n"
        for s in list(pendientes[:10]):
            msg += _fmt_servicio_asignacion_line(s, estados_dict) + "\n"

        if total_pend > 10:
            msg += f"\n… y {total_pend - 10} más."

        msg += "\n\nTip: escribe `asignación` para ver el resumen completo."
        return msg.strip()

    total = activos.count()
    if total == 0:
        return "No tienes asignaciones/pendientes activos en este momento. ✅"

    counts = {}
    for s in activos.only("estado"):
        k = s.estado or "—"
        counts[k] = counts.get(k, 0) + 1

    msg = "🧭 *Tu asignación (pendientes/activos)*\n\n"
    msg += f"Total activos: *{total}*\n\n"
    msg += "📌 *Por estado:*\n"
    orden = ["asignado", "en_progreso", "en_revision_supervisor"]
    usados = set()
    for k in orden:
        if k in counts:
            usados.add(k)
            msg += f"• {estados_dict.get(k, k)}: {counts[k]}\n"
    for k, c in sorted(counts.items(), key=lambda x: x[0]):
        if k in usados:
            continue
        msg += f"• {estados_dict.get(k, k)}: {c}\n"

    msg += "\n🧾 *Pendientes más próximos:*\n"
    for s in list(activos[:12]):
        msg += _fmt_servicio_asignacion_line(s, estados_dict) + "\n"

    msg += "\n📲 Puedes pedir:\n• `asignación de hoy`\n• `asignación`"
    return msg.strip()


def _handler_mis_proyectos_pendientes(usuario: CustomUser) -> str:
    estados = ["asignado", "en_progreso", "en_revision_supervisor"]

    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado__in=estados,
    ).order_by("fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos/servicios pendientes en este momento. ✅"

    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

    msg = f"Tienes *{total}* proyectos pendientes.\n\n"
    msg += "Te muestro los últimos asignados:\n"

    for s in qs[:10]:
        du = s.du or "—"
        id_ref = s.id_claro or (getattr(s, "id_new", None) or "Sin ID")
        detalle = (s.detalle_tarea or "").strip()
        if len(detalle) > 80:
            detalle = detalle[:77] + "…"

        est = estados_dict.get(s.estado, s.estado)
        msg += f"• DU `{du}` / `{id_ref}` – {est}: {detalle}\n"

    return msg


def _handler_mis_proyectos_rechazados(usuario: CustomUser) -> str:
    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado="rechazado_supervisor",
    ).order_by("-fecha_aprobacion_supervisor", "-fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos rechazados actualmente. ✅"

    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

    msg = f"Tienes *{total}* proyectos rechazados por supervisor.\n\n"
    for s in qs[:10]:
        du = s.du or "—"
        id_ref = s.id_claro or (getattr(s, "id_new", None) or "Sin ID")
        motivo = (s.motivo_rechazo or "").strip()
        if len(motivo) > 80:
            motivo = motivo[:77] + "…"

        est = estados_dict.get(s.estado, s.estado)
        msg += f"• DU `{du}` / `{id_ref}` – {est} – Motivo: {motivo or 'Sin detalle'}\n"

    return msg


def _handler_mis_rendiciones_pendientes(usuario: CustomUser, texto_usuario: str) -> str:
    tokens = set(_tokenize(texto_usuario))

    if {"hacer", "crear", "nueva", "nuevo", "declarar", "ingresar"} & tokens:
        return (
            "Por ahora todavía *no puedo crear rendiciones nuevas* desde el bot 🤖.\n\n"
            "Para declarar un gasto debes hacerlo en la sección de *Mis Rendiciones* "
            "de la app web.\n"
            "A futuro iremos habilitando este flujo por aquí para que sea más rápido."
        )

    generic = {"gasto", "gastos", "rendicion", "rendiciones"}
    if tokens and tokens <= generic:
        return (
            "Para ayudarte mejor con tus rendiciones dime qué necesitas exactamente:\n\n"
            "• Si quieres ver las *pendientes*: escribe `rendiciones pendientes`\n"
            "• Si quieres las *aprobadas* y por quién: `rendiciones aprobadas`\n"
            "• Si quieres las *rechazadas*: `rendiciones rechazadas`\n"
            "• Si son solo de *hoy*: agrega `de hoy`, por ejemplo `rendiciones pendientes de hoy`.\n"
            "• Si quieres ver todas tus rendiciones de un día específico: `rendiciones de hoy` o `rendiciones de ayer`."
        )

    estados = []
    titulo = ""
    extra_label = ""

    if "rechazadas" in tokens or "rechazado" in tokens or "rechazada" in tokens:
        estados = ["rechazado_supervisor", "rechazado_pm", "rechazado_finanzas"]
        titulo = "Rendiciones rechazadas"
        extra_label = "rechazadas"
    elif "aprobadas" in tokens or "aprobado" in tokens or "aprobada" in tokens:
        estados = ["aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"]
        titulo = "Rendiciones aprobadas"
        extra_label = "aprobadas"
    else:
        estados = [
            "pendiente_abono_usuario",
            "pendiente_supervisor",
            "pendiente_pm",
            "pendiente_finanzas",
        ]
        titulo = "Rendiciones pendientes en el flujo de aprobación"
        extra_label = "pendientes"

    hoy = timezone.localdate()
    fecha = None
    if "hoy" in tokens:
        fecha = hoy
    elif "ayer" in tokens:
        fecha = hoy - timedelta(days=1)

    filtros = {"usuario": usuario, "status__in": estados}
    if fecha is not None:
        filtros["fecha"] = fecha

    qs = CartolaMovimiento.objects.filter(**filtros)

    total = qs.count()
    if total == 0:
        if fecha:
            return (
                f"No tienes rendiciones {extra_label} para el día "
                f"{fecha.strftime('%d-%m-%Y')}."
            )
        return f"No tienes rendiciones {extra_label} en este momento. ✅"

    suma_cargos = qs.aggregate(total=Sum("cargos"))["total"] or 0

    msg = f"🧾 *{titulo}*\n"
    if fecha:
        msg += f"Del día {fecha.strftime('%d-%m-%Y')}.\n"

    msg += (
        f"\nTienes *{total}* rendiciones en este grupo.\n"
        f"Monto total (cargos): ${suma_cargos:,.0f}\n\n"
        "Ejemplos recientes:\n"
    )

    estados_dict = dict(getattr(CartolaMovimiento, "ESTADOS", []))

    for m in qs.order_by("-fecha")[:5]:
        proyecto = m.proyecto.nombre if m.proyecto else "Sin proyecto"
        tipo = m.tipo.nombre if m.tipo else "Sin tipo"
        estado_legible = estados_dict.get(m.status, m.status)
        msg += (
            f"• {m.fecha.strftime('%d-%m-%Y')} – {proyecto} – {tipo} – "
            f"${m.cargos:,.0f} – {estado_legible}\n"
        )

    msg += (
        "\nSi quieres otro filtro, puedes decirme por ejemplo:\n"
        "• `rendiciones aprobadas`\n"
        "• `rendiciones rechazadas`\n"
        "• `rendiciones pendientes de hoy`"
    )

    return msg


def _get_basura_info_from_settings() -> dict:
    """
    Lee configuración de basura desde settings.
    Nuevo formato:
      - BOT_GZ_TEXTO_BASURA
      - BOT_GZ_URL_BASURA (Google Maps)
    Mantiene compatibilidad con claves antiguas (dirección en texto).
    """
    texto = getattr(settings, "BOT_GZ_TEXTO_BASURA", None)
    texto = str(texto).strip() if texto else ""

    url = getattr(settings, "BOT_GZ_URL_BASURA", None)
    url = str(url).strip() if url else ""

    direccion = _get_direccion_retiro_basura_from_settings() or ""
    direccion = str(direccion).strip() if direccion else ""

    if direccion.lower().startswith("http") and not url:
        url = direccion
        direccion = ""

    return {"texto": texto, "url": url, "direccion": direccion}


def _get_direccion_retiro_basura_from_settings() -> Optional[str]:
    """
    Lee la dirección de retiro de basura desde settings.
    Soporta varios nombres por compatibilidad.
    Acepta string directo o dict por sucursal/ciudad (opcional).
    """
    candidates = [
        "GZ_DIRECCION_RETIRO_BASURA",
        "DIRECCION_RETIRO_BASURA",
        "RETIRO_BASURA_DIRECCION",
        "DIRECCION_BASURA",
        "BASURA_DIRECCION",
    ]

    for key in candidates:
        val = getattr(settings, key, None)
        if not val:
            continue

        if isinstance(val, dict):
            default = val.get("default") or val.get("DEFAULT")
            if default:
                txt = str(default).strip()
                if txt:
                    return txt
            for _k, _v in val.items():
                if _v:
                    txt = str(_v).strip()
                    if txt:
                        return txt
            continue

        txt = str(val).strip()
        if txt:
            return txt

    return None


def _handler_direccion_basura(usuario: CustomUser, texto_usuario: str = "") -> str:
    """
    Responde info de disposición/retiro de basura usando settings:
      - BOT_GZ_TEXTO_BASURA
      - BOT_GZ_URL_BASURA
    """
    info = _get_basura_info_from_settings() or {}
    norm = _normalize(texto_usuario or "")

    titulo = "🗑️ *Retiro de basura*"
    if (("donde" in norm and ("botar" in norm or "tira" in norm or "tirar" in norm)) or ("direccion" in norm)):
        titulo = "🗑️ *¿Dónde botar la basura?*"

    texto = (info.get("texto") or "").strip()
    direccion = (info.get("direccion") or "").strip()
    url = (info.get("url") or "").strip()

    if texto or direccion or url:
        msg = f"{titulo}\n\n"
        if texto:
            msg += f"📝 {texto}\n"
        if direccion:
            msg += f"📍 Dirección: {direccion}\n"
        if url:
            msg += f"🗺️ Mapa: {url}\n"
        return msg.strip()

    return _responder_direccion_basura()


# ===================== Router principal de intents =====================

def run_intent(
    intent: Optional[BotIntent],
    texto_usuario: str,
    sesion: BotSession,
    usuario: Optional[CustomUser],
    inbound_log: BotMessageLog,
) -> str:
    chat_id = sesion.chat_id

    if usuario is None:
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
        return _respuesta_sin_usuario(chat_id)

    # ============================
    # ✅ Wizard Rendición (texto)
    # ============================
    try:
        wiz_state = _rend_wiz_get(chat_id)
    except Exception:
        wiz_state = None

    norm = _normalize(texto_usuario or "")
    triggers_start = {
        "nueva rendicion",
        "nueva rendicion gasto",
        "nueva rendicion de gasto",
        "crear rendicion",
        "crear rendicion gasto",
        "nueva rendicion de gastos",
        "nueva rendicion gastos",
    }

    if usuario is not None and (wiz_state or norm in triggers_start):
        inbound_log.status = "ok"
        inbound_log.marcar_para_entrenamiento = False
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        if norm in triggers_start and not wiz_state:
            return _rendicion_wizard_start(chat_id, usuario)

        return _rendicion_wizard_handle_message(
            chat_id=chat_id,
            usuario=usuario,
            message={"text": texto_usuario or ""},
        )

    if not intent:
        tokens = set(_tokenize(texto_usuario))

        # ✅ Basura (fallback directo por tokens)
        if {"basura", "residuos", "desechos"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_direccion_basura(usuario, texto_usuario)

        if ("supervisor" in tokens) and ({"aprobado", "aprobados", "aprobada", "aprobadas", "aprobacion", "aprobación"} & tokens):
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_mis_proyectos(usuario, texto_usuario)

        if _es_saludo(texto_usuario):
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            nombre = usuario.first_name or usuario.get_full_name() or ""
            nombre = nombre.strip()
            saludo_nombre = f"{nombre}, " if nombre else ""

            return (
                f"👋 Hola {saludo_nombre}soy el bot de *GZ Services*.\n\n"
                "Puedo ayudarte con:\n"
                "🧾 *Liquidaciones*\n"
                "• `mis últimas 3 liquidaciones`\n"
                "• `liquidación de noviembre 2025` / `liquidación 11/2025`\n\n"
                "📄 *Contrato de trabajo*\n"
                "• `mi contrato vigente`\n"
                "• `mi contrato y sus extensiones`\n"
                "• `mis anexos`\n\n"
                "🧭 *Asignación (pendientes/activos)*\n"
                "• `asignación` / `asignación de hoy`\n\n"
                "📌 *Proyectos / servicios*\n"
                "• `mis proyectos` (resumen)\n"
                "• `proyectos aprobados por supervisor`\n"
                "• `proyectos en ejecución` / `proyectos finalizados`\n"
                "• `total monto proyectos`\n"
                "• `monto proyecto 13_512` (o pega DU / ID NEW)\n"
                "• `mapa de mis proyectos`\n\n"
                "🧾 *Rendiciones de gastos*\n"
                "• `rendiciones pendientes` / `rendiciones aprobadas` / `rendiciones rechazadas`\n"
                "• `rendiciones pendientes de hoy`\n\n"
                "📊 *Producción*\n"
                "• `mi producción hasta hoy`\n"
                "• `mi producción de este mes`\n"
                "• `mi producción 2025-08-01 a 2025-08-31`\n\n"
                "📡 *Info de sitios*\n"
                "• Envía un ID: `13_094` o `CL-13-00421-05` o `CL-13-SN-00421-05`\n\n"
                "✅ Escribe frases cortas (yo te guío)."
            )

        if (
            sesion.ultimo_intent
            and sesion.ultimo_intent.slug
            in ["mis_rendiciones_pendientes", "ayuda_rendicion_gastos"]
        ):
            if {"rechazadas", "rechazada", "aprobadas", "aprobada", "pendientes", "hoy", "ayer"} & tokens:
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

        txt_up = (texto_usuario or "").strip().upper()
        site_hit = (
            re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
            or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)
            or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)
        )
        if site_hit:
            if (
                (sesion.ultimo_intent and sesion.ultimo_intent.slug == "info_sitio_id_claro")
                or ("sitio" in tokens or "site" in tokens)
                or _normalize(texto_usuario) == _normalize(site_hit.group(0))
            ):
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_info_sitio_id_claro(texto_usuario)

        if sesion.ultimo_intent and sesion.ultimo_intent.slug in ["mis_proyectos_pendientes", "mis_proyectos_rechazados"]:
            if (
                {"estado", "estados", "status", "situacion", "situación", "ejecucion", "ejecución", "progreso",
                 "revision", "revisión", "supervisor", "finalizados", "finalizado",
                 "rechazados", "rechazado", "asignados", "asignado", "monto", "total", "suma"} & tokens
            ):
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

                qs_all = (
                    ServicioCotizado.objects
                    .filter(trabajadores_asignados=usuario)
                    .annotate(n_tecs=Count("trabajadores_asignados", distinct=True))
                    .order_by("-fecha_creacion")
                )

                total = qs_all.count()
                if total == 0:
                    return "No tienes proyectos/servicios asignados actualmente. ✅"

                total_mmoo = Decimal("0")
                for s in list(qs_all):
                    total_mmoo += _mmoo_share_for_user(s, usuario)

                counts = {}
                for s in qs_all.only("estado"):
                    k = s.estado or "—"
                    counts[k] = counts.get(k, 0) + 1

                estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

                msg = "🧭 *Resumen de tus proyectos*\n\n"
                msg += f"• Total: *{total}*\n"
                msg += f"• Tu MMOO total: *{_fmt_clp(total_mmoo)}*\n\n"
                msg += "📌 *Por estado:*\n"
                orden_preferido = [
                    "asignado",
                    "en_progreso",
                    "en_revision_supervisor",
                    "aprobado_supervisor",
                    "rechazado_supervisor",
                    "informe_subido",
                    "finalizado_trabajador",
                    "finalizado",
                ]
                usados = set()
                for key in orden_preferido:
                    if key in counts:
                        usados.add(key)
                        msg += f"• {estados_dict.get(key, key)}: {counts[key]}\n"
                for key, c in sorted(counts.items(), key=lambda x: x[0]):
                    if key in usados:
                        continue
                    msg += f"• {estados_dict.get(key, key)}: {c}\n"

                msg += "\n🧾 *Últimos asignados/actualizados:*\n"
                for s in qs_all[:8]:
                    du = s.du or "—"
                    id_claro = s.id_claro or "Sin ID Claro"
                    detalle = (s.detalle_tarea or "").strip()
                    if len(detalle) > 60:
                        detalle = detalle[:57] + "…"
                    est = estados_dict.get(s.estado, s.estado)
                    msg += f"• DU `{du}` / `{id_claro}` – {est}: {detalle}\n"

                msg += (
                    "\nSi quieres algo más específico, dime por ejemplo:\n"
                    "• `proyectos asignados` / `proyectos en progreso` / `proyectos finalizados`\n"
                    "• `monto total de mis proyectos`\n"
                )
                return msg

        if {"asignacion", "asignaciones", "asignado", "asignados"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_asignacion(usuario, texto_usuario)

        if {"proyectos", "proyecto", "servicios", "servicio"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_mis_proyectos(usuario, texto_usuario)

        if sesion.ultimo_intent and sesion.ultimo_intent.slug in ["mi_produccion_hasta_hoy"]:
            if (
                {"mes", "anterior", "pasado", "este", "actual", "produccion", "producción", "hoy", "fecha"} & tokens
                or any(t in _MESES for t in tokens)
            ):
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_mi_produccion(usuario, texto_usuario)

        if {"produccion", "producción"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_mi_produccion(usuario, texto_usuario)

        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        return (
            "Por ahora todavía estoy aprendiendo y no pude entender bien tu mensaje 🤖.\n\n"
            "Puedes pedirme, por ejemplo:\n"
            "• ver mi liquidación de sueldo\n"
            "• consultar mi contrato de trabajo\n"
            "• ver mis proyectos pendientes\n"
            "• ver mis rendiciones de gastos pendientes por aprobación\n\n"
            "Intenta usar frases cortas y directas, y yo te voy guiando."
        )

    inbound_log.status = "ok"
    inbound_log.marcar_para_entrenamiento = intent.requiere_revision_humana
    inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

    slug = intent.slug

    if slug == "cronograma_produccion_corte":
        return _handler_cronograma_produccion(usuario)

    if slug == "mis_liquidaciones":
        return _handler_mis_liquidaciones(usuario, texto_usuario)

    if slug == "mi_contrato_vigente":
        return _handler_mi_contrato(usuario, texto_usuario)

    if slug == "mi_produccion_hasta_hoy":
        return _handler_mi_produccion(usuario, texto_usuario)

    if slug == "info_sitio_id_claro":
        return _handler_info_sitio_id_claro(texto_usuario)

    if slug == "mis_proyectos_pendientes":
        return _handler_mis_proyectos(usuario, texto_usuario)

    if slug == "mis_proyectos_rechazados":
        return _handler_mis_proyectos_rechazados(usuario)

    if slug == "ayuda_rendicion_gastos":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "mis_rendiciones_pendientes":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "direccion_basura":
        return _handler_direccion_basura(usuario, texto_usuario)

    if slug == "mi_asignacion":
        return _handler_asignacion(usuario, texto_usuario)

    return (
        f"Recibí tu consulta y la reconocí como '{intent.nombre}', "
        "pero esta funcionalidad aún se está terminando de implementar en el bot.\n\n"
        "Mientras tanto, puedes revisar esa información directamente en la app web."
    )


# ===================== Entry point: manejar update de Telegram =====================

def handle_telegram_update(update: dict) -> None:
    """
    Punto de entrada para el webhook de Telegram.
    ✅ Soporta:
    - message / edited_message (texto y/o caption)
    - callback_query (inline_keyboard)
    - wizard de rendición (incluye comprobante como PDF/foto aunque venga SIN texto)

    ✅ NUEVO:
    - Vinculación por /start <token> generado en la web (activar_telegram).
    """
    callback = update.get("callback_query")
    if callback:
        msg_obj = callback.get("message") or {}
        chat = msg_obj.get("chat") or {}
        from_user = callback.get("from") or {}
        text = (callback.get("data") or "").strip()

        cb_id = callback.get("id")
        token = _get_bot_token()
        if token and cb_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id},
                    timeout=5,
                )
            except Exception:
                logger.exception("No se pudo hacer answerCallbackQuery (callback_id=%s)", cb_id)

        message = dict(msg_obj)
        message["text"] = text

    else:
        message = update.get("message") or update.get("edited_message")
        if not message:
            logger.info("Update de Telegram sin 'message' ni 'callback_query': %s", update)
            return

        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        text = ((message.get("text") or message.get("caption") or "")).strip()

    chat_id = str(chat.get("id") or "")
    if not chat_id:
        logger.info("Update de Telegram sin chat_id: %s", update)
        return

    # =========================
    # ✅ NUEVO: /start <token> => vincular usuario
    # =========================
    if text and text.startswith("/start"):
        parts = text.split(maxsplit=1)
        start_token = parts[1].strip() if len(parts) > 1 else ""

        if start_token:
            cache_key = f"tg_link:{start_token}"
            user_id = cache.get(cache_key)

            if user_id:
                u = CustomUser.objects.filter(id=user_id).first()
                if u:
                    # Vincular
                    u.telegram_chat_id = chat_id
                    u.telegram_activo = True
                    u.save(update_fields=["telegram_chat_id", "telegram_activo"])

                    # Consumir token (one-time)
                    cache.delete(cache_key)

                    # Crear/actualizar sesión y amarrarla al usuario
                    sesion, _ = get_or_create_session(chat_id, from_user)
                    if sesion.usuario_id != u.id:
                        sesion.usuario = u
                        sesion.save(update_fields=["usuario"])

                    send_telegram_message(
                        chat_id,
                        "✅ Listo, tu cuenta quedó vinculada con Telegram.\n\n"
                        "Ahora puedes pedirme: `mis liquidaciones`, `mi contrato`, `asignación`, `producción`, etc.",
                        sesion=sesion,
                        usuario=u,
                        intent=None,
                        meta={"linked_by": "start_token"},
                        marcar_para_entrenamiento=False,
                        reply_markup=_ik_main_menu(),
                    )
                    return

            # Token inválido / expirado
            sesion, usuario = get_or_create_session(chat_id, from_user)
            send_telegram_message(
                chat_id,
                "⚠️ Ese link de activación no es válido o ya expiró.\n\n"
                "Vuelve a la web → *Activar Telegram* y genera un link nuevo.",
                sesion=sesion,
                usuario=usuario,
                intent=None,
                meta={"link_error": "invalid_or_expired"},
                marcar_para_entrenamiento=False,
            )
            return

    # =========================
    # Flujo normal (igual que tuyo)
    # =========================
    sesion, usuario = get_or_create_session(chat_id, from_user)
    sesion.ultima_interaccion = timezone.now()
    sesion.save(update_fields=["ultima_interaccion"])

    has_file = bool(message.get("document") or message.get("photo"))
    text_for_log = text if text else ("[archivo]" if has_file else "")

    wizard_state = _rend_wiz_get(chat_id)
    if not text_for_log and not wizard_state:
        logger.info("Mensaje sin texto/caption y sin wizard activo (chat_id=%s)", chat_id)
        return

    inbound_log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="in",
        texto=text_for_log,
        status="ok",
        meta={"update_id": update.get("update_id"), "callback": bool(callback)},
    )

    # WIZARD rendición
    norm = _normalize(text or "")
    triggers_start = {
        "nueva rendicion",
        "nueva rendicion gasto",
        "nueva rendicion de gasto",
        "crear rendicion",
        "crear rendicion gasto",
        "nueva rendicion de gastos",
        "nueva rendicion gastos",
        "nueva rendicion de gastos",
    }

    if usuario is not None and (wizard_state or norm in triggers_start):
        if norm in triggers_start and not wizard_state:
            reply_text = _rendicion_wizard_start(chat_id, usuario)
        else:
            reply_text = _rendicion_wizard_handle_message(
                chat_id=chat_id,
                usuario=usuario,
                message=message,
            )

        inbound_log.status = "ok"
        inbound_log.marcar_para_entrenamiento = False
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        send_telegram_message(
            chat_id,
            reply_text,
            sesion=sesion,
            usuario=usuario,
            intent=None,
            meta={"from_update_id": update.get("update_id"), "wizard": True},
            marcar_para_entrenamiento=False,
        )
        return

    intent, confianza = detect_intent_from_text(text, scope=sesion.contexto) if text else (None, 0.0)
    if intent:
        sesion.ultimo_intent = intent
        sesion.save(update_fields=["ultimo_intent"])

    inbound_log.intent_detectado = intent
    inbound_log.confianza = confianza
    inbound_log.save(update_fields=["intent_detectado", "confianza"])

    reply_text = run_intent(intent, text, sesion, usuario, inbound_log)

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


# ===================== WIZARD: Crear rendición (gasto) =====================

_REND_WIZ_TTL = 60 * 30  # 30 min


def _rend_wiz_key(chat_id: str) -> str:
    return f"gzbot:rendicion:{str(chat_id)}"


def _rend_wiz_get(chat_id: str) -> Optional[dict]:
    return cache.get(_rend_wiz_key(chat_id))


def _rend_wiz_set(chat_id: str, data: dict) -> None:
    cache.set(_rend_wiz_key(chat_id), data, timeout=_REND_WIZ_TTL)


def _rend_wiz_clear(chat_id: str) -> None:
    cache.delete(_rend_wiz_key(chat_id))


def _parse_clp_to_decimal(raw: str) -> Optional[Decimal]:
    """
    Acepta: 320240 | 320.240 | 320,240 | $320.240 | 320.240,50
    Devuelve Decimal con 2 decimales.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("$", "").replace(" ", "")

    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        if "," in s:
            parts = s.split(",")
            if len(parts[-1]) in (1, 2):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            if s.count(".") >= 2:
                s = s.replace(".", "")
            elif s.count(".") == 1:
                a, b = s.split(".")
                if len(b) == 3:
                    s = a + b

    try:
        v = Decimal(s).quantize(Decimal("0.01"))
        if v < 0:
            return None
        return v
    except Exception:
        return None


def _wiz_fmt_choices(items: list[tuple[int, str]], title: str) -> str:
    import html as _html
    msg = f"<b>{_html.escape(title)}</b>\n"
    for i, (_id, name) in enumerate(items, start=1):
        msg += f"{i}) {_html.escape(name or '—')}\n"
    return msg.strip()


def _get_default_projects(limit: int = 10) -> list[tuple[int, str]]:
    qs = Proyecto.objects.order_by("nombre")[:limit]
    return [(p.id, p.nombre) for p in qs]


def _get_default_tipos(limit: int = 50) -> list[tuple[int, str]]:
    qs = TipoGasto._base_manager.order_by("nombre")[:limit]
    return [(t.id, t.nombre) for t in qs]


def _get_recent_projects_for_user(usuario: CustomUser, limit: int = 6) -> list[tuple[int, str]]:
    qs = (
        CartolaMovimiento.objects
        .filter(usuario=usuario, proyecto__isnull=False)
        .select_related("proyecto")
        .order_by("-fecha")
        .only("proyecto_id", "proyecto__nombre")
    )
    seen = set()
    out = []
    for m in qs[:80]:
        pid = m.proyecto_id
        if pid and pid not in seen:
            seen.add(pid)
            out.append((pid, m.proyecto.nombre))
            if len(out) >= limit:
                break
    return out


def _search_projects(query: str, limit: int = 8) -> list[tuple[int, str]]:
    q = (query or "").strip()
    if not q:
        return []
    qs = Proyecto.objects.filter(nombre__icontains=q).order_by("nombre")[:limit]
    return [(p.id, p.nombre) for p in qs]


def _get_recent_tipos_for_user(usuario: CustomUser, limit: int = 6) -> list[tuple[int, str]]:
    qs = (
        CartolaMovimiento.objects
        .filter(usuario=usuario, tipo__isnull=False)
        .select_related("tipo")
        .order_by("-fecha")
        .only("tipo_id", "tipo__nombre")
    )
    seen = set()
    out = []
    for m in qs[:120]:
        tid = m.tipo_id
        if tid and tid not in seen:
            seen.add(tid)
            out.append((tid, m.tipo.nombre))
            if len(out) >= limit:
                break
    return out

def _merge_unique(primary: list[tuple[int, str]], fallback: list[tuple[int, str]], limit: int = 10) -> list[tuple[int, str]]:
    seen = set()
    out: list[tuple[int, str]] = []
    for _id, name in (primary or []):
        if _id and _id not in seen:
            seen.add(_id)
            out.append((_id, name))
            if len(out) >= limit:
                return out
    for _id, name in (fallback or []):
        if _id and _id not in seen:
            seen.add(_id)
            out.append((_id, name))
            if len(out) >= limit:
                return out
    return out


def _search_tipos(query: str, limit: int = 10) -> list[tuple[int, str]]:
    q = (query or "").strip()
    if not q:
        return []
    qs = TipoGasto.objects.filter(nombre__icontains=q).order_by("nombre")[:limit]
    return [(t.id, t.nombre) for t in qs]


TIPO_DOC_MAP = {
    "1": "factura",
    "2": "boleta",
    "3": "otros",
    "factura": "factura",
    "boleta": "boleta",
    "otros": "otros",
}


def _norm_doc_choice(txt: str) -> str | None:
    t = (txt or "").strip().lower()
    return TIPO_DOC_MAP.get(t)


def _clean_num_doc(txt: str) -> str:
    # deja solo dígitos
    t = re.sub(r"\D+", "", (txt or "").strip())
    return t


def _doc_requires_sii(tipo_doc: str) -> bool:
    return tipo_doc in ("factura", "boleta")


def _rendicion_wizard_start(chat_id: str, usuario: CustomUser) -> str:
    """
    Inicia flujo de rendición (gasto):
    proyecto, tipo, tipo_doc, rut, numero_doc, observaciones, numero_transferencia, cargos, comprobante, confirmación.
    """
    recents = _get_recent_projects_for_user(usuario, limit=6)
    defaults = _get_default_projects(limit=8)

    state = {
        "step": "proyecto",
        "data": {
            "proyecto_id": None,
            "tipo_id": None,
            "tipo_doc": None,
            "numero_doc": None,
            "rut_factura": None,
            "observaciones": "",
            "cargos": None,
            "comprobante_file_id": None,
            "comprobante_filename": None,
        },
        "choices": {
            "proyectos": recents or defaults or [],
            "tipos": [],
        }
    }
    _rend_wiz_set(chat_id, state)

    msg = (
        "🧾 <b>Nueva rendición (gasto)</b>\n\n"
        "Paso 1/9: <b>Proyecto</b>\n"
        "Responde con el <b>número</b> (recomendado) o escribe parte del nombre para buscar.\n\n"
    )

    if state["choices"]["proyectos"]:
        label = "Proyectos recientes:" if recents else "Proyectos disponibles:"
        msg += _wiz_fmt_choices(state["choices"]["proyectos"], label)
        msg += "\n\nTambién puedes escribir otro texto para buscar."
    else:
        msg += "Escribe parte del nombre del proyecto para buscar."

    msg += "\n\nEscribe <b>cancelar</b> para salir."
    return msg


def _tg_extract_file_from_message(message: dict) -> Optional[dict]:
    """
    Extrae un archivo desde un message de Telegram:
    - document => file_id + filename
    - photo => file_id (mayor resolución) + filename generado
    Retorna: {"file_id": str, "filename": str}
    """
    doc = message.get("document")
    if doc and doc.get("file_id"):
        return {
            "file_id": doc.get("file_id"),
            "filename": doc.get("file_name") or "comprobante",
        }

    photos = message.get("photo")
    if photos and isinstance(photos, list):
        best = photos[-1]  # usualmente el de mayor resolución
        if best and best.get("file_id"):
            fu = best.get("file_unique_id") or best.get("file_id")
            return {
                "file_id": best.get("file_id"),
                "filename": f"comprobante_{fu}.jpg",
            }

    return None


def _tg_download_file(token: str, file_id: str) -> tuple[str, bytes]:
    """
    Descarga un archivo desde Telegram y retorna (filename, bytes).
    """
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=15
    )
    data = r.json()
    if not (isinstance(data, dict) and data.get("ok") and data.get("result")):
        raise RuntimeError(f"getFile failed: {str(data)[:250]}")
    file_path = data["result"].get("file_path")
    if not file_path:
        raise RuntimeError("getFile ok pero sin file_path")

    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    r2 = requests.get(url, timeout=30)
    r2.raise_for_status()

    filename = file_path.split("/")[-1] or "comprobante"
    return filename, r2.content


def validar_rut_chileno(rut: str) -> bool:
    """Valida DV del RUT chileno (acepta puntos/guión y K)."""
    if not rut:
        return False
    rut = rut.replace(".", "").replace("-", "").strip().upper()
    if len(rut) < 2:
        return False
    cuerpo, dv = rut[:-1], rut[-1]
    if not cuerpo.isdigit():
        return False

    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1

    resto = suma % 11
    dv_esperado = "0" if resto == 0 else "K" if resto == 1 else str(11 - resto)
    return dv == dv_esperado


def verificar_rut_sii(rut: str) -> bool:
    """
    Verifica RUT contra el endpoint clásico del SII.
    Si el SII cambia/bloquea, esto podría fallar. Maneja excepción afuera.
    """
    url = "https://zeus.sii.cl/cgi_rut/CONSULTA.cgi"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.post(url, data={"RUT": rut}, headers=headers, timeout=7)
        txt = resp.text or ""
        return "RUT no válido" not in txt and "RUT INVALIDO" not in txt.upper()
    except Exception:
        # Si hay problema de red, no bloqueamos
        return True


def _rendicion_wizard_handle_message(
    *,
    chat_id: str,
    usuario: CustomUser,
    message: dict,
) -> str:
    """
    Wizard con UX PRO:
    - En Proyecto/Tipo: muestra lista y permite elegir por número.
    - ✅ Incluye: Tipo de documento + Nº documento (comprobante) + Validación SII (RUT)
    - Al final: CONFIRMACIÓN antes de guardar.
    - ✅ FIX: Si editas un campo desde CONFIRM, vuelve al resumen final (no re-pide todo).
    - ✅ FIX: No pide N° transferencia (solo N° comprobante = numero_doc).
    """
    import html as _html
    import re as _re

    state = _rend_wiz_get(chat_id)
    if not state:
        return "No tengo un flujo activo. Escribe: <b>nueva rendición</b>."

    text_in = (message.get("text") or message.get("caption") or "").strip()
    norm = _normalize(text_in)

    if norm == "cancelar":
        _rend_wiz_clear(chat_id)
        return "✅ Listo, cancelé la creación de la rendición."

    step = state.get("step")
    data = state.get("data") or {}
    choices = state.get("choices") or {}
    state.setdefault("choices", choices)

    # =================== Helpers UX ===================
    def _proyectos_sugeridos_msg(extra_top: str = "") -> str:
        opts = choices.get("proyectos") or []
        if not opts:
            opts = _get_default_projects(limit=10)
            state["choices"]["proyectos"] = opts
            _rend_wiz_set(chat_id, state)
        msg = ""
        if extra_top:
            msg += extra_top.strip() + "\n\n"
        msg += _wiz_fmt_choices(opts, "Elige un proyecto:")
        msg += "\n\nTip: también puedes escribir parte del nombre para buscar."
        msg += "\n\nEscribe <b>cancelar</b> para salir."
        return msg

    def _tipos_sugeridos_msg(extra_top: str = "") -> str:
        # ✅ SIEMPRE regenerar lista desde BD (recientes + todos)
        rec = _get_recent_tipos_for_user(usuario, limit=6)
        opts = _merge_unique(rec, _get_default_tipos(limit=50), limit=50)

        state["choices"]["tipos"] = opts
        _rend_wiz_set(chat_id, state)

        msg = ""
        if extra_top:
            msg += extra_top.strip() + "\n\n"
        msg += _wiz_fmt_choices(opts, "Elige un tipo de gasto:")
        msg += "\n\nTip: también puedes escribir parte del nombre para buscar."
        msg += "\n\nEscribe <b>cancelar</b> para salir."
        return msg

    def _tipo_doc_prompt(extra_top: str = "") -> str:
        top = (extra_top.strip() + "\n\n") if extra_top else ""
        return (
            top
            + "📄 Paso 3/9: <b>Tipo de documento</b>\n"
              "Responde con el <b>número</b>:\n\n"
              "1) Factura\n"
              "2) Boleta\n"
              "3) Otros\n\n"
              "Escribe <b>cancelar</b> para salir."
        )

    def _norm_tipo_doc(s: str) -> str | None:
        t = (s or "").strip().lower()
        if t in {"1", "factura"}:
            return "factura"
        if t in {"2", "boleta"}:
            return "boleta"
        if t in {"3", "otros", "otro"}:
            return "otros"
        return None

    def _clean_num_doc_local(s: str) -> str:
        return _re.sub(r"\D+", "", (s or "").strip())

    def _confirm_msg(proyecto_nombre: str, tipo_nombre: str) -> str:
        tipo_doc = (data.get("tipo_doc") or "").strip() or "—"
        rut = (data.get("rut_factura") or "").strip() or "—"
        n_doc = (data.get("numero_doc") or "").strip() or "—"
        obs = _html.escape((data.get("observaciones") or "").strip() or "—")

        monto = data.get("cargos")
        monto_txt = _html.escape(_fmt_clp(monto)) if monto is not None else "—"

        msg = (
            "🧾 <b>Revisa tu rendición</b>\n\n"
            f"• <b>Proyecto:</b> {_html.escape(proyecto_nombre)}\n"
            f"• <b>Tipo gasto:</b> {_html.escape(tipo_nombre)}\n"
            f"• <b>Tipo doc:</b> {_html.escape(tipo_doc)}\n"
            f"• <b>RUT emisor:</b> {_html.escape(rut)}\n"
            f"• <b>N° comprobante:</b> {_html.escape(n_doc)}\n"
            f"• <b>Monto:</b> <b>{monto_txt}</b>\n"
            f"• <b>Observaciones:</b> {obs}\n"
            f"• <b>Comprobante:</b> recibido ✅\n\n"
            "<b>¿Confirmas para enviar?</b>\n"
            "1) ✅ Confirmar y guardar\n"
            "2) ✏️ Cambiar proyecto\n"
            "3) ✏️ Cambiar tipo de gasto\n"
            "4) ✏️ Cambiar tipo de documento\n"
            "5) ✏️ Cambiar RUT emisor\n"
            "6) ✏️ Cambiar N° comprobante\n"
            "7) ✏️ Cambiar monto\n"
            "8) ✏️ Cambiar observaciones\n"
            "9) 📎 Cambiar comprobante\n"
            "10) ❌ Cancelar\n\n"
            "Responde con el <b>número</b>."
        )
        return msg

    def _maybe_back_to_confirm() -> str | None:
        """
        ✅ Si venimos editando desde confirmación, volver al resumen final.
        Si falta algo crítico, redirige al paso faltante manteniendo el flag.
        """
        if not state.get("return_to_confirm"):
            return None

        td = (data.get("tipo_doc") or "").strip()
        if td in {"factura", "boleta"}:
            if not (data.get("rut_factura") or "").strip():
                state["step"] = "rut_factura"
                _rend_wiz_set(chat_id, state)
                return (
                    "Paso 4/9: <b>RUT emisor</b>\n"
                    "Escribe el RUT del emisor (Ej: <code>77.084.679-K</code> o <code>77084679-K</code>).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )
            if not (data.get("numero_doc") or "").strip():
                state["step"] = "numero_doc"
                _rend_wiz_set(chat_id, state)
                return (
                    "Paso 5/9: <b>N° comprobante</b>\n"
                    "Escribe el número del comprobante (solo números).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )

        if data.get("cargos") in (None, "", 0, 0.0):
            state["step"] = "monto"
            _rend_wiz_set(chat_id, state)
            return (
                "Paso 6/9: <b>Monto</b>\n"
                "Escribe el monto (ej: <code>320.240</code> o <code>$320.240</code>).\n\n"
                "Escribe <b>cancelar</b> para salir."
            )

        if not data.get("comprobante_file_id"):
            state["step"] = "comprobante"
            _rend_wiz_set(chat_id, state)
            return (
                "Paso 8/9: <b>Comprobante</b>\n"
                "Ahora envíame el comprobante como <b>PDF</b> o <b>imagen</b> (jpg/png).\n\n"
                "Escribe <b>cancelar</b> para salir."
            )

        try:
            proyecto = Proyecto.objects.get(id=data["proyecto_id"])
            tipo = TipoGasto.objects.get(id=data["tipo_id"])
        except Exception:
            _rend_wiz_clear(chat_id)
            return "Se perdió la selección de proyecto/tipo. Inicia de nuevo con: <b>nueva rendición</b>."

        state["return_to_confirm"] = False
        state["step"] = "confirm"
        _rend_wiz_set(chat_id, state)
        return _confirm_msg(proyecto.nombre, tipo.nombre)

    # =================== STEP: PROYECTO ===================
    if step == "proyecto":
        if norm.isdigit():
            idx = int(norm)
            opts = choices.get("proyectos") or []
            if 1 <= idx <= len(opts):
                pid, pname = opts[idx - 1]
                data["proyecto_id"] = pid
                state["data"] = data

                state["step"] = "tipo"
                _rend_wiz_set(chat_id, state)

                back = _maybe_back_to_confirm()
                if back:
                    return back

                rec_tipos = _get_recent_tipos_for_user(usuario, limit=6)
                state["choices"]["tipos"] = _merge_unique(rec_tipos, _get_default_tipos(limit=50), limit=50)
                _rend_wiz_set(chat_id, state)

                return (
                    f"✅ <b>Proyecto seleccionado:</b> {_html.escape(pname)}\n\n"
                    "Paso 2/9: <b>Tipo de gasto</b>\n"
                    "Responde con el <b>número</b> (recomendado) o escribe parte del nombre para buscar.\n\n"
                    + _wiz_fmt_choices(state["choices"]["tipos"], "Opciones:")
                    + "\n\nEscribe <b>cancelar</b> para salir."
                )

            return _proyectos_sugeridos_msg("Ese número no está en la lista. Prueba otra opción.")

        q = (text_in or "").strip()
        found = _search_projects(q, limit=10)

        if not found:
            rec = _get_recent_projects_for_user(usuario, limit=6)
            opts = rec or _get_default_projects(limit=10)
            state["choices"]["proyectos"] = opts
            _rend_wiz_set(chat_id, state)
            return _proyectos_sugeridos_msg("No encontré proyectos con ese texto.")

        if len(found) == 1:
            pid, pname = found[0]
            data["proyecto_id"] = pid
            state["data"] = data

            state["step"] = "tipo"
            _rend_wiz_set(chat_id, state)

            back = _maybe_back_to_confirm()
            if back:
                return back

            rec_tipos = _get_recent_tipos_for_user(usuario, limit=6)
            state["choices"]["tipos"] = _merge_unique(rec_tipos, _get_default_tipos(limit=10), limit=10)
            _rend_wiz_set(chat_id, state)

            return (
                f"✅ <b>Proyecto:</b> {_html.escape(pname)}\n\n"
                "Paso 2/9: <b>Tipo de gasto</b>\n"
                "Responde con el <b>número</b> (recomendado) o escribe parte del nombre para buscar.\n\n"
                + _wiz_fmt_choices(state["choices"]["tipos"], "Opciones:")
                + "\n\nEscribe <b>cancelar</b> para salir."
            )

        state["choices"]["proyectos"] = found
        _rend_wiz_set(chat_id, state)
        return (
            "Encontré estos proyectos. Responde con el <b>número</b>:\n\n"
            + _wiz_fmt_choices(found, "Resultados:")
            + "\n\nEscribe <b>cancelar</b> para salir."
        )

    # =================== STEP: TIPO ===================
    if step == "tipo":
        if norm.isdigit():
            idx = int(norm)
            opts = choices.get("tipos") or []
            if 1 <= idx <= len(opts):
                tid, tname = opts[idx - 1]
                data["tipo_id"] = tid
                state["data"] = data

                state["step"] = "tipo_doc"
                _rend_wiz_set(chat_id, state)

                back = _maybe_back_to_confirm()
                if back:
                    return back

                return (
                    f"✅ <b>Tipo seleccionado:</b> {_html.escape(tname)}\n\n"
                    + _tipo_doc_prompt()
                )
            return _tipos_sugeridos_msg("Ese número no está en la lista. Prueba otra opción.")

        q = (text_in or "").strip()
        found = _search_tipos(q, limit=12)
        if not found:
            rec = _get_recent_tipos_for_user(usuario, limit=6)
            opts = rec or _get_default_tipos(limit=10)
            state["choices"]["tipos"] = opts
            _rend_wiz_set(chat_id, state)
            return _tipos_sugeridos_msg("No encontré ese tipo con ese texto.")

        if len(found) == 1:
            tid, tname = found[0]
            data["tipo_id"] = tid
            state["data"] = data

            state["step"] = "tipo_doc"
            _rend_wiz_set(chat_id, state)

            back = _maybe_back_to_confirm()
            if back:
                return back

            return (
                f"✅ <b>Tipo:</b> {_html.escape(tname)}\n\n"
                + _tipo_doc_prompt()
            )

        state["choices"]["tipos"] = found
        _rend_wiz_set(chat_id, state)
        return (
            "Encontré estos tipos. Responde con el <b>número</b>:\n\n"
            + _wiz_fmt_choices(found, "Resultados:")
            + "\n\nEscribe <b>cancelar</b> para salir."
        )

    # =================== STEP: TIPO DOC ✅ ===================
    if step == "tipo_doc":
        td = _norm_tipo_doc(norm)
        if not td:
            return _tipo_doc_prompt("No entendí el tipo de documento. Elige 1, 2 o 3.")

        data["tipo_doc"] = td

        if td == "otros":
            data["rut_factura"] = ""
            data["numero_doc"] = ""
            state["step"] = "monto"   # 👉 ahora va a monto
            state["data"] = data
            _rend_wiz_set(chat_id, state)

            back = _maybe_back_to_confirm()
            if back:
                return back

            return (
                "✅ <b>Tipo de documento:</b> Otros\n\n"
                "Paso 6/9: <b>Monto</b>\n"
                "Escribe el monto (ej: <code>320.240</code> o <code>$320.240</code>).\n\n"
                "Escribe <b>cancelar</b> para salir."
            )

        state["step"] = "rut_factura"
        state["data"] = data
        _rend_wiz_set(chat_id, state)

        back = _maybe_back_to_confirm()
        if back:
            return back

        return (
            f"✅ <b>Tipo de documento:</b> {_html.escape(td.title())}\n\n"
            "Paso 4/9: <b>RUT emisor</b>\n"
            "Escribe el RUT del emisor (Ej: <code>77.084.679-K</code> o <code>77084679-K</code>).\n\n"
            "Escribe <b>cancelar</b> para salir."
        )

    # =================== STEP: RUT FACTURA/BOLETA ✅ (SII) ===================
    if step == "rut_factura":
        rut = (text_in or "").strip()
        if not validar_rut_chileno(rut):
            return "❌ RUT inválido. Ejemplo: <code>12.345.678-5</code>"

        ok_sii = True
        try:
            ok_sii = verificar_rut_sii(rut)
        except Exception:
            ok_sii = True

        if not ok_sii:
            return "❌ No pude validar ese RUT en SII. Revisa el RUT e intenta nuevamente."

        data["rut_factura"] = rut
        state["step"] = "numero_doc"
        state["data"] = data
        _rend_wiz_set(chat_id, state)

        back = _maybe_back_to_confirm()
        if back:
            return back

        return (
            "✅ <b>RUT validado.</b>\n\n"
            "Paso 5/9: <b>N° comprobante</b>\n"
            "Escribe el número del comprobante (solo números).\n\n"
            "Escribe <b>cancelar</b> para salir."
        )

    # =================== STEP: NUMERO DOC ✅ ===================
    if step == "numero_doc":
        n = _clean_num_doc_local(text_in)
        if not n:
            return "❌ Número de comprobante inválido. Debe contener solo números (sin puntos)."

        data["numero_doc"] = n
        state["step"] = "monto"   # 👉 ahora va a monto
        state["data"] = data
        _rend_wiz_set(chat_id, state)

        back = _maybe_back_to_confirm()
        if back:
            return back

        return (
            f"✅ <b>N° comprobante guardado:</b> <code>{_html.escape(n)}</code>\n\n"
            "Paso 6/9: <b>Monto</b>\n"
            "Escribe el monto (ej: <code>320.240</code> o <code>$320.240</code>).\n\n"
            "Escribe <b>cancelar</b> para salir."
        )

    # =================== STEP: MONTO ===================
    if step == "monto":
        v = _parse_clp_to_decimal(text_in)
        if v is None:
            return "Monto inválido. Ejemplo válido: <code>320.240</code> (o <code>$320.240</code>)."
        data["cargos"] = v
        state["step"] = "observaciones"   # 👉 monto -> observaciones
        state["data"] = data
        _rend_wiz_set(chat_id, state)

        back = _maybe_back_to_confirm()
        if back:
            return back

        return (
            f"✅ <b>Monto guardado:</b> <b>{_html.escape(_fmt_clp(v))}</b>\n\n"
            "Paso 7/9: <b>Observaciones</b>\n"
            "Escribe observaciones (puede ir vacío si no aplica).\n\n"
            "Escribe <b>cancelar</b> para salir."
        )

    # =================== STEP: OBSERVACIONES ===================
    if step == "observaciones":
        obs = (text_in or "").strip()
        # ✅ Puede ir vacío (como en web)
        data["observaciones"] = obs
        state["step"] = "comprobante"  # 👉 observaciones -> comprobante
        state["data"] = data
        _rend_wiz_set(chat_id, state)

        back = _maybe_back_to_confirm()
        if back:
            return back

        return (
            "✅ <b>Observaciones guardadas.</b>\n\n"
            "Paso 8/9: <b>Comprobante</b>\n"
            "Ahora envíame el comprobante como <b>PDF</b> o <b>imagen</b> (jpg/png).\n\n"
            "Escribe <b>cancelar</b> para salir."
        )

    # =================== STEP: COMPROBANTE (archivo) ===================
    if step == "comprobante":
        file_info = _tg_extract_file_from_message(message)
        if not file_info:
            return (
                "Necesito que envíes el comprobante como archivo (PDF/JPG/PNG) o foto.\n"
                "Envíalo ahora. (o <b>cancelar</b>)"
            )

        fname = (file_info.get("filename") or "comprobante").strip()
        low = fname.lower()
        if not any(low.endswith(ext) for ext in (".pdf", ".jpg", ".jpeg", ".png")):
            return "Formato no permitido. Envíalo como PDF o imagen (jpg/png)."

        data["comprobante_file_id"] = file_info["file_id"]
        data["comprobante_filename"] = fname
        state["data"] = data
        state["step"] = "confirm"
        _rend_wiz_set(chat_id, state)

        try:
            proyecto = Proyecto.objects.get(id=data["proyecto_id"])
            tipo = TipoGasto.objects.get(id=data["tipo_id"])
        except Exception:
            _rend_wiz_clear(chat_id)
            return "Se perdió la selección de proyecto/tipo. Inicia de nuevo con: <b>nueva rendición</b>."

        return _confirm_msg(proyecto.nombre, tipo.nombre)

    # =================== STEP: CONFIRM ===================
    if step == "confirm":
        # Permitir reemplazar comprobante si mandan otro archivo en confirm
        file_info = _tg_extract_file_from_message(message)
        if file_info and file_info.get("file_id"):
            fname = (file_info.get("filename") or "comprobante").strip()
            low = fname.lower()
            if any(low.endswith(ext) for ext in (".pdf", ".jpg", ".jpeg", ".png")):
                data["comprobante_file_id"] = file_info["file_id"]
                data["comprobante_filename"] = fname
                state["data"] = data
                _rend_wiz_set(chat_id, state)

        if norm.isdigit():
            op = int(norm)

            if op == 1:
                token = _get_bot_token()
                if not token:
                    return "No tengo token de Telegram configurado. No puedo descargar el comprobante."

                try:
                    proyecto = Proyecto.objects.get(id=data["proyecto_id"])
                    tipo = TipoGasto.objects.get(id=data["tipo_id"])
                except Exception:
                    _rend_wiz_clear(chat_id)
                    return "Se perdió la selección de proyecto/tipo. Inicia de nuevo con: <b>nueva rendición</b>."

                file_id = data.get("comprobante_file_id")
                if not file_id:
                    state["step"] = "comprobante"
                    _rend_wiz_set(chat_id, state)
                    return (
                        "No tengo el comprobante registrado. Envíamelo otra vez.\n\n"
                        "Paso 8/9: <b>Comprobante</b>\n"
                        "Envíame el comprobante como PDF o imagen."
                    )

                td = (data.get("tipo_doc") or "").strip()
                if td in {"factura", "boleta"}:
                    rut = (data.get("rut_factura") or "").strip()
                    n_doc = (data.get("numero_doc") or "").strip()
                    if not rut or not n_doc:
                        state["step"] = "tipo_doc"
                        _rend_wiz_set(chat_id, state)
                        return "⚠️ Falta Tipo doc / RUT / Nº comprobante. Volvamos a <b>Tipo de documento</b>."

                try:
                    fname_dl, blob = _tg_download_file(token, file_id)
                except Exception as e:
                    logger.exception("Error descargando comprobante Telegram (confirm)")
                    return f"Tuve un problema descargando el archivo desde Telegram: {e}"

                fname_final = (data.get("comprobante_filename") or fname_dl or "comprobante").strip()

                mov = CartolaMovimiento(
                    usuario=usuario,
                    proyecto=proyecto,
                    tipo=tipo,
                    tipo_doc=(data.get("tipo_doc") or "").strip() or None,
                    rut_factura=(data.get("rut_factura") or "").strip() or None,
                    numero_doc=(data.get("numero_doc") or "").strip() or None,
                    observaciones=data.get("observaciones") or "",
                    cargos=Decimal(str(data.get("cargos") or 0)),
                    abonos=Decimal("0.00"),
                    status="pendiente_supervisor",
                )
                mov.comprobante = ContentFile(blob, name=fname_final)
                mov.save()

                _rend_wiz_clear(chat_id)

                return (
                    "✅ <b>Rendición creada y enviada</b>\n\n"
                    f"• <b>Proyecto:</b> {_html.escape(proyecto.nombre)}\n"
                    f"• <b>Tipo gasto:</b> {_html.escape(tipo.nombre)}\n"
                    f"• <b>Tipo doc:</b> {_html.escape((mov.tipo_doc or '—'))}\n"
                    f"• <b>RUT:</b> {_html.escape((mov.rut_factura or '—'))}\n"
                    f"• <b>N° comprobante:</b> {_html.escape((mov.numero_doc or '—'))}\n"
                    f"• <b>Monto:</b> <b>{_html.escape(_fmt_clp(mov.cargos))}</b>\n"
                    f"• <b>Estado:</b> <b>Pendiente supervisor</b>\n\n"
                    "Puedes ver el detalle en la web en <b>Mis Rendiciones</b>."
                )

            # ✅ FIX: al editar desde confirm, marcamos que debemos volver a confirm
            if op in {2, 3, 4, 5, 6, 7, 8, 9, 10}:
                state["return_to_confirm"] = True

            if op == 2:
                state["step"] = "proyecto"
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar proyecto</b>\n\n"
                    "Paso 1/9: <b>Proyecto</b>\n"
                    "Responde con el número o escribe parte del nombre para buscar.\n\n"
                    + _wiz_fmt_choices(state["choices"].get("proyectos") or _get_default_projects(10), "Opciones:")
                    + "\n\nEscribe <b>cancelar</b> para salir."
                )

            if op == 3:
                state["step"] = "tipo"
                rec_tipos = _get_recent_tipos_for_user(usuario, 6)
                state["choices"]["tipos"] = _merge_unique(rec_tipos, _get_default_tipos(10), limit=10)
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar tipo de gasto</b>\n\n"
                    "Paso 2/9: <b>Tipo de gasto</b>\n"
                    "Responde con el número o escribe parte del nombre para buscar.\n\n"
                    + _wiz_fmt_choices(state["choices"]["tipos"], "Opciones:")
                    + "\n\nEscribe <b>cancelar</b> para salir."
                )

            if op == 4:
                state["step"] = "tipo_doc"
                _rend_wiz_set(chat_id, state)
                return _tipo_doc_prompt("✏️ <b>Cambiar tipo de documento</b>")

            if op == 5:
                if (data.get("tipo_doc") or "").strip() == "otros":
                    state["step"] = "tipo_doc"
                    _rend_wiz_set(chat_id, state)
                    return _tipo_doc_prompt("⚠️ Para cambiar RUT, primero elige Factura o Boleta.")
                state["step"] = "rut_factura"
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar RUT emisor</b>\n\n"
                    "Paso 4/9: <b>RUT emisor</b>\n"
                    "Escribe el RUT del emisor (Ej: <code>77.084.679-K</code>).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )

            if op == 6:
                if (data.get("tipo_doc") or "").strip() == "otros":
                    state["step"] = "tipo_doc"
                    _rend_wiz_set(chat_id, state)
                    return _tipo_doc_prompt("⚠️ Para cambiar Nº comprobante, primero elige Factura o Boleta.")
                state["step"] = "numero_doc"
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar N° comprobante</b>\n\n"
                    "Paso 5/9: <b>N° comprobante</b>\n"
                    "Escribe el número (solo números).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )

            if op == 7:
                state["step"] = "monto"
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar monto</b>\n\n"
                    "Paso 6/9: <b>Monto</b>\n"
                    "Escribe el monto (ej: <code>320.240</code> o <code>$320.240</code>)."
                    "\n\nEscribe <b>cancelar</b> para salir."
                )

            if op == 8:
                state["step"] = "observaciones"
                _rend_wiz_set(chat_id, state)
                return (
                    "✏️ <b>Cambiar observaciones</b>\n\n"
                    "Paso 7/9: <b>Observaciones</b>\n"
                    "Escribe observaciones (puede ir vacío si no aplica).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )

            if op == 9:
                state["step"] = "comprobante"
                _rend_wiz_set(chat_id, state)
                return (
                    "📎 <b>Cambiar comprobante</b>\n\n"
                    "Paso 8/9: <b>Comprobante</b>\n"
                    "Envíame el comprobante como <b>PDF</b> o <b>imagen</b> (jpg/png).\n\n"
                    "Escribe <b>cancelar</b> para salir."
                )

            if op == 10:
                _rend_wiz_clear(chat_id)
                return "✅ Listo, cancelé la creación de la rendición."

        if norm in {"si", "sí", "ok", "confirmo", "confirmar", "confirmado", "dale"}:
            message2 = dict(message)
            message2["text"] = "1"
            return _rendicion_wizard_handle_message(chat_id=chat_id, usuario=usuario, message=message2)

        return "Responde con el <b>número</b> (1 a 10) para confirmar, editar o cancelar."

    return "No entendí ese paso. Escribe <b>cancelar</b> y vuelve a intentar."
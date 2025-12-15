# bot_gz/services.py

import logging
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.core.exceptions import FieldError
from django.db.models import Count, Q, Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento
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
    "por",
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
    Ej: 'contrato de Edgardo', 'liquidación de juan', 'liquidacion de edgardo', etc.

    ✅ ROBUSTO:
    - Mantiene detección por palabras capitalizadas (como antes).
    - Agrega detección por patrón "X de <nombre>" aunque el nombre venga en minúsculas.
    - Solo se usa para mostrar mensajes de privacidad (no para buscar info de terceros).
    """
    texto_original = texto_original or ""
    norm = _normalize(texto_original)
    if not norm:
        return False

    # Palabras que suelen venir como "candidatos" pero NO son nombres de personas
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

    # Normalizamos nombres del usuario
    nombres_usuario = set()
    for campo in [usuario.first_name, usuario.last_name, getattr(usuario, "full_name", ""), usuario.get_full_name()]:
        if campo:
            for trozo in _normalize(str(campo)).split():
                if trozo:
                    nombres_usuario.add(trozo)

    # --------------------------
    # 1) Detección por Mayúsculas (como antes)
    # --------------------------
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

    # --------------------------
    # 2) Heurística "de <nombre>" aunque esté en minúsculas
    #    Solo si el texto habla de cosas personales sensibles.
    # --------------------------
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

    # buscar secuencias: <trigger> ... (de|del|para|por) <candidato>
    # ejemplo: "liquidacion de edgardo", "contrato del juan perez"
    for i, tok in enumerate(tokens[:-1]):
        if tok not in preps:
            continue

        j = i + 1
        while j < len(tokens) and tokens[j] in skip_after_prep:
            j += 1
        if j >= len(tokens):
            continue

        # Tomamos 1 o 2 tokens como candidato (nombre / nombre apellido)
        cand1 = tokens[j]
        cand2 = tokens[j + 1] if (j + 1) < len(tokens) else None

        # cand1 debe ser alfabético y no ser palabra "no nombre"
        if not cand1.isalpha():
            continue
        if cand1 in NO_NOMBRES or cand1 in _STOPWORDS:
            continue

        # si cand1 coincide con el usuario, no es "otra persona"
        if cand1 in nombres_usuario:
            continue

        # caso 2 palabras: "juan perez"
        if cand2 and cand2.isalpha() and (cand2 not in NO_NOMBRES) and (cand2 not in _STOPWORDS):
            # si cualquiera no coincide con usuario, lo consideramos "otra persona"
            if cand2 not in nombres_usuario:
                return True

        # con 1 palabra basta (ej: edgardo)
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

    # "ultimas" / "últimos" / "ultimo" / "última"
    if not re.search(r"\bultim[oa]s?\b", norm):
        return None

    # 1) número explícito
    m = re.search(r"\bultim[oa]s?\s+(\d{1,2})\b", norm)
    if m:
        try:
            n = int(m.group(1))
            return max(1, min(n, 12))
        except Exception:
            return None

    # 2) número en palabras
    m2 = re.search(r"\bultim[oa]s?\s+([a-z]+)\b", norm)
    if m2:
        palabra = m2.group(1)
        if palabra in _NUM_PALABRAS:
            n = _NUM_PALABRAS[palabra]
            return max(1, min(n, 12))

    # Si dijeron "mis últimas liquidaciones" sin número -> default 3
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

def detect_intent_from_text(
    texto: str, scope: Optional[str] = None
) -> Tuple[Optional[BotIntent], float]:
    """
    Detección de intent basada en:
    1) overlap de tokens con ejemplos de entrenamiento
    2) refuerzo por palabras clave (liquidación, contrato, rendiciones, etc.)

    Así, frases como "liquidacion", "ver mi liquidacion", "necesito mi contrato",
    etc. se entienden aunque sean muy cortas.
    """
    user_tokens = set(_tokenize(texto))
    if not user_tokens:
        return None, 0.0

    # --- 1) Matching con ejemplos de entrenamiento ---
    examples_qs = (
        BotTrainingExample.objects.filter(activo=True).select_related("intent")
    )

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

    # --- 2) Reglas rápidas por palabras clave ---
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

    # Liquidaciones
    if {"liquidacion", "liquidaciones"} & user_tokens:
        add_keyword_candidate("mis_liquidaciones", 0.9)

    # Contrato
    if "contrato" in user_tokens or "contratos" in user_tokens:
        add_keyword_candidate("mi_contrato_vigente", 0.9)

    # Producción
    if "produccion" in user_tokens:
        add_keyword_candidate("mi_produccion_hasta_hoy", 0.8)

    # Proyectos pendientes / asignados (mantengo tu lógica)
    if "proyectos" in user_tokens or "servicios" in user_tokens:
        if (
            "pendientes" in user_tokens
            or "pendiente" in user_tokens
            or "asignados" in user_tokens
        ):
            add_keyword_candidate("mis_proyectos_pendientes", 0.8)

        # Asignación (sinónimo de proyectos/servicios asignados)
    if {"asignacion", "asignaciones", "asignado", "asignados", "asignada", "asignadas"} & user_tokens:
        add_keyword_candidate("mi_asignacion", 0.95)

    # ✅ EXTRA (SIN BORRAR): soporta singular y "proyectos" a secas (resumen PRO)
    if {"proyectos", "proyecto", "servicios", "servicio"} & user_tokens:
        # si NO pidió rechazados explícito, igual lo mandamos a resumen/pendientes
        if not ({"rechazados", "rechazado", "rechazadas", "rechazada"} & user_tokens):
            add_keyword_candidate("mis_proyectos_pendientes", 0.75)

    # Proyectos rechazados (mantengo tu lógica)
    if (
        "rechazados" in user_tokens
        or "rechazado" in user_tokens
        or "rechazadas" in user_tokens
        or "rechazada" in user_tokens
    ):
        add_keyword_candidate("mis_proyectos_rechazados", 0.8)

    # Rendiciones / gastos
    if (
        "rendicion" in user_tokens
        or "rendiciones" in user_tokens
        or "gasto" in user_tokens
        or "gastos" in user_tokens
    ):
        add_keyword_candidate("mis_rendiciones_pendientes", 0.8)
        add_keyword_candidate("ayuda_rendicion_gastos", 0.8)

    # Dirección de la basura / residuos
    if (
        "basura" in user_tokens
        or "residuos" in user_tokens
        or "desechos" in user_tokens
    ):
        add_keyword_candidate("direccion_basura", 0.9)

    # Corte de producción / cuándo pagan
    if (
        "pago" in user_tokens
        or "pagan" in user_tokens
        or "pagar" in user_tokens
        or "corte" in user_tokens
        or "cronograma" in user_tokens
    ):
        add_keyword_candidate("cronograma_produccion_corte", 0.7)

    # Info sitio por ID Claro (tu lógica)
    if "sitio" in user_tokens or "site" in user_tokens:
        add_keyword_candidate("info_sitio_id_claro", 0.7)

    # ✅ EXTRA (SIN BORRAR): si el texto parece un ID de sitio, aunque no diga "sitio"
    # - ID CLARO: 13_094 o 13 094
    # - ID SITES / NEW: CL-13-00421-05, CL-13-SN-00421-05
    # - Otros cortos tipo MA5694
    txt_up = (texto or "").strip().upper()
    if (
        re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
        or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)
        or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)
    ):
        add_keyword_candidate("info_sitio_id_claro", 0.72)

    # --- Elegir el mejor resultado entre ejemplos y reglas ---
    final_intent = best_intent
    final_score = best_score

    if keyword_intent and keyword_score > final_score:
        final_intent = keyword_intent
        final_score = keyword_score

    # Umbral mínimo para aceptar un intent
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
    """
    Maneja consultas de liquidaciones (PRO):
    - "últimas N" => devuelve N liquidaciones (más recientes) en 1 solo mensaje
    - "julio y septiembre" => devuelve ambas (si año es ambiguo, pregunta cuál)
    - "julio" (sin año) => si hay varios años, pregunta el año
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tus propias* liquidaciones de sueldo.\n"
            "Si un compañero necesita la suya, debe pedirla directamente a RRHH o entrar con su usuario."
        )

    qs = Liquidacion.objects.filter(tecnico=usuario).order_by("-año", "-mes")

    if not qs.exists():
        return "Por ahora no tengo liquidaciones de sueldo cargadas a tu nombre en el sistema."

    # ========= 1) "últimas N liquidaciones" =========
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

    # ========= 2) Lista de meses (julio y septiembre, etc.) =========
    meses_multi = _parse_meses_multi(texto_usuario)
    anio_explicito = _parse_anio_explicito(texto_usuario)

    if meses_multi:
        # Si no viene año, verificamos ambigüedad por mes
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

        # Ya tenemos año (explícito o no ambiguo)
        # Si no hay año explícito, elegimos el único año disponible por mes (si existe)
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

        # Ordenamos por año/mes desc para que se vea pro
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

    # ========= 3) Un solo mes (con o sin año) =========
    tokens = _tokenize(texto_usuario)

    # detectar mes por nombre (sin forzar año actual)
    mes = None
    for t in tokens:
        if t in _MESES:
            mes = _MESES[t]
            break

    # detectar mes por número (si escriben "07/2025" ya lo toma el regex de abajo)
    if mes is None:
        mnum = re.search(r"\b(0?[1-9]|1[0-2])\b", _normalize(texto_usuario))
        if mnum:
            mes = int(mnum.group(1))

    # detectar patrón 07/2025 o 07-2025
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

    # ========= 4) Sin mes/año y sin "últimas N" -> guía =========
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

# -------------------- (TODO LO DEMÁS IGUAL) --------------------
# A partir de aquí no toqué nada más: sigue exactamente como lo pegaste.
# (Para que puedas copiar/pegar sin sorpresas.)

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
        # Usa tu lógica del modelo
        code = getattr(c, "status_code", None) or c.status_code
        if code in ("indefinido", "vigente", "por_vencer"):
            activos.append(c)
        else:
            # fallback por fecha si algo raro
            if c.fecha_termino and c.fecha_termino < hoy:
                vencidos.append(c)
            else:
                vencidos.append(c)

    # contrato actual = el más reciente activo, si existe; sino el más reciente de todos
    contrato_actual = activos[0] if activos else contratos[0]

    # extensiones = todo lo anterior al contrato actual (por fecha_inicio), normalmente vencidos
    extensiones = [c for c in contratos if c.id != contrato_actual.id and c.fecha_inicio <= contrato_actual.fecha_inicio]
    extensiones_vencidas = [c for c in extensiones if c.status_code == "vencido"]

    # por si existieran 2 activos (raro pero posible), lo reportamos
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

    # === Si pide "todos mis contratos" o "mis contratos" ===
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

    # === Caso normal: contrato actual + (opcional) extensiones ===
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

    # Si hay más de un activo (por si acaso), lo avisamos
    if otros_activos:
        msg += "\n\n⚠️ Nota: veo más de un contrato marcado como vigente/activo en el sistema."

    # Extensiones vencidas (si el usuario las pidió)
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

    # Anexos RRHH (DocumentoTrabajador), si el usuario los pidió
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

    # Si NO pidió extensiones/anexos y existen extensiones vencidas, pregúntale (PRO, sin ser latero)
    if (not quiere_extensiones) and (not quiere_anexos) and extensiones_vencidas:
        msg += (
            f"\n\nTengo además *{len(extensiones_vencidas)}* contrato(s) anterior(es) vencido(s) (extensiones).\n"
            "¿Quieres que te los envíe también?\n"
            "Ejemplo: `mi contrato y sus extensiones`"
        )

    return msg



def _month_start_end(year: int, month: int):
    from datetime import date

    # inicio
    start = date(year, month, 1)
    # fin (inicio del mes siguiente - 1 día)
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

    # normaliza guiones “raros”
    raw = raw.replace("–", "-").replace("—", "-")

    # 1) Buscar dos fechas YYYY-MM-DD o YYYY/MM/DD
    ymd = re.findall(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    if len(ymd) >= 2:
        try:
            y1, m1, d1 = map(int, ymd[0])
            y2, m2, d2 = map(int, ymd[1])
            return date(y1, m1, d1), date(y2, m2, d2)
        except ValueError:
            return None

    # 2) Buscar dos fechas DD-MM-YYYY o DD/MM/YYYY
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
    """
    Devuelve (mes, año) si detecta mes en texto.
    Si no viene año, usa el año actual.
    """
    parsed = _parse_mes_anio_desde_texto(texto)  # tu helper existente
    if parsed:
        mes, anio = parsed
        return mes, anio
    return None


def _parse_flags_estados_produccion(tokens: set[str]) -> dict:
    """
    Decide si el usuario quiere incluir estados:
    - asignados, ejecución, pendientes, finalizados, etc.
    Si no dice nada, devolvemos default "lo que ya calcula hasta hoy".
    """
    return {
        "incluye_asignados": bool({"asignado", "asignados"} & tokens),
        "incluye_ejecucion": bool({"ejecucion", "ejecución", "en_progreso", "progreso"} & tokens),
        "incluye_pendiente_supervisor": bool({"pendiente", "pendientes", "supervisor"} & tokens),
        "incluye_finalizados": bool({"finalizado", "finalizados"} & tokens),
        "incluye_todo": bool({"todo", "todos", "completo"} & tokens),
    }


def responder_produccion_rango(usuario, date_from, date_to, *, incluir_estados=None):
    """
    Implementa el cálculo por rango.
    - date_from/date_to: date
    - incluir_estados: dict flags opcional
    Debe devolver string listo para Telegram.
    """
    # TODO: aquí reutiliza tu lógica actual pero filtrando por fechas del servicio
    # (fecha_creacion / fecha_aprobacion_supervisor / fecha_finalizado según tu criterio)
    # Mientras tanto, puedes devolver un mensaje temporal si aún no filtras.
    return (
        f"📊 *Producción estimada*\n"
        f"Rango: {date_from.strftime('%d-%m-%Y')} al {date_to.strftime('%d-%m-%Y')}\n\n"
        "⚙️ (Pendiente: aplicar filtro por fechas en el cálculo)\n"
        "Si quieres, dime qué fecha del servicio usar: creación, aprobación supervisor o finalización."
    )


def _handler_mi_produccion(usuario: CustomUser, texto_usuario: str) -> str:
    # Privacidad: producción solo del propio usuario
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tu propia producción*.\n"
            "No tengo permiso para entregar información de producción de otros compañeros."
        )

    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()
    norm = _normalize(texto_usuario)

    flags = _parse_flags_estados_produccion(tokens)

    # 1) Rango explícito de fechas
    rango = _parse_rango_fechas(texto_usuario)
    if rango:
        d1, d2 = rango
        if d2 < d1:
            d1, d2 = d2, d1
        return responder_produccion_rango(usuario, d1, d2, incluir_estados=flags)

    # 2) "hasta hoy / a la fecha"
    if {"hoy", "ahora", "fecha"} & tokens or ("hasta hoy" in norm) or ("a la fecha" in norm):
        return _responder_produccion_hasta_hoy(usuario)

    # 3) "este mes" / "mes actual" => desde inicio de mes hasta HOY
    if ("este mes" in norm) or ("mes actual" in norm) or ("mes" in tokens and ({"este", "actual"} & tokens)):
        start, _end = _month_start_end(hoy.year, hoy.month)
        return responder_produccion_rango(usuario, start, hoy, incluir_estados=flags)

    # 4) "mes anterior" / "mes pasado" => mes completo anterior
    if ("mes anterior" in norm) or ("mes pasado" in norm) or ("mes" in tokens and ({"anterior", "pasado"} & tokens)):
        year = hoy.year
        month = hoy.month - 1
        if month == 0:
            month = 12
            year -= 1
        start, end = _month_start_end(year, month)
        return responder_produccion_rango(usuario, start, end, incluir_estados=flags)

    # 5) Mes específico (ej: "agosto", "julio 2025", "08/2025")
    parsed_mes = _parse_mes_produccion(texto_usuario)
    if parsed_mes:
        mes, anio = parsed_mes
        start, end = _month_start_end(anio, mes)
        return responder_produccion_rango(usuario, start, end, incluir_estados=flags)

    # 6) Menú PRO
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

    # 1) ID CLARO: 13_094 o 13-094
    m = re.search(r"\b(\d{2})[_-](\d{3})\b", raw)
    if m:
        return ("id_claro", f"{m.group(1)}_{m.group(2)}")

    # 2) ID SITES / ID NEW: CL-13-00421-05 | CL-13-SN-00421-05 | CL-13-ÑÑ-01837-11
    #    - Segmento medio (SN/TC/CN/ÑÑ) es opcional, pero si viene, lo soporta.
    m2 = re.search(
        r"\b(CL-\d{2}-(?:[A-ZÑ]{2,3}-)?\d{5}-\d{2})\b",
        raw,
        flags=re.IGNORECASE,
    )
    if m2:
        return ("cl_code", m2.group(1).upper())

    return None


def _is_only_site_id(texto: str, kind: str, value: str) -> bool:
    """
    True si el usuario mandó básicamente solo el ID (con o sin backticks/comillas).
    """
    cleaned = (texto or "").strip().strip("`'\"").upper()

    if kind == "id_claro":
        # aceptar 13_094 o 13-094
        return cleaned == value.upper() or cleaned == value.replace("_", "-").upper()

    return cleaned == value.upper()


def _find_sitio_by_any_id(kind: str, value: str) -> Tuple[Optional[SitioMovil], str]:
    """
    Busca por:
    - id_claro (13_094)
    - id_sites (CL-13-00421-05)
    - id_sites_new (CL-13-SN-00421-05 / CL-13-ÑÑ-01837-11)
    Devuelve (sitio, matched_field)
    """
    qs = SitioMovil.objects.all()

    if kind == "id_claro":
        variants = [value, value.replace("_", "-")]
        for v in variants:
            sm = qs.filter(id_claro__iexact=v).first()
            if sm:
                return sm, "id_claro"
        return None, "id_claro"

    # cl_code: puede ser id_sites o id_sites_new
    sm = qs.filter(id_sites__iexact=value).first()
    if sm:
        return sm, "id_sites"

    sm = qs.filter(id_sites_new__iexact=value).first()
    if sm:
        return sm, "id_sites_new"

    # (por si acaso alguien pega algo raro y coincide con id_claro)
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

    # Respuesta PRO
    msg = "📡 *Información del Sitio*\n\n"

    msg += f"• ID Sites: {sitio.id_sites or '—'}\n"
    msg += f"• ID Claro: {sitio.id_claro or '—'}\n"
    msg += f"• ID New: {sitio.id_sites_new or '—'}\n"
    msg += f"• Región: {sitio.region or '—'}\n"

    if sitio.nombre:
        msg += f"• Nombre: {sitio.nombre}\n"
    if sitio.direccion:
        msg += f"• Dirección: {sitio.direccion}\n"
    if sitio.comuna:
        msg += f"• Comuna: {sitio.comuna}\n"

    # Acceso / seguridad
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

    # Coordenadas
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
    """
    Retorna el monto de mano de obra que le corresponde al usuario.
    - Si existe un monto por técnico en el through (asignación), usa eso.
    - Si no, divide monto_mmoo total por cantidad de técnicos asignados.
    """
    total = Decimal(str(getattr(s, "monto_mmoo", None) or 0))

    # 1) Intentar monto por técnico si existe en el through model
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
                (f.name for f in through._meta.fields
                 if getattr(f, "is_relation", False) and getattr(f, "related_model", None) == ServicioCotizado),
                None
            )
            usr_fk = next(
                (f.name for f in through._meta.fields
                 if getattr(f, "is_relation", False) and getattr(f, "related_model", None) == CustomUser),
                None
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

    # 2) Fallback: división por cantidad de técnicos asignados
    n = getattr(s, "n_tecs", None)
    if not n:
        try:
            n = s.trabajadores_asignados.count()
        except Exception:
            n = 1
    n = int(n or 1)

    return (total / Decimal(n)) if n else total


def _extract_project_id(texto: str) -> Optional[str]:
    """
    Detecta:
    - ID CLARO: 13_094
    - ID SITES: CL-13-00421-05
    - ID NEW:   CL-13-SN-00421-05 (y variantes CN/TC/TE/TA/ÑÑ etc)
    """
    t = (texto or "").strip()

    m = re.search(r"\b\d{2}_\d{3,5}\b", t)  # 13_094, 13_913, etc
    if m:
        return m.group(0)

    m2 = re.search(r"\bCL-\d{2}-\d{4,6}-\d{2}\b", t, flags=re.IGNORECASE)  # id_sites
    if m2:
        return m2.group(0)

    m3 = re.search(r"\bCL-\d{2}-[A-ZÑ]{1,3}-\d{4,6}-\d{2}\b", t, flags=re.IGNORECASE)  # id_new
    if m3:
        return m3.group(0)

    return None


def _project_month_filter(texto_usuario: str):
    """
    Soporta:
    - "este mes", "mes actual"
    - "mes pasado", "mes anterior"
    - "agosto 2025", "08/2025"
    Retorna (start_date, end_date, label) o None
    """
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
    """
    Decide qué grupos mostrar según el texto.
    Si no especifica, devolvemos TODOS (resumen pro).
    """
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
    """
    Devuelve:
    - 1 link "ruta" (dir) si hay >=2 puntos
    - y una lista de links individuales (search) como fallback
    """
    id_claros = [s.id_claro for s in servicios if s.id_claro]
    id_news = [s.id_new for s in servicios if s.id_new]

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
        key = (s.id_claro or "").strip() or (s.id_new or "").strip()
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

    # Google limita waypoints; mandamos máximo 10 puntos (1 destino + 9 waypoints)
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

    # filtro por mes (usa fecha_creacion como referencia)
    mf = _project_month_filter(texto_usuario)
    mes_label = None
    if mf:
        start, end, mes_label = mf
        base = base.filter(fecha_creacion__date__gte=start, fecha_creacion__date__lte=end)

    # consulta específica por ID (13_913 / CL-xx / CL-xx-YY-xxxx-zz)
    pid = _extract_project_id(texto_usuario)
    if pid:
        pid_u = pid.strip().upper()
        s = (
            base.filter(id_claro__iexact=pid_u).first()
            or base.filter(id_new__iexact=pid_u).first()
            or base.filter(du__iexact=pid_u).first()
        )
        if not s:
            # si lo mandaron como ID Sites/NEW pero el servicio guarda id_claro/id_new, igual damos pista
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
        msg += f"• DU: {s.du or '—'}\n"
        msg += f"• ID Claro: {s.id_claro or '—'}\n"
        msg += f"• ID New: {s.id_new or '—'}\n"
        msg += f"• Estado: {estado_legible}\n"

        # MMOO: siempre mostramos "tu parte" (lo que te corresponde)
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

    # “mapa / maps”
    if {"mapa", "maps", "google", "ubicacion", "ubicación"} & tokens:
        # para mapa tomamos activos por defecto
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

    # “monto / total / suma”
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

    # RESUMEN PRO (default): conteo por estados + mini listado + menú
    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

    # Para resumen, consideramos TODOS los buckets (aunque pidan "proyectos" a secas)
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

    # muestra ejemplos recientes (mezclados)
    ejemplos = list(base.order_by("-fecha_creacion")[:8])
    if ejemplos:
        msg += "Últimos proyectos (recientes):\n"
        for s in ejemplos:
            du = s.du or "—"
            idc = s.id_claro or (s.id_new or "—")
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
    """
    Campo de fecha para interpretar "me asignaron hoy".
    Preferimos fecha_asignacion si existe. Si no, caemos a fecha_creacion.
    """
    candidatos = ["fecha_asignacion", "fecha_asignado", "fecha_creacion", "created_at", "creado"]
    nombres = {f.name for f in ServicioCotizado._meta.get_fields() if hasattr(f, "name")}
    for c in candidatos:
        if c in nombres:
            return c
    return "fecha_creacion"


def _filter_by_day(qs, field_name: str, day):
    """
    Filtra por día tolerando DateTimeField vs DateField.
    """
    try:
        return qs.filter(**{f"{field_name}__date": day})
    except FieldError:
        # si el campo es DateField, __date puede fallar
        return qs.filter(**{field_name: day})


def _fmt_servicio_asignacion_line(s: ServicioCotizado, estados_dict: dict) -> str:
    du = s.du or "—"
    idref = s.id_claro or (getattr(s, "id_new", None) or "—")
    est = estados_dict.get(s.estado, s.estado)
    det = (s.detalle_tarea or "").strip()
    if len(det) > 90:
        det = det[:87] + "…"
    return f"• DU {du} / {idref} – {est} – {det}"


def _handler_asignacion(usuario: CustomUser, texto_usuario: str) -> str:
    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()

    # “de hoy / para hoy / hoy”
    es_hoy = bool({"hoy"} & tokens) or ("de hoy" in _normalize(texto_usuario)) or ("para hoy" in _normalize(texto_usuario))

    base = ServicioCotizado.objects.filter(trabajadores_asignados=usuario)
    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

    # Activos (lo que normalmente se entiende como “mi asignación”)
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

        # Si NO hay asignación hoy => avisar + mostrar pendientes activos
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

    # Caso “asignación” (sin hoy): resumen + listado
    total = activos.count()
    if total == 0:
        return "No tienes asignaciones/pendientes activos en este momento. ✅"

    # Conteo por estado dentro de activos
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
        msg += f"• DU {du} / {id_ref} – {est}: {detalle}\n"

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
        msg += f"• DU {du} / {id_ref} – {est} – Motivo: {motivo or 'Sin detalle'}\n"

    return msg


def _handler_mis_rendiciones_pendientes(
    usuario: CustomUser, texto_usuario: str
) -> str:
    """
    Resumen de rendiciones de gastos.
    - Si el mensaje habla de "hacer / crear / declarar" -> explica que el flujo
      de creación por bot aún no está activo.
    - Si el mensaje es muy genérico ("gasto", "rendiciones"), pregunta qué tipo quiere.
    - Soporta filtros por pendientes / aprobadas / rechazadas.
    - Soporta filtro por día con "hoy" o "ayer".
    """
    tokens = set(_tokenize(texto_usuario))

    # Caso: el usuario quiere CREAR una rendición nueva
    if {"hacer", "crear", "nueva", "nuevo", "declarar"} & tokens:
        return (
            "Por ahora todavía *no puedo crear rendiciones nuevas* desde el bot 🤖.\n\n"
            "Para declarar un gasto debes hacerlo en la sección de *Mis Rendiciones* "
            "de la app web.\n"
            "A futuro iremos habilitando este flujo por aquí para que sea más rápido."
        )

    # Caso 1: mensaje ultra-genérico -> hacemos preguntas
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

    # Filtro por estado
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

    # Filtro por día (hoy / ayer)
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


def _handler_direccion_basura(usuario: CustomUser) -> str:
    """
    Llama al helper de services_tecnico que lee BOT_GZ_URL_BASURA / BOT_GZ_TEXTO_BASURA.
    """
    return _responder_direccion_basura()


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

    # Si no se detectó intent, probamos algunos casos especiales antes del fallback
    if not intent:
        tokens = set(_tokenize(texto_usuario))

        # ✅ EXTRA: si preguntan "aprobados por el supervisor" (aunque no digan "proyectos")
        if ("supervisor" in tokens) and ({"aprobado", "aprobados", "aprobada", "aprobadas", "aprobacion", "aprobación"} & tokens):
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_mis_proyectos(usuario, texto_usuario)

        # 1) Saludo simple
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

        # 2) Seguimiento de conversación sobre rendiciones:
        #    Ej: primero pregunta por pendientes, luego escribe solo "y rechazadas?"
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

        # ==========================================================
        # 3) SITIOS: si mandan un ID (ID CLARO / ID SITES / ID NEW)
        #    aunque no digan "sitio", igual responder.
        # ==========================================================
        txt_up = (texto_usuario or "").strip().upper()
        site_hit = (
            re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)  # 13_094 o 13 094
            or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)  # CL-13-00421-05 / CL-13-SN-00421-05
            or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)  # MA5694 (u otros)
        )
        if site_hit:
            # Si veníamos hablando de sitios, o si menciona "sitio", o si mandó solo el ID
            if (
                (sesion.ultimo_intent and sesion.ultimo_intent.slug == "info_sitio_id_claro")
                or ("sitio" in tokens or "site" in tokens)
                or _normalize(texto_usuario) == _normalize(site_hit.group(0))
            ):
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_info_sitio_id_claro(texto_usuario)

        # =========================
        # 4) PROYECTOS (PRO)
        # =========================
        # Si veníamos hablando de proyectos y preguntan "estado/status/en ejecución/finalizados/monto/total"
        # evitamos el fallback y damos un resumen pro (counts + total $ + últimos).
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

                # total MMOO (tu parte)
                total_mmoo = Decimal("0")
                for s in list(qs_all):
                    total_mmoo += _mmoo_share_for_user(s, usuario)

                # conteo por estado
                counts = {}
                for s in qs_all.only("estado"):
                    k = s.estado or "—"
                    counts[k] = counts.get(k, 0) + 1

                estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

                msg = "🧭 *Resumen de tus proyectos*\n\n"
                msg += f"• Total: *{total}*\n"
                msg += f"• Tu MMOO total: *{_fmt_clp(total_mmoo)}*\n\n"
                msg += "📌 *Por estado:*\n"
                # orden pro: primero los “activos”
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
                # otros estados que existan
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
                    msg += f"• DU {du} / {id_claro} – {est}: {detalle}\n"

                msg += (
                    "\nSi quieres algo más específico, dime por ejemplo:\n"
                    "• `proyectos asignados` / `proyectos en progreso` / `proyectos finalizados`\n"
                    "• `monto total de mis proyectos`\n"
                )
                return msg

        # Atajo: "asignación" (aunque no haya intent)
        if {"asignacion", "asignaciones", "asignado", "asignados"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_asignacion(usuario, texto_usuario)

        # Atajo: si menciona proyectos/servicios sin intent (evita fallback)
        if {"proyectos", "proyecto", "servicios", "servicio"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            # por ahora usamos el handler PRO completo (robusto)
            return _handler_mis_proyectos(usuario, texto_usuario)

        # =========================
        # 5) PRODUCCIÓN (INTEGRADO)
        # =========================

        # 5.1) Seguimiento de conversación sobre producción:
        #      si veníamos hablando de producción y el usuario dice solo "agosto", "mes anterior", etc.
        if sesion.ultimo_intent and sesion.ultimo_intent.slug in ["mi_produccion_hasta_hoy"]:
            if (
                {"mes", "anterior", "pasado", "este", "actual", "produccion", "producción", "hoy", "fecha"} & tokens
                or any(t in _MESES for t in tokens)
            ):
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_mi_produccion(usuario, texto_usuario)

        # 5.2) Atajo por keywords:
        #      si NO hubo intent pero el texto menciona producción, lo mandamos al handler igual.
        if {"produccion", "producción"} & tokens:
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            return _handler_mi_produccion(usuario, texto_usuario)

        # Si nada de lo anterior aplica -> fallback estándar
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
        return _handler_mi_contrato(usuario, texto_usuario)

    if slug == "mi_produccion_hasta_hoy":
        return _handler_mi_produccion(usuario, texto_usuario)

    if slug == "info_sitio_id_claro":
        return _handler_info_sitio_id_claro(texto_usuario)

    if slug == "mis_proyectos_pendientes":
        # usamos el handler PRO completo (robusto)
        return _handler_mis_proyectos(usuario, texto_usuario)

    if slug == "mis_proyectos_rechazados":
        # ✅ IMPORTANTE: mantengo firma original (solo usuario) para no romper nada
        return _handler_mis_proyectos_rechazados(usuario)

    # Tanto para "ayuda_rendicion_gastos" como para "mis_rendiciones_pendientes"
    # usamos el mismo handler que entiende pendientes/aprobadas/rechazadas/hoy/ayer.
    if slug == "ayuda_rendicion_gastos":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "mis_rendiciones_pendientes":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "direccion_basura":
        return _handler_direccion_basura(usuario)

    if slug == "mi_asignacion":
        return _handler_asignacion(usuario, texto_usuario)

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
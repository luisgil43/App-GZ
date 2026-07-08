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
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento, Proyecto, TipoGasto
from liquidaciones.models import Liquidacion
from operaciones.models import (EvidenciaFoto, RequisitoFoto, ServicioCotizado,
                                SesionFotos, SesionFotoTecnico, SitioMovil)
from rrhh.models import ContratoTrabajo, CronogramaPago, DocumentoTrabajador
from usuarios.models import CustomUser

from .ai_engine import ai_suggest_intent
from .ai_humanizer import humanize_bot_response
from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample
from .permissions import user_can_use_bot_intent
from .services_ruta import (es_intencion_planificar_ruta,
                            procesar_planificacion_ruta, ruta_get)
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
    2) refuerzo por palabras clave
    3) ayuda/menú se maneja como respuesta directa en run_intent
    """
    norm = _normalize(texto)
    user_tokens = set(_tokenize(texto))

    if not user_tokens and not norm:
        return None, 0.0

    # Ayuda / menú no necesita BotIntent en BD.
    # Lo resolveremos en run_intent cuando no haya intent.
    frases_ayuda = {
        "ayuda",
        "menu",
        "menú",
        "opciones",
        "comandos",
        "que puedo hacer",
        "qué puedo hacer",
        "que cosas puedo hacer",
        "qué cosas puedo hacer",
        "que cosas puedo hacer en este bot",
        "qué cosas puedo hacer en este bot",
        "que hace este bot",
        "qué hace este bot",
        "para que sirve este bot",
        "para qué sirve este bot",
        "funciones",
        "funcionalidades",
    }

    if norm in {_normalize(x) for x in frases_ayuda}:
        return None, 0.0

    examples_qs = BotTrainingExample.objects.filter(activo=True).select_related(
        "intent"
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

    # =========================
    # Liquidaciones
    # =========================
    if {"liquidacion", "liquidaciones", "sueldo"} & user_tokens:
        add_keyword_candidate("mis_liquidaciones", 0.9)

    # =========================
    # Contrato / anexos
    # =========================
    if {"contrato", "contratos", "anexo", "anexos"} & user_tokens:
        add_keyword_candidate("mi_contrato_vigente", 0.9)

    # =========================
    # Producción
    # =========================
    if {"produccion", "producción"} & user_tokens:
        add_keyword_candidate("mi_produccion_hasta_hoy", 0.9)

    # =========================
    # Asignación / pega
    # =========================
    if (
        {
            "asignacion",
            "asignación",
            "asignaciones",
            "asignado",
            "asignados",
            "asignada",
            "asignadas",
        }
        & user_tokens
        or {"pega", "trabajo", "sitio"} & user_tokens
        and {"tengo", "voy", "donde", "dónde"} & user_tokens
        or "donde tengo que ir" in norm
        or "dónde tengo que ir" in norm
        or "que pega tengo" in norm
        or "qué pega tengo" in norm
        or "que sitio tengo" in norm
        or "qué sitio tengo" in norm
    ):
        add_keyword_candidate("mi_asignacion", 0.95)

    # =========================
    # Proyectos
    # =========================
    if {"proyectos", "proyecto", "servicios", "servicio"} & user_tokens:
        if {"rechazados", "rechazado", "rechazadas", "rechazada"} & user_tokens:
            add_keyword_candidate("mis_proyectos_rechazados", 0.85)
        else:
            add_keyword_candidate("mis_proyectos_pendientes", 0.8)

    if {"proceso", "ejecucion", "ejecución", "progreso"} & user_tokens:
        add_keyword_candidate("mis_proyectos_pendientes", 0.75)

    if {"rechazados", "rechazado", "rechazadas", "rechazada"} & user_tokens:
        add_keyword_candidate("mis_proyectos_rechazados", 0.8)

    # =========================
    # Rendiciones
    # =========================
    if {"rendicion", "rendición", "rendiciones", "gasto", "gastos"} & user_tokens:
        add_keyword_candidate("mis_rendiciones_pendientes", 0.85)

    # =========================
    # Basura
    # =========================
    if {"basura", "residuos", "desechos", "botar", "tirar"} & user_tokens:
        add_keyword_candidate("direccion_basura", 0.9)


    # =========================
    # Sitio
    # =========================
    if {"sitio", "site"} & user_tokens:
        add_keyword_candidate("info_sitio_id_claro", 0.7)

    txt_up = (texto or "").strip().upper()
    if (
        re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
        or re.search(r"\bCL-\d{2}(?:-[A-Z]{2,3}-)?\d{5}-\d{2}\b", txt_up)
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


def _resolver_intent_por_contexto(
    texto: str,
    *,
    sesion: Optional[BotSession],
) -> Tuple[Optional[BotIntent], float, dict]:
    """
    Resuelve respuestas cortas usando el contexto conversacional.

    Esto evita errores como:
    - Bot pregunta por producción
    - Usuario responde: "este mes"
    - La IA lo manda a liquidaciones

    También permite continuidad:
    - "cual filtro?"
    - "aprobación supervisor"
    - "finalización"
    - "creación"
    """
    texto = (texto or "").strip()
    norm = _normalize(texto)
    tokens = set(_tokenize(texto))

    meta = {
        "context_used": False,
        "context_reason": "",
        "context_last_intent": None,
    }

    if not texto or not sesion or not sesion.ultimo_intent:
        return None, 0.0, meta

    ultimo_slug = sesion.ultimo_intent.slug
    meta["context_last_intent"] = ultimo_slug

    # =========================
    # Producción: respuestas cortas / continuidad
    # =========================
    if ultimo_slug == "mi_produccion_hasta_hoy":
        es_rango = bool(_parse_rango_fechas(texto))
        es_mes_nombre = any(t in _MESES for t in tokens)
        es_mes_anio = bool(_parse_mes_produccion(texto))

        frases_produccion = {
            "este mes",
            "mes actual",
            "actual",
            "este",
            "mes anterior",
            "mes pasado",
            "anterior",
            "pasado",
            "hoy",
            "hasta hoy",
            "a la fecha",
            "fecha",
            "cual filtro",
            "cuál filtro",
            "que filtro",
            "qué filtro",
            "cual filtro usar",
            "cuál filtro usar",
            "que fecha",
            "qué fecha",
            "que fecha uso",
            "qué fecha uso",
            "cual fecha",
            "cuál fecha",
            "explicame el filtro",
            "explícame el filtro",
            "no entiendo el filtro",
            "creacion",
            "creación",
            "fecha creacion",
            "fecha creación",
            "aprobacion",
            "aprobación",
            "aprobacion supervisor",
            "aprobación supervisor",
            "fecha aprobacion",
            "fecha aprobación",
            "finalizacion",
            "finalización",
            "fecha finalizacion",
            "fecha finalización",
        }

        tokens_produccion = {
            "mes",
            "actual",
            "este",
            "anterior",
            "pasado",
            "hoy",
            "fecha",
            "fechas",
            "produccion",
            "producción",
            "hasta",
            "filtro",
            "filtros",
            "campo",
            "calculo",
            "cálculo",
            "creacion",
            "creación",
            "creado",
            "creados",
            "aprobacion",
            "aprobación",
            "aprobado",
            "aprobados",
            "supervisor",
            "finalizacion",
            "finalización",
            "finalizado",
            "finalizados",
            "terminado",
            "terminados",
        }

        if (
            norm in frases_produccion
            or es_rango
            or es_mes_nombre
            or es_mes_anio
            or (tokens and tokens <= tokens_produccion)
            or ({"filtro", "filtros", "fecha", "fechas", "campo"} & tokens)
            or (
                {
                    "creacion",
                    "creación",
                    "aprobacion",
                    "aprobación",
                    "finalizacion",
                    "finalización",
                }
                & tokens
            )
        ):
            meta["context_used"] = True
            meta["context_reason"] = (
                "Respuesta corta resuelta como continuación de producción."
            )
            return sesion.ultimo_intent, 1.0, meta

    # =========================
    # Liquidaciones: respuestas cortas
    # =========================
    if ultimo_slug == "mis_liquidaciones":
        es_mes_nombre = any(t in _MESES for t in tokens)
        es_mes_anio = bool(_parse_mes_anio_desde_texto(texto))
        es_ultimas = bool(_parse_ultimas_n_desde_texto(texto))

        frases_liquidacion = {
            "este mes",
            "mes actual",
            "mes anterior",
            "mes pasado",
            "anterior",
            "pasado",
            "ultimas",
            "ultimos",
            "últimas",
            "últimos",
            "las ultimas",
            "las últimas",
            "mis ultimas",
            "mis últimas",
        }

        tokens_liquidacion = {
            "mes",
            "actual",
            "este",
            "anterior",
            "pasado",
            "ultimas",
            "ultimos",
            "liquidacion",
            "liquidaciones",
        }

        if (
            norm in frases_liquidacion
            or es_mes_nombre
            or es_mes_anio
            or es_ultimas
            or (tokens and tokens <= tokens_liquidacion)
        ):
            meta["context_used"] = True
            meta["context_reason"] = (
                "Respuesta corta resuelta como continuación de liquidaciones."
            )
            return sesion.ultimo_intent, 1.0, meta

    # =========================
    # Rendiciones: respuestas cortas
    # =========================
    if ultimo_slug in ["mis_rendiciones_pendientes", "ayuda_rendicion_gastos"]:
        tokens_rendiciones = {
            "pendientes",
            "pendiente",
            "aprobadas",
            "aprobada",
            "aprobados",
            "aprobado",
            "rechazadas",
            "rechazada",
            "rechazados",
            "rechazado",
            "hoy",
            "ayer",
        }

        if tokens and tokens <= tokens_rendiciones:
            meta["context_used"] = True
            meta["context_reason"] = (
                "Respuesta corta resuelta como continuación de rendiciones."
            )
            return sesion.ultimo_intent, 1.0, meta

    # =========================
    # Info sitio: si el usuario responde solo con ID
    # =========================
    if ultimo_slug == "info_sitio_id_claro":
        txt_up = texto.strip().upper()
        site_hit = (
            re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
            or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)
            or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)
        )

        if site_hit:
            meta["context_used"] = True
            meta["context_reason"] = (
                "ID de sitio resuelto como continuación de info_sitio_id_claro."
            )
            return sesion.ultimo_intent, 1.0, meta

    # =========================
    # Asignación: respuestas cortas
    # =========================
    if ultimo_slug == "mi_asignacion":
        frases_asignacion = {
            "hoy",
            "para hoy",
            "de hoy",
            "asignacion",
            "asignación",
            "pega",
            "mi pega",
            "tengo pega",
            "donde voy",
            "dónde voy",
            "donde tengo que ir",
            "dónde tengo que ir",
            "que sitio tengo",
            "qué sitio tengo",
        }

        tokens_asignacion = {
            "hoy",
            "asignacion",
            "asignación",
            "pega",
            "trabajo",
            "sitio",
            "asignado",
            "asignada",
            "donde",
            "dónde",
            "voy",
        }

        if norm in frases_asignacion or (tokens and tokens <= tokens_asignacion):
            meta["context_used"] = True
            meta["context_reason"] = (
                "Respuesta corta resuelta como continuación de asignación."
            )
            return sesion.ultimo_intent, 1.0, meta

    return None, 0.0, meta


def detect_intent_with_ai_fallback(
    texto: str,
    *,
    usuario: Optional[CustomUser],
    contexto: str = "tecnico",
) -> Tuple[Optional[BotIntent], float, dict]:
    """
    Detección de intent con IA como motor principal.

    Flujo:
    1. Si hay usuario vinculado y la IA está activa, la IA intenta clasificar primero.
    2. Django valida que el intent exista y que el usuario pueda usarlo.
    3. Si la IA no puede clasificar, cae al motor tradicional.
    4. La IA NO consulta datos, NO decide permisos y NO responde al usuario.
    """

    texto = (texto or "").strip()

    ai_meta = {
        "ai_used": False,
        "ai_result": None,
        "traditional_intent": None,
        "traditional_confidence": 0.0,
        "ai_first": True,
    }

    min_conf = getattr(settings, "BOT_GZ_AI_MIN_CONFIDENCE", 0.65)

    # =========================
    # 1) IA primero
    # =========================
    if texto and usuario is not None and getattr(settings, "BOT_GZ_AI_ENABLED", False):
        try:
            ai_result = ai_suggest_intent(
                texto_usuario=texto,
                usuario=usuario,
                contexto=contexto,
            )

            ai_meta["ai_used"] = True
            ai_meta["ai_result"] = ai_result

            if ai_result.get("ok"):
                ai_slug = ai_result.get("intent_slug")

                try:
                    ai_conf = float(ai_result.get("confidence") or 0)
                except Exception:
                    ai_conf = 0.0

                if ai_slug and ai_conf >= min_conf:
                    # Seguridad final: Django valida permisos.
                    if user_can_use_bot_intent(usuario, ai_slug):
                        ai_intent = BotIntent.objects.filter(
                            slug=ai_slug,
                            activo=True,
                        ).first()

                        if ai_intent:
                            return ai_intent, ai_conf, ai_meta

                        ai_meta["ai_intent_not_found"] = ai_slug
                    else:
                        ai_meta["ai_blocked_by_permission"] = True

        except Exception as e:
            logger.exception("GZ Bot AI principal falló")
            ai_meta["ai_error"] = str(e)

    # =========================
    # 2) Motor tradicional como respaldo
    # =========================
    intent, confianza = detect_intent_from_text(texto, scope=contexto)

    ai_meta["traditional_intent"] = intent.slug if intent else None
    ai_meta["traditional_confidence"] = confianza

    if intent and confianza >= min_conf:
        return intent, confianza, ai_meta

    return None, 0.0, ai_meta


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

# ===================== PRODUCCIÓN BOT =====================

_BOT_EST_PROD = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
_BOT_AJ_POS = {"ajuste_bono", "ajuste_adelanto"}
_BOT_AJ_NEG = {"ajuste_descuento"}
_BOT_ESTADOS_PROD_Y_AJUSTES = _BOT_EST_PROD | _BOT_AJ_POS | _BOT_AJ_NEG


def _bot_monto_firmado_por_estado(monto, estado: str) -> Decimal:
    base = Decimal(str(monto or 0))

    if estado in _BOT_AJ_NEG:
        return -abs(base)

    if estado in _BOT_AJ_POS:
        return abs(base)

    return base


def _bot_month_aliases(month: str) -> list[str]:
    s = (month or "").strip()
    if not s:
        return []

    meses = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]

    m = re.match(r"^(\d{4})[-/](\d{1,2})$", s)
    if not m:
        return [s]

    y = int(m.group(1))
    mm = int(m.group(2))

    if not 1 <= mm <= 12:
        return [s]

    nombre = meses[mm - 1]
    mm2 = str(mm).zfill(2)

    return [
        f"{y}-{mm2}",
        f"{y}/{mm}",
        f"{mm}-{y}",
        f"{mm2}-{y}",
        f"{mm}/{y}",
        f"{nombre} {y}",
        f"{nombre.lower()} {y}",
        f"{nombre} de {y}",
        f"{nombre.lower()} de {y}",
    ]


def _bot_yyyy_mm_from_date(d) -> str:
    if hasattr(d, "date"):
        d = d.date()
    return f"{d.year:04d}-{d.month:02d}"


def _bot_mes_label(month: str) -> str:
    meses = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]

    try:
        y = int(month[:4])
        m = int(month[5:7])
        return f"{meses[m]} {y}"
    except Exception:
        return month


def _bot_responder_produccion_mes(usuario: CustomUser, month: str) -> str:
    aliases = _bot_month_aliases(month)

    cond_mes = Q()
    for a in aliases:
        cond_mes |= Q(mes_produccion__icontains=a)

    qs = (
        ServicioCotizado.objects
        .filter(
            estado__in=_BOT_ESTADOS_PROD_Y_AJUSTES,
            trabajadores_asignados=usuario,
        )
        .filter(cond_mes)
        .prefetch_related("trabajadores_asignados")
        .order_by("-fecha_aprobacion_supervisor", "-id")
    )

    total = Decimal("0.00")
    aprobados = Decimal("0.00")
    ajustes = Decimal("0.00")
    detalles = []

    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        if not asignados:
            continue

        firmado = _bot_monto_firmado_por_estado(s.monto_mmoo, s.estado)
        parte = (Decimal(str(firmado)) / len(asignados)).quantize(Decimal("0.01"))

        total += parte

        if s.estado in _BOT_EST_PROD:
            aprobados += parte
        else:
            ajustes += parte

        du = s.du or "—"
        id_ref = s.id_claro or getattr(s, "id_new", None) or "—"
        tarea = (s.detalle_tarea or "").strip()

        if len(tarea) > 55:
            tarea = tarea[:52] + "…"

        detalles.append({
            "du": du,
            "id_ref": id_ref,
            "estado": s.estado,
            "tarea": tarea,
            "monto": parte,
        })

    label = _bot_mes_label(month)

    if not detalles:
        return (
            f"📊 *Producción de {label}*\n\n"
            "No encontré producción aprobada ni ajustes asociados a tu usuario para ese mes.\n\n"
            "Recuerda: la producción entra cuando el proyecto está aprobado por supervisor "
            "o en los estados posteriores de pago."
        )

    msg = f"📊 *Producción de {label}*\n\n"
    msg += f"✅ Total producción/ajustes: *{_fmt_clp(total)}*\n"
    msg += f"• Producción aprobada: {_fmt_clp(aprobados)}\n"
    msg += f"• Ajustes: {_fmt_clp(ajustes)}\n\n"

    msg += "Detalle:\n"

    for d in detalles[:15]:
        estado = d["estado"]

        if estado == "ajuste_bono":
            estado_txt = "Bono"
        elif estado == "ajuste_adelanto":
            estado_txt = "Adelanto"
        elif estado == "ajuste_descuento":
            estado_txt = "Descuento"
        else:
            estado_txt = "Producción aprobada"

        msg += (
            f"• DU `{d['du']}` / `{d['id_ref']}` – {estado_txt} – "
            f"{_fmt_clp(d['monto'])}"
        )

        if d["tarea"]:
            msg += f" – {d['tarea']}"

        msg += "\n"

    if len(detalles) > 15:
        msg += f"\nMostrando 15 de {len(detalles)} registros."

    return msg.strip()

def responder_produccion_rango(usuario, date_from, date_to, *, incluir_estados=None):
    """
    Respuesta para rangos/meses que todavía no tienen cálculo exacto conectado.

    Importante:
    - NO preguntamos por filtro de fecha.
    - NO desviamos al usuario a un flujo que todavía no calcula nada.
    - Para "este mes", _handler_mi_produccion usa directamente
      _responder_produccion_hasta_hoy(usuario).
    """
    return (
        f"📊 *Producción por rango*\n"
        f"Periodo solicitado: *{date_from.strftime('%d-%m-%Y')} al {date_to.strftime('%d-%m-%Y')}*\n\n"
        "Por ahora el cálculo exacto por rango todavía está en desarrollo.\n\n"
        "Actualmente puedo mostrarte la producción acumulada disponible hasta hoy.\n"
        "Para verla, escribe:\n"
        "• `mi producción hasta hoy`\n"
        "• `producción este mes`\n"
        "• `mi producción de este mes`"
    )


def _handler_mi_produccion(usuario: CustomUser, texto_usuario: str) -> str:
    """
    Producción del bot usando la misma lógica del módulo producción:
    - aprobado_supervisor / aprobado_pm / aprobado_finanzas
    - ajuste_bono / ajuste_adelanto / ajuste_descuento
    - prorrateo por cantidad de técnicos asignados.
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tu propia producción*.\n"
            "No tengo permiso para entregar información de producción de otros compañeros."
        )

    tokens = set(_tokenize(texto_usuario))
    hoy = timezone.localdate()
    norm = _normalize(texto_usuario)

    current_month = _bot_yyyy_mm_from_date(hoy)

    if (
        {"filtro", "filtros", "campo"} & tokens
        or "cual filtro" in norm
        or "cuál filtro" in norm
        or "que filtro" in norm
        or "qué filtro" in norm
    ):
        return (
            "📊 *Producción*\n\n"
            "No necesitas elegir filtro.\n"
            "El bot calcula tu producción usando la misma base del módulo de producción:\n\n"
            "• Proyectos aprobados por supervisor\n"
            "• Proyectos aprobados para pago\n"
            "• Bonos, adelantos y descuentos\n"
            "• Monto prorrateado si hay varios técnicos\n\n"
            "Puedes pedirlo así:\n"
            "• `producción este mes`\n"
            "• `mi producción de este mes`\n"
            "• `producción mes anterior`"
        )

    if norm in {"produccion", "producción", "mi produccion", "mi producción"}:
        return (
            "📊 ¿Qué producción necesitas?\n\n"
            "Puedes pedirme:\n"
            "• `producción este mes`\n"
            "• `mi producción de este mes`\n"
            "• `producción mes anterior`\n"
            "• `producción julio 2026`"
        )

    if (
        {"hoy", "ahora"} & tokens
        or "hasta hoy" in norm
        or "a la fecha" in norm
        or "este mes" in norm
        or "mes actual" in norm
        or norm in {"este", "actual"}
        or ("mes" in tokens and ({"este", "actual"} & tokens))
    ):
        return _bot_responder_produccion_mes(usuario, current_month)

    if (
        "mes anterior" in norm
        or "mes pasado" in norm
        or norm in {"anterior", "pasado"}
        or ("mes" in tokens and ({"anterior", "pasado"} & tokens))
    ):
        year = hoy.year
        month = hoy.month - 1

        if month == 0:
            month = 12
            year -= 1

        target_month = f"{year:04d}-{month:02d}"
        return _bot_responder_produccion_mes(usuario, target_month)

    parsed_mes = _parse_mes_produccion(texto_usuario)
    if parsed_mes:
        mes, anio = parsed_mes
        target_month = f"{anio:04d}-{mes:02d}"
        return _bot_responder_produccion_mes(usuario, target_month)

    rango = _parse_rango_fechas(texto_usuario)
    if rango:
        d1, d2 = rango
        if d2 < d1:
            d1, d2 = d2, d1

        return (
            f"📊 *Producción por rango*\n"
            f"Periodo solicitado: *{d1.strftime('%d-%m-%Y')} al {d2.strftime('%d-%m-%Y')}*\n\n"
            "Por ahora el bot calcula producción por mes de producción.\n"
            "Puedes pedirme por ejemplo:\n"
            "• `producción julio 2026`\n"
            "• `producción este mes`"
        )

    return (
        "📊 ¿Qué producción necesitas?\n\n"
        "Puedes pedirme:\n"
        "• `producción este mes`\n"
        "• `mi producción de este mes`\n"
        "• `producción mes anterior`\n"
        "• `producción julio 2026`"
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
    "finalizados": {"en_revision_supervisor"},
    "rechazados": {"rechazado_supervisor"},
}

_PROJ_BUCKET_LABEL = {
    "asignados": "Asignados",
    "en_ejecucion": "En ejecución",
    "revision_supervisor": "En revisión supervisor",
    "aprobado_supervisor": "Aprobado por supervisor",
    "finalizados": "Enviados a revisión supervisor",
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
    norm = _normalize(texto_usuario)

    base = ServicioCotizado.objects.filter(trabajadores_asignados=usuario).annotate(
        n_tecs=Count("trabajadores_asignados", distinct=True)
    )

    mf = _project_month_filter(texto_usuario)
    mes_label = None
    if mf:
        start, end, mes_label = mf
        base = base.filter(
            fecha_creacion__date__gte=start, fecha_creacion__date__lte=end
        )

    pid = _extract_project_id(texto_usuario)

    # =========================
    # Detalle por ID / DU
    # =========================
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
                "• `13_094` ID Claro\n"
                "• `CL-13-00421-05` ID Sites\n"
                "• `CL-13-SN-00421-05` ID New\n"
                "• o el DU"
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
            msg += f"\n🛠️ Tarea:\n{s.detalle_tarea.strip()}\n"

        msg += "\nSi quieres finalizar un proyecto en proceso, dime: `quiero finalizar un proyecto`."
        return msg.strip()

    # =========================
    # Pregunta: ¿cómo cotizaciones?
    # =========================
    if {"cotizacion", "cotizaciones", "cotizado", "cotizada"} & tokens:
        return (
            "📊 *Sobre el cálculo de producción*\n\n"
            "La producción que te muestra el bot por ahora es un *estimado* basado en los servicios "
            "asignados a tu usuario y los montos de mano de obra registrados en cada servicio.\n\n"
            "Por eso el mensaje dice que está basado en *cotizaciones y costos de mano de obra*.\n\n"
            "En simple:\n"
            "• Si el servicio está aprobado por supervisor, se suma como producción aprobada.\n"
            "• Si está pendiente de aprobación, queda separado como pendiente.\n"
            "• Si el servicio tiene varios técnicos, el monto se reparte según la lógica disponible del sistema.\n\n"
            "Más adelante podemos compararlo contra pagos mensuales cerrados para mayor precisión."
        )

    # =========================
    # Selección de estado específico
    # =========================
    quiere_lista = bool(
        {"cuales", "cuáles", "lista", "listar", "detalle", "ver", "mostrar", "dime"}
        & tokens
    )

    estado_objetivo = None
    titulo_estado = None

    if {"proceso", "ejecucion", "ejecución", "progreso"} & tokens:
        estado_objetivo = ["en_progreso"]
        titulo_estado = "proyectos en proceso / ejecución"

    elif {"asignado", "asignados", "asignada", "asignadas"} & tokens:
        estado_objetivo = ["asignado"]
        titulo_estado = "proyectos asignados"

    elif {"revision", "revisión"} & tokens and "supervisor" in tokens:
        estado_objetivo = ["en_revision_supervisor"]
        titulo_estado = "proyectos en revisión supervisor"

    elif {
        "aprobado",
        "aprobados",
        "aprobada",
        "aprobadas",
        "aprobacion",
        "aprobación",
    } & tokens:
        estado_objetivo = ["aprobado_supervisor"]
        titulo_estado = "proyectos aprobados por supervisor"

    elif {
        "finalizado",
        "finalizados",
        "finalice",
        "finalicé",
        "termine",
        "terminé",
    } & tokens:
        estado_objetivo = ["en_revision_supervisor"]
        titulo_estado = "proyectos enviados a revisión supervisor"

    elif {"rechazado", "rechazados", "rechazada", "rechazadas"} & tokens:
        estado_objetivo = ["rechazado_supervisor"]
        titulo_estado = "proyectos rechazados"

    if estado_objetivo:
        qs = base.filter(estado__in=estado_objetivo).order_by("-fecha_creacion")
        total = qs.count()
        estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))

        periodo = f" del mes {mes_label}" if mes_label else " actuales"

        if total == 0:
            return f"✅ No tienes {titulo_estado}{periodo}."

        servicios = list(qs[:15])

        msg = f"📌 *Tus {titulo_estado}{periodo}*\n\n"
        msg += f"Total: *{total}*\n\n"

        for s in servicios:
            du = s.du or "—"
            id_ref = s.id_claro or (getattr(s, "id_new", None) or "—")
            est = estados_dict.get(s.estado, s.estado)
            det = (s.detalle_tarea or "").strip()

            if len(det) > 90:
                det = det[:87] + "…"

            msg += f"• DU `{du}` / `{id_ref}` – {est}"
            if det:
                msg += f" – {det}"
            msg += "\n"

        if total > len(servicios):
            msg += f"\nMostrando {len(servicios)} de {total}."

        if estado_objetivo == ["en_progreso"]:
            msg += (
                "\n\nSi quieres finalizar uno o varios, dime:\n"
                "• `quiero finalizar un proyecto`"
            )

        return msg.strip()

    # =========================
    # Mapa
    # =========================
    if {"mapa", "maps", "google", "ubicacion", "ubicación"} & tokens:
        bucket_keys = _pick_project_buckets(tokens)
        estados = set().union(
            *(_PROJ_BUCKETS[k] for k in bucket_keys if k in _PROJ_BUCKETS)
        )
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
            msg += f"Ruta sugerida usando tu ubicación actual:\n{ruta}\n\n"
        else:
            msg += "No pude armar una ruta única. Te dejo links individuales:\n\n"

        if links:
            msg += "\n".join(links[:12])
            if len(links) > 12:
                msg += f"\n… y {len(links) - 12} más."
        else:
            msg += "No encontré coordenadas en SitioMovil para tus proyectos."

        return msg.strip()

    # =========================
    # Total monto
    # =========================
    if {"monto", "montos", "total", "suma", "sumo", "cuanto", "cuánto"} & tokens:
        bucket_keys = _pick_project_buckets(tokens)
        estados = set().union(
            *(_PROJ_BUCKETS[k] for k in bucket_keys if k in _PROJ_BUCKETS)
        )
        qs = base.filter(estado__in=list(estados)).order_by("-fecha_creacion")

        servicios = list(qs)
        total_proy = len(servicios)

        total_mmoo = Decimal("0")
        for s in servicios:
            total_mmoo += _mmoo_share_for_user(s, usuario)

        extra = f" del mes {mes_label}" if mes_label else ""
        labels = ", ".join(_PROJ_BUCKET_LABEL[k] for k in bucket_keys)

        return (
            f"💰 *Total proyectos / montos{extra}*\n\n"
            f"Grupos: *{labels}*\n"
            f"• Proyectos: *{total_proy}*\n"
            f"• Tu MMOO total: *{_fmt_clp(total_mmoo)}*\n\n"
            "Si quieres el detalle de uno, dime: `monto proyecto 13_913`."
        )

    # =========================
    # Resumen general
    # =========================
    estados_dict = dict(getattr(ServicioCotizado, "ESTADOS", []))
    bucket_keys = [
        "asignados",
        "en_ejecucion",
        "revision_supervisor",
        "aprobado_supervisor",
        "finalizados",
        "rechazados",
    ]

    msg = "📌 *Resumen de tus proyectos"
    msg += f" del mes {mes_label}" if mes_label else " actuales"
    msg += "*\n\n"

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
        msg += "Últimos proyectos recientes:\n"

        for s in ejemplos:
            du = s.du or "—"
            idc = s.id_claro or (getattr(s, "id_new", None) or "—")
            est = estados_dict.get(s.estado, s.estado)
            det = (s.detalle_tarea or "").strip()

            if len(det) > 60:
                det = det[:57] + "…"

            msg += f"• DU `{du}` / `{idc}` – {est}"
            if det:
                msg += f" – {det}"
            msg += "\n"

    msg += (
        "\n📲 Puedes pedirme:\n"
        "• `cuáles están en proceso`\n"
        "• `cuáles proyectos tengo asignados ahora`\n"
        "• `cuáles proyectos finalicé este mes`\n"
        "• `proyectos aprobados por supervisor`\n"
        "• `quiero finalizar un proyecto`\n"
        "• `mapa de mis proyectos asignados`"
    )

    return msg.strip()


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

    return _responder_direccion_basura()

# ===================== WIZARD: Finalizar proyectos =====================

_FINALIZAR_PROY_TTL = 60 * 20


def _finalizar_proy_key(chat_id: str) -> str:
    return f"gzbot:finalizar_proyectos:{str(chat_id)}"


def _finalizar_proy_get(chat_id: str) -> Optional[dict]:
    return cache.get(_finalizar_proy_key(chat_id))


def _finalizar_proy_set(chat_id: str, data: dict) -> None:
    cache.set(_finalizar_proy_key(chat_id), data, timeout=_FINALIZAR_PROY_TTL)


def _finalizar_proy_clear(chat_id: str) -> None:
    cache.delete(_finalizar_proy_key(chat_id))


def _bot_get_or_create_sesion_fotos(servicio: ServicioCotizado) -> SesionFotos:
    sesion_fotos, _ = SesionFotos.objects.get_or_create(
        servicio=servicio,
        defaults={"estado": "asignado"},
    )
    return sesion_fotos


def _bot_estado_legible_servicio(estado: str) -> str:
    estados = dict(getattr(ServicioCotizado, "ESTADOS", []))
    return estados.get(estado, estado or "—")


def _bot_nombre_usuario(u: CustomUser) -> str:
    return (u.get_full_name() or u.username or f"Usuario {u.id}").strip()


def _bot_parse_indices_finalizar(texto: str, max_n: int) -> list[int]:
    raw = _normalize(texto or "")
    nums = re.findall(r"\b\d{1,3}\b", raw)

    out = []
    seen = set()

    for n in nums:
        try:
            i = int(n)
        except Exception:
            continue

        if 1 <= i <= max_n and i not in seen:
            seen.add(i)
            out.append(i)

    return out


def _bot_tecnico_acepto(asg: SesionFotoTecnico) -> bool:
    if getattr(asg, "aceptado_en", None):
        return True

    return asg.estado in {
        "en_proceso",
        "en_revision_supervisor",
        "aprobado_supervisor",
        "aprobado_pm",
    }


def _bot_tecnico_finalizo(asg: SesionFotoTecnico) -> bool:
    if getattr(asg, "finalizado_en", None):
        return True

    return asg.estado in {
        "en_revision_supervisor",
        "aprobado_supervisor",
        "aprobado_pm",
    }


def _bot_sync_asignaciones_servicio(
    servicio: ServicioCotizado,
) -> tuple[SesionFotos, dict[int, SesionFotoTecnico]]:
    sesion_fotos = _bot_get_or_create_sesion_fotos(servicio)

    asignados_ids = list(servicio.trabajadores_asignados.values_list("id", flat=True))

    existentes = {
        a.tecnico_id: a
        for a in sesion_fotos.asignaciones.filter(tecnico_id__in=asignados_ids)
    }

    for tecnico_id in asignados_ids:
        if tecnico_id not in existentes:
            existentes[tecnico_id] = SesionFotoTecnico.objects.create(
                sesion=sesion_fotos,
                tecnico_id=tecnico_id,
                estado="asignado",
            )

    return sesion_fotos, existentes


def _bot_missing_requisitos_finalizar(sesion_fotos: SesionFotos) -> list[str]:
    def _norm_title_local(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = re.sub(r"\s+", " ", s)
        return s

    canon_by_norm = {}

    qs = RequisitoFoto.objects.filter(
        tecnico_sesion__sesion=sesion_fotos, activo=True
    ).values("id", "titulo", "obligatorio", "orden")

    for r in qs:
        norm = _norm_title_local(r["titulo"])
        b = canon_by_norm.get(norm)

        if not b:
            canon_by_norm[norm] = {
                "id": r["id"],
                "titulo": r["titulo"],
                "obligatorio": r["obligatorio"],
                "orden": r["orden"],
                "ids": {r["id"]},
            }
        else:
            b["ids"].add(r["id"])
            if (r["orden"], r["id"]) < (b["orden"], b["id"]):
                b["id"] = r["id"]
                b["titulo"] = r["titulo"]
                b["obligatorio"] = r["obligatorio"]
                b["orden"] = r["orden"]

    if not canon_by_norm:
        return []

    all_ids = [rid for b in canon_by_norm.values() for rid in b["ids"]]

    ids_with_ev = set(
        EvidenciaFoto.objects.filter(requisito_id__in=all_ids).values_list(
            "requisito_id", flat=True
        )
    )

    missing = []

    for _norm, b in sorted(
        canon_by_norm.items(), key=lambda x: (x[1]["orden"], x[1]["id"])
    ):
        if not b["obligatorio"]:
            continue

        done = any(rid in ids_with_ev for rid in b["ids"])
        if not done:
            missing.append(b["titulo"])

    return missing


def _bot_servicios_en_progreso_para_finalizar(usuario: CustomUser):
    servicios = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado="en_progreso",
        )
        .prefetch_related("trabajadores_asignados", "sesion_fotos__asignaciones")
        .order_by("-fecha_creacion")
    )

    out = []

    for servicio in servicios:
        _sesion_fotos, asignaciones_map = _bot_sync_asignaciones_servicio(servicio)
        mi_asg = asignaciones_map.get(usuario.id)

        if mi_asg and _bot_tecnico_finalizo(mi_asg):
            continue

        out.append(servicio)

    return out


def _bot_fmt_servicio_finalizar_opcion(s: ServicioCotizado, idx: int) -> str:
    du = s.du or "—"
    id_ref = s.id_claro or (getattr(s, "id_new", None) or "—")
    detalle = (s.detalle_tarea or "").strip()

    if len(detalle) > 90:
        detalle = detalle[:87] + "…"

    return f"{idx}) DU `{du}` / `{id_ref}` – {detalle or 'Sin detalle'}"


def _handler_iniciar_finalizar_proyectos(
    usuario: CustomUser,
    sesion: BotSession,
) -> str:
    servicios = list(_bot_servicios_en_progreso_para_finalizar(usuario))[:20]

    if not servicios:
        _finalizar_proy_clear(sesion.chat_id)
        return (
            "✅ No tienes proyectos *en proceso* pendientes por finalizar.\n\n"
            "Puedes revisar tus proyectos con:\n"
            "• `cuáles están en proceso`\n"
            "• `cuáles proyectos tengo asignados ahora`"
        )

    state = {
        "step": "seleccion",
        "servicio_ids": [s.id for s in servicios],
    }
    _finalizar_proy_set(sesion.chat_id, state)

    msg = "✅ *Finalizar proyecto(s)*\n\n"
    msg += "Estos son los proyectos que tienes actualmente *en proceso*:\n\n"

    for idx, s in enumerate(servicios, start=1):
        msg += _bot_fmt_servicio_finalizar_opcion(s, idx) + "\n"

    msg += (
        "\nResponde con el número del proyecto que deseas finalizar.\n"
        "También puedes indicar varios números para finalizar varios a la vez.\n\n"
        "Ejemplos:\n"
        "• `1`\n"
        "• `1, 3`\n"
        "• `1 2 4`\n\n"
        "Si prefieres no continuar, escribe `cancelar`."
    )

    return msg.strip()


def _bot_finalizar_un_servicio_desde_bot(
    *,
    servicio: ServicioCotizado,
    usuario: CustomUser,
) -> tuple[bool, str]:
    if not servicio.trabajadores_asignados.filter(id=usuario.id).exists():
        return False, (
            f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
            "  No estás asignado a este proyecto."
        )

    if servicio.estado != "en_progreso":
        return False, (
            f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
            f"  No está en progreso. Estado actual: {_bot_estado_legible_servicio(servicio.estado)}."
        )

    sesion_fotos, asignaciones_map = _bot_sync_asignaciones_servicio(servicio)
    mi_asg = asignaciones_map.get(usuario.id)

    if not mi_asg:
        return False, (
            f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
            "  No encontré tu asignación fotográfica. Intenta desde la web."
        )

    if not _bot_tecnico_acepto(mi_asg):
        return False, (
            f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
            "  Primero debes aceptar la asignación antes de finalizar."
        )

    missing = _bot_missing_requisitos_finalizar(sesion_fotos)

    if missing:
        return False, (
            f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
            "  No se puede finalizar porque faltan fotos requeridas:\n"
            f"  {', '.join(missing)}"
        )

    now_ = timezone.now()

    with transaction.atomic():
        mi_asg.estado = "en_revision_supervisor"
        mi_asg.finalizado_en = now_
        mi_asg.save(update_fields=["estado", "finalizado_en"])

        servicio.tecnico_finalizo = usuario
        servicio.save(update_fields=["tecnico_finalizo"])

        asignados = list(servicio.trabajadores_asignados.all())
        asignados_ids = [u.id for u in asignados]

        asignaciones = list(
            sesion_fotos.asignaciones.filter(
                tecnico_id__in=asignados_ids
            ).select_related("tecnico")
        )

        pendientes_aceptar = []
        pendientes_finalizar = []

        for asg in asignaciones:
            if not _bot_tecnico_acepto(asg):
                pendientes_aceptar.append(_bot_nombre_usuario(asg.tecnico))
                continue

            if not _bot_tecnico_finalizo(asg):
                pendientes_finalizar.append(_bot_nombre_usuario(asg.tecnico))

        if pendientes_aceptar or pendientes_finalizar:
            sesion_fotos.estado = "en_proceso"
            sesion_fotos.save(update_fields=["estado"])

            msg = (
                f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
                "  ✅ Tu parte fue marcada como finalizada.\n"
                "  El proyecto completo sigue *en progreso* porque falta otro técnico.\n"
            )

            if pendientes_aceptar:
                msg += f"  Falta aceptar: {', '.join(pendientes_aceptar)}\n"

            if pendientes_finalizar:
                msg += f"  Falta finalizar: {', '.join(pendientes_finalizar)}\n"

            return True, msg.strip()

        sesion_fotos.asignaciones.filter(tecnico_id__in=asignados_ids).update(
            estado="en_revision_supervisor",
            finalizado_en=now_,
        )

        sesion_fotos.estado = "en_revision_supervisor"
        sesion_fotos.save(update_fields=["estado"])

        servicio.estado = "en_revision_supervisor"
        servicio.tecnico_finalizo = usuario
        servicio.save(update_fields=["estado", "tecnico_finalizo"])

    return True, (
        f"• DU `{servicio.du}` / `{servicio.id_claro or '—'}`\n"
        "  ✅ Proyecto enviado a *revisión del supervisor*."
    )


def _handler_confirmar_finalizar_proyectos(
    *,
    usuario: CustomUser,
    sesion: BotSession,
    texto_usuario: str,
) -> str:
    state = _finalizar_proy_get(sesion.chat_id)

    if not state:
        return _handler_iniciar_finalizar_proyectos(usuario, sesion)

    norm = _normalize(texto_usuario or "")
    tokens = set(_tokenize(texto_usuario or ""))

    if norm in {"cancelar", "salir", "no", "anular", "no continuar"}:
        _finalizar_proy_clear(sesion.chat_id)
        return "✅ Listo, cancelé la finalización de proyectos."

    servicio_ids = state.get("servicio_ids") or []

    if not servicio_ids:
        _finalizar_proy_clear(sesion.chat_id)
        return "Se perdió la lista de proyectos. Escribe nuevamente: `quiero finalizar un proyecto`."

    indices = _bot_parse_indices_finalizar(texto_usuario, len(servicio_ids))

    # Si no respondió con número y parece que cambió de tema, cancelamos finalizar y respondemos ese tema.
    if not indices:
        cambio_tema = False

        temas = {
            "produccion",
            "producción",
            "liquidacion",
            "liquidaciones",
            "contrato",
            "contratos",
            "rendicion",
            "rendiciones",
            "gasto",
            "gastos",
            "asignacion",
            "asignación",
            "proyectos",
            "proyecto",
            "servicios",
            "servicio",
            "sitio",
            "basura",
            "ruta",
            "mapa",
            "ayuda",
            "menu",
            "menú",
        }

        if temas & tokens or es_intencion_planificar_ruta(texto_usuario):
            cambio_tema = True

        if cambio_tema:
            _finalizar_proy_clear(sesion.chat_id)

            fake_log = BotMessageLog.objects.create(
                sesion=sesion,
                usuario=usuario,
                chat_id=sesion.chat_id,
                direccion="in",
                texto=texto_usuario,
                status="ok",
                meta={"auto_redirect_from": "finalizar_proyectos"},
            )

            resolved = _resolver_texto_generico_sin_intent(
                texto_usuario=texto_usuario,
                usuario=usuario,
                sesion=sesion,
                inbound_log=fake_log,
            )

            if resolved:
                return (
                    "✅ Cancelé la finalización de proyectos y seguí con tu nueva consulta.\n\n"
                    + resolved
                )

        return (
            "No entendí qué proyecto quieres finalizar.\n\n"
            "Responde con el número de la lista.\n"
            "Ejemplo:\n"
            "• `1`\n"
            "• `1, 2`\n\n"
            "O escribe `cancelar`.\n\n"
            "Si quieres hacer otra consulta, también puedes escribir por ejemplo:\n"
            "• `producción`\n"
            "• `liquidación`\n"
            "• `contrato`\n"
            "• `proyectos`\n"
            "• `ayuda`"
        )

    selected_ids = [servicio_ids[i - 1] for i in indices]

    servicios = list(
        ServicioCotizado.objects.filter(
            id__in=selected_ids,
            trabajadores_asignados=usuario,
        ).prefetch_related("trabajadores_asignados", "sesion_fotos__asignaciones")
    )

    by_id = {s.id: s for s in servicios}

    ok_count = 0
    respuestas = []

    for sid in selected_ids:
        servicio = by_id.get(sid)

        if not servicio:
            respuestas.append("• Proyecto no encontrado o ya no está asignado a ti.")
            continue

        ok, msg = _bot_finalizar_un_servicio_desde_bot(
            servicio=servicio,
            usuario=usuario,
        )

        if ok:
            ok_count += 1

        respuestas.append(msg)

    _finalizar_proy_clear(sesion.chat_id)

    titulo = "✅ *Resultado de finalización*"
    if ok_count == 0:
        titulo = "⚠️ *No se pudo finalizar*"

    return (
        f"{titulo}\n\n" + "\n\n".join(respuestas) + "\n\nPuedes revisar ahora con:\n"
        "• `cuáles están en proceso`\n"
        "• `cuáles proyectos envié a revisión supervisor`\n"
        "• `cuáles proyectos tengo asignados ahora`"
    )


# ===================== Router principal de intents =====================

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


def _handler_menu_bot(usuario: Optional[CustomUser] = None) -> str:
    """
    Menú principal del bot.
    Se usa para:
    - ayuda
    - menú
    - qué puedo hacer
    - opciones
    - comandos
    - fallback cuando no entiende
    """
    nombre = ""
    if usuario:
        nombre = usuario.first_name or usuario.get_full_name() or ""
        nombre = nombre.strip()

    saludo = f"👋 Hola {nombre}.\n\n" if nombre else "👋 Hola.\n\n"

    return (
        saludo + "Soy el bot de *GZ Services* y puedo ayudarte con estas opciones:\n\n"
        "🧭 *Asignaciones / trabajos*\n"
        "• `asignación`\n"
        "• `asignación de hoy`\n"
        "• `qué pega tengo`\n"
        "• `dónde tengo que ir`\n\n"
        "🗺️ *Planificación de ruta*\n"
        "• `planifica mi ruta`\n"
        "• `mejor ruta para hoy`\n"
        "• `ordena mis sitios`\n"
        "• `qué sitio hago primero`\n\n"
        "📌 *Proyectos / servicios*\n"
        "• `proyectos`\n"
        "• `cuáles están en proceso`\n"
        "• `cuáles proyectos tengo asignados ahora`\n"
        "• `proyectos aprobados por supervisor`\n"
        "• `cuáles proyectos envié a revisión supervisor`\n"
        "• `mapa de mis proyectos asignados`\n\n"
        "✅ *Finalizar proyectos*\n"
        "• `finalizar`\n"
        "• `quiero finalizar un proyecto`\n"
        "• `finalizar proyectos`\n\n"
        "📊 *Producción*\n"
        "• `producción`\n"
        "• `producción este mes`\n"
        "• `mi producción de este mes`\n"
        "• `producción mes anterior`\n"
        "• `producción julio 2026`\n\n"
        "🧾 *Liquidaciones*\n"
        "• `liquidación`\n"
        "• `liquidaciones`\n"
        "• `mis últimas 3 liquidaciones`\n"
        "• `liquidación de noviembre 2025`\n"
        "• `liquidaciones de julio y septiembre 2025`\n\n"
        "📄 *Contrato / documentos*\n"
        "• `contrato`\n"
        "• `contratos`\n"
        "• `mi contrato vigente`\n"
        "• `mi contrato y sus extensiones`\n"
        "• `mis anexos`\n\n"
        "🧾 *Rendiciones de gastos*\n"
        "• `rendición de gasto`\n"
        "• `rendiciones pendientes`\n"
        "• `rendiciones aprobadas`\n"
        "• `rendiciones rechazadas`\n"
        "• `nueva rendición`\n\n"
        "📡 *Información de sitios*\n"
        "• `sitio 13_094`\n"
        "• `dirección sitio 13_094`\n"
        "• `CL-13-00421-05`\n"
        "• `CL-13-SN-00421-05`\n\n"
        "🗑️ *Basura / retiro de residuos*\n"
        "• `dónde boto la basura`\n"
        "• `dirección basura`\n\n"
        "También puedes escribir:\n"
        "• `ayuda`\n"
        "• `menú`\n"
        "• `qué puedo hacer`\n\n"
        "✅ Usa frases cortas y directas."
    )


def _handler_opciones_liquidaciones(usuario: CustomUser) -> str:
    return (
        "🧾 *Liquidaciones*\n\n"
        "Sí, puedo ayudarte con tus liquidaciones.\n\n"
        "Puedes pedirme por ejemplo:\n"
        "• `mis liquidaciones`\n"
        "• `mis últimas 3 liquidaciones`\n"
        "• `liquidación de noviembre 2025`\n"
        "• `liquidaciones de julio y septiembre 2025`\n\n"
        "Si quieres una específica, dime el mes y año."
    )


def _handler_opciones_contrato(usuario: CustomUser) -> str:
    return (
        "📄 *Contrato / documentos*\n\n"
        "Puedo ayudarte con tus contratos y documentos laborales.\n\n"
        "Puedes pedirme por ejemplo:\n"
        "• `mi contrato vigente`\n"
        "• `contratos`\n"
        "• `mi contrato y sus extensiones`\n"
        "• `mis anexos`\n\n"
        "Por seguridad solo puedo mostrar documentos asociados a tu usuario."
    )


def _handler_opciones_rendiciones(usuario: CustomUser) -> str:
    return (
        "🧾 *Rendiciones de gastos*\n\n"
        "Puedo ayudarte con tus rendiciones.\n\n"
        "Puedes pedirme por ejemplo:\n"
        "• `nueva rendición`\n"
        "• `rendiciones pendientes`\n"
        "• `rendiciones aprobadas`\n"
        "• `rendiciones rechazadas`\n"
        "• `rendiciones pendientes de hoy`\n\n"
        "Para crear una nueva, escribe: `nueva rendición`."
    )


def _handler_opciones_sitio() -> str:
    return (
        "📡 *Información de sitios*\n\n"
        "Para darte la dirección o información del sitio, necesito que me envíes un identificador.\n\n"
        "Ejemplos:\n"
        "• `sitio 13_094`\n"
        "• `dirección sitio 13_094`\n"
        "• `CL-13-00421-05`\n"
        "• `CL-13-SN-00421-05`\n\n"
        "Con eso puedo mostrarte nombre, dirección, comuna, región, acceso y mapa si existen coordenadas."
    )


def _handler_opciones_produccion() -> str:
    return (
        "📊 *Producción*\n\n"
        "Puedo mostrarte tu producción usando la base del módulo de producción.\n\n"
        "Puedes pedirme por ejemplo:\n"
        "• `producción este mes`\n"
        "• `mi producción de este mes`\n"
        "• `producción mes anterior`\n"
        "• `producción julio 2026`"
    )


def _handler_pagos_no_disponible() -> str:
    return (
        "📆 *Pagos / cortes de producción*\n\n"
        "Por ahora esta opción no está disponible desde el bot.\n\n"
        "Puedes revisar la información de pagos o cortes directamente en la plataforma web "
        "o consultar con administración/finanzas."
    )


def _resolver_texto_generico_sin_intent(
    *,
    texto_usuario: str,
    usuario: CustomUser,
    sesion: BotSession,
    inbound_log: BotMessageLog,
) -> Optional[str]:
    """
    Resuelve palabras genéricas del menú aunque la IA o el matcher no hayan detectado intent.

    Ejemplos:
    - liquidacion
    - contratos
    - rendicion de gasto
    - direccion sitio
    - proyectos
    - produccion
    """
    texto_usuario = texto_usuario or ""
    norm = _normalize(texto_usuario)
    tokens = set(_tokenize(texto_usuario))

    def _ok() -> None:
        inbound_log.status = "ok"
        inbound_log.marcar_para_entrenamiento = False
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

    ayuda_frases = {
        "ayuda",
        "menu",
        "menú",
        "opciones",
        "comandos",
        "funciones",
        "funcionalidades",
        "que puedo hacer",
        "qué puedo hacer",
        "que cosas puedo hacer",
        "qué cosas puedo hacer",
        "que cosas puedo hacer en este bot",
        "qué cosas puedo hacer en este bot",
        "que hace este bot",
        "qué hace este bot",
        "para que sirve este bot",
        "para qué sirve este bot",
        "como funciona",
        "cómo funciona",
        "que puedes hacer",
        "qué puedes hacer",
    }

    ayuda_norm = {_normalize(x) for x in ayuda_frases}

    if (
        norm in ayuda_norm
        or {
            "ayuda",
            "menu",
            "menú",
            "opciones",
            "comandos",
            "funciones",
            "funcionalidades",
        }
        & tokens
        or ("puedo" in tokens and "hacer" in tokens)
        or ("puedes" in tokens and "hacer" in tokens)
        or ("sirve" in tokens and "bot" in tokens)
        or _es_saludo(texto_usuario)
    ):
        _ok()
        return _handler_menu_bot(usuario)

    if norm in {"cancelar", "salir", "anular", "no continuar"}:
        _ok()
        return (
            "✅ No tengo ningún flujo activo para cancelar.\n\n"
            "Estas son las opciones que puedes usar ahora:\n\n"
            + _handler_menu_bot(usuario)
        )

    if {"liquidacion", "liquidaciones", "sueldo"} & tokens:
        _ok()

        # Si el usuario solo puso "liquidacion/liquidaciones", respondemos con opciones.
        if tokens <= {"liquidacion", "liquidaciones", "sueldo"}:
            return _handler_opciones_liquidaciones(usuario)

        return _handler_mis_liquidaciones(usuario, texto_usuario)

    if {"contrato", "contratos", "anexo", "anexos", "documento", "documentos"} & tokens:
        _ok()

        if tokens <= {"contrato", "contratos", "documento", "documentos"}:
            return _handler_opciones_contrato(usuario)

        return _handler_mi_contrato(usuario, texto_usuario)

    if {"rendicion", "rendiciones", "gasto", "gastos"} & tokens:
        _ok()

        if tokens <= {"rendicion", "rendiciones", "gasto", "gastos"}:
            return _handler_opciones_rendiciones(usuario)

        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if {"basura", "residuos", "desechos", "botar", "tirar"} & tokens:
        _ok()
        return _handler_direccion_basura(usuario, texto_usuario)

    if {"pago", "pagos", "pagan", "pagar", "corte", "cronograma"} & tokens:
        _ok()
        return _handler_pagos_no_disponible()

    if "sitio" in tokens or "site" in tokens:
        txt_up = texto_usuario.strip().upper()
        site_hit = (
            re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up)
            or re.search(r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up)
            or re.search(r"\b[A-Z]{2,3}\d{3,6}\b", txt_up)
        )

        _ok()

        if site_hit:
            return _handler_info_sitio_id_claro(texto_usuario)

        return _handler_opciones_sitio()

    txt_up = texto_usuario.strip().upper()
    site_hit = re.search(r"\b\d{2}[_\s]\d{3}\b", txt_up) or re.search(
        r"\bCL-\d{2}(?:-[A-Z]{2})?-\d{5}-\d{2}\b", txt_up
    )

    if site_hit:
        _ok()
        return _handler_info_sitio_id_claro(texto_usuario)

    if {"produccion", "producción"} & tokens:
        _ok()

        if tokens <= {"produccion", "producción"}:
            return _handler_opciones_produccion()

        return _handler_mi_produccion(usuario, texto_usuario)

    if {
        "asignacion",
        "asignación",
        "asignaciones",
        "asignado",
        "asignados",
        "pega",
    } & tokens:
        _ok()
        return _handler_asignacion(usuario, texto_usuario)

    if {"proyectos", "proyecto", "servicios", "servicio"} & tokens:
        _ok()
        return _handler_mis_proyectos(usuario, texto_usuario)

    if es_intencion_planificar_ruta(texto_usuario):
        _ok()
        return procesar_planificacion_ruta(
            chat_id=sesion.chat_id,
            usuario=usuario,
            texto=texto_usuario,
            message={"text": texto_usuario},
        )

    pregunta_estado_proyectos = bool(
        {
            "cuales",
            "cuáles",
            "lista",
            "listar",
            "mostrar",
            "muestra",
            "ver",
            "dime",
            "estado",
            "estados",
            "proceso",
            "ejecucion",
            "ejecución",
            "progreso",
            "asignado",
            "asignados",
            "revision",
            "revisión",
            "aprobado",
            "aprobados",
            "finalizado",
            "finalizados",
            "rechazado",
            "rechazados",
        }
        & tokens
    )

    if pregunta_estado_proyectos:
        _ok()
        return _handler_mis_proyectos(usuario, texto_usuario)

    return None


# ===================== Router principal de intents =====================


def run_intent(
    intent: Optional[BotIntent],
    texto_usuario: str,
    sesion: BotSession,
    usuario: Optional[CustomUser],
    inbound_log: BotMessageLog,
) -> str:
    chat_id = sesion.chat_id
    texto_usuario = texto_usuario or ""

    norm_run = _normalize(texto_usuario)
    tokens_run = set(_tokenize(texto_usuario))

    # ============================
    # Seguridad: sin usuario vinculado
    # ============================
    if usuario is None:
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
        return _respuesta_sin_usuario(chat_id)

    # ============================
    # Wizard finalizar proyectos
    # ============================
    try:
        finalizar_state = _finalizar_proy_get(chat_id)
    except Exception:
        finalizar_state = None

    quiere_finalizar_proyecto = (
        ({"finalizar", "terminar", "cerrar"} & tokens_run)
        or norm_run
        in {
            "quiero finalizar",
            "quiero terminar",
            "quiero cerrar",
            "finalizar",
            "finalizar proyecto",
            "finalizar proyectos",
            "terminar proyecto",
            "terminar proyectos",
        }
        or "quiero finalizar" in norm_run
        or "quiero terminar" in norm_run
        or "quiero cerrar" in norm_run
    )

    es_pregunta_finalizados = bool(
        {
            "cuales",
            "cuáles",
            "dime",
            "ver",
            "mostrar",
            "lista",
            "listar",
        }
        & tokens_run
        and {
            "finalice",
            "finalicé",
            "finalizado",
            "finalizados",
            "terminado",
            "terminados",
        }
        & tokens_run
    )

    if finalizar_state or (quiere_finalizar_proyecto and not es_pregunta_finalizados):
        inbound_log.status = "ok"
        inbound_log.marcar_para_entrenamiento = False
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        if finalizar_state:
            return _handler_confirmar_finalizar_proyectos(
                usuario=usuario,
                sesion=sesion,
                texto_usuario=texto_usuario,
            )

        return _handler_iniciar_finalizar_proyectos(usuario, sesion)

    # ============================
    # Wizard Rendición
    # ============================
    try:
        wiz_state = _rend_wiz_get(chat_id)
    except Exception:
        wiz_state = None

    triggers_start = {
        "nueva rendicion",
        "nueva rendicion gasto",
        "nueva rendicion de gasto",
        "crear rendicion",
        "crear rendicion gasto",
        "nueva rendicion de gastos",
        "nueva rendicion gastos",
    }

    if wiz_state or norm_run in triggers_start:
        inbound_log.status = "ok"
        inbound_log.marcar_para_entrenamiento = False
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        if norm_run in triggers_start and not wiz_state:
            return _rendicion_wizard_start(chat_id, usuario)

        return _rendicion_wizard_handle_message(
            chat_id=chat_id,
            usuario=usuario,
            message={"text": texto_usuario},
        )

    # ============================
    # Sin intent: resolver palabras genéricas del menú
    # ============================
    if not intent:
        resolved = _resolver_texto_generico_sin_intent(
            texto_usuario=texto_usuario,
            usuario=usuario,
            sesion=sesion,
            inbound_log=inbound_log,
        )

        if resolved:
            return resolved

        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        return (
            "No pude identificar exactamente qué necesitas, pero puedo ayudarte con estas opciones:\n\n"
            + _handler_menu_bot(usuario)
        )

    # ============================
    # Hay intent detectado
    # ============================
    inbound_log.status = "ok"
    inbound_log.marcar_para_entrenamiento = intent.requiere_revision_humana
    inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

    slug = intent.slug

    if slug == "cronograma_produccion_corte":
        return _handler_pagos_no_disponible()

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
        return _handler_mis_proyectos(usuario, texto_usuario)

    if slug == "ayuda_rendicion_gastos":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "mis_rendiciones_pendientes":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "direccion_basura":
        return _handler_direccion_basura(usuario, texto_usuario)

    if slug == "mi_asignacion":
        return _handler_asignacion(usuario, texto_usuario)

    resolved = _resolver_texto_generico_sin_intent(
        texto_usuario=texto_usuario,
        usuario=usuario,
        sesion=sesion,
        inbound_log=inbound_log,
    )

    if resolved:
        return resolved

    return (
        "Reconocí tu consulta, pero esa opción todavía no tiene una respuesta final configurada.\n\n"
        "Estas son las opciones disponibles ahora:\n\n" + _handler_menu_bot(usuario)
    )


# ===================== Entry point: manejar update de Telegram =====================


def handle_telegram_update(update: dict) -> None:
    """
    Punto de entrada para el webhook de Telegram.
    ✅ Soporta:
    - message / edited_message (texto y/o caption)
    - callback_query (inline_keyboard)
    - wizard de rendición (incluye comprobante como PDF/foto aunque venga SIN texto)
    - wizard de planificación de ruta (incluye ubicación Telegram)

    ✅ Vinculación por /start <token> generado en la web (activar_telegram).

    ✅ IA segura:
    - Usa IA como clasificador principal cuando corresponde.
    - Django valida permisos, existencia del intent y ejecuta handlers reales.
    - La IA no consulta BD ni entrega datos directamente.

    ✅ Contexto conversacional:
    - Si el usuario responde algo corto como "este mes", "mes anterior", "hoy",
      se interpreta según el último intent de la sesión antes de volver a clasificar desde cero.

    ✅ Humanizer:
    - Django genera la respuesta real.
    - La IA solo mejora el tono si es seguro hacerlo.
    - No inventa datos ni consulta la base de datos.
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
                logger.exception(
                    "No se pudo hacer answerCallbackQuery (callback_id=%s)", cb_id
                )

        message = dict(msg_obj)
        message["text"] = text

    else:
        message = update.get("message") or update.get("edited_message")
        if not message:
            logger.info(
                "Update de Telegram sin 'message' ni 'callback_query': %s", update
            )
            return

        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        text = ((message.get("text") or message.get("caption") or "")).strip()

    chat_id = str(chat.get("id") or "")
    if not chat_id:
        logger.info("Update de Telegram sin chat_id: %s", update)
        return

    # =========================
    # /start <token> => vincular usuario
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
                    u.telegram_chat_id = chat_id
                    u.telegram_activo = True
                    u.save(update_fields=["telegram_chat_id", "telegram_activo"])

                    cache.delete(cache_key)

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
    # Flujo normal
    # =========================
    sesion, usuario = get_or_create_session(chat_id, from_user)
    sesion.ultima_interaccion = timezone.now()
    sesion.save(update_fields=["ultima_interaccion"])

    has_file = bool(message.get("document") or message.get("photo"))
    has_location = bool(message.get("location"))

    text_for_log = (
        text
        if text
        else ("[ubicacion]" if has_location else ("[archivo]" if has_file else ""))
    )

    wizard_state = _rend_wiz_get(chat_id)
    ruta_state = ruta_get(chat_id)

    if not text_for_log and not wizard_state and not ruta_state:
        logger.info(
            "Mensaje sin texto/caption, sin ubicación y sin wizard activo (chat_id=%s)",
            chat_id,
        )
        return

    inbound_log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="in",
        texto=text_for_log,
        status="ok",
        meta={
            "update_id": update.get("update_id"),
            "callback": bool(callback),
            "has_location": has_location,
            "has_file": has_file,
        },
    )

    # =========================
    # Wizard rendición
    # =========================
    norm = _normalize(text or "")
    triggers_start = {
        "nueva rendicion",
        "nueva rendicion gasto",
        "nueva rendicion de gasto",
        "crear rendicion",
        "crear rendicion gasto",
        "nueva rendicion de gastos",
        "nueva rendicion gastos",
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
            meta={
                "from_update_id": update.get("update_id"),
                "wizard": True,
                "wizard_type": "rendicion",
                "humanizer_used": False,
                "humanizer_result": None,
            },
            marcar_para_entrenamiento=False,
        )
        return

    # =========================
    # Wizard planificación de ruta
    # =========================
    ruta_state = ruta_get(chat_id)

    if usuario is not None and (
        ruta_state
        or es_intencion_planificar_ruta(text)
        or (has_location and ruta_state)
    ):
        reply_text = procesar_planificacion_ruta(
            chat_id=chat_id,
            usuario=usuario,
            texto=text,
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
            meta={
                "from_update_id": update.get("update_id"),
                "wizard": True,
                "wizard_type": "ruta",
                "humanizer_used": False,
                "humanizer_result": None,
            },
            marcar_para_entrenamiento=False,
        )
        return

    # =========================
    # Detección intent con contexto + IA
    # =========================
    if text:
        intent_ctx, confianza_ctx, ctx_meta = _resolver_intent_por_contexto(
            text,
            sesion=sesion,
        )

        if intent_ctx:
            intent = intent_ctx
            confianza = confianza_ctx
            ai_meta = {
                "ai_used": False,
                "ai_result": None,
                "traditional_intent": None,
                "traditional_confidence": 0.0,
                "resolved_by_context": True,
                **ctx_meta,
            }
        else:
            intent, confianza, ai_meta = detect_intent_with_ai_fallback(
                text,
                usuario=usuario,
                contexto=sesion.contexto,
            )
            ai_meta = {
                **ai_meta,
                "resolved_by_context": False,
                **ctx_meta,
            }
    else:
        intent, confianza, ai_meta = (
            None,
            0.0,
            {
                "ai_used": False,
                "ai_result": None,
                "traditional_intent": None,
                "traditional_confidence": 0.0,
                "resolved_by_context": False,
            },
        )

    if intent:
        sesion.ultimo_intent = intent
        sesion.save(update_fields=["ultimo_intent"])

    inbound_log.intent_detectado = intent
    inbound_log.confianza = confianza
    inbound_log.meta = {
        **(inbound_log.meta or {}),
        **ai_meta,
    }
    inbound_log.save(update_fields=["intent_detectado", "confianza", "meta"])

    # =========================
    # Ejecutar handler Django
    # =========================
    reply_text = run_intent(intent, text, sesion, usuario, inbound_log)

    # =========================
    # Humanizer IA seguro
    # =========================
    humanizer_meta = {
        "humanizer_used": False,
        "humanizer_result": None,
    }

    try:
        if intent:
            humanized = humanize_bot_response(
                respuesta_original=reply_text,
                texto_usuario=text,
                intent_slug=intent.slug,
                usuario=usuario,
            )

            humanizer_meta["humanizer_used"] = bool(humanized.get("ok"))
            humanizer_meta["humanizer_result"] = {
                "ok": humanized.get("ok"),
                "reason": humanized.get("reason"),
            }

            if humanized.get("ok") and humanized.get("text"):
                reply_text = humanized["text"]

    except Exception as e:
        logger.exception("GZ Bot Humanizer falló")
        humanizer_meta["humanizer_used"] = False
        humanizer_meta["humanizer_result"] = {
            "ok": False,
            "reason": str(e),
        }

    marcar_train_out = (intent is None) or intent.requiere_revision_humana

    send_telegram_message(
        chat_id,
        reply_text,
        sesion=sesion,
        usuario=usuario,
        intent=intent,
        meta={
            "from_update_id": update.get("update_id"),
            **ai_meta,
            **humanizer_meta,
        },
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

# bot_gz/engine.py

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

from usuarios.models import CustomUser

from . import services_tecnico
from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample
from .telegram_api import send_message_telegram

logger = logging.getLogger(__name__)


# ========= Helpers de normalización de texto =========


def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


@dataclass
class BotResult:
    texto: str
    status: str = "ok"  # "ok", "fallback", "error"
    intent: Optional[BotIntent] = None
    marcar_para_entrenamiento: bool = False
    meta: Optional[Dict[str, Any]] = None
    nuevo_estado: str = ""


# ========= Sesiones y resolución de usuario =========


def _resolver_usuario_por_chat(chat_id: str) -> Optional[CustomUser]:
    """
    Asocia el chat_id de Telegram con un usuario interno.
    Usamos CustomUser.telegram_chat_id.
    """
    from usuarios.models import CustomUser as User

    return User.objects.filter(
        telegram_chat_id=str(chat_id),
        telegram_activo=True,
        is_active=True,
    ).first()


def _get_or_create_sesion(chat_id: str, usuario: Optional[CustomUser]) -> BotSession:
    sesion = (
        BotSession.objects.filter(chat_id=str(chat_id), activa=True)
        .select_related("usuario", "ultimo_intent")
        .first()
    )
    if not sesion:
        sesion = BotSession.objects.create(
            chat_id=str(chat_id),
            usuario=usuario,
            contexto="tecnico",
        )
    else:
        if usuario and sesion.usuario is None:
            sesion.usuario = usuario
            sesion.save(update_fields=["usuario"])
    return sesion


# ========= Detección de intent =========


def _buscar_intent_por_slug(slug: str) -> Optional[BotIntent]:
    return BotIntent.objects.filter(slug=slug, activo=True).first()


def _detectar_por_training_examples(
    texto_norm: str,
) -> Tuple[Optional[BotIntent], float]:
    """
    Estrategia simple:
    - comparamos texto normalizado contra los ejemplos activos
    - si el ejemplo está contenido como substring, asumimos match.
    """
    ejemplos = (
        BotTrainingExample.objects.select_related("intent")
        .filter(activo=True, intent__activo=True)
        .all()
    )

    mejor_intent: Optional[BotIntent] = None
    mejor_score: float = 0.0

    for ej in ejemplos:
        ej_norm = _normalize_text(ej.texto)
        if not ej_norm:
            continue
        if ej_norm in texto_norm:
            # heurística simple: más largo = más específico
            score = min(1.0, len(ej_norm) / max(len(texto_norm), 1))
            if score > mejor_score:
                mejor_score = score
                mejor_intent = ej.intent

    return mejor_intent, mejor_score


def _detectar_por_reglas(
    texto_norm: str,
) -> Tuple[Optional[BotIntent], float, Dict[str, Any]]:
    """
    Reglas hechas a mano basadas en las cosas que quieres automatizar
    para los técnicos.
    Devuelve (intent, confianza, meta).
    """

    meta: Dict[str, Any] = {}

    # Comandos tipo /mis_liquidaciones
    if texto_norm.startswith("/"):
        cmd = texto_norm[1:].strip()
        intent = _buscar_intent_por_slug(cmd)
        if intent:
            return intent, 0.95, meta

    # Palabras clave
    # Nota: muy simple por ahora, pero ya estructurado para extender.
    # --- Rendiciones pendientes ---
    if ("rendicion" in texto_norm or "rendicion" in texto_norm or "declaracion" in texto_norm) and (
        "pendiente" in texto_norm or "aprobacion" in texto_norm
    ):
        intent = _buscar_intent_por_slug("rendiciones_pendientes")
        if intent:
            return intent, 0.8, meta

    # --- Liquidaciones ---
    if "liquidacion" in texto_norm or "liquidacion" in texto_norm:
        intent = _buscar_intent_por_slug("mis_liquidaciones")
        if intent:
            return intent, 0.8, meta

    # --- Contratos ---
    if "contrato" in texto_norm:
        intent = _buscar_intent_por_slug("mis_contratos")
        if intent:
            return intent, 0.8, meta

    # --- Producción ---
    if "produccion" in texto_norm or "produccion" in texto_norm:
        if "hoy" in texto_norm or "fecha" in texto_norm or "llevo" in texto_norm:
            intent = _buscar_intent_por_slug("mi_produccion_hasta_hoy")
            if intent:
                return intent, 0.8, meta

    # --- Proyectos rechazados ---
    if "proyecto" in texto_norm and "rechaz" in texto_norm:
        intent = _buscar_intent_por_slug("mis_proyectos_rechazados")
        if intent:
            return intent, 0.8, meta

    # --- Corte / pago ---
    if "corte" in texto_norm or "pago" in texto_norm:
        if "produccion" in texto_norm or "produccion" in texto_norm or "sueld" in texto_norm:
            intent = _buscar_intent_por_slug("corte_produccion")
            if intent:
                return intent, 0.75, meta

    # --- Sitio / ubicación ---
    if ("sitio" in texto_norm or "id claro" in texto_norm) and (
        "ubicacion" in texto_norm
        or "direcc" in texto_norm
        or "informacion" in texto_norm
    ):
        intent = _buscar_intent_por_slug("info_sitio")
        if intent:
            # intentar extraer un código sencillo tipo texto con mayúsculas/números
            m = re.search(r"(ma?\d{3,6}|\b[a-z]{2}\d{3,6}\b)", texto_norm)
            if m:
                meta["codigo_sitio"] = m.group(0)
            return intent, 0.7, meta

    # --- Basura / residuos ---
    if "basura" in texto_norm or "residuo" in texto_norm:
        intent = _buscar_intent_por_slug("direccion_basura")
        if intent:
            return intent, 0.8, meta

    # --- Proyectos aprobados del mes ---
    if "proyecto" in texto_norm and "aprob" in texto_norm and (
        "mes" in texto_norm or "este mes" in texto_norm
    ):
        intent = _buscar_intent_por_slug("proyectos_aprobados_mes")
        if intent:
            return intent, 0.75, meta

    # --- Proyectos pendientes de supervisor ---
    if "proyecto" in texto_norm and (
        "pendiente supervisor" in texto_norm
        or "supervisor pendiente" in texto_norm
        or ("pendiente" in texto_norm and "supervisor" in texto_norm)
    ):
        intent = _buscar_intent_por_slug("proyectos_pendientes_supervisor")
        if intent:
            return intent, 0.8, meta

    # --- Rendición de gasto por bot ---
    if "rendir" in texto_norm and "gasto" in texto_norm:
        intent = _buscar_intent_por_slug("rendicion_gasto_por_bot")
        if intent:
            return intent, 0.7, meta

    return None, 0.0, meta


def detectar_intent(
    texto: str, usuario: Optional[CustomUser], contexto: str
) -> Tuple[Optional[BotIntent], float, Dict[str, Any]]:
    texto_norm = _normalize_text(texto)

    # 1) Intent por ejemplos de entrenamiento
    intent_ej, score_ej = _detectar_por_training_examples(texto_norm)
    if intent_ej and score_ej >= 0.75:
        return intent_ej, score_ej, {}

    # 2) Reglas hechas a mano
    intent_reg, score_reg, meta = _detectar_por_reglas(texto_norm)
    if intent_reg:
        return intent_reg, score_reg, meta

    # 3) Si el score por ejemplos era decente, usarlo igual como fallback
    if intent_ej and score_ej >= 0.4:
        return intent_ej, score_ej, {}

    return None, 0.0, {}


# ========= Router de intents =========


def _enrutar_intent(
    intent: BotIntent,
    usuario: Optional[CustomUser],
    sesion: BotSession,
    texto: str,
    meta_intent: Dict[str, Any],
) -> BotResult:
    """
    Llama a la función adecuada del servicio según el intent.slug.
    Solo implementamos por ahora intents de técnico.
    """

    if usuario is None:
        return BotResult(
            texto=(
                "Hola 👋. Aún no tengo asociado este chat de Telegram a un usuario interno.\n\n"
                "Pide a RRHH o al administrador que registren tu *chat ID* en tu ficha de usuario "
                "para que pueda mostrarte tu información personal (proyectos, rendiciones, etc.)."
            ),
            status="error",
            intent=None,
            marcar_para_entrenamiento=False,
        )

    slug = intent.slug

    try:
        # === Intents pensados para técnicos ===
        if slug == "rendiciones_pendientes":
            texto_resp = services_tecnico.responder_rendiciones_pendientes(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "mis_liquidaciones":
            texto_resp = services_tecnico.responder_mis_liquidaciones(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "mis_contratos":
            texto_resp = services_tecnico.responder_mis_contratos(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "mi_produccion_hasta_hoy":
            texto_resp = services_tecnico.responder_produccion_hasta_hoy(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "mis_proyectos_rechazados":
            texto_resp = services_tecnico.responder_proyectos_rechazados(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "corte_produccion":
            texto_resp = services_tecnico.responder_corte_produccion()
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "info_sitio":
            codigo = meta_intent.get("codigo_sitio", "")
            texto_resp = services_tecnico.responder_info_sitio_por_codigo(codigo)
            return BotResult(texto=texto_resp, intent=intent, meta=meta_intent)

        if slug == "direccion_basura":
            texto_resp = services_tecnico.responder_direccion_basura()
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "proyectos_aprobados_mes":
            texto_resp = services_tecnico.responder_proyectos_aprobados_mes(usuario)
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "proyectos_pendientes_supervisor":
            texto_resp = services_tecnico.responder_proyectos_pendientes_supervisor(
                usuario
            )
            return BotResult(texto=texto_resp, intent=intent)

        if slug == "rendicion_gasto_por_bot":
            texto_resp = services_tecnico.responder_rendicion_por_bot_pendiente()
            # este sí lo queremos ver seguido en la consola para ir habilitándolo
            return BotResult(
                texto=texto_resp, intent=intent, marcar_para_entrenamiento=True
            )

        # === Cualquier otro intent que aún no tenga handler ===
        return BotResult(
            texto=(
                "Tengo identificado lo que me estás pidiendo, "
                f"pero ese flujo (`{slug}`) todavía no está implementado del todo.\n"
                "Lo vamos a ir habilitando paso a paso 💪."
            ),
            status="ok",
            intent=intent,
            marcar_para_entrenamiento=True,
        )

    except Exception:
        logger.exception("Error en handler de intent '%s'", slug)
        return BotResult(
            texto="Ocurrió un error interno al procesar tu solicitud. Inténtalo de nuevo en unos minutos.",
            status="error",
            intent=intent,
            marcar_para_entrenamiento=True,
        )


# ========= Logging de mensajes =========


def _log_mensaje(
    *,
    sesion: Optional[BotSession],
    usuario: Optional[CustomUser],
    chat_id: str,
    direccion: str,
    texto: str,
    intent_detectado: Optional[BotIntent],
    confianza: Optional[float],
    status: str,
    marcar_para_entrenamiento: bool,
    meta: Optional[Dict[str, Any]],
    intent_corregido: Optional[BotIntent] = None,
) -> BotMessageLog:
    return BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=str(chat_id),
        direccion=direccion,
        texto=texto,
        intent_detectado=intent_detectado,
        intent_corregido=intent_corregido,
        confianza=confianza,
        status=status,
        marcar_para_entrenamiento=marcar_para_entrenamiento,
        meta=meta or {},
    )


# ========= Punto principal: procesar un mensaje de texto =========


def procesar_mensaje_texto(chat_id: str, texto: str, raw_update: Optional[dict] = None):
    """
    Punto principal que deben llamar las vistas del webhook.
    Se encarga de:
    - Resolver usuario y sesión
    - Detectar intent
    - Ejecutar handler
    - Loguear entrada y salida
    - Enviar respuesta por Telegram
    """
    texto = (texto or "").strip()
    if not texto:
        return

    usuario = _resolver_usuario_por_chat(chat_id)
    sesion = _get_or_create_sesion(chat_id, usuario)
    sesion.ultima_interaccion = timezone.now()
    sesion.save(update_fields=["ultima_interaccion"])

    # 1) Log de entrada
    log_in = _log_mensaje(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="in",
        texto=texto,
        intent_detectado=None,
        confianza=None,
        status="ok",
        marcar_para_entrenamiento=False,
        meta={"raw_update": raw_update} if raw_update else None,
    )

    # 2) Detectar intent
    intent, confianza, meta_intent = detectar_intent(texto, usuario, sesion.contexto)

    # 3) Si no hay intent claro, fallback
    if not intent or confianza < 0.4:
        texto_fallback = (
            "Por ahora no entendí bien lo que necesitas 🤔.\n\n"
            "Puedes pedirme cosas como:\n"
            "- `¿Cuántas rendiciones tengo pendientes?`\n"
            "- `Pásame mi liquidación de sueldo`\n"
            "- `Quiero saber mi producción hasta hoy`\n"
            "- `¿Cuáles proyectos tengo rechazados?`\n"
            "- `Dame la información del sitio ID Claro XXXX`\n"
        )

        result = BotResult(
            texto=texto_fallback,
            status="fallback",
            intent=intent,
            marcar_para_entrenamiento=True,
            meta={"confianza": confianza, "meta_intent": meta_intent},
        )
    else:
        result = _enrutar_intent(intent, usuario, sesion, texto, meta_intent)

    # 4) Actualizar sesión con último intent / estado
    if result.intent:
        sesion.ultimo_intent = result.intent
    if result.nuevo_estado:
        sesion.estado = result.nuevo_estado
    sesion.save(update_fields=["ultimo_intent", "estado", "ultima_interaccion"])

    # 5) Log de salida
    _log_mensaje(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="out",
        texto=result.texto,
        intent_detectado=intent,
        confianza=confianza,
        status=result.status,
        marcar_para_entrenamiento=result.marcar_para_entrenamiento,
        meta=result.meta,
    )

    # 6) Enviar respuesta por Telegram
    ok = send_message_telegram(chat_id, result.texto)
    if not ok:
        logger.error("No se pudo enviar mensaje de respuesta a chat_id=%s", chat_id)


# ========= Helper para manejar el update crudo de Telegram =========


def handle_telegram_update(update: dict):
    """
    Recibe el JSON completo que envía Telegram al webhook.
    Extrae chat_id y texto si es un mensaje de texto normal.
    Ignora otros tipos de update.
    """
    if not isinstance(update, dict):
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        # podrías manejar callbacks, etc. más adelante
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    text = message.get("text")
    if not text:
        # Solo manejamos mensajes de texto en esta primera versión
        procesar_mensaje_texto(str(chat_id), "mensaje_no_soportado", raw_update=update)
        return

    procesar_mensaje_texto(str(chat_id), text, raw_update=update)
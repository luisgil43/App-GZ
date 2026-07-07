# bot_gz/ai_engine.py

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests
from django.conf import settings

from .permissions import build_ai_capabilities_text, user_can_use_bot_intent

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> Optional[dict]:
    """
    Extrae un JSON simple desde la respuesta del modelo.
    """
    if not text:
        return None

    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def ai_suggest_intent(
    *,
    texto_usuario: str,
    usuario,
    contexto: str = "tecnico",
) -> dict:
    """
    Usa IA SOLO para sugerir un intent existente.
    No consulta BD.
    No entrega datos sensibles.
    No responde directamente al usuario.

    Retorna:
    {
      "ok": bool,
      "intent_slug": str | None,
      "confidence": float,
      "reason": str,
      "params": dict
    }
    """
    if not getattr(settings, "BOT_GZ_AI_ENABLED", False):
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "AI disabled",
            "params": {},
        }

    api_key = getattr(settings, "OPENAI_API_KEY_GZ_BOT", "").strip()
    model = getattr(settings, "OPENAI_MODEL_GZ_BOT", "gpt-4.1-mini").strip()

    if not api_key:
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "Missing OPENAI_API_KEY_GZ_BOT",
            "params": {},
        }

    texto_usuario = (texto_usuario or "").strip()
    if not texto_usuario:
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "Empty text",
            "params": {},
        }

    capabilities = build_ai_capabilities_text(usuario)

    if not capabilities.strip():
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "User has no bot capabilities",
            "params": {},
        }

    system_prompt = f"""
Eres un clasificador seguro para el bot corporativo GZ Services.

Tu tarea:
- Leer el mensaje del usuario.
- Elegir SOLO UNO de los intents permitidos.
- Si no estás seguro, devuelve intent_slug null.
- NO respondas al usuario final.
- NO inventes datos.
- NO intentes acceder a información.
- NO sugieras intents fuera de la lista permitida.
- Si el usuario pide información de otra persona y el intent sería personal, igual clasifica el intent personal; Django aplicará seguridad.
- Devuelve SOLO JSON válido.

Contexto de sesión: {contexto}

Intents permitidos para este usuario:
{capabilities}

Formato obligatorio:
{{
  "intent_slug": "slug_o_null",
  "confidence": 0.0,
  "reason": "explicacion breve",
  "params": {{}}
}}
""".strip()

    user_prompt = f"""
Mensaje del usuario:
{texto_usuario}
""".strip()

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "temperature": 0,
        "max_output_tokens": 300,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=15,
        )
    except Exception as e:
        logger.exception("GZ Bot AI: error conectando con OpenAI")
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": f"OpenAI request exception: {e}",
            "params": {},
        }

    if resp.status_code >= 400:
        logger.error(
            "GZ Bot AI: OpenAI respondió error %s: %s",
            resp.status_code,
            resp.text[:800],
        )
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": f"OpenAI HTTP {resp.status_code}",
            "params": {},
        }

    try:
        data = resp.json()
    except Exception:
        logger.error("GZ Bot AI: respuesta OpenAI no JSON: %s", resp.text[:800])
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "OpenAI response is not JSON",
            "params": {},
        }

    # Responses API normalmente trae output_text.
    output_text = data.get("output_text") or ""

    if not output_text:
        # Fallback por si viene como output[].content[].text
        try:
            parts = []
            for item in data.get("output", []):
                for content in item.get("content", []):
                    txt = content.get("text")
                    if txt:
                        parts.append(txt)
            output_text = "\n".join(parts).strip()
        except Exception:
            output_text = ""

    parsed = _extract_json_object(output_text)
    if not parsed:
        logger.warning("GZ Bot AI: no pude parsear JSON. output=%s", output_text[:800])
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "Could not parse AI JSON",
            "params": {},
        }

    intent_slug = parsed.get("intent_slug")
    if intent_slug in ("null", "None", "", None):
        intent_slug = None

    try:
        confidence = float(parsed.get("confidence") or 0)
    except Exception:
        confidence = 0.0

    reason = str(parsed.get("reason") or "").strip()
    params = parsed.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # Seguridad final: la IA no puede devolver algo fuera de permisos.
    if intent_slug and not user_can_use_bot_intent(usuario, intent_slug):
        logger.warning(
            "GZ Bot AI: intent no permitido devuelto por IA. user=%s intent=%s",
            getattr(usuario, "id", None),
            intent_slug,
        )
        return {
            "ok": False,
            "intent_slug": None,
            "confidence": 0.0,
            "reason": "AI returned non-allowed intent",
            "params": {},
        }

    return {
        "ok": bool(intent_slug),
        "intent_slug": intent_slug,
        "confidence": confidence,
        "reason": reason,
        "params": params,
    }

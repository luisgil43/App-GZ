# bot_gz/ai_humanizer.py

from __future__ import annotations

import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def should_humanize_response(texto: str) -> bool:
    """
    Evita mandar a IA respuestas que no conviene modificar:
    - URLs/PDFs
    - mensajes HTML complejos
    - respuestas muy cortas
    - errores sensibles
    """
    texto = (texto or "").strip()

    if not texto:
        return False

    if len(texto) < 40:
        return False

    if "http://" in texto or "https://" in texto:
        return False

    if "<b>" in texto or "<code>" in texto or "</" in texto:
        return False

    if "Por seguridad" in texto:
        return False

    if "No tengo permiso" in texto:
        return False

    if "RUT" in texto or "contrato" in texto.lower():
        # Lo dejamos intacto por ser sensible/legal.
        return False

    return True


def humanize_bot_response(
    *,
    respuesta_original: str,
    texto_usuario: str = "",
    intent_slug: str = "",
    usuario=None,
) -> dict:
    """
    Humaniza una respuesta generada por Django.

    Seguridad:
    - No inventa datos.
    - No agrega montos, fechas, nombres, links ni información nueva.
    - Solo mejora tono, claridad y cercanía.
    """
    if not getattr(settings, "BOT_GZ_AI_ENABLED", False):
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "AI disabled",
        }

    api_key = getattr(settings, "OPENAI_API_KEY_GZ_BOT", "").strip()
    model = getattr(settings, "OPENAI_MODEL_GZ_BOT", "gpt-4.1-mini").strip()

    if not api_key:
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "Missing OPENAI_API_KEY_GZ_BOT",
        }

    if not should_humanize_response(respuesta_original):
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "Response not suitable for humanization",
        }

    system_prompt = """
Eres un asistente corporativo de GZ Services.

Tu tarea es reescribir una respuesta ya generada por el sistema para que suene más humana,
clara, cordial y profesional.

Reglas estrictas:
- NO inventes información.
- NO agregues montos, fechas, nombres, estados, URLs ni datos que no estén en la respuesta original.
- NO elimines información importante.
- NO cambies el sentido.
- Mantén el idioma español.
- Mantén emojis si ayudan, pero sin exagerar.
- Mantén formato simple para Telegram.
- No hagas la respuesta demasiado larga.
- Si la respuesta original contiene listas, conserva listas.
- Si hay una instrucción operativa, consérvala.
""".strip()

    user_prompt = f"""
Mensaje del usuario:
{texto_usuario}

Intent:
{intent_slug}

Respuesta original del sistema:
{respuesta_original}

Reescribe la respuesta de forma más humana y profesional.
Devuelve solo el texto final.
""".strip()

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_output_tokens": 700,
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
        logger.exception("GZ Bot Humanizer: error conectando con OpenAI")
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": f"OpenAI request exception: {e}",
        }

    if resp.status_code >= 400:
        logger.error(
            "GZ Bot Humanizer: OpenAI respondió error %s: %s",
            resp.status_code,
            resp.text[:800],
        )
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": f"OpenAI HTTP {resp.status_code}",
        }

    try:
        data = resp.json()
    except Exception:
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "OpenAI response is not JSON",
        }

    output_text = data.get("output_text") or ""

    if not output_text:
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

    output_text = (output_text or "").strip()

    if not output_text:
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "Empty humanized response",
        }

    # Seguridad extra: evitar que la IA meta links nuevos.
    original_urls = set(re.findall(r"https?://\S+", respuesta_original or ""))
    new_urls = set(re.findall(r"https?://\S+", output_text or ""))

    if new_urls - original_urls:
        return {
            "ok": False,
            "text": respuesta_original,
            "reason": "Humanizer added new URLs",
        }

    return {
        "ok": True,
        "text": output_text,
        "reason": "humanized",
    }

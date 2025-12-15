# bot_gz/telegram_api.py

from __future__ import annotations

import logging
from typing import Union

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_bot_token() -> str | None:
    """
    Reutiliza la misma lógica que usas en notificaciones:
    intenta TELEGRAM_BOT_TOKEN_GZ y luego TELEGRAM_BOT_TOKEN.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN_GZ", None) or getattr(
        settings, "TELEGRAM_BOT_TOKEN", None
    )
    if not token:
        logger.error(
            "Telegram bot: no se encontró TELEGRAM_BOT_TOKEN_GZ ni TELEGRAM_BOT_TOKEN en settings."
        )
    return token


def send_message_telegram(chat_id: Union[str, int], text: str) -> bool:
    """
    Envía un mensaje simple de texto al chat indicado.
    No usa Markdown para evitar problemas con entidades.
    """
    token = _get_bot_token()
    if not token:
        return False

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
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
            return True

        desc = ""
        if isinstance(data, dict):
            desc = data.get("description") or ""
        if not desc:
            desc = resp.text[:500]

        logger.error(
            "Error enviando mensaje Telegram (%s): %s", resp.status_code, desc
        )
        return False

    except Exception:
        logger.exception("Excepción al enviar mensaje Telegram")
        return False
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

def send_pdf_telegram(chat_id, archivo, nombre_archivo, caption=""):
    """
    Envía un archivo PDF real a Telegram mediante sendDocument.

    archivo: FileField de Django.
    No modifica el archivo original.
    """
    import io

    token = _get_bot_token()
    if not token:
        return False

    if not archivo or not archivo.name:
        logger.error("Telegram PDF: archivo no disponible")
        return False

    try:
        with archivo.open("rb") as fuente:
            contenido = fuente.read()

        if not contenido.startswith(b"%PDF-"):
            logger.error(
                "Telegram PDF: el archivo no contiene un PDF válido"
            )
            return False

        nombre = str(nombre_archivo)

        if not nombre.lower().endswith(".pdf"):
            nombre += ".pdf"

        api_url = f"https://api.telegram.org/bot{token}/sendDocument"

        payload = {
            "chat_id": str(chat_id),
            "caption": caption[:1024],
        }

        files = {
            "document": (
                nombre,
                io.BytesIO(contenido),
                "application/pdf",
            )
        }

        respuesta = requests.post(
            api_url,
            data=payload,
            files=files,
            timeout=60,
        )

        data = respuesta.json()

        if respuesta.status_code == 200 and data.get("ok"):
            return True

        logger.error(
            "Telegram PDF: error HTTP %s: %s",
            respuesta.status_code,
            data.get("description", "Sin descripción"),
        )
        return False

    except Exception:
        logger.exception("Error enviando PDF por Telegram")
        return False

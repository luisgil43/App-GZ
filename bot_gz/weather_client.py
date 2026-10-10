# bot_gz/weather_client.py

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


WEATHER_CODE_MAP = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla con escarcha",
    51: "Llovizna ligera",
    53: "Llovizna moderada",
    55: "Llovizna intensa",
    61: "Lluvia ligera",
    63: "Lluvia moderada",
    65: "Lluvia intensa",
    80: "Chubascos ligeros",
    81: "Chubascos moderados",
    82: "Chubascos fuertes",
    95: "Tormenta",
    96: "Tormenta con granizo",
    99: "Tormenta fuerte con granizo",
}


def _to_decimal(value, default=None):
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def clasificar_uv(uv) -> str:
    try:
        uv = float(uv or 0)
    except Exception:
        uv = 0

    if uv < 3:
        return "Bajo"
    if uv < 6:
        return "Moderado"
    if uv < 8:
        return "Alto"
    if uv < 11:
        return "Muy alto"
    return "Extremo"


def factor_solar_por_uv(uv) -> str:
    """
    Recomendación preventiva para trabajadores en terreno en Chile.
    Siempre recomienda uso de protector solar; cambia el factor según UV.
    """
    try:
        uv = float(uv or 0)
    except Exception:
        uv = 0

    if uv < 3:
        return "USAR protector solar FPS 30"

    if uv < 6:
        return "USAR protector solar FPS 30+"

    return "USAR protector solar FPS 50+"


def _round_coord(value, decimals: int = 3) -> str:
    """
    Redondea coordenadas para cachear por zona aproximada.

    3 decimales ≈ 100 metros aprox.
    Esto evita hacer 20 consultas si varios sitios están muy cerca.
    """
    try:
        return str(round(float(value), decimals))
    except Exception:
        return str(value)


def _weather_cache_key(*, latitud, longitud, timezone_str: str) -> str:
    """
    Cache diario por coordenada aproximada.
    """
    hoy = timezone.localdate().isoformat()
    lat = _round_coord(latitud)
    lng = _round_coord(longitud)
    tz = (timezone_str or "America/Santiago").replace("/", "_")
    return f"bot_gz:weather_uv:v2:{hoy}:{tz}:{lat}:{lng}"


def _weather_last_cache_key(*, latitud, longitud, timezone_str: str) -> str:
    """
    Último clima válido conocido por coordenada aproximada.
    Sirve como respaldo si Open-Meteo responde 429 o falla.
    """
    lat = _round_coord(latitud)
    lng = _round_coord(longitud)
    tz = (timezone_str or "America/Santiago").replace("/", "_")
    return f"bot_gz:weather_uv:last:v2:{tz}:{lat}:{lng}"


def _fallback_weather_payload(reason: str = "unavailable") -> dict:
    """
    Respuesta preventiva cuando no hay clima disponible.

    Importante:
    - No inventa temperatura.
    - Mantiene recomendación preventiva.
    - Permite que services_clima pueda construir y enviar el mensaje.
    """
    return {
        "temperatura_c": None,
        "sensacion_c": None,
        "viento_kmh": None,
        "indice_uv": None,
        "nivel_uv": "No disponible",
        "factor_solar_recomendado": "USAR protector solar FPS 30+",
        "radiacion_wm2": None,
        "radiacion_diaria_mj_m2": None,
        "prob_lluvia": None,
        "condicion": "Clima no disponible temporalmente",
        "fuente": f"fallback-{reason}",
        "raw": {
            "fallback": True,
            "reason": reason,
        },
    }


def _parse_open_meteo_payload(data: dict) -> dict:
    """
    Convierte el JSON de Open-Meteo al formato usado por el bot.
    """
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}

    weather_code = current.get("weather_code")
    if weather_code is None:
        try:
            weather_code = (daily.get("weather_code") or [None])[0]
        except Exception:
            weather_code = None

    try:
        uv_max = (daily.get("uv_index_max") or [None])[0]
    except Exception:
        uv_max = None

    try:
        prob_lluvia = (daily.get("precipitation_probability_max") or [None])[0]
    except Exception:
        prob_lluvia = None

    try:
        radiacion_sum = (daily.get("shortwave_radiation_sum") or [None])[0]
    except Exception:
        radiacion_sum = None

    radiacion_wm2 = None
    try:
        vals = [v for v in (hourly.get("shortwave_radiation") or []) if v is not None]
        if vals:
            radiacion_wm2 = max(vals)
    except Exception:
        radiacion_wm2 = None

    condicion = WEATHER_CODE_MAP.get(
        weather_code,
        f"Código clima {weather_code}" if weather_code is not None else "Sin condición",
    )

    return {
        "temperatura_c": _to_decimal(current.get("temperature_2m")),
        "sensacion_c": _to_decimal(current.get("apparent_temperature")),
        "viento_kmh": _to_decimal(current.get("wind_speed_10m")),
        "indice_uv": _to_decimal(uv_max),
        "nivel_uv": clasificar_uv(uv_max),
        "factor_solar_recomendado": factor_solar_por_uv(uv_max),
        "radiacion_wm2": _to_decimal(radiacion_wm2),
        "radiacion_diaria_mj_m2": _to_decimal(radiacion_sum),
        "prob_lluvia": _to_decimal(prob_lluvia),
        "condicion": condicion,
        "fuente": "open-meteo",
        "raw": data,
    }


def get_weather_uv(*, latitud, longitud, timezone_str: Optional[str] = None) -> dict:
    """
    Consulta clima/UV/radiación por coordenadas usando Open-Meteo.

    Versión protegida:
    - Usa cache diario para no consultar Open-Meteo en cada prueba.
    - Guarda último clima válido.
    - Si Open-Meteo responde 429, usa último cache si existe.
    - Si no hay cache, devuelve payload preventivo sin romper el flujo.
    """
    timezone_str = timezone_str or getattr(
        settings,
        "BOT_GZ_CLIMA_TIMEZONE",
        "America/Santiago",
    )

    cache_key = _weather_cache_key(
        latitud=latitud,
        longitud=longitud,
        timezone_str=timezone_str,
    )
    last_cache_key = _weather_last_cache_key(
        latitud=latitud,
        longitud=longitud,
        timezone_str=timezone_str,
    )

    cached = cache.get(cache_key)
    if cached:
        cached = dict(cached)
        cached["fuente"] = f"{cached.get('fuente') or 'open-meteo'}-cache"
        return cached

    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": str(latitud),
        "longitude": str(longitud),
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
                "is_day",
            ]
        ),
        "hourly": ",".join(
            [
                "uv_index",
                "shortwave_radiation",
                "precipitation_probability",
            ]
        ),
        "daily": ",".join(
            [
                "uv_index_max",
                "precipitation_probability_max",
                "shortwave_radiation_sum",
                "weather_code",
            ]
        ),
        "forecast_days": 1,
        "timezone": timezone_str,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)

        if resp.status_code == 429:
            logger.warning(
                "Open-Meteo 429 Too Many Requests para lat=%s lng=%s. Usando cache si existe.",
                latitud,
                longitud,
            )

            last_cached = cache.get(last_cache_key)
            if last_cached:
                last_cached = dict(last_cached)
                last_cached["fuente"] = (
                    f"{last_cached.get('fuente') or 'open-meteo'}-last-cache-429"
                )
                return last_cached

            fallback = _fallback_weather_payload("open-meteo-429")
            cache.set(cache_key, fallback, timeout=60 * 15)
            return fallback

        resp.raise_for_status()
        data = resp.json()

        parsed = _parse_open_meteo_payload(data)

        # Cache diario: 6 horas.
        cache.set(cache_key, parsed, timeout=60 * 60 * 6)

        # Último clima válido: 3 días.
        cache.set(last_cache_key, parsed, timeout=60 * 60 * 24 * 3)

        return parsed

    except requests.exceptions.HTTPError as e:
        status_code = None
        try:
            status_code = e.response.status_code
        except Exception:
            status_code = None

        logger.warning(
            "HTTPError consultando Open-Meteo lat=%s lng=%s status=%s: %s",
            latitud,
            longitud,
            status_code,
            e,
        )

        last_cached = cache.get(last_cache_key)
        if last_cached:
            last_cached = dict(last_cached)
            last_cached["fuente"] = (
                f"{last_cached.get('fuente') or 'open-meteo'}-last-cache-http-error"
            )
            return last_cached

        fallback = _fallback_weather_payload(f"http-error-{status_code or 'unknown'}")
        cache.set(cache_key, fallback, timeout=60 * 15)
        return fallback

    except requests.exceptions.RequestException as e:
        logger.warning(
            "Error de red consultando Open-Meteo lat=%s lng=%s: %s",
            latitud,
            longitud,
            e,
        )

        last_cached = cache.get(last_cache_key)
        if last_cached:
            last_cached = dict(last_cached)
            last_cached["fuente"] = (
                f"{last_cached.get('fuente') or 'open-meteo'}-last-cache-network-error"
            )
            return last_cached

        fallback = _fallback_weather_payload("network-error")
        cache.set(cache_key, fallback, timeout=60 * 15)
        return fallback

    except Exception as e:
        logger.exception(
            "Error inesperado consultando Open-Meteo lat=%s lng=%s",
            latitud,
            longitud,
        )

        last_cached = cache.get(last_cache_key)
        if last_cached:
            last_cached = dict(last_cached)
            last_cached["fuente"] = (
                f"{last_cached.get('fuente') or 'open-meteo'}-last-cache-exception"
            )
            return last_cached

        fallback = _fallback_weather_payload("exception")
        cache.set(cache_key, fallback, timeout=60 * 15)
        return fallback

# bot_gz/weather_client.py

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

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


def get_weather_uv(*, latitud, longitud, timezone_str: Optional[str] = None) -> dict:
    """
    Consulta clima/UV/radiación por coordenadas usando Open-Meteo.
    """
    timezone_str = timezone_str or getattr(
        settings,
        "BOT_GZ_CLIMA_TIMEZONE",
        "America/Santiago",
    )

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

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

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

    # Radiación actual/promedio cercana: tomamos el máximo horario disponible del día
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

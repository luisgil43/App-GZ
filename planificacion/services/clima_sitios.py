
"""Pronóstico meteorológico por coordenadas GPS."""

import json
import math
import os

from datetime import datetime, timedelta, timezone as utc_timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.utils import timezone


API_MET = (
    "https://api.met.no/weatherapi/"
    "locationforecast/2.0/compact"
)

ZONA_CHILE = ZoneInfo("America/Santiago")


def _coordenadas(latitud, longitud):
    try:
        latitud = float(latitud)
        longitud = float(longitud)
    except (TypeError, ValueError):
        return None

    if not (
        math.isfinite(latitud)
        and math.isfinite(longitud)
        and -90 <= latitud <= 90
        and -180 <= longitud <= 180
    ):
        return None

    return round(latitud, 4), round(longitud, 4)


def _condicion(codigo):
    codigo = str(codigo or "").lower()
    base = codigo.split("_")[0]

    condiciones = {
        "clearsky": ("☀️", "Despejado"),
        "fair": ("🌤️", "Mayormente despejado"),
        "partlycloudy": ("⛅", "Parcialmente nublado"),
        "cloudy": ("☁️", "Nublado"),
        "fog": ("🌫️", "Niebla"),
        "lightrain": ("🌦️", "Lluvia ligera"),
        "rain": ("🌧️", "Lluvia"),
        "heavyrain": ("🌧️", "Lluvia intensa"),
        "lightrainshowers": ("🌦️", "Chubascos ligeros"),
        "rainshowers": ("🌧️", "Chubascos"),
        "heavyrainshowers": ("🌧️", "Chubascos intensos"),
        "snow": ("❄️", "Nieve"),
        "lightsnow": ("🌨️", "Nieve ligera"),
        "heavysnow": ("❄️", "Nieve intensa"),
        "sleet": ("🌨️", "Aguanieve"),
    }

    if "thunder" in base:
        return "⛈️", "Tormenta"

    return condiciones.get(
        base,
        ("🌡️", "Condición no clasificada"),
    )


def _contacto():
    """
    Recupera el contacto del entorno o del archivo .env.

    Permite que el servicio meteorológico reconozca
    la configuración aunque Django se haya iniciado
    antes de agregar MET_NORWAY_CONTACT.
    """
    contacto = os.environ.get(
        "MET_NORWAY_CONTACT", ""
    ).strip()

    if not contacto:
        from pathlib import Path
        from dotenv import dotenv_values

        archivo_env = (
            Path(__file__).resolve().parents[2] / ".env"
        )

        if archivo_env.is_file():
            valores = dotenv_values(archivo_env)

            contacto = str(
                valores.get("MET_NORWAY_CONTACT") or ""
            ).strip()

    if (
        not contacto
        or "@" not in contacto
        or " " in contacto
    ):
        return None

    return contacto


def _segundos_expiracion(encabezados):
    valor = encabezados.get("Expires")

    if valor:
        try:
            fecha = parsedate_to_datetime(valor)

            if fecha.tzinfo is None:
                fecha = fecha.replace(
                    tzinfo=utc_timezone.utc
                )

            segundos = int(
                (
                    fecha
                    - datetime.now(utc_timezone.utc)
                ).total_seconds()
            )

            return max(1, segundos)

        except (TypeError, ValueError, OverflowError):
            pass

    return 1800


def _obtener_pronostico(latitud, longitud):
    contacto = _contacto()

    if not contacto:
        return None, "Falta configurar MET_NORWAY_CONTACT."

    coordenadas = _coordenadas(
        latitud,
        longitud,
    )

    if coordenadas is None:
        return None, "Coordenadas GPS inválidas."

    lat, lon = coordenadas

    clave = f"gz_met_weather_v1:{lat}:{lon}"

    ahora = timezone.now()

    anterior = cache.get(clave)

    if (
        anterior
        and anterior.get("expira")
        and ahora < anterior["expira"]
    ):
        return anterior["datos"], None

    encabezados = {
        "User-Agent": (
            f"GZServicesWeather/1.0 ({contacto})"
        ),
        "Accept": "application/json",
    }

    if anterior and anterior.get("last_modified"):
        encabezados["If-Modified-Since"] = (
            anterior["last_modified"]
        )

    parametros = urlencode({
        "lat": lat,
        "lon": lon,
    })

    peticion = Request(
        f"{API_MET}?{parametros}",
        headers=encabezados,
    )

    try:
        with urlopen(peticion, timeout=10) as respuesta:
            datos = json.load(respuesta)

            segundos = _segundos_expiracion(
                respuesta.headers
            )

            cache.set(
                clave,
                {
                    "datos": datos,
                    "expira": (
                        ahora
                        + timedelta(seconds=segundos)
                    ),
                    "last_modified": (
                        respuesta.headers.get(
                            "Last-Modified"
                        )
                    ),
                },
                timeout=segundos + 86400,
            )

            return datos, None

    except HTTPError as exc:
        if exc.code == 304 and anterior:
            segundos = _segundos_expiracion(
                exc.headers
            )

            cache.set(
                clave,
                {
                    **anterior,
                    "expira": (
                        ahora
                        + timedelta(seconds=segundos)
                    ),
                },
                timeout=segundos + 86400,
            )

            return anterior["datos"], None

        return (
            None,
            f"Proveedor meteorológico: HTTP {exc.code}.",
        )

    except (
        URLError,
        TimeoutError,
        OSError,
        ValueError,
    ):
        return (
            None,
            "No fue posible consultar el proveedor meteorológico.",
        )


def consultar_clima_sitio(
    *,
    latitud,
    longitud,
    fecha,
):
    coordenadas = _coordenadas(
        latitud,
        longitud,
    )

    if coordenadas is None:
        return {
            "disponible": False,
            "mensaje": "Sin coordenadas GPS válidas.",
        }

    hoy = timezone.now().astimezone(
        ZONA_CHILE
    ).date()

    if fecha < hoy:
        return {
            "disponible": False,
            "fecha": fecha.isoformat(),
            "mensaje": "Fecha histórica sin observación disponible.",
        }

    datos, error = _obtener_pronostico(
        *coordenadas
    )

    if error:
        return {
            "disponible": False,
            "fecha": fecha.isoformat(),
            "mensaje": error,
        }

    serie = (
        datos.get("properties", {})
        .get("timeseries", [])
    )

    candidatos = []

    for punto in serie:
        try:
            instante = datetime.fromisoformat(
                punto["time"].replace(
                    "Z",
                    "+00:00",
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

        instante_local = instante.astimezone(
            ZONA_CHILE
        )

        if instante_local.date() == fecha:
            candidatos.append(
                (instante_local, punto)
            )

    if not candidatos:
        return {
            "disponible": False,
            "fecha": fecha.isoformat(),
            "mensaje": "Pronóstico aún no disponible.",
        }

    if fecha == hoy:
        referencia = timezone.now().astimezone(
            ZONA_CHILE
        )

        futuros = [
            par
            for par in candidatos
            if par[0] >= referencia
        ]

        if futuros:
            candidatos = futuros

    else:
        referencia = datetime(
            fecha.year,
            fecha.month,
            fecha.day,
            12,
            tzinfo=ZONA_CHILE,
        )

    instante, punto = min(
        candidatos,
        key=lambda par: abs(
            (
                par[0] - referencia
            ).total_seconds()
        ),
    )

    data = punto.get("data", {})

    detalles = (
        data.get("instant", {})
        .get("details", {})
    )

    periodo = (
        data.get("next_1_hours")
        or data.get("next_6_hours")
        or data.get("next_12_hours")
        or {}
    )

    codigo = (
        periodo.get("summary", {})
        .get("symbol_code")
    )

    icono, condicion = _condicion(codigo)

    temperatura = detalles.get(
        "air_temperature"
    )

    if temperatura is None:
        return {
            "disponible": False,
            "fecha": fecha.isoformat(),
            "mensaje": "Temperatura no disponible.",
        }

    return {
        "disponible": True,
        "fuente": "MET Norway",
        "fecha": fecha.isoformat(),
        "hora_pronostico": instante.isoformat(),
        "tipo": (
            "estimacion_actual"
            if fecha == hoy
            else "pronostico"
        ),
        "temperatura": temperatura,
        "icono": icono,
        "condicion": condicion,
        "codigo_meteorologico": codigo,
        "precipitacion_mm": (
            periodo.get("details", {})
            .get("precipitation_amount")
        ),
        "latitud": coordenadas[0],
        "longitud": coordenadas[1],
    }

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


# ============================================================
# EXCEPCIÓN
# ============================================================


class ErrorGeocodificacionCuadrilla(Exception):
    pass


# ============================================================
# API KEY
# ============================================================


def _obtener_google_geocoding_api_key():
    """
    Obtiene la API key destinada a llamadas server-side.

    Prioridad:

    1. GOOGLE_MAPS_SERVER_KEY
       Clave privada utilizada por Django.

    2. GOOGLE_MAPS_API_KEY
       Fallback temporal únicamente para mantener
       compatibilidad con instalaciones anteriores.

    IMPORTANTE:
    Para producción debemos utilizar GOOGLE_MAPS_SERVER_KEY
    para Geocoding.
    """

    api_key = (
        getattr(
            settings,
            "GOOGLE_MAPS_SERVER_KEY",
            "",
        )
        or getattr(
            settings,
            "GOOGLE_MAPS_API_KEY",
            "",
        )
        or ""
    )

    return api_key.strip()


# ============================================================
# MENSAJE DE ERROR GOOGLE
# ============================================================


def _construir_mensaje_error_google(
    *,
    status,
    error_message="",
):
    """
    Convierte los estados de Google en mensajes entendibles
    para el usuario.

    El error_message real se conserva para diagnóstico,
    pero nunca mostramos ni registramos la API key.
    """

    mensajes = {
        "ZERO_RESULTS": ("Google Maps no encontró la dirección indicada."),
        "REQUEST_DENIED": (
            "Google Maps rechazó la solicitud. "
            "Revisa la API key de servidor, la Geocoding API "
            "y la configuración de facturación/restricciones."
        ),
        "OVER_DAILY_LIMIT": (
            "Se alcanzó un límite de Google Maps o existe "
            "un problema con la facturación del proyecto."
        ),
        "OVER_QUERY_LIMIT": (
            "Se alcanzó temporalmente el límite de consultas " "de Google Maps."
        ),
        "INVALID_REQUEST": ("La dirección enviada a Google Maps no es válida."),
        "UNKNOWN_ERROR": ("Google Maps tuvo un error temporal. " "Intenta nuevamente."),
    }

    mensaje = mensajes.get(
        status,
        (
            "No fue posible geocodificar la dirección. "
            f"Estado Google: {status or 'desconocido'}."
        ),
    )

    if error_message:
        mensaje = f"{mensaje} " f"Detalle: {error_message}"

    return mensaje


# ============================================================
# GEOCODIFICACIÓN
# ============================================================


def geocodificar_direccion_cuadrilla(
    direccion,
):
    """
    Convierte una dirección textual en coordenadas.

    La petición se realiza desde Django utilizando
    preferentemente GOOGLE_MAPS_SERVER_KEY.

    Devuelve:

    {
        "latitud": ...,
        "longitud": ...,
        "direccion_formateada": ...,
        "place_id": ...,
    }

    No modifica la base de datos.
    """

    direccion = (direccion or "").strip()

    if not direccion:
        raise ErrorGeocodificacionCuadrilla("La dirección de la base está vacía.")

    api_key = _obtener_google_geocoding_api_key()

    if not api_key:
        raise ErrorGeocodificacionCuadrilla(
            "Google Maps no tiene configurada " "GOOGLE_MAPS_SERVER_KEY."
        )

    # ========================================================
    # PARÁMETROS
    # ========================================================

    parametros = {
        "address": direccion,
        "key": api_key,
        "region": "cl",
        "language": "es",
    }

    # ========================================================
    # LLAMADA HTTP
    # ========================================================

    try:
        response = requests.get(
            GOOGLE_GEOCODING_URL,
            params=parametros,
            timeout=10,
        )

        response.raise_for_status()

    except requests.Timeout as exc:
        logger.warning(
            "Timeout consultando Google Geocoding para dirección: %s",
            direccion,
        )

        raise ErrorGeocodificacionCuadrilla(
            "Google Maps tardó demasiado en responder."
        ) from exc

    except requests.RequestException as exc:
        logger.exception(
            "Error HTTP consultando Google Geocoding " "para dirección: %s",
            direccion,
        )

        raise ErrorGeocodificacionCuadrilla(
            "No fue posible consultar Google Maps."
        ) from exc

    # ========================================================
    # RESPUESTA JSON
    # ========================================================

    try:
        data = response.json()

    except ValueError as exc:
        logger.error("Google Geocoding devolvió una respuesta " "que no es JSON.")

        raise ErrorGeocodificacionCuadrilla(
            "Google Maps devolvió una respuesta inválida."
        ) from exc

    status = (
        data.get(
            "status",
            "",
        )
        or ""
    ).strip()

    error_message = (
        data.get(
            "error_message",
            "",
        )
        or ""
    ).strip()

    # ========================================================
    # ERROR GOOGLE
    # ========================================================

    if status != "OK":

        logger.warning(
            "Google Geocoding rechazó/falló la solicitud. "
            "status=%s error_message=%s direccion=%s",
            status,
            error_message,
            direccion,
        )

        mensaje = _construir_mensaje_error_google(
            status=status,
            error_message=error_message,
        )

        raise ErrorGeocodificacionCuadrilla(mensaje)

    # ========================================================
    # RESULTADOS
    # ========================================================

    resultados = (
        data.get(
            "results",
            [],
        )
        or []
    )

    if not resultados:
        raise ErrorGeocodificacionCuadrilla(
            "Google Maps no devolvió resultados " "para la dirección indicada."
        )

    resultado = resultados[0]

    geometry = (
        resultado.get(
            "geometry",
            {},
        )
        or {}
    )

    location = (
        geometry.get(
            "location",
            {},
        )
        or {}
    )

    latitud = location.get("lat")

    longitud = location.get("lng")

    if latitud is None or longitud is None:
        raise ErrorGeocodificacionCuadrilla(
            "Google Maps encontró la dirección, "
            "pero no devolvió coordenadas válidas."
        )

    # ========================================================
    # RESULTADO NORMALIZADO
    # ========================================================

    return {
        "latitud": float(latitud),
        "longitud": float(longitud),
        "direccion_formateada": (
            resultado.get(
                "formatted_address",
                "",
            )
            or direccion
        ),
        "place_id": (
            resultado.get(
                "place_id",
                "",
            )
            or ""
        ),
    }

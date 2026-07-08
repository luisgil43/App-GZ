# bot_gz/services_ruta.py

from __future__ import annotations

import itertools
import math
import re
from decimal import Decimal
from typing import Optional
from urllib.parse import quote_plus, urlencode

import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

from operaciones.models import ServicioCotizado, SitioMovil
from usuarios.models import CustomUser

from .models import BotRutaDireccionFrecuente

RUTA_WIZ_TTL = 60 * 30  # 30 minutos

RUTA_ESTADOS_ACTIVOS = [
    "asignado",
    "en_progreso",
]


def ruta_key(chat_id: str) -> str:
    return f"gzbot:ruta:{str(chat_id)}"


def ruta_get(chat_id: str) -> Optional[dict]:
    return cache.get(ruta_key(chat_id))


def ruta_set(chat_id: str, data: dict) -> None:
    cache.set(ruta_key(chat_id), data, timeout=RUTA_WIZ_TTL)


def ruta_clear(chat_id: str) -> None:
    cache.delete(ruta_key(chat_id))


def _norm(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = texto.replace("á", "a")
    texto = texto.replace("é", "e")
    texto = texto.replace("í", "i")
    texto = texto.replace("ó", "o")
    texto = texto.replace("ú", "u")
    texto = texto.replace("ñ", "n")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def es_intencion_planificar_ruta(texto: str) -> bool:
    """
    Detecta frases para iniciar planificación de ruta.
    """
    norm = _norm(texto)

    frases = {
        "planifica mi ruta",
        "planificar mi ruta",
        "planifica la ruta",
        "planificar la ruta",
        "arma mi ruta",
        "armar mi ruta",
        "arma la ruta",
        "mejor ruta",
        "mejor ruta para hoy",
        "ordena mis sitios",
        "ordenar mis sitios",
        "que sitio hago primero",
        "que sitio conviene hacer primero",
        "organiza mis asignaciones",
        "organizar mis asignaciones",
        "ruta de hoy",
        "ruta para hoy",
        "mis rutas",
        "mi ruta",
    }

    if norm in frases:
        return True

    tokens = set(norm.split())

    if {"ruta", "planifica"} & tokens:
        return True

    if "ruta" in tokens and ({"hoy", "sitios", "asignaciones", "proyectos"} & tokens):
        return True

    if "sitio" in tokens and "primero" in tokens:
        return True

    return False


def _to_decimal(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _coord_ok(lat, lng) -> bool:
    lat_d = _to_decimal(lat)
    lng_d = _to_decimal(lng)

    if lat_d is None or lng_d is None:
        return False

    try:
        lat_f = float(lat_d)
        lng_f = float(lng_d)
    except Exception:
        return False

    return -90 <= lat_f <= 90 and -180 <= lng_f <= 180


def _coord_tuple(lat, lng) -> Optional[tuple[float, float]]:
    if not _coord_ok(lat, lng):
        return None

    return float(_to_decimal(lat)), float(_to_decimal(lng))


def _dist_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """
    Distancia Haversine aproximada en KM.
    """
    lat1, lon1 = a
    lat2, lon2 = b

    r = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2

    return 2 * r * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def _buscar_sitio_para_servicio(servicio: ServicioCotizado) -> Optional[SitioMovil]:
    id_claro = (servicio.id_claro or "").strip()
    id_new = (getattr(servicio, "id_new", "") or "").strip()

    qs = SitioMovil.objects.all()

    sitio = None

    if id_claro:
        sitio = qs.filter(id_claro__iexact=id_claro).first()

    if not sitio and id_new:
        sitio = qs.filter(
            Q(id_sites_new__iexact=id_new) | Q(id_sites__iexact=id_new)
        ).first()

    if not sitio:
        return None

    if not _coord_ok(sitio.latitud, sitio.longitud):
        return None

    return sitio


def _servicios_planificables(usuario: CustomUser) -> list[dict]:
    """
    Busca servicios activos del técnico y los cruza con SitioMovil.
    """
    servicios = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado__in=RUTA_ESTADOS_ACTIVOS,
        )
        .prefetch_related("trabajadores_asignados")
        .order_by("fecha_creacion", "id")
    )

    out = []

    for s in servicios:
        sitio = _buscar_sitio_para_servicio(s)

        if not sitio:
            continue

        coord = _coord_tuple(sitio.latitud, sitio.longitud)

        if not coord:
            continue

        id_claro_final = (s.id_claro or sitio.id_claro or "").strip()
        id_new_final = (
            getattr(s, "id_new", "") or sitio.id_sites_new or sitio.id_sites or ""
        ).strip()

        out.append(
            {
                "servicio_id": s.id,
                "du": s.du or "—",
                "id_claro": id_claro_final,
                "id_new": id_new_final,
                "estado": s.estado or "",
                "detalle": (s.detalle_tarea or "").strip(),
                "sitio_nombre": sitio.nombre or "",
                "direccion": sitio.direccion or "",
                "comuna": sitio.comuna or "",
                "region": sitio.region or "",
                "lat": coord[0],
                "lng": coord[1],
            }
        )

    return out


def _sitio_label(item: dict) -> str:
    """
    Formato pedido:
    ID CLARO - Nombre del sitio
    """
    ref = item.get("id_claro") or item.get("id_new") or "—"
    nombre = item.get("sitio_nombre") or item.get("direccion") or "Sitio sin nombre"
    return f"{ref} – {nombre}"


def _fmt_servicio_opcion(item: dict, idx: int) -> str:
    sitio_txt = _sitio_label(item)
    comuna = item.get("comuna") or ""
    detalle = item.get("detalle") or ""

    if len(detalle) > 70:
        detalle = detalle[:67] + "…"

    line = f"{idx}) DU `{item.get('du')}` / {sitio_txt}"

    if comuna:
        line += f" – {comuna}"

    if detalle:
        line += f" – {detalle}"

    return line


def iniciar_planificacion_ruta(
    *,
    chat_id: str,
    usuario: CustomUser,
) -> str:
    """
    Inicia el wizard de planificación de ruta.
    """
    servicios = _servicios_planificables(usuario)

    if not servicios:
        ruta_clear(chat_id)
        return (
            "🗺️ *Planificación de ruta*\n\n"
            "No encontré asignaciones activas con coordenadas para armar una ruta.\n\n"
            "Para poder planificar necesito que tus proyectos estén en estado "
            "*asignado* o *en progreso*, y que el sitio tenga latitud/longitud en `SitioMovil`."
        )

    state = {
        "step": "origen",
        "usuario_id": usuario.id,
        "servicios": servicios,
        "origen": None,
        "pickup": None,
        "pickup_frecuentes": [],
        "seleccionados": [],
        "destino_final": None,
    }

    ruta_set(chat_id, state)

    msg = "🗺️ *Planificación de ruta*\n\n"
    msg += "Encontré estas asignaciones activas con ubicación:\n\n"

    for idx, item in enumerate(servicios[:20], start=1):
        msg += _fmt_servicio_opcion(item, idx) + "\n"

    if len(servicios) > 20:
        msg += f"\nMostrando 20 de {len(servicios)} sitios.\n"

    msg += (
        "\nPrimero necesito saber desde dónde sales.\n\n"
        "Responde una opción:\n"
        "1) `ubicación actual`\n"
        "2) `casa`\n"
        "3) `oficina`\n"
        "4) `bodega`\n"
        "5) escribe una dirección manual\n\n"
        "También puedes compartir tu ubicación desde Telegram.\n\n"
        "Escribe `cancelar` para salir."
    )

    return msg.strip()


def _extraer_location(message: dict) -> Optional[dict]:
    loc = message.get("location") or {}

    lat = loc.get("latitude")
    lng = loc.get("longitude")

    coord = _coord_tuple(lat, lng)

    if not coord:
        return None

    return {
        "tipo": "telegram_location",
        "label": "Ubicación actual",
        "lat": coord[0],
        "lng": coord[1],
    }


def _parse_coord_text(texto: str) -> Optional[dict]:
    """
    Acepta texto tipo:
    - -33.4489,-70.6693
    - -33.4489 -70.6693
    """
    raw = (texto or "").strip().replace(",", " ")

    nums = re.findall(r"-?\d+(?:\.\d+)?", raw)

    if len(nums) < 2:
        return None

    coord = _coord_tuple(nums[0], nums[1])

    if not coord:
        return None

    return {
        "tipo": "coordenadas",
        "label": "Ubicación indicada",
        "lat": coord[0],
        "lng": coord[1],
    }


def _direccion_parece_incompleta(texto: str) -> bool:
    """
    Evita aceptar textos demasiado genéricos como:
    - casa
    - oficina
    - bodega
    - compañero
    - aqui
    """
    raw = (texto or "").strip()
    norm = _norm(raw)

    if not norm:
        return True

    palabras_invalidas = {
        "casa",
        "mi casa",
        "oficina",
        "la oficina",
        "bodega",
        "la bodega",
        "companero",
        "compañero",
        "casa companero",
        "casa compañero",
        "aqui",
        "aca",
        "aqui mismo",
        "aca mismo",
    }

    if norm in palabras_invalidas:
        return True

    # Muy corto, sin número y sin coma normalmente no sirve para ruta real.
    tokens = norm.split()
    tiene_numero = bool(re.search(r"\d+", raw))

    if len(tokens) < 3 and not tiene_numero:
        return True

    return False


def _geocode_cache_key(direccion: str) -> str:
    norm = _norm(direccion)
    return f"gzbot:ruta:geocode:v1:{norm}"


def _geocode_address(direccion: str) -> Optional[dict]:
    """
    Convierte una dirección manual en coordenadas reales.

    Usa Google Geocoding si existe GOOGLE_MAPS_API_KEY.
    Si no hay key, usa Nominatim como respaldo simple.

    Retorna:
    {
        "direccion": "dirección normalizada",
        "lat": float,
        "lng": float,
        "fuente": "google" | "nominatim"
    }
    """
    direccion = (direccion or "").strip()

    if _direccion_parece_incompleta(direccion):
        return None

    cache_key = _geocode_cache_key(direccion)
    cached = cache.get(cache_key)
    if cached:
        return cached

    google_key = (
        getattr(settings, "GOOGLE_MAPS_API_KEY", None)
        or getattr(settings, "GOOGLE_GEOCODING_API_KEY", None)
        or ""
    )
    google_key = str(google_key).strip()

    # =========================
    # 1) Google Geocoding API
    # =========================
    if google_key:
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={
                    "address": direccion,
                    "key": google_key,
                    "region": "cl",
                    "language": "es",
                },
                timeout=12,
            )
            data = resp.json()

            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                loc = result.get("geometry", {}).get("location", {})

                lat = loc.get("lat")
                lng = loc.get("lng")

                if _coord_ok(lat, lng):
                    out = {
                        "direccion": result.get("formatted_address") or direccion,
                        "lat": float(lat),
                        "lng": float(lng),
                        "fuente": "google",
                    }
                    cache.set(cache_key, out, timeout=60 * 60 * 24 * 30)
                    return out

        except Exception:
            pass

    # =========================
    # 2) Fallback Nominatim
    # =========================
    try:
        query = direccion

        # Si no trae país, ayudamos un poco al geocoder.
        if "chile" not in _norm(query):
            query = f"{query}, Chile"

        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "cl",
                "addressdetails": 1,
            },
            headers={"User-Agent": "GZServicesBot/1.0"},
            timeout=12,
        )
        data = resp.json()

        if isinstance(data, list) and data:
            result = data[0]
            lat = result.get("lat")
            lng = result.get("lon")

            if _coord_ok(lat, lng):
                out = {
                    "direccion": result.get("display_name") or direccion,
                    "lat": float(lat),
                    "lng": float(lng),
                    "fuente": "nominatim",
                }
                cache.set(cache_key, out, timeout=60 * 60 * 24 * 30)
                return out

    except Exception:
        pass

    return None


def _mensaje_direccion_no_validada(label: str, texto: str = "") -> str:
    label = label or "ubicación"
    ejemplo = "San Pablo 1539, Santiago, Chile"

    return (
        f"📍 No pude validar esa dirección para *{label}*.\n\n"
        "Para que la ruta funcione bien necesito una dirección real y completa, "
        "o que compartas la ubicación desde Telegram.\n\n"
        "Ejemplo válido:\n"
        f"• `{ejemplo}`\n\n"
        "También puedes enviar la ubicación exacta usando el clip 📎 de Telegram → Ubicación.\n\n"
        "Escribe `cancelar` para salir."
    )


def _parse_origen_desde_texto(texto: str) -> dict:
    norm = _norm(texto)

    coord = _parse_coord_text(texto)
    if coord:
        coord["validada"] = True
        return coord

    if norm in {
        "1",
        "ubicacion actual",
        "actual",
        "mi ubicacion",
    }:
        return {
            "tipo": "current_location",
            "label": "Ubicación actual",
            "lat": None,
            "lng": None,
            "validada": True,
        }

    tipo_frecuente = _tipo_frecuente_desde_texto(texto)

    if tipo_frecuente:
        return {
            "tipo": "requiere_frecuente",
            "tipo_frecuente": tipo_frecuente,
            "label": _label_tipo_frecuente(tipo_frecuente),
            "lat": None,
            "lng": None,
            "validada": False,
        }

    direccion_original = (texto or "").strip()
    geocoded = _geocode_address(direccion_original)

    if not geocoded:
        return {
            "tipo": "direccion_invalida",
            "label": "origen",
            "direccion": direccion_original,
            "lat": None,
            "lng": None,
            "validada": False,
            "error": "direccion_no_validada",
        }

    return {
        "tipo": "direccion",
        "label": geocoded["direccion"],
        "direccion": geocoded["direccion"],
        "direccion_original": direccion_original,
        "lat": geocoded["lat"],
        "lng": geocoded["lng"],
        "validada": True,
        "fuente_geocoding": geocoded.get("fuente"),
    }


def _parse_si_no(texto: str) -> Optional[bool]:
    norm = _norm(texto)

    if norm in {"no", "n", "nop", "ninguno", "sin companero"}:
        return False

    if norm in {"si", "s", "ok", "dale", "con companero"}:
        return True

    return None


def _tipo_frecuente_desde_texto(texto: str) -> Optional[str]:
    norm = _norm(texto)

    if norm in {"2", "casa", "mi casa"}:
        return "casa"

    if norm in {"3", "oficina", "la oficina"}:
        return "oficina"

    if norm in {"4", "bodega", "la bodega"}:
        return "bodega"

    return None


def _label_tipo_frecuente(tipo: str) -> str:
    labels = {
        "casa": "Casa",
        "oficina": "Oficina",
        "bodega": "Bodega",
        "companero": "Compañero",
        "otro": "Dirección frecuente",
    }
    return labels.get(tipo, tipo)


def _direccion_frecuente_a_point(obj: BotRutaDireccionFrecuente) -> dict:
    point = {
        "tipo": "frecuente",
        "tipo_frecuente": obj.tipo,
        "label": obj.nombre or _label_tipo_frecuente(obj.tipo),
        "direccion": obj.direccion or "",
        "lat": None,
        "lng": None,
        "frecuente_id": obj.id,
    }

    if obj.latitud is not None and obj.longitud is not None:
        point["lat"] = float(obj.latitud)
        point["lng"] = float(obj.longitud)

    return point


def _buscar_direccion_frecuente(
    *,
    usuario: CustomUser,
    tipo: str,
) -> Optional[dict]:
    obj = (
        BotRutaDireccionFrecuente.objects.filter(
            usuario=usuario, tipo=tipo, activo=True
        )
        .order_by("-actualizado_en", "-id")
        .first()
    )

    if not obj:
        return None

    return _direccion_frecuente_a_point(obj)


def _buscar_direcciones_frecuentes(
    *,
    usuario: CustomUser,
    tipo: str,
    limit: int = 5,
) -> list[dict]:
    """
    Busca varias direcciones frecuentes activas.
    Para compañero conviene mostrar opciones antes de asumir.
    """
    qs = BotRutaDireccionFrecuente.objects.filter(
        usuario=usuario,
        tipo=tipo,
        activo=True,
    ).order_by("-actualizado_en", "-id")[:limit]

    return [_direccion_frecuente_a_point(obj) for obj in qs]


def _point_resumen_corto(point: dict) -> str:
    """
    Texto corto para mostrar una dirección frecuente.
    """
    direccion = (point.get("direccion") or "").strip()
    label = (point.get("label") or "").strip()

    txt = direccion or label or "Ubicación guardada"

    if len(txt) > 90:
        txt = txt[:87] + "…"

    return txt


def _prompt_pickup_con_opciones(
    *,
    usuario: CustomUser,
) -> tuple[str, list[dict]]:
    """
    Arma pregunta para compañero mostrando direcciones guardadas si existen.
    """
    frecuentes = _buscar_direcciones_frecuentes(
        usuario=usuario,
        tipo="companero",
        limit=5,
    )

    msg = "¿Debes pasar a buscar a un compañero antes de iniciar la ruta?\n\n"

    if frecuentes:
        msg += "Tengo estas direcciones guardadas para compañero:\n\n"

        for idx, point in enumerate(frecuentes, start=1):
            msg += f"{idx}) {_point_resumen_corto(point)}\n"

        otro_idx = len(frecuentes) + 1
        no_idx = len(frecuentes) + 2

        msg += (
            f"\n{otro_idx}) `otro compañero / otra dirección`\n"
            f"{no_idx}) `no pasar a buscar a nadie`\n\n"
            "También puedes escribir:\n"
            "• `no`\n"
            "• `otro`\n"
            "• o compartir/escribir una ubicación nueva directamente\n\n"
            "Escribe `cancelar` para salir."
        )
    else:
        msg += (
            "Responde:\n"
            "• `no`\n"
            "• `sí`\n"
            "• o comparte/escribe la ubicación del compañero directamente\n\n"
            "Escribe `cancelar` para salir."
        )

    return msg.strip(), frecuentes


def _parse_pickup_frecuente_choice(
    texto: str,
    frecuentes: list[dict],
) -> dict:
    """
    Interpreta respuesta cuando hay direcciones frecuentes de compañero.

    Retorna:
    {
      "accion": "usar" | "otro" | "no" | "invalid",
      "point": dict | None
    }
    """
    norm = _norm(texto)

    if norm in {"no", "n", "nop", "ninguno", "sin companero", "no pasar", "no paso"}:
        return {"accion": "no", "point": None}

    if norm in {
        "otro",
        "otra",
        "otro companero",
        "otra direccion",
        "nueva direccion",
        "direccion nueva",
        "ubicacion nueva",
    }:
        return {"accion": "otro", "point": None}

    if norm.isdigit():
        idx = int(norm)

        if 1 <= idx <= len(frecuentes):
            return {
                "accion": "usar",
                "point": frecuentes[idx - 1],
            }

        if idx == len(frecuentes) + 1:
            return {"accion": "otro", "point": None}

        if idx == len(frecuentes) + 2:
            return {"accion": "no", "point": None}

    return {"accion": "invalid", "point": None}


def _guardar_direccion_frecuente(
    *,
    usuario: CustomUser,
    tipo: str,
    point: dict,
) -> Optional[BotRutaDireccionFrecuente]:
    if not point:
        return None

    lat = point.get("lat")
    lng = point.get("lng")

    label_tipo = _label_tipo_frecuente(tipo)

    direccion = (point.get("direccion") or "").strip()
    label = (point.get("label") or "").strip()

    if not direccion:
        if lat is not None and lng is not None:
            direccion = f"Ubicación guardada por Telegram ({lat}, {lng})"
        else:
            direccion = label or label_tipo

    nombre = label_tipo

    # Para casa/oficina/bodega queremos una sola dirección activa.
    if tipo != "companero":
        obj, _created = BotRutaDireccionFrecuente.objects.update_or_create(
            usuario=usuario,
            tipo=tipo,
            activo=True,
            defaults={
                "nombre": nombre[:120],
                "direccion": direccion,
                "latitud": lat if lat is not None else None,
                "longitud": lng if lng is not None else None,
            },
        )
        return obj

    # Para compañero pueden existir varias direcciones guardadas.
    # Evitamos duplicar si ya existe una igual por coordenadas o dirección.
    qs = BotRutaDireccionFrecuente.objects.filter(
        usuario=usuario,
        tipo="companero",
        activo=True,
    )

    if lat is not None and lng is not None:
        existente = qs.filter(latitud=lat, longitud=lng).first()
        if existente:
            existente.nombre = nombre[:120]
            existente.direccion = direccion
            existente.save(update_fields=["nombre", "direccion", "actualizado_en"])
            return existente

    if direccion:
        existente = qs.filter(direccion__iexact=direccion).first()
        if existente:
            existente.nombre = nombre[:120]
            if lat is not None:
                existente.latitud = lat
            if lng is not None:
                existente.longitud = lng
            existente.save(
                update_fields=[
                    "nombre",
                    "latitud",
                    "longitud",
                    "actualizado_en",
                ]
            )
            return existente

    return BotRutaDireccionFrecuente.objects.create(
        usuario=usuario,
        tipo="companero",
        nombre=nombre[:120],
        direccion=direccion,
        latitud=lat if lat is not None else None,
        longitud=lng if lng is not None else None,
        activo=True,
    )


def _point_desde_texto_o_location(
    *,
    texto: str,
    message: dict,
    label_default: str,
) -> dict:
    loc = _extraer_location(message)

    if loc:
        loc["label"] = label_default
        loc["direccion"] = f"Ubicación compartida por Telegram - {label_default}"
        loc["tipo"] = "telegram_location"
        loc["validada"] = True
        return loc

    coord = _parse_coord_text(texto)

    if coord:
        coord["label"] = label_default
        coord["direccion"] = f"Coordenadas indicadas - {label_default}"
        coord["tipo"] = "coordenadas"
        coord["validada"] = True
        return coord

    direccion_original = (texto or "").strip()

    geocoded = _geocode_address(direccion_original)

    if not geocoded:
        return {
            "tipo": "direccion_invalida",
            "label": label_default,
            "direccion": direccion_original,
            "lat": None,
            "lng": None,
            "validada": False,
            "error": "direccion_no_validada",
        }

    return {
        "tipo": "direccion",
        "label": label_default,
        "direccion": geocoded["direccion"],
        "direccion_original": direccion_original,
        "lat": geocoded["lat"],
        "lng": geocoded["lng"],
        "validada": True,
        "fuente_geocoding": geocoded.get("fuente"),
    }


def _prompt_ingresar_direccion_frecuente(tipo: str) -> str:
    label = _label_tipo_frecuente(tipo)

    return (
        f"📍 Todavía no tengo guardada tu dirección de *{label}*.\n\n"
        f"Compárteme la ubicación de *{label}* desde Telegram "
        "o escribe la dirección completa.\n\n"
        "Ejemplo:\n"
        "• `Lord Cochrane 347, Santiago`\n\n"
        "Escribe `cancelar` para salir."
    )


def _prompt_confirmar_point(point: dict, contexto_label: str) -> str:
    direccion = (point.get("direccion") or "").strip()
    label = (point.get("label") or "").strip()

    if point.get("tipo") == "telegram_location":
        ubicacion_txt = "Ubicación compartida desde Telegram"
    elif point.get("tipo") == "coordenadas":
        ubicacion_txt = "Coordenadas indicadas manualmente"
    else:
        ubicacion_txt = direccion or label or "Ubicación indicada"

    msg = f"📍 Validé esta ubicación para *{contexto_label}*:\n\n"
    msg += f"*{ubicacion_txt}*\n"

    if point.get("lat") is not None and point.get("lng") is not None:
        msg += (
            "\nLa usaré como punto exacto en Google Maps.\n"
            f"https://www.google.com/maps/search/?api=1&query={point.get('lat')},{point.get('lng')}\n"
        )

    msg += (
        "\n¿Está correcta?\n"
        "• `sí`\n"
        "• `no`\n"
        "• o escribe la dirección corregida\n\n"
        "Escribe `cancelar` para salir."
    )

    return msg


def _prompt_guardar_frecuente(tipo: str, point: dict) -> str:
    label = _label_tipo_frecuente(tipo)

    if tipo == "companero":
        return (
            "¿Deseas guardar esta dirección como ubicación de *Compañero* "
            "para poder elegirla en futuras rutas?\n\n"
            "Responde:\n"
            "• `sí`\n"
            "• `no`\n\n"
            "Escribe `cancelar` para salir."
        )

    return (
        f"¿Deseas guardar esta dirección como *{label}* para no tener que ingresarla todos los días?\n\n"
        "Responde:\n"
        "• `sí`\n"
        "• `no`\n\n"
        "Escribe `cancelar` para salir."
    )


def _continuar_a_pickup(state: dict, chat_id: str) -> str:
    state["step"] = "pickup"

    # Guardamos opciones frecuentes del compañero para no asumir automáticamente.
    usuario_id = state.get("usuario_id")
    frecuentes = []

    if usuario_id:
        try:
            usuario = CustomUser.objects.filter(id=usuario_id).first()
            if usuario:
                _msg, frecuentes = _prompt_pickup_con_opciones(usuario=usuario)
        except Exception:
            frecuentes = []

    state["pickup_frecuentes"] = frecuentes
    ruta_set(chat_id, state)

    if usuario_id:
        try:
            usuario = CustomUser.objects.filter(id=usuario_id).first()
            if usuario:
                msg, frecuentes = _prompt_pickup_con_opciones(usuario=usuario)
                state["pickup_frecuentes"] = frecuentes
                ruta_set(chat_id, state)
                return msg
        except Exception:
            pass

    return (
        "Perfecto.\n\n"
        "¿Debes pasar a buscar a un compañero antes de iniciar la ruta?\n\n"
        "Responde:\n"
        "• `no`\n"
        "• `sí`\n"
        "• o comparte/escribe la ubicación del compañero directamente\n\n"
        "Escribe `cancelar` para salir."
    )


def _continuar_a_seleccion(state: dict, chat_id: str) -> str:
    servicios = state.get("servicios") or []

    state["step"] = "seleccion"
    ruta_set(chat_id, state)

    msg = "Ahora dime qué sitios quieres incluir en la ruta.\n\n"
    msg += "Responde `todos` o indica los números separados por coma.\n\n"

    for idx, item in enumerate(servicios[:20], start=1):
        msg += _fmt_servicio_opcion(item, idx) + "\n"

    return msg.strip()


def _parse_indices(texto: str, max_n: int) -> list[int]:
    norm = _norm(texto)

    if norm in {"todos", "todas", "todo", "usar todos", "usar todas"}:
        return list(range(1, max_n + 1))

    nums = re.findall(r"\b\d{1,3}\b", norm)

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


def _point_coord(point: Optional[dict]) -> Optional[tuple[float, float]]:
    if not point:
        return None

    if point.get("lat") is None or point.get("lng") is None:
        return None

    try:
        return float(point["lat"]), float(point["lng"])
    except Exception:
        return None


def _servicio_coord(item: dict) -> tuple[float, float]:
    return float(item["lat"]), float(item["lng"])


def _distancia_total_aprox(
    *,
    inicio: Optional[tuple[float, float]],
    servicios: list[dict],
    destino: Optional[tuple[float, float]],
) -> float:
    """
    Calcula distancia aproximada total para comparar rutas.
    """
    if not servicios:
        return 0.0

    total = 0.0
    actual = inicio

    for item in servicios:
        c = _servicio_coord(item)

        if actual:
            total += _dist_km(actual, c)

        actual = c

    if destino and actual:
        total += _dist_km(actual, destino)

    return total


def _ordenar_por_cercania(
    *,
    origen: Optional[tuple[float, float]],
    servicios: list[dict],
) -> list[dict]:
    """
    Orden simple nearest-neighbor.
    Si no hay coordenada real de origen, mantiene orden original.
    """
    if not servicios:
        return []

    if not origen:
        return servicios

    pendientes = list(servicios)
    ordenados = []
    actual = origen

    while pendientes:
        pendientes.sort(key=lambda item: _dist_km(actual, _servicio_coord(item)))
        elegido = pendientes.pop(0)
        ordenados.append(elegido)
        actual = _servicio_coord(elegido)

    return ordenados


def _ordenar_ruta_con_destino(
    *,
    origen: dict,
    pickup: Optional[dict],
    servicios: list[dict],
    destino_final: Optional[dict],
) -> tuple[list[dict], dict]:
    """
    Ordena la ruta considerando:
    - origen
    - parada para compañero
    - sitios seleccionados
    - destino final

    Si hay pocos sitios, prueba todas las combinaciones.
    Si hay muchos, usa heurística por cercanía.
    """
    origen_coord = _point_coord(origen)
    pickup_coord = _point_coord(pickup)
    destino_coord = _point_coord(destino_final)

    inicio_real = pickup_coord or origen_coord

    if not servicios:
        return [], {
            "metodo": "sin_servicios",
            "distancia_km": 0,
            "considera_destino": bool(destino_coord),
            "considera_pickup": bool(pickup),
        }

    # Si no hay coordenada de inicio real, mantenemos orden original.
    if not inicio_real:
        return servicios, {
            "metodo": "orden_original_sin_coord_origen",
            "distancia_km": _distancia_total_aprox(
                inicio=None,
                servicios=servicios,
                destino=destino_coord,
            ),
            "considera_destino": bool(destino_coord),
            "considera_pickup": bool(pickup),
        }

    # Para 8 sitios o menos, probamos todas las combinaciones y elegimos la menor distancia.
    if len(servicios) <= 8:
        mejor = None
        mejor_dist = None

        for perm in itertools.permutations(servicios):
            perm_list = list(perm)
            dist = _distancia_total_aprox(
                inicio=inicio_real,
                servicios=perm_list,
                destino=destino_coord,
            )

            if mejor_dist is None or dist < mejor_dist:
                mejor_dist = dist
                mejor = perm_list

        return mejor or servicios, {
            "metodo": "comparacion_total",
            "distancia_km": round(float(mejor_dist or 0), 1),
            "considera_destino": bool(destino_coord),
            "considera_pickup": bool(pickup),
        }

    # Para más de 8, usamos cercanía.
    ordenados = _ordenar_por_cercania(
        origen=inicio_real,
        servicios=servicios,
    )

    return ordenados, {
        "metodo": "cercania",
        "distancia_km": round(
            _distancia_total_aprox(
                inicio=inicio_real,
                servicios=ordenados,
                destino=destino_coord,
            ),
            1,
        ),
        "considera_destino": bool(destino_coord),
        "considera_pickup": bool(pickup),
    }


def _maps_place_from_point(point: dict) -> str:
    if point.get("lat") is not None and point.get("lng") is not None:
        return f"{point['lat']},{point['lng']}"

    direccion = point.get("direccion") or point.get("label") or "Current Location"

    if point.get("tipo") == "current_location":
        return "Current+Location"

    return quote_plus(direccion)


def _maps_place_from_servicio(item: dict) -> str:
    return f"{item['lat']},{item['lng']}"


def _build_google_maps_url(
    *,
    origen: dict,
    servicios_ordenados: list[dict],
    pickup: Optional[dict] = None,
    destino_final: Optional[dict] = None,
) -> str:
    """
    Construye URL compatible con Google Maps Directions.

    ✅ Corrige:
    - Codificación correcta de waypoints.
    - Evita que Google Maps interprete textos con espacios/ñ como puntos rotos.
    - Soporta origen, pickup, sitios y destino final.
    """
    puntos_intermedios = []

    if pickup:
        puntos_intermedios.append(_maps_place_from_point(pickup))

    for item in servicios_ordenados:
        puntos_intermedios.append(_maps_place_from_servicio(item))

    origin = _maps_place_from_point(origen)

    destino_es_ultimo_sitio = (
        not destino_final or destino_final.get("tipo") == "ultimo_sitio"
    )

    if destino_es_ultimo_sitio:
        if puntos_intermedios:
            destination = puntos_intermedios[-1]
            waypoints_list = puntos_intermedios[:-1]
        else:
            destination = origin
            waypoints_list = []
    else:
        destination = _maps_place_from_point(destino_final)
        waypoints_list = puntos_intermedios

    params = {
        "api": "1",
        "origin": origin,
        "destination": destination,
        "travelmode": "driving",
        "dir_action": "navigate",
    }

    if waypoints_list:
        params["waypoints"] = "|".join(waypoints_list)

    return "https://www.google.com/maps/dir/?" + urlencode(params, safe=",|+")


def _parse_destino_final_desde_texto(
    *,
    texto: str,
    message: dict,
    origen: dict,
    pickup: Optional[dict] = None,
) -> dict:
    """
    Interpreta dónde finalizar la ruta.

    Si el usuario dice "casa de mi compañero" y ya existe pickup,
    usa la misma ubicación del compañero.
    """
    loc = _extraer_location(message)
    if loc:
        loc["label"] = "Destino final indicado"
        return loc

    coord = _parse_coord_text(texto)
    if coord:
        coord["label"] = "Destino final indicado"
        return coord

    norm = _norm(texto)

    if norm in {
        "1",
        "ultimo sitio",
        "ultimo",
        "en el ultimo sitio",
        "terminar en el ultimo sitio",
        "finalizar en el ultimo sitio",
        "dejarlo en el ultimo sitio",
    }:
        return {
            "tipo": "ultimo_sitio",
            "label": "Último sitio",
            "lat": None,
            "lng": None,
        }

    if norm in {
        "2",
        "volver al origen",
        "volver a origen",
        "volver a mi ubicacion inicial",
        "ubicacion inicial",
        "origen",
        "inicio",
        "volver al inicio",
        "retornar al inicio",
        "retornar al origen",
    }:
        destino = dict(origen or {})
        destino["label"] = "Volver al origen"
        destino["tipo"] = destino.get("tipo") or "origen"
        return destino

    frases_companero = {
        "6",
        "casa de mi companero",
        "la casa de mi companero",
        "casa del companero",
        "la casa del companero",
        "donde mi companero",
        "donde el companero",
        "donde esta mi companero",
        "volver donde mi companero",
        "volver donde el companero",
        "retornar donde mi companero",
        "retornar donde el companero",
        "dejar al companero",
        "dejar a mi companero",
        "dejar el companero",
        "dejarlo en su casa",
        "dejarlo donde vive",
        "casa de el",
        "su casa",
    }

    if norm in frases_companero or (
        "companero" in norm
        and (
            "casa" in norm
            or "volver" in norm
            or "retornar" in norm
            or "dejar" in norm
            or "ubicacion" in norm
        )
    ):
        if pickup:
            destino = dict(pickup)
            destino["label"] = "Casa / ubicación del compañero"
            destino["tipo"] = destino.get("tipo") or "pickup"
            return destino

        return {
            "tipo": "requiere_frecuente",
            "tipo_frecuente": "companero",
            "label": "Casa / ubicación del compañero",
            "lat": None,
            "lng": None,
        }

    if norm in {"3", "casa", "mi casa", "volver a casa", "retornar a casa"}:
        return {
            "tipo": "requiere_frecuente",
            "tipo_frecuente": "casa",
            "label": "Casa",
            "lat": None,
            "lng": None,
        }

    if norm in {"4", "oficina", "la oficina", "volver a oficina", "retornar a oficina"}:
        return {
            "tipo": "requiere_frecuente",
            "tipo_frecuente": "oficina",
            "label": "Oficina",
            "lat": None,
            "lng": None,
        }

    if norm in {"5", "bodega", "la bodega", "volver a bodega", "retornar a bodega"}:
        return {
            "tipo": "requiere_frecuente",
            "tipo_frecuente": "bodega",
            "label": "Bodega",
            "lat": None,
            "lng": None,
        }

    return {
        "tipo": "direccion",
        "label": texto.strip(),
        "direccion": texto.strip(),
        "lat": None,
        "lng": None,
    }


def _prompt_destino_final() -> str:
    return (
        "Perfecto. Ahora dime dónde quieres finalizar la ruta.\n\n"
        "Responde una opción:\n"
        "1) `último sitio`\n"
        "2) `volver al origen`\n"
        "3) `casa`\n"
        "4) `oficina`\n"
        "5) `bodega`\n"
        "6) `casa de mi compañero`\n"
        "7) escribe otra dirección\n\n"
        "También puedes compartir una ubicación desde Telegram.\n\n"
        "Escribe `cancelar` para salir."
    )


def _analisis_ruta_texto(
    *,
    origen: dict,
    pickup: Optional[dict],
    servicios_ordenados: list[dict],
    destino_final: Optional[dict],
    analisis: dict,
) -> str:
    if not servicios_ordenados:
        return ""

    primero = servicios_ordenados[0]
    ultimo = servicios_ordenados[-1]

    origen_label = origen.get("label") or origen.get("direccion") or "Ubicación actual"
    pickup_label = ""
    if pickup:
        pickup_label = (
            pickup.get("label") or pickup.get("direccion") or "Punto del compañero"
        )

    destino_label = (
        (destino_final or {}).get("label")
        or (destino_final or {}).get("direccion")
        or "Último sitio"
    )

    msg = "🧠 *Análisis de ruta*\n\n"

    msg += f"Te conviene hacer primero *{_sitio_label(primero)}*"

    if len(servicios_ordenados) > 1:
        msg += f" y dejar *{_sitio_label(ultimo)}* hacia el final"

    msg += ".\n\n"

    msg += "La ruta fue armada considerando:\n"
    msg += f"• Punto de inicio: *{origen_label}*\n"

    if pickup:
        msg += f"• Primero pasar por: *{pickup_label}*\n"

    msg += f"• Punto donde quieres finalizar: *{destino_label}*\n"

    if analisis.get("distancia_km"):
        msg += f"• Recorrido aproximado comparado: *{analisis['distancia_km']} km*\n"

    metodo = analisis.get("metodo")

    if metodo == "comparacion_total":
        msg += (
            "\nSe compararon las combinaciones posibles de los sitios seleccionados "
            "y se eligió el orden con menor recorrido aproximado según coordenadas.\n"
        )
    elif metodo == "cercania":
        msg += (
            "\nComo hay varios sitios, se ordenaron por cercanía desde el punto inicial "
            "para evitar recorridos innecesarios.\n"
        )
    elif metodo == "orden_original_sin_coord_origen":
        msg += (
            "\nNo tengo coordenada exacta del origen o destino, por eso Google Maps terminará "
            "de ajustar el recorrido al abrir la ruta.\n"
        )

    if (
        pickup
        and destino_final
        and destino_final.get("label") == "Casa / ubicación del compañero"
    ):
        msg += (
            "\nComo indicaste que debes finalizar en la casa/ubicación del compañero, "
            "la ruta también considera retornar a ese mismo punto al terminar los sitios.\n"
        )

    msg += (
        "\nImportante: el bot calcula la conveniencia por coordenadas y distancia aproximada. "
        "El tráfico real, cortes y tiempos exactos los calculará Google Maps al abrir el enlace."
    )

    return msg.strip()


def _resumen_ruta(
    *,
    origen: dict,
    servicios_ordenados: list[dict],
    pickup: Optional[dict] = None,
    destino_final: Optional[dict] = None,
    analisis: Optional[dict] = None,
) -> str:
    analisis = analisis or {}

    url = _build_google_maps_url(
        origen=origen,
        servicios_ordenados=servicios_ordenados,
        pickup=pickup,
        destino_final=destino_final,
    )

    msg = "🗺️ *Ruta planificada*\n\n"
    msg += f"Origen: *{origen.get('label') or origen.get('direccion') or 'Ubicación actual'}*\n"

    if pickup:
        msg += f"Parada para compañero: *{pickup.get('label') or pickup.get('direccion') or 'Punto indicado'}*\n"

    destino_label = (
        (destino_final or {}).get("label")
        or (destino_final or {}).get("direccion")
        or "Último sitio"
    )
    msg += f"Destino final: *{destino_label}*\n"

    analisis_txt = _analisis_ruta_texto(
        origen=origen,
        pickup=pickup,
        servicios_ordenados=servicios_ordenados,
        destino_final=destino_final,
        analisis=analisis,
    )

    if analisis_txt:
        msg += "\n" + analisis_txt + "\n"

    msg += "\nOrden sugerido:\n"

    for idx, item in enumerate(servicios_ordenados, start=1):
        comuna = item.get("comuna") or ""
        msg += f"{idx}) DU `{item.get('du')}` / {_sitio_label(item)}"

        if comuna:
            msg += f" – {comuna}"

        msg += "\n"

    msg += f"\n📍 Abrir ruta en Google Maps:\n{url}\n\n"
    msg += (
        "Google Maps calculará el tráfico real al abrir la ruta.\n"
        "Recuerda validar en terreno restricciones de acceso, llaves o permisos del sitio."
    )

    return msg.strip()


def procesar_planificacion_ruta(
    *,
    chat_id: str,
    usuario: CustomUser,
    texto: str,
    message: Optional[dict] = None,
) -> str:
    """
    Procesa el wizard completo de ruta.

    Versión inteligente:
    - Si el usuario elige casa/oficina/bodega, busca dirección frecuente.
    - Si no existe, pide dirección o ubicación.
    - Permite confirmar/corregir direcciones.
    - Valida/geocodifica direcciones manuales antes de aceptarlas.
    - Permite guardar frecuentes.
    - Para compañero NO asume automáticamente la dirección guardada:
      primero pregunta si desea usar una guardada, otra nueva o no pasar a buscar a nadie.
    - Permite usar la ubicación del compañero como destino final.
    """
    message = message or {}
    texto = texto or ""

    state = ruta_get(chat_id)

    if not state:
        return iniciar_planificacion_ruta(chat_id=chat_id, usuario=usuario)

    norm = _norm(texto)

    if norm in {"cancelar", "salir", "no continuar"}:
        ruta_clear(chat_id)
        return "✅ Listo, cancelé la planificación de ruta."

    step = state.get("step")
    servicios = state.get("servicios") or []

    # =========================
    # ORIGEN
    # =========================
    if step == "origen":
        loc = _extraer_location(message)

        if loc:
            loc["validada"] = True
            origen = loc
        else:
            origen = _parse_origen_desde_texto(texto)

        if origen.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada("origen", texto)

        if origen.get("tipo") == "requiere_frecuente":
            tipo = origen.get("tipo_frecuente")

            frecuente = _buscar_direccion_frecuente(
                usuario=usuario,
                tipo=tipo,
            )

            if frecuente:
                state["origen"] = frecuente
                ruta_set(chat_id, state)
                return _continuar_a_pickup(state, chat_id)

            state["pending_point"] = {
                "target": "origen",
                "tipo_frecuente": tipo,
                "label": _label_tipo_frecuente(tipo),
                "preguntar_guardar": True,
            }
            state["step"] = "esperando_direccion"
            ruta_set(chat_id, state)

            return _prompt_ingresar_direccion_frecuente(tipo)

        if origen.get("tipo") == "direccion" and not origen.get("lat"):
            return _mensaje_direccion_no_validada("origen", texto)

        state["origen"] = origen
        ruta_set(chat_id, state)
        return _continuar_a_pickup(state, chat_id)

    # =========================
    # ESPERANDO DIRECCIÓN
    # =========================
    if step == "esperando_direccion":
        pending = state.get("pending_point") or {}
        label = pending.get("label") or "ubicación"

        point = _point_desde_texto_o_location(
            texto=texto,
            message=message,
            label_default=label,
        )

        if point.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada(label, texto)

        state["pending_point_value"] = point
        state["step"] = "confirmar_direccion"
        ruta_set(chat_id, state)

        return _prompt_confirmar_point(point, label)

    # =========================
    # CONFIRMAR / CORREGIR DIRECCIÓN
    # =========================
    if step == "confirmar_direccion":
        pending = state.get("pending_point") or {}
        point = state.get("pending_point_value") or {}

        target = pending.get("target")
        tipo_frecuente = pending.get("tipo_frecuente")
        preguntar_guardar = bool(pending.get("preguntar_guardar"))

        si_no = _parse_si_no(texto)

        if si_no is False:
            state["step"] = "esperando_direccion"
            ruta_set(chat_id, state)

            label = pending.get("label") or "ubicación"
            return (
                f"Perfecto, corrígeme la dirección de *{label}*.\n\n"
                "Puedes escribirla nuevamente con dirección completa o compartir ubicación desde Telegram.\n\n"
                "Ejemplo:\n"
                "• `San Pablo 1539, Santiago, Chile`"
            )

        if si_no is True:
            if point.get("tipo") == "direccion_invalida":
                label = pending.get("label") or "ubicación"
                return _mensaje_direccion_no_validada(
                    label, point.get("direccion") or texto
                )

            if target == "origen":
                state["origen"] = point

            elif target == "pickup":
                state["pickup"] = point

            elif target == "destino_final":
                state["destino_final"] = point

            if preguntar_guardar and tipo_frecuente:
                state["step"] = "guardar_frecuente"
                ruta_set(chat_id, state)
                return _prompt_guardar_frecuente(tipo_frecuente, point)

            state.pop("pending_point", None)
            state.pop("pending_point_value", None)
            ruta_set(chat_id, state)

            if target == "origen":
                return _continuar_a_pickup(state, chat_id)

            if target == "pickup":
                return _continuar_a_seleccion(state, chat_id)

            if target == "destino_final":
                origen = state.get("origen") or {
                    "tipo": "current_location",
                    "label": "Ubicación actual",
                    "lat": None,
                    "lng": None,
                }
                pickup = state.get("pickup")
                seleccionados = state.get("seleccionados") or []
                destino_final = state.get("destino_final")

                servicios_ordenados, analisis = _ordenar_ruta_con_destino(
                    origen=origen,
                    pickup=pickup,
                    servicios=seleccionados,
                    destino_final=destino_final,
                )

                ruta_clear(chat_id)

                return _resumen_ruta(
                    origen=origen,
                    servicios_ordenados=servicios_ordenados,
                    pickup=pickup,
                    destino_final=destino_final,
                    analisis=analisis,
                )

        label = pending.get("label") or "ubicación"

        nuevo_point = _point_desde_texto_o_location(
            texto=texto,
            message=message,
            label_default=label,
        )

        if nuevo_point.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada(label, texto)

        state["pending_point_value"] = nuevo_point
        ruta_set(chat_id, state)

        return _prompt_confirmar_point(nuevo_point, label)

    # =========================
    # GUARDAR FRECUENTE
    # =========================
    if step == "guardar_frecuente":
        pending = state.get("pending_point") or {}
        point = state.get("pending_point_value") or {}

        target = pending.get("target")
        tipo_frecuente = pending.get("tipo_frecuente")

        si_no = _parse_si_no(texto)

        if si_no is None:
            return (
                "No entendí si deseas guardarla.\n\n" "Responde:\n" "• `sí`\n" "• `no`"
            )

        guardada = False

        if si_no is True and tipo_frecuente and point:
            if point.get("tipo") == "direccion_invalida":
                return _mensaje_direccion_no_validada(
                    pending.get("label") or "ubicación",
                    point.get("direccion") or texto,
                )

            obj = _guardar_direccion_frecuente(
                usuario=usuario,
                tipo=tipo_frecuente,
                point=point,
            )
            guardada = bool(obj)

        state.pop("pending_point", None)
        state.pop("pending_point_value", None)

        prefijo = ""
        if si_no is True:
            if guardada:
                prefijo = "✅ Dirección guardada correctamente.\n\n"
            else:
                prefijo = (
                    "⚠️ No pude guardar la dirección, pero continuaré con la ruta.\n\n"
                )

        ruta_set(chat_id, state)

        if target == "origen":
            return prefijo + _continuar_a_pickup(state, chat_id)

        if target == "pickup":
            return prefijo + _continuar_a_seleccion(state, chat_id)

        if target == "destino_final":
            origen = state.get("origen") or {
                "tipo": "current_location",
                "label": "Ubicación actual",
                "lat": None,
                "lng": None,
            }
            pickup = state.get("pickup")
            seleccionados = state.get("seleccionados") or []
            destino_final = state.get("destino_final")

            servicios_ordenados, analisis = _ordenar_ruta_con_destino(
                origen=origen,
                pickup=pickup,
                servicios=seleccionados,
                destino_final=destino_final,
            )

            ruta_clear(chat_id)

            return prefijo + _resumen_ruta(
                origen=origen,
                servicios_ordenados=servicios_ordenados,
                pickup=pickup,
                destino_final=destino_final,
                analisis=analisis,
            )

    # =========================
    # PICKUP / COMPAÑERO
    # =========================
    if step == "pickup":
        loc = _extraer_location(message)
        coord = _parse_coord_text(texto)

        # Si comparte ubicación directamente, la tomamos como nuevo punto de compañero.
        if loc:
            loc["label"] = "ubicación del compañero"
            loc["direccion"] = "Ubicación compartida por Telegram - compañero"
            loc["validada"] = True

            state["pending_point"] = {
                "target": "pickup",
                "tipo_frecuente": "companero",
                "label": "ubicación del compañero",
                "preguntar_guardar": True,
            }
            state["pending_point_value"] = loc
            state["step"] = "confirmar_direccion"
            ruta_set(chat_id, state)

            return _prompt_confirmar_point(loc, "ubicación del compañero")

        # Si escribe coordenadas directamente, las tomamos como nuevo punto.
        if coord:
            coord["label"] = "ubicación del compañero"
            coord["direccion"] = "Coordenadas indicadas - compañero"
            coord["validada"] = True

            state["pending_point"] = {
                "target": "pickup",
                "tipo_frecuente": "companero",
                "label": "ubicación del compañero",
                "preguntar_guardar": True,
            }
            state["pending_point_value"] = coord
            state["step"] = "confirmar_direccion"
            ruta_set(chat_id, state)

            return _prompt_confirmar_point(coord, "ubicación del compañero")

        # Direcciones frecuentes ya cargadas en el state.
        frecuentes = state.get("pickup_frecuentes") or []

        # Si por algún motivo no están en state, las buscamos ahora.
        if not frecuentes:
            frecuentes = _buscar_direcciones_frecuentes(
                usuario=usuario,
                tipo="companero",
                limit=5,
            )
            state["pickup_frecuentes"] = frecuentes
            ruta_set(chat_id, state)

        # Si hay direcciones guardadas, NO asumimos con "sí".
        if frecuentes:
            choice = _parse_pickup_frecuente_choice(texto, frecuentes)

            if choice["accion"] == "no":
                state["pickup"] = None
                ruta_set(chat_id, state)
                return _continuar_a_seleccion(state, chat_id)

            if choice["accion"] == "usar":
                point = choice["point"]

                if point.get("tipo") == "direccion_invalida":
                    return _mensaje_direccion_no_validada(
                        "ubicación del compañero",
                        point.get("direccion") or texto,
                    )

                state["pickup"] = point
                ruta_set(chat_id, state)
                return _continuar_a_seleccion(state, chat_id)

            if choice["accion"] == "otro":
                state["pending_point"] = {
                    "target": "pickup",
                    "tipo_frecuente": "companero",
                    "label": "ubicación del compañero",
                    "preguntar_guardar": True,
                }
                state["step"] = "esperando_direccion"
                ruta_set(chat_id, state)

                return (
                    "Perfecto. Envíame la ubicación del otro compañero por Telegram "
                    "o escribe la dirección completa donde debes pasar a buscarlo.\n\n"
                    "Ejemplo:\n"
                    "• `San Pablo 1539, Santiago, Chile`\n\n"
                    "Escribe `cancelar` para salir."
                )

            msg, frecuentes_actualizadas = _prompt_pickup_con_opciones(usuario=usuario)
            state["pickup_frecuentes"] = frecuentes_actualizadas
            ruta_set(chat_id, state)

            return "No entendí cuál opción quieres usar para el compañero.\n\n" + msg

        # Si no hay direcciones frecuentes guardadas, procesamos sí/no normal.
        si_no = _parse_si_no(texto)

        if si_no is False:
            state["pickup"] = None
            ruta_set(chat_id, state)
            return _continuar_a_seleccion(state, chat_id)

        if si_no is True:
            state["pending_point"] = {
                "target": "pickup",
                "tipo_frecuente": "companero",
                "label": "ubicación del compañero",
                "preguntar_guardar": True,
            }
            state["step"] = "esperando_direccion"
            ruta_set(chat_id, state)

            return (
                "Envíame la ubicación del compañero por Telegram "
                "o escribe la dirección donde debes pasar a buscarlo.\n\n"
                "Ejemplo:\n"
                "• `San Pablo 1539, Santiago, Chile`\n\n"
                "Escribe `cancelar` para salir."
            )

        # Si escribió una dirección directamente.
        point = _point_desde_texto_o_location(
            texto=texto,
            message=message,
            label_default="ubicación del compañero",
        )

        if point.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada("ubicación del compañero", texto)

        state["pending_point"] = {
            "target": "pickup",
            "tipo_frecuente": "companero",
            "label": "ubicación del compañero",
            "preguntar_guardar": True,
        }
        state["pending_point_value"] = point
        state["step"] = "confirmar_direccion"
        ruta_set(chat_id, state)

        return _prompt_confirmar_point(point, "ubicación del compañero")

    # =========================
    # PICKUP LOCATION LEGACY
    # =========================
    if step == "pickup_location":
        point = _point_desde_texto_o_location(
            texto=texto,
            message=message,
            label_default="ubicación del compañero",
        )

        if point.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada("ubicación del compañero", texto)

        state["pending_point"] = {
            "target": "pickup",
            "tipo_frecuente": "companero",
            "label": "ubicación del compañero",
            "preguntar_guardar": True,
        }
        state["pending_point_value"] = point
        state["step"] = "confirmar_direccion"
        ruta_set(chat_id, state)

        return _prompt_confirmar_point(point, "ubicación del compañero")

    # =========================
    # SELECCIÓN DE SITIOS
    # =========================
    if step == "seleccion":
        indices = _parse_indices(texto, len(servicios))

        if not indices:
            return (
                "No entendí qué sitios quieres incluir.\n\n"
                "Responde por ejemplo:\n"
                "• `todos`\n"
                "• `1`\n"
                "• `1, 3, 4`\n\n"
                "O escribe `cancelar`."
            )

        seleccionados = []

        for i in indices:
            try:
                seleccionados.append(servicios[i - 1])
            except Exception:
                pass

        if not seleccionados:
            return "No encontré sitios válidos con esa selección."

        state["seleccionados"] = seleccionados
        state["step"] = "destino_final"
        ruta_set(chat_id, state)

        return _prompt_destino_final()

    # =========================
    # DESTINO FINAL
    # =========================
    if step == "destino_final":
        origen = state.get("origen") or {
            "tipo": "current_location",
            "label": "Ubicación actual",
            "lat": None,
            "lng": None,
        }

        pickup = state.get("pickup")
        seleccionados = state.get("seleccionados") or []

        if not seleccionados:
            ruta_clear(chat_id)
            return (
                "Se perdió la selección de sitios.\n"
                "Escribe nuevamente: `planifica mi ruta`."
            )

        destino_final = _parse_destino_final_desde_texto(
            texto=texto,
            message=message,
            origen=origen,
            pickup=pickup,
        )

        if destino_final.get("tipo") == "direccion_invalida":
            return _mensaje_direccion_no_validada("destino final", texto)

        if destino_final.get("tipo") == "requiere_frecuente":
            tipo = destino_final.get("tipo_frecuente")

            frecuente = _buscar_direccion_frecuente(
                usuario=usuario,
                tipo=tipo,
            )

            if frecuente:
                destino_final = frecuente
            else:
                state["pending_point"] = {
                    "target": "destino_final",
                    "tipo_frecuente": tipo,
                    "label": destino_final.get("label") or _label_tipo_frecuente(tipo),
                    "preguntar_guardar": True,
                }
                state["step"] = "esperando_direccion"
                ruta_set(chat_id, state)

                return _prompt_ingresar_direccion_frecuente(tipo)

        if destino_final.get("tipo") == "direccion" and not destino_final.get("lat"):
            point = _point_desde_texto_o_location(
                texto=texto,
                message=message,
                label_default="destino final",
            )

            if point.get("tipo") == "direccion_invalida":
                return _mensaje_direccion_no_validada("destino final", texto)

            destino_final = point

        state["destino_final"] = destino_final
        ruta_set(chat_id, state)

        servicios_ordenados, analisis = _ordenar_ruta_con_destino(
            origen=origen,
            pickup=pickup,
            servicios=seleccionados,
            destino_final=destino_final,
        )

        ruta_clear(chat_id)

        return _resumen_ruta(
            origen=origen,
            servicios_ordenados=servicios_ordenados,
            pickup=pickup,
            destino_final=destino_final,
            analisis=analisis,
        )

    ruta_clear(chat_id)
    return (
        "Se perdió el flujo de planificación de ruta.\n"
        "Escribe nuevamente: `planifica mi ruta`."
    )

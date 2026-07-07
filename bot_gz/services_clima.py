# bot_gz/services_clima.py

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from operaciones.models import ServicioCotizado, SitioMovil
from usuarios.models import CustomUser

from .models import AlertaClimaDiaria
from .services import send_telegram_message
from .weather_client import get_weather_uv

logger = logging.getLogger(__name__)


DEFAULT_ACTIVE_STATES = [
    "asignado",
    "en_progreso",
    
]


def _active_states() -> list[str]:
    val = getattr(settings, "BOT_GZ_CLIMA_ACTIVE_STATES", None)
    if isinstance(val, str) and val.strip():
        return [x.strip() for x in val.split(",") if x.strip()]
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return DEFAULT_ACTIVE_STATES


def _max_servicios_por_usuario() -> int:
    try:
        return int(getattr(settings, "BOT_GZ_CLIMA_MAX_SERVICIOS_POR_USUARIO", 5))
    except Exception:
        return 5


def _decimal_or_none(value):
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return None


def _resolver_sitio_para_servicio(servicio: ServicioCotizado) -> Optional[SitioMovil]:
    id_claro = (getattr(servicio, "id_claro", "") or "").strip()
    id_new = (getattr(servicio, "id_new", "") or "").strip()

    q = Q()
    has_q = False

    if id_claro:
        q |= Q(id_claro__iexact=id_claro)
        has_q = True

    if id_new:
        q |= Q(id_sites_new__iexact=id_new) | Q(id_sites__iexact=id_new)
        has_q = True

    if not has_q:
        return None

    return (
        SitioMovil.objects.filter(q)
        .exclude(latitud__isnull=True)
        .exclude(longitud__isnull=True)
        .first()
    )


def _servicios_activos_para_usuario(usuario: CustomUser):
    return (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado__in=_active_states(),
        )
        .annotate(n_tecs=Count("trabajadores_asignados", distinct=True))
        .order_by("fecha_creacion")
    )


def _build_maps_url(lat, lng) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def _crear_alerta_obj(
    *,
    usuario: CustomUser,
    servicio: ServicioCotizado,
    sitio: SitioMovil,
    fecha,
    clima: dict,
) -> AlertaClimaDiaria:
    id_claro = (getattr(servicio, "id_claro", "") or "") or (
        getattr(sitio, "id_claro", "") or ""
    )
    id_new = (getattr(servicio, "id_new", "") or "") or (
        getattr(sitio, "id_sites_new", "") or ""
    )

    return AlertaClimaDiaria.objects.create(
        trabajador=usuario,
        servicio=servicio,
        sitio=sitio,
        fecha=fecha,
        chat_id=str(getattr(usuario, "telegram_chat_id", "") or ""),
        id_claro=id_claro,
        id_new=id_new,
        nombre_sitio=getattr(sitio, "nombre", "") or "",
        direccion=getattr(sitio, "direccion", "") or "",
        latitud=_decimal_or_none(getattr(sitio, "latitud", None)),
        longitud=_decimal_or_none(getattr(sitio, "longitud", None)),
        temperatura_c=clima.get("temperatura_c"),
        sensacion_c=clima.get("sensacion_c"),
        viento_kmh=clima.get("viento_kmh"),
        indice_uv=clima.get("indice_uv"),
        nivel_uv=clima.get("nivel_uv") or "",
        factor_solar_recomendado=clima.get("factor_solar_recomendado") or "",
        radiacion_wm2=clima.get("radiacion_wm2"),
        prob_lluvia=clima.get("prob_lluvia"),
        condicion=clima.get("condicion") or "",
        fuente=clima.get("fuente") or "open-meteo",
        estado_envio="pendiente",
    )


def _fmt_num(value, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
        if n.is_integer():
            return f"{int(n)}{suffix}"
        return f"{n:.1f}{suffix}"
    except Exception:
        return f"{value}{suffix}"


def construir_mensaje_alerta_clima(
    *,
    usuario: CustomUser,
    fecha,
    alertas: list[AlertaClimaDiaria],
) -> str:
    nombre = (usuario.first_name or usuario.get_full_name() or "").strip()
    saludo = f"Hola {nombre}," if nombre else "Hola,"

    msg = (
        f"🌤️ *Alerta diaria de clima y radiación*\n"
        f"{saludo} este es el resumen preventivo para hoy *{fecha.strftime('%d-%m-%Y')}*.\n\n"
    )

    if not alertas:
        msg += (
            "No encontré sitios con coordenadas para tus asignaciones activas de hoy."
        )
        return msg

    for idx, alerta in enumerate(alertas[: _max_servicios_por_usuario()], start=1):
        sitio_nombre = (
            alerta.nombre_sitio or alerta.id_claro or alerta.id_new or "Sitio"
        )
        lat = (
            str(alerta.latitud).replace(",", ".") if alerta.latitud is not None else ""
        )
        lng = (
            str(alerta.longitud).replace(",", ".")
            if alerta.longitud is not None
            else ""
        )

        nivel_uv = alerta.nivel_uv or "—"
        factor_solar = alerta.factor_solar_recomendado or "USAR protector solar"

        if nivel_uv == "Bajo":
            uv_icon = "🟢"
        elif nivel_uv == "Moderado":
            uv_icon = "🟡"
        elif nivel_uv == "Alto":
            uv_icon = "🟠"
        elif nivel_uv == "Muy alto":
            uv_icon = "🔴"
        elif nivel_uv == "Extremo":
            uv_icon = "🟣"
        else:
            uv_icon = "⚪"

        msg += f"*{idx}) {sitio_nombre}*\n"

        if alerta.id_claro:
            msg += f"• ID Claro: `{alerta.id_claro}`\n"
        if alerta.id_new:
            msg += f"• ID New: `{alerta.id_new}`\n"

        msg += f"• Condición: {alerta.condicion or '—'}\n"
        msg += f"• Temperatura: {_fmt_num(alerta.temperatura_c, '°C')}\n"
        msg += f"• Sensación: {_fmt_num(alerta.sensacion_c, '°C')}\n"
        msg += f"• Viento: {_fmt_num(alerta.viento_kmh, ' km/h')}\n"
        msg += f"• Prob. lluvia: {_fmt_num(alerta.prob_lluvia, '%')}\n"
        msg += f"• UV máx.: {_fmt_num(alerta.indice_uv)} {uv_icon} {nivel_uv}\n"
        msg += f"• ☀️ Protección solar: *{factor_solar}*\n"
        msg += f"• Radiación máx.: {_fmt_num(alerta.radiacion_wm2, ' W/m²')}\n"

        if lat and lng:
            msg += f"• Mapa: {_build_maps_url(lat, lng)}\n"

        msg += "\n"

    if len(alertas) > _max_servicios_por_usuario():
        msg += f"… y {len(alertas) - _max_servicios_por_usuario()} sitio(s) más.\n\n"

    msg += (
        "👷 Recomendación preventiva: usa protector solar, mantente hidratado "
        "y revisa tus EPP antes de iniciar la jornada."
    )

    return msg.strip()


def procesar_alertas_clima_diarias(
    *,
    fecha=None,
    user_id: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """
    Genera y envía alertas diarias por Telegram.

    dry_run=True:
    - No envía Telegram.
    - No marca enviado.
    - Devuelve resumen para pruebas.
    """
    if fecha is None:
        fecha = timezone.localdate()

    if not getattr(settings, "BOT_GZ_CLIMA_ENABLED", True):
        return {
            "ok": False,
            "reason": "BOT_GZ_CLIMA_ENABLED=False",
            "fecha": str(fecha),
            "usuarios": 0,
            "enviados": 0,
            "errores": 0,
        }

    usuarios = CustomUser.objects.filter(telegram_activo=True)

    if user_id:
        usuarios = usuarios.filter(id=user_id)

    total_usuarios = 0
    enviados = 0
    errores = 0
    sin_telegram = 0
    sin_asignacion = 0
    sin_ubicacion = 0
    dry_messages = []

    for usuario in usuarios:
        total_usuarios += 1

        chat_id = str(getattr(usuario, "telegram_chat_id", "") or "").strip()
        if not chat_id:
            sin_telegram += 1
            AlertaClimaDiaria.objects.create(
                trabajador=usuario,
                fecha=fecha,
                estado_envio="sin_telegram",
                error_envio="Usuario sin telegram_chat_id",
            )
            continue

        if not force:
            ya_enviado = AlertaClimaDiaria.objects.filter(
                trabajador=usuario,
                fecha=fecha,
                estado_envio="enviado",
            ).exists()
            if ya_enviado:
                continue

        servicios = list(
            _servicios_activos_para_usuario(usuario)[: _max_servicios_por_usuario()]
        )

        if not servicios:
            sin_asignacion += 1
            AlertaClimaDiaria.objects.create(
                trabajador=usuario,
                fecha=fecha,
                chat_id=chat_id,
                estado_envio="sin_asignacion",
                error_envio="Sin servicios activos/asignados",
            )
            continue

        alertas_usuario = []

        for servicio in servicios:
            sitio = _resolver_sitio_para_servicio(servicio)
            if not sitio:
                sin_ubicacion += 1
                AlertaClimaDiaria.objects.create(
                    trabajador=usuario,
                    servicio=servicio,
                    fecha=fecha,
                    chat_id=chat_id,
                    id_claro=getattr(servicio, "id_claro", "") or "",
                    id_new=getattr(servicio, "id_new", "") or "",
                    estado_envio="sin_ubicacion",
                    error_envio="No se encontró SitioMovil con coordenadas",
                )
                continue

            try:
                clima = get_weather_uv(
                    latitud=getattr(sitio, "latitud"),
                    longitud=getattr(sitio, "longitud"),
                )
                alerta = _crear_alerta_obj(
                    usuario=usuario,
                    servicio=servicio,
                    sitio=sitio,
                    fecha=fecha,
                    clima=clima,
                )
                alertas_usuario.append(alerta)
            except Exception as e:
                errores += 1
                logger.exception(
                    "Error obteniendo clima para usuario=%s servicio=%s",
                    usuario.id,
                    servicio.id,
                )
                AlertaClimaDiaria.objects.create(
                    trabajador=usuario,
                    servicio=servicio,
                    sitio=sitio,
                    fecha=fecha,
                    chat_id=chat_id,
                    id_claro=getattr(servicio, "id_claro", "") or "",
                    id_new=getattr(servicio, "id_new", "") or "",
                    estado_envio="error",
                    error_envio=str(e),
                )

        if not alertas_usuario:
            continue

        mensaje = construir_mensaje_alerta_clima(
            usuario=usuario,
            fecha=fecha,
            alertas=alertas_usuario,
        )

        if dry_run:
            dry_messages.append(
                {
                    "usuario_id": usuario.id,
                    "usuario": str(usuario),
                    "chat_id": chat_id,
                    "mensaje": mensaje,
                }
            )
            continue

        try:
            send_telegram_message(
                chat_id,
                mensaje,
                usuario=usuario,
                intent=None,
                meta={
                    "tipo": "alerta_clima_diaria",
                    "fecha": str(fecha),
                    "servicios": [a.servicio_id for a in alertas_usuario],
                },
                marcar_para_entrenamiento=False,
            )

            for alerta in alertas_usuario:
                alerta.estado_envio = "enviado"
                alerta.mensaje_enviado = mensaje
                alerta.enviado_en = timezone.now()
                alerta.save(
                    update_fields=["estado_envio", "mensaje_enviado", "enviado_en"]
                )

            enviados += 1

        except Exception as e:
            errores += 1
            logger.exception(
                "Error enviando alerta clima a Telegram usuario=%s", usuario.id
            )
            for alerta in alertas_usuario:
                alerta.estado_envio = "error"
                alerta.error_envio = str(e)
                alerta.save(update_fields=["estado_envio", "error_envio"])

    return {
        "ok": True,
        "fecha": str(fecha),
        "usuarios": total_usuarios,
        "enviados": enviados,
        "errores": errores,
        "sin_telegram": sin_telegram,
        "sin_asignacion": sin_asignacion,
        "sin_ubicacion": sin_ubicacion,
        "dry_run": dry_run,
        "dry_messages": dry_messages,
    }

# notificaciones/views_cron.py
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection
from django.http import HttpResponseForbidden, JsonResponse
from django.test.client import RequestFactory
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from bot_gz.services_clima import procesar_alertas_clima_diarias
from flota.views_cron import cron_flota_mantenciones
from prevencion.views_cron import cron_prevencion_documentos
from rrhh.views_alerta import cron_contratos_por_vencer

logger = logging.getLogger(__name__)


def _response_to_dict(response, default_name: str) -> dict:
    try:
        raw = response.content.decode("utf-8") if hasattr(response, "content") else ""
    except Exception:
        raw = ""

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {
            "status": "invalid-json",
            "detail": raw or f"Respuesta no JSON desde {default_name}",
        }

    return {
        "http_status": getattr(response, "status_code", 200),
        **data,
    }


def _token_or_general(setting_name: str) -> str:
    """
    Permite que uses SOLO CRON_GENERAL_TOKEN si quieres.
    Si el token específico no está seteado, cae al general.
    """
    specific = (getattr(settings, setting_name, "") or "").strip()
    if specific:
        return specific
    return (getattr(settings, "CRON_GENERAL_TOKEN", "") or "").strip()


def _safe_call(name: str, fn, rf: RequestFactory, path: str, params: dict) -> dict:
    """
    Ejecuta un sub-cron y nunca deja que rompa el cron general.
    Importante: captura IntegrityError (race de get_or_create).
    """
    try:
        req = rf.get(path, data=params)
        resp = fn(req)
        return _response_to_dict(resp, name)
    except IntegrityError:
        # Esto pasa típicamente por carrera en get_or_create del lock diario.
        logger.exception("CRON general: IntegrityError en sub-cron %s", name)
        return {
            "http_status": 200,
            "status": "already-run-race",
            "detail": f"{name}: IntegrityError (race) en lock diario; tratar como ya ejecutado",
        }
    except Exception as e:
        logger.exception("CRON general: fallo inesperado en sub-cron %s", name)
        return {
            "http_status": 200,
            "status": "error",
            "detail": e.__class__.__name__,
        }


def _try_pg_advisory_lock(lock_key: int) -> bool:
    """
    Lock global cross-process sin migraciones (solo Postgres).
    Retorna True si tomó el lock; False si ya hay otro proceso corriendo.
    """
    if connection.vendor != "postgresql":
        return True  # en dev (sqlite) no hay advisory locks; seguimos
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s);", [lock_key])
        row = cur.fetchone()
        return bool(row and row[0])


def _pg_advisory_unlock(lock_key: int) -> None:
    if connection.vendor != "postgresql":
        return
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", [lock_key])
    except Exception:
        # no queremos romper el response por unlock
        logger.exception("CRON general: fallo liberando advisory lock")


@require_http_methods(["GET", "HEAD"])
def cron_diario_general(request):
    """
    Cron central:
    - Token ?token=... (ideal: CRON_GENERAL_TOKEN)
    - Dispara internamente RRHH / Flota / Prevención / Bot GZ Clima
    - Nunca responde 500 (para evitar reintentos agresivos del monitor)
    - Lock global (Postgres advisory lock) para evitar concurrencia

    Parámetros útiles:
    - force=1              Fuerza ejecución general de sub-crons que soportan force.
    - clima_force=1        Fuerza solo el bloque de clima aunque no sea la hora.
    - clima_dry_run=1      Prueba clima sin enviar Telegram.
    - clima_user_id=2      Ejecuta clima solo para un usuario específico.
    """

    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (getattr(settings, "CRON_GENERAL_TOKEN", "") or "").strip()

    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()
    force_run = (request.GET.get("force") or "").strip() == "1"

    # ✅ Lock global por día (ESTABLE: no usamos hash() de Python)
    import zlib

    lock_key = zlib.crc32(f"cron_diario_general:{hoy.isoformat()}".encode("utf-8"))

    if not _try_pg_advisory_lock(lock_key):
        return JsonResponse(
            {
                "status": "already-running",
                "date": str(hoy),
                "time": ahora.strftime("%H:%M:%S"),
                "force": force_run,
            },
            status=200,
        )

    rf = RequestFactory()
    resultados = {}

    try:
        # =========================
        # RRHH
        # =========================
        rrhh_params = {"token": _token_or_general("CONTRATOS_CRON_TOKEN")}
        if force_run:
            rrhh_params["force"] = "1"

        resultados["rrhh"] = _safe_call(
            "rrhh",
            cron_contratos_por_vencer,
            rf,
            "/rrhh/cron/contratos/",
            rrhh_params,
        )

        # =========================
        # Flota
        # =========================
        flota_params = {"token": _token_or_general("FLOTA_CRON_TOKEN")}
        if force_run:
            flota_params["force"] = "1"

        resultados["flota"] = _safe_call(
            "flota",
            cron_flota_mantenciones,
            rf,
            "/flota/cron/mantenciones/",
            flota_params,
        )

        # =========================
        # Prevención
        # =========================
        prev_params = {"token": _token_or_general("PREVENCION_CRON_TOKEN")}
        if force_run:
            prev_params["force"] = "1"

        resultados["prevencion"] = _safe_call(
            "prevencion",
            cron_prevencion_documentos,
            rf,
            "/prevencion/cron/documentos/",
            prev_params,
        )

        # =========================
        # Bot GZ - Alertas clima / UV
        # =========================
        try:
            clima_user_id = None
            clima_user_id_raw = (request.GET.get("clima_user_id") or "").strip()

            if clima_user_id_raw:
                try:
                    clima_user_id = int(clima_user_id_raw)
                except Exception:
                    clima_user_id = None

            clima_dry_run = (request.GET.get("clima_dry_run") or "").strip() == "1"

            if _should_run_clima_now(ahora, request):
                resultados["bot_gz_clima"] = procesar_alertas_clima_diarias(
                    fecha=hoy,
                    user_id=clima_user_id,
                    dry_run=clima_dry_run,
                    force=force_run,
                )
            else:
                resultados["bot_gz_clima"] = {
                    "ok": True,
                    "status": "skipped-by-hour",
                    "detail": (
                        "Clima no ejecutado porque aún no está dentro de la ventana "
                        "horaria configurada."
                    ),
                    "run_hour": int(getattr(settings, "BOT_GZ_CLIMA_RUN_HOUR", 8)),
                    "run_minute": int(getattr(settings, "BOT_GZ_CLIMA_RUN_MINUTE", 0)),
                    "window_minutes": int(
                        getattr(settings, "BOT_GZ_CLIMA_RUN_WINDOW_MINUTES", 10)
                    ),
                    "current_time": ahora.strftime("%H:%M:%S"),
                }

        except Exception as e:
            logger.exception("CRON general: fallo inesperado en bot_gz_clima")
            resultados["bot_gz_clima"] = {
                "ok": False,
                "status": "error",
                "detail": e.__class__.__name__,
            }

        # ✅ Diagnóstico extra para flota
        try:
            from flota.models import (FlotaAlertaEnviada,
                                      FlotaCronDiarioEjecutado)

            flota_status = (resultados.get("flota") or {}).get("status")
            if flota_status in ("already-run", "already-run-race"):
                lock_exists = FlotaCronDiarioEjecutado.objects.filter(
                    nombre="flota_mantenciones",
                    fecha=hoy,
                ).exists()
                alerts_today = FlotaAlertaEnviada.objects.filter(sent_on=hoy).count()

                resultados["flota_debug"] = {
                    "lock_exists": bool(lock_exists),
                    "alerts_today_count": int(alerts_today),
                    "note": (
                        "Si lock_exists=True y alerts_today_count=0 "
                        "pero hay servicios overdue, entonces hoy quedó marcado como ejecutado sin enviar."
                    ),
                }

        except Exception:
            logger.exception("CRON general: fallo agregando flota_debug")
            resultados["flota_debug"] = {"error": "debug_failed"}

    finally:
        _pg_advisory_unlock(lock_key)

    overall_status = "ok"

    for value in resultados.values():
        if not isinstance(value, dict):
            continue

        status = value.get("status")
        ok = value.get("ok")

        if status in ("error", "invalid-json") or ok is False:
            overall_status = "partial-error"
            break

    return JsonResponse(
        {
            "status": overall_status,
            "date": str(hoy),
            "time": ahora.strftime("%H:%M:%S"),
            "force": force_run,
            "results": resultados,
        },
        status=200,
        json_dumps_params={"ensure_ascii": False},
    )


def _should_run_clima_now(ahora, request) -> bool:
    """
    Controla que el bloque de clima solo corra en una ventana horaria.

    Como Better ejecuta el cron cada 3 minutos, usamos una ventana:
    - Por defecto: desde 08:00 hasta 08:10
    - force=1 permite correrlo manualmente para pruebas generales
    - clima_force=1 permite forzar solo clima
    """
    if (request.GET.get("force") or "").strip() == "1":
        return True

    if (request.GET.get("clima_force") or "").strip() == "1":
        return True

    hora_inicio = int(getattr(settings, "BOT_GZ_CLIMA_RUN_HOUR", 8))
    minuto_inicio = int(getattr(settings, "BOT_GZ_CLIMA_RUN_MINUTE", 0))
    ventana_minutos = int(getattr(settings, "BOT_GZ_CLIMA_RUN_WINDOW_MINUTES", 10))

    inicio = ahora.replace(
        hour=hora_inicio,
        minute=minuto_inicio,
        second=0,
        microsecond=0,
    )

    fin = inicio + timedelta(minutes=ventana_minutos)

    return inicio <= ahora < fin

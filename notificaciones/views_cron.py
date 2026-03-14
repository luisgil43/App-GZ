from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.test.client import RequestFactory
from django.utils import timezone
from django.views.decorators.http import require_http_methods

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


@require_http_methods(["GET", "HEAD"])
def cron_diario_general(request):
    """
    Cron central:
    - Vive en app notificaciones.
    - Protegido por ?token=...
    - Dispara internamente:
        * RRHH
        * Flota
        * Prevención
    - No cambia la lógica interna de cada cron existente.
    """

    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (
        getattr(settings, "CRON_GENERAL_TOKEN", "")
        or getattr(settings, "PREVENCION_CRON_TOKEN", "")
        or ""
    ).strip()

    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    force_run = (request.GET.get("force") or "").strip() == "1"

    rf = RequestFactory()

    resultados = {}

    try:
        # =========================
        # RRHH
        # =========================
        rrhh_params = {
            "token": getattr(settings, "CONTRATOS_CRON_TOKEN", "") or "",
        }
        if force_run:
            rrhh_params["force"] = "1"

        rrhh_request = rf.get("/rrhh/cron/contratos/", data=rrhh_params)
        rrhh_response = cron_contratos_por_vencer(rrhh_request)
        resultados["rrhh"] = _response_to_dict(rrhh_response, "rrhh")

        # =========================
        # Flota
        # =========================
        flota_params = {
            "token": getattr(settings, "FLOTA_CRON_TOKEN", "") or "",
        }
        if force_run:
            flota_params["force"] = "1"

        flota_request = rf.get("/flota/cron/mantenciones/", data=flota_params)
        flota_response = cron_flota_mantenciones(flota_request)
        resultados["flota"] = _response_to_dict(flota_response, "flota")

        # =========================
        # Prevención
        # =========================
        prevencion_params = {
            "token": getattr(settings, "PREVENCION_CRON_TOKEN", "") or "",
        }
        if force_run:
            prevencion_params["force"] = "1"

        prevencion_request = rf.get("/prevencion/cron/documentos/", data=prevencion_params)
        prevencion_response = cron_prevencion_documentos(prevencion_request)
        resultados["prevencion"] = _response_to_dict(prevencion_response, "prevencion")

    except Exception as e:
        logger.exception("Fallo cron central diario")
        return JsonResponse(
            {
                "status": "error",
                "detail": e.__class__.__name__,
                "date": str(ahora.date()),
            },
            status=500,
        )

    overall_status = "ok"
    if any(v.get("http_status", 200) >= 400 for v in resultados.values()):
        overall_status = "partial-error"

    return JsonResponse(
        {
            "status": overall_status,
            "date": str(ahora.date()),
            "time": ahora.strftime("%H:%M:%S"),
            "force": force_run,
            "results": resultados,
        },
        status=200,
    )
# prevencion/views_cron.py
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (PrevencionAlertaEnviada, PrevencionCronDiarioEjecutado,
                     PrevencionDocument, PrevencionNotificationSettings)

logger = logging.getLogger(__name__)


def _get_logo_url() -> str:
    return getattr(
        settings,
        "PLANIX_LOGO_URL",
        "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
    )


def _send_email(*, subject: str, to_emails: list[str], cc_emails: list[str], text_body: str, html_body: str):
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=to_emails,
        cc=cc_emails or None,
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


def _already_sent_pre(doc_id: int, threshold: int) -> bool:
    return PrevencionAlertaEnviada.objects.filter(
        doc_id=doc_id,
        mode="pre_days",
        threshold=threshold,
    ).exists()


def _mark_sent_pre(doc_id: int, threshold: int):
    PrevencionAlertaEnviada.objects.create(
        doc_id=doc_id,
        mode="pre_days",
        threshold=threshold,
        sent_on=None,
    )


def _already_sent_overdue_today(doc_id: int, today) -> bool:
    return PrevencionAlertaEnviada.objects.filter(
        doc_id=doc_id,
        mode="overdue_days",
        threshold=0,
        sent_on=today,
    ).exists()


def _mark_sent_overdue_today(doc_id: int, today):
    PrevencionAlertaEnviada.objects.create(
        doc_id=doc_id,
        mode="overdue_days",
        threshold=0,
        sent_on=today,
    )


@require_http_methods(["GET", "HEAD"])
def cron_prevencion_documentos(request):
    """
    CRON Prevención:
    - Token ?token=...
    - Solo una vez por día (lock DB); si falla, libera lock para reintentar.
    - Nunca antes de las 08:00 (hora local), salvo force=1.
    - Envía 1 solo correo consolidado.
    - Reglas:
        * Vigente: >20 días
        * Próximo: <=20 y >0, notifica por próximo umbral pendiente:
            20..11 -> umbral 10
            10..6  -> umbral 5
            5..1   -> umbral 1
        * Vencido: <=0, notifica todos los días (1 vez/día)
        * Docs sin vencimiento no entran al cron.
    """

    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (getattr(settings, "PREVENCION_CRON_TOKEN", "") or "").strip()

    logger.info("CRON Prevención token recibido=%r esperado=%r", token_recibido, token_esperado)

    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()
    force_run = (request.GET.get("force") or "").strip() == "1"

    if ahora.hour < 8 and not force_run:
        return JsonResponse(
            {"status": "before-8am", "detail": "Aún no son las 08:00"},
            status=200,
        )

    job_name = "prevencion_documentos"

    cron_obj = None
    if force_run:
        PrevencionCronDiarioEjecutado.objects.filter(nombre=job_name, fecha=hoy).delete()
        cron_obj = PrevencionCronDiarioEjecutado.objects.create(nombre=job_name, fecha=hoy)
    else:
        cron_obj, created = PrevencionCronDiarioEjecutado.objects.get_or_create(nombre=job_name, fecha=hoy)
        if not created:
            return JsonResponse({"status": "already-run", "detail": "Ya se ejecutó hoy"}, status=200)

    enviados = 0
    errores = 0
    ultimo_error = None
    success = False

    try:
        cfg, _ = PrevencionNotificationSettings.objects.get_or_create(pk=1)
        if not cfg.enabled:
            success = True
            return JsonResponse({"status": "disabled", "detail": "Notificaciones desactivadas"}, status=200)

        docs = (
            PrevencionDocument.objects
            .select_related("doc_type")
            .prefetch_related("workers")
            .filter(
                current=True,
                notify_enabled=True,
                no_requiere_vencimiento=False,
                expiry_date__isnull=False,
            )
        )

        upcoming_items = []
        overdue_items = []

        for d in docs:
            remaining = d.remaining_days(today=hoy)
            if remaining is None:
                continue

            if remaining <= 0:
                if not _already_sent_overdue_today(d.id, hoy):
                    overdue_items.append({
                        "doc": d,
                        "remaining": remaining,
                        "scope": d.scope,
                        "type": d.doc_type.name,
                        "workers": list(d.workers.all()),
                    })
                continue

            chosen = None

            # Próximo umbral pendiente real
            if 11 <= remaining <= 20:
                if not _already_sent_pre(d.id, 10):
                    chosen = 10
            elif 6 <= remaining <= 10:
                if not _already_sent_pre(d.id, 5):
                    chosen = 5
            elif 1 <= remaining <= 5:
                if not _already_sent_pre(d.id, 1):
                    chosen = 1

            if chosen is not None:
                upcoming_items.append({
                    "doc": d,
                    "remaining": remaining,
                    "threshold": chosen,
                    "scope": d.scope,
                    "type": d.doc_type.name,
                    "workers": list(d.workers.all()),
                })

        if not upcoming_items and not overdue_items:
            success = True
            return JsonResponse({"status": "ok", "detail": "Nada que notificar", "sent": 0}, status=200)

        to_emails = cfg.get_to_emails()
        cc_emails = cfg.get_cc_emails()

        if cfg.include_worker:
            for item in upcoming_items + overdue_items:
                if item["scope"] in {"trabajador", "ambos"}:
                    for w in item["workers"]:
                        em = (getattr(w, "email", "") or "").strip()
                        if em:
                            to_emails.append(em)

        seen = set()
        to_final = []
        for e in to_emails:
            k = e.lower()
            if k in seen:
                continue
            seen.add(k)
            to_final.append(e)

        seen2 = set()
        cc_final = []
        for e in cc_emails:
            k = e.lower()
            if k in seen2:
                continue
            seen2.add(k)
            cc_final.append(e)

        if not to_final and cc_final:
            to_final, cc_final = cc_final, []

        if not to_final:
            success = True
            return JsonResponse({"status": "skipped", "detail": "Sin destinatarios configurados"}, status=200)

        logo_url = _get_logo_url()
        subject = f"[GZ Services] Prevención - Documentos por vencer/vencidos ({hoy:%Y-%m-%d})"

        def _row_html(d, remaining, badge, extra):
            workers_txt = "—"
            if d.scope in {"trabajador", "ambos"}:
                workers_txt = ", ".join([(w.get_full_name() or w.username) for w in d.workers.all()]) or "—"

            exp = d.expiry_date.strftime("%d-%m-%Y") if d.expiry_date else "—"
            iss = d.issue_date.strftime("%d-%m-%Y") if d.issue_date else "—"

            return f"""
<tr>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{d.doc_type.name}</td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{d.get_scope_display()}</td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;">{workers_txt}</td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">{iss}</td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">{exp}</td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">
    <span style="display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;{badge}">{extra}</span>
  </td>
  <td style="padding:10px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">{remaining}</td>
</tr>
"""

        badge_up = "background:#fef3c7;color:#92400e;"
        badge_od = "background:#fee2e2;color:#991b1b;"

        up_rows = ""
        for it in upcoming_items:
            d = it["doc"]
            up_rows += _row_html(
                d,
                it["remaining"],
                badge_up,
                f"Próximo (umbral {it['threshold']} días)"
            )

        od_rows = ""
        for it in overdue_items:
            d = it["doc"]
            od_rows += _row_html(d, it["remaining"], badge_od, "Vencido")

        html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="max-width:900px;margin:auto;background:#fff;padding:28px;border-radius:12px;
              box-shadow:0 5px 15px rgba(0,0,0,.10);
              font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Logo Planix" style="max-width:180px;height:auto;">
    </div>

    <h2 style="font-size:20px;margin:0 0 6px;color:#111827;">📄 Prevención - Estado de documentos</h2>
    <p style="font-size:13px;color:#6b7280;margin:0 0 18px;">
      Fecha: <strong>{hoy:%d-%m-%Y}</strong>. Este correo es consolidado (1 solo envío).
    </p>

    {"<h3 style='margin:18px 0 10px;color:#111827;'>🟡 Próximos a vencer</h3>" if up_rows else ""}
    {"<div style='border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;'><table style='width:100%;border-collapse:collapse;font-size:13px;'><thead style='background:#f9fafb;'><tr><th style='text-align:left;padding:10px;'>Documento</th><th style='text-align:left;padding:10px;'>Alcance</th><th style='text-align:left;padding:10px;'>Trabajadores</th><th style='text-align:left;padding:10px;'>Creación</th><th style='text-align:left;padding:10px;'>Caduca</th><th style='text-align:left;padding:10px;'>Estado</th><th style='text-align:left;padding:10px;'>Días</th></tr></thead><tbody>" + up_rows + "</tbody></table></div>" if up_rows else ""}

    {"<h3 style='margin:22px 0 10px;color:#111827;'>🔴 Vencidos</h3>" if od_rows else ""}
    {"<div style='border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;'><table style='width:100%;border-collapse:collapse;font-size:13px;'><thead style='background:#f9fafb;'><tr><th style='text-align:left;padding:10px;'>Documento</th><th style='text-align:left;padding:10px;'>Alcance</th><th style='text-align:left;padding:10px;'>Trabajadores</th><th style='text-align:left;padding:10px;'>Creación</th><th style='text-align:left;padding:10px;'>Caduca</th><th style='text-align:left;padding:10px;'>Estado</th><th style='text-align:left;padding:10px;'>Días</th></tr></thead><tbody>" + od_rows + "</tbody></table></div>" if od_rows else ""}

    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">
      Enviado automáticamente por el sistema Planix. No responder a este correo.
    </p>
  </div>
</body>
</html>
"""

        text_body = (
            "Prevención - Documentos por vencer/vencidos\n"
            f"Fecha: {hoy:%Y-%m-%d}\n\n"
            f"Próximos: {len(upcoming_items)}\n"
            f"Vencidos: {len(overdue_items)}\n"
        )

        _send_email(
            subject=subject,
            to_emails=to_final,
            cc_emails=cc_final,
            text_body=text_body,
            html_body=html_body,
        )
        enviados = 1

        for it in upcoming_items:
            _mark_sent_pre(it["doc"].id, int(it["threshold"]))

        for it in overdue_items:
            _mark_sent_overdue_today(it["doc"].id, hoy)

        success = True

    except Exception as e:
        errores += 1
        ultimo_error = e.__class__.__name__
        logger.exception("Fallo CRON Prevención")
        success = False

    finally:
        if not success and cron_obj:
            PrevencionCronDiarioEjecutado.objects.filter(pk=cron_obj.pk).delete()

    return JsonResponse(
        {
            "status": "ok" if success else "retry-enabled",
            "date": str(hoy),
            "sent": enviados,
            "send_errors": errores,
            "last_error": ultimo_error,
        },
        status=200,
    )
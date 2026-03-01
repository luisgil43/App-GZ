# flota/views_cron.py
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (FlotaAlertaEnviada, FlotaCronDiarioEjecutado, Vehicle,
                     VehicleNotificationSettings, VehicleService,
                     VehicleServiceType)

logger = logging.getLogger(__name__)


def _get_logo_url() -> str:
    return getattr(
        settings,
        "PLANIX_LOGO_URL",
        "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
    )


def _build_recipients(cfg: VehicleNotificationSettings):
    to_emails = cfg.get_to_emails()
    cc_emails = cfg.get_cc_emails()
    return to_emails, cc_emails


def _latest_service_for_type(vehicle_id: int, st_type: VehicleServiceType):
    return (
        VehicleService.objects
        .filter(vehicle_id=vehicle_id, service_type_obj_id=st_type.id)
        .order_by("-service_date", "-created_at", "-pk")
        .first()
    )


def _already_sent_pre(vehicle_id: int, type_id: int, base_service_id: int, mode: str, threshold: int) -> bool:
    return FlotaAlertaEnviada.objects.filter(
        vehicle_id=vehicle_id,
        service_type_id=type_id,
        base_service_id=base_service_id,
        mode=mode,
        threshold=threshold,
    ).exists()


def _mark_sent_pre(vehicle_id: int, type_id: int, base_service_id: int, mode: str, threshold: int):
    FlotaAlertaEnviada.objects.create(
        vehicle_id=vehicle_id,
        service_type_id=type_id,
        base_service_id=base_service_id,
        mode=mode,
        threshold=threshold,
        sent_on=None,  # pre = una sola vez por base_service
    )


def _already_sent_overdue_today(vehicle_id: int, type_id: int, base_service_id: int, mode: str, today) -> bool:
    return FlotaAlertaEnviada.objects.filter(
        vehicle_id=vehicle_id,
        service_type_id=type_id,
        base_service_id=base_service_id,
        mode=mode,
        threshold=0,
        sent_on=today,
    ).exists()


def _mark_sent_overdue_today(vehicle_id: int, type_id: int, base_service_id: int, mode: str, today):
    FlotaAlertaEnviada.objects.create(
        vehicle_id=vehicle_id,
        service_type_id=type_id,
        base_service_id=base_service_id,
        mode=mode,
        threshold=0,
        sent_on=today,  # overdue = una vez por día
    )


def _send_email(
    *,
    subject: str,
    to_emails: list[str],
    cc_emails: list[str],
    text_body: str,
    html_body: str,
):
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


@require_http_methods(["GET", "HEAD"])
def cron_flota_mantenciones(request):
    """
    CRON Flota (similar RRHH):
    - Token ?token=...
    - Solo una vez por día (lock DB); si falla, libera lock para reintentar.
    - Nunca antes de las 08:00 (hora local).
    - Revisa vehículos con VehicleNotificationSettings.enabled=True
    - Para cada VehicleServiceType activo con frecuencia (KM y/o días):
        * Busca último servicio (VehicleService con service_type_obj)
        * Calcula vencimiento por KM y/o días
        * PRE: dispara cuando remaining <= step (1000/500/100 etc) UNA sola vez por base_service
        * OVERDUE: si notify_on_overdue=True, dispara TODOS los días (una vez/día) hasta nuevo servicio
    """

    # 1) Seguridad token
    token_recibido = request.GET.get("token")
    token_esperado = getattr(settings, "FLOTA_CRON_TOKEN", "")
    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()

    # 2) No ejecutar antes de las 08:00
    if ahora.hour < 8:
        return JsonResponse(
            {"status": "before-8am", "detail": "Aún no son las 08:00"},
            status=200,
        )

    # 3) Lock diario
    job_name = "flota_mantenciones"
    cron_obj, created = FlotaCronDiarioEjecutado.objects.get_or_create(nombre=job_name, fecha=hoy)
    if not created:
        return JsonResponse(
            {"status": "already-run", "detail": "Ya se ejecutó hoy"},
            status=200,
        )

    enviados = 0
    saltados = 0
    errores_envio = 0
    ultimo_error = None
    success = False

    logo_url = _get_logo_url()

    try:
        # Vehículos con notificaciones activas
        cfgs = (
            VehicleNotificationSettings.objects
            .select_related("vehicle")
            .filter(enabled=True)
        )

        # Tipos con frecuencia (KM o días)
        tipos = (
            VehicleServiceType.objects
            .filter(is_active=True)
            .order_by("name")
        )

        for cfg in cfgs:
            v = cfg.vehicle
            to_emails, cc_emails = _build_recipients(cfg)

            # Si no hay a quién enviar, saltamos
            if not to_emails and not cc_emails:
                saltados += 1
                continue

            km_actual = int(v.kilometraje_actual or 0)

            for t in tipos:
                # Solo tipos con frecuencia real
                has_km = bool((t.interval_km or 0) > 0)
                has_days = bool((t.interval_days or 0) > 0)
                if not has_km and not has_days:
                    continue

                last = _latest_service_for_type(v.id, t)
                if not last:
                    # sin base -> no podemos calcular
                    continue

                # -----------------------
                #  A) ALERTAS POR KM
                # -----------------------
                if has_km:
                    if last.kilometraje_declarado is None:
                        # sin KM base, no podemos calcular por KM
                        pass
                    else:
                        due_km = int(last.kilometraje_declarado) + int(t.interval_km)
                        remaining_km = due_km - km_actual

                        # Overdue por KM
                        if remaining_km <= 0:
                            if t.notify_on_overdue:
                                if not _already_sent_overdue_today(v.id, t.id, last.id, "overdue_km", hoy):
                                    subject = f"[GZ Services] Mantención vencida (KM) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hola,\n\n"
                                        f"El vehículo {v.patente} tiene una mantención vencida.\n"
                                        f"Tipo: {t.name}\n"
                                        f"KM actual: {km_actual}\n"
                                        f"Vencía en: {due_km}\n\n"
                                        "Este mensaje fue generado automáticamente por el sistema Planix.\n"
                                    )

                                    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;
              box-shadow:0 5px 15px rgba(0,0,0,.10);
              font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Logo Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">⚠️ Mantención vencida (por KM)</h2>
    <p style="font-size:14px;color:#374151;margin:0 0 12px;">
      El vehículo <strong>{v.patente}</strong> tiene una mantención vencida.
    </p>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehículo:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Tipo:</strong> {t.name}</li>
        <li><strong>KM actual:</strong> {km_actual:,}</li>
        <li><strong>Vencía en:</strong> {due_km:,}</li>
        <li><strong>Último servicio:</strong> {last.service_date:%Y-%m-%d} (KM {int(last.kilometraje_declarado):,})</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">
      Enviado automáticamente por el sistema Planix. No responder a este correo.
    </p>
  </div>
</body>
</html>
""".replace(",", ".")

                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,  # si TO vacío, usamos CC como fallback
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        errores_envio += 1
                                        ultimo_error = e.__class__.__name__
                                        logger.exception("Fallo envío flota overdue_km veh=%s tipo=%s", v.id, t.id)
                                    else:
                                        _mark_sent_overdue_today(v.id, t.id, last.id, "overdue_km", hoy)
                                        enviados += 1
                            continue

                        # Pre-alertas por KM (1000/500/100...)
                        steps = t.alert_km_steps_list
                        if steps:
                            for threshold in steps:
                                if remaining_km <= int(threshold):
                                    if not _already_sent_pre(v.id, t.id, last.id, "pre_km", int(threshold)):
                                        subject = f"[GZ Services] Mantención próxima (KM) - {t.name} - {v.patente}"
                                        text_body = (
                                            "Hola,\n\n"
                                            f"El vehículo {v.patente} tiene una mantención próxima.\n"
                                            f"Tipo: {t.name}\n"
                                            f"KM actual: {km_actual}\n"
                                            f"Vence en: {due_km}\n"
                                            f"Faltan: {remaining_km}\n\n"
                                            "Este mensaje fue generado automáticamente por el sistema Planix.\n"
                                        )

                                        html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;
              box-shadow:0 5px 15px rgba(0,0,0,.10);
              font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Logo Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">🔔 Mantención próxima (por KM)</h2>
    <p style="font-size:14px;color:#374151;margin:0 0 12px;">
      El vehículo <strong>{v.patente}</strong> está próximo a vencimiento.
    </p>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehículo:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Tipo:</strong> {t.name}</li>
        <li><strong>KM actual:</strong> {km_actual:,}</li>
        <li><strong>Vence en:</strong> {due_km:,}</li>
        <li><strong>Faltan:</strong> {remaining_km:,} km (umbral: {int(threshold):,})</li>
        <li><strong>Último servicio:</strong> {last.service_date:%Y-%m-%d} (KM {int(last.kilometraje_declarado):,})</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">
      Enviado automáticamente por el sistema Planix. No responder a este correo.
    </p>
  </div>
</body>
</html>
""".replace(",", ".")

                                        try:
                                            _send_email(
                                                subject=subject,
                                                to_emails=to_emails or cc_emails,
                                                cc_emails=cc_emails if to_emails else [],
                                                text_body=text_body,
                                                html_body=html_body,
                                            )
                                        except Exception as e:
                                            errores_envio += 1
                                            ultimo_error = e.__class__.__name__
                                            logger.exception("Fallo envío flota pre_km veh=%s tipo=%s", v.id, t.id)
                                        else:
                                            _mark_sent_pre(v.id, t.id, last.id, "pre_km", int(threshold))
                                            enviados += 1

                                    # si ya entró en un umbral, no forzamos a mandar todos a la vez
                                    break

                # -----------------------
                #  B) ALERTAS POR DÍAS
                # -----------------------
                if has_days:
                    due_date = last.service_date + timedelta(days=int(t.interval_days))
                    remaining_days = (due_date - hoy).days

                    # Overdue por días
                    if remaining_days <= 0:
                        if t.notify_on_overdue:
                            if not _already_sent_overdue_today(v.id, t.id, last.id, "overdue_days", hoy):
                                subject = f"[GZ Services] Mantención vencida (días) - {t.name} - {v.patente}"
                                text_body = (
                                    "Hola,\n\n"
                                    f"El vehículo {v.patente} tiene una mantención vencida.\n"
                                    f"Tipo: {t.name}\n"
                                    f"Vencía el: {due_date:%Y-%m-%d}\n\n"
                                    "Este mensaje fue generado automáticamente por el sistema Planix.\n"
                                )

                                html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;
              box-shadow:0 5px 15px rgba(0,0,0,.10);
              font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Logo Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">⚠️ Mantención vencida (por días)</h2>
    <p style="font-size:14px;color:#374151;margin:0 0 12px;">
      El vehículo <strong>{v.patente}</strong> tiene una mantención vencida.
    </p>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehículo:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Tipo:</strong> {t.name}</li>
        <li><strong>Vencía el:</strong> {due_date:%Y-%m-%d}</li>
        <li><strong>Último servicio:</strong> {last.service_date:%Y-%m-%d}</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">
      Enviado automáticamente por el sistema Planix. No responder a este correo.
    </p>
  </div>
</body>
</html>
"""

                                try:
                                    _send_email(
                                        subject=subject,
                                        to_emails=to_emails or cc_emails,
                                        cc_emails=cc_emails if to_emails else [],
                                        text_body=text_body,
                                        html_body=html_body,
                                    )
                                except Exception as e:
                                    errores_envio += 1
                                    ultimo_error = e.__class__.__name__
                                    logger.exception("Fallo envío flota overdue_days veh=%s tipo=%s", v.id, t.id)
                                else:
                                    _mark_sent_overdue_today(v.id, t.id, last.id, "overdue_days", hoy)
                                    enviados += 1
                        continue

                    # Pre-alertas por días
                    steps_days = t.alert_days_steps_list
                    if steps_days:
                        for threshold in steps_days:
                            if remaining_days <= int(threshold):
                                if not _already_sent_pre(v.id, t.id, last.id, "pre_days", int(threshold)):
                                    subject = f"[GZ Services] Mantención próxima (días) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hola,\n\n"
                                        f"El vehículo {v.patente} tiene una mantención próxima.\n"
                                        f"Tipo: {t.name}\n"
                                        f"Vence el: {due_date:%Y-%m-%d}\n"
                                        f"Faltan: {remaining_days} día(s)\n\n"
                                        "Este mensaje fue generado automáticamente por el sistema Planix.\n"
                                    )

                                    html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;
              box-shadow:0 5px 15px rgba(0,0,0,.10);
              font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Logo Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">🔔 Mantención próxima (por días)</h2>
    <p style="font-size:14px;color:#374151;margin:0 0 12px;">
      El vehículo <strong>{v.patente}</strong> está próximo a vencimiento.
    </p>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehículo:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Tipo:</strong> {t.name}</li>
        <li><strong>Vence el:</strong> {due_date:%Y-%m-%d}</li>
        <li><strong>Faltan:</strong> {remaining_days} día(s) (umbral: {int(threshold)})</li>
        <li><strong>Último servicio:</strong> {last.service_date:%Y-%m-%d}</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">
      Enviado automáticamente por el sistema Planix. No responder a este correo.
    </p>
  </div>
</body>
</html>
"""

                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        errores_envio += 1
                                        ultimo_error = e.__class__.__name__
                                        logger.exception("Fallo envío flota pre_days veh=%s tipo=%s", v.id, t.id)
                                    else:
                                        _mark_sent_pre(v.id, t.id, last.id, "pre_days", int(threshold))
                                        enviados += 1

                                break

        success = (errores_envio == 0)

    finally:
        # Si hubo problemas, liberamos lock para reintentar hoy
        if not success:
            FlotaCronDiarioEjecutado.objects.filter(pk=cron_obj.pk).delete()

    return JsonResponse(
        {
            "status": "ok" if success else "retry-enabled",
            "date": str(hoy),
            "method": request.method,
            "sent": enviados,
            "skipped": saltados,
            "send_errors": errores_envio,
            "last_error": ultimo_error,
        },
        status=200,
    )
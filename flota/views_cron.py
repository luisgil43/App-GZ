# flota/views_cron.py
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (FlotaAlertaEnviada, FlotaCronDiarioEjecutado,
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


def _try_pg_advisory_lock(lock_key: int) -> bool:
    """
    Lock global cross-process sin migraciones (solo Postgres).
    Retorna True si tomó el lock; False si ya hay otro proceso corriendo.
    """
    if connection.vendor != "postgresql":
        return True
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
        logger.exception("CRON flota: fallo liberando advisory lock")


def _mark_sent_pre_safe(vehicle_id: int, type_id: int, base_service_id: int, threshold: int) -> bool:
    """
    PRE KM: una vez por base_service + threshold.
    Devuelve True si se creó (o sea, NO estaba enviada).
    """
    try:
        _obj, created = FlotaAlertaEnviada.objects.get_or_create(
            vehicle_id=vehicle_id,
            service_type_id=type_id,
            base_service_id=base_service_id,
            mode="pre_km",
            threshold=int(threshold),
            defaults={"sent_on": None},
        )
        return created
    except IntegrityError:
        return False


def _mark_sent_pre_days_safe(vehicle_id: int, type_id: int, base_service_id: int, threshold: int) -> bool:
    """
    PRE DAYS: una vez por base_service + threshold.
    """
    try:
        _obj, created = FlotaAlertaEnviada.objects.get_or_create(
            vehicle_id=vehicle_id,
            service_type_id=type_id,
            base_service_id=base_service_id,
            mode="pre_days",
            threshold=int(threshold),
            defaults={"sent_on": None},
        )
        return created
    except IntegrityError:
        return False


def _mark_sent_overdue_today_safe(vehicle_id: int, type_id: int, base_service_id: int, mode: str, today) -> bool:
    """
    OVERDUE: una vez por día (sent_on = hoy).
    Devuelve True si se creó (o sea, NO estaba enviada hoy).
    """
    try:
        _obj, created = FlotaAlertaEnviada.objects.get_or_create(
            vehicle_id=vehicle_id,
            service_type_id=type_id,
            base_service_id=base_service_id,
            mode=mode,
            threshold=0,
            sent_on=today,
        )
        return created
    except IntegrityError:
        return False


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
    CRON Flota:
    - Token ?token=...
    - Solo una vez por día (lock DB)
    - Nunca antes de las 08:00 (hora local), salvo force=1
    - PRE: una sola vez por base_service+threshold
    - OVERDUE: una vez al día (si notify_on_overdue)
    - ✅ Anti-spam: nunca borramos el lock diario automáticamente
    - ✅ Anti-concurrencia: advisory lock por día (Postgres) **ESTABLE**
    """
    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (getattr(settings, "FLOTA_CRON_TOKEN", "") or "").strip()
    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()
    force_run = (request.GET.get("force") or "").strip() == "1"

    # ✅ permite pruebas antes de las 08:00 con force=1
    if ahora.hour < 8 and not force_run:
        return JsonResponse({"status": "before-8am", "detail": "Aún no son las 08:00"}, status=200)

    # ✅ advisory lock por día (ESTABLE: no usamos hash() de Python)
    import zlib
    lock_key = zlib.crc32(f"flota_mantenciones:{hoy.isoformat()}".encode("utf-8"))

    if not _try_pg_advisory_lock(lock_key):
        return JsonResponse({"status": "already-running", "detail": "Otro proceso ya está ejecutando"}, status=200)

    job_name = "flota_mantenciones"

    try:
        # ✅ force: resetea lock diario para permitir re-ejecutar hoy
        if force_run:
            FlotaCronDiarioEjecutado.objects.filter(nombre=job_name, fecha=hoy).delete()

        # Lock diario robusto contra concurrencia
        try:
            with transaction.atomic():
                _cron_obj, created = FlotaCronDiarioEjecutado.objects.get_or_create(nombre=job_name, fecha=hoy)
        except IntegrityError:
            created = False

        if not created and not force_run:
            return JsonResponse({"status": "already-run", "detail": "Ya se ejecutó hoy"}, status=200)

        enviados = 0
        saltados = 0
        errores_envio = 0
        errores_logica = 0
        ultimo_error = None

        logo_url = _get_logo_url()

        cfgs = (
            VehicleNotificationSettings.objects
            .select_related("vehicle")
            .filter(enabled=True)
        )

        tipos = (
            VehicleServiceType.objects
            .filter(is_active=True)
            .order_by("name")
        )

        for cfg in cfgs:
            v = cfg.vehicle
            to_emails, cc_emails = _build_recipients(cfg)

            if not to_emails and not cc_emails:
                saltados += 1
                continue

            km_actual = int(v.kilometraje_actual or 0)

            for t in tipos:
                has_km = bool((t.interval_km or 0) > 0)
                has_days = bool((t.interval_days or 0) > 0)
                if not has_km and not has_days:
                    continue

                last = _latest_service_for_type(v.id, t)
                if not last:
                    continue

                # -----------------------
                # A) ALERTAS POR KM
                # -----------------------
                if has_km and last.kilometraje_declarado is not None:
                    try:
                        due_km = int(last.kilometraje_declarado) + int(t.interval_km)
                        remaining_km = due_km - km_actual

                        # Overdue KM (una vez al día)
                        if remaining_km <= 0:
                            if t.notify_on_overdue:
                                if _mark_sent_overdue_today_safe(v.id, t.id, last.id, "overdue_km", hoy):
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
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        errores_envio += 1
                                        ultimo_error = e.__class__.__name__
                                        logger.exception("Fallo envío flota overdue_km veh=%s tipo=%s", v.id, t.id)
                                    else:
                                        enviados += 1
                            continue

                        # Pre-alertas KM
                        steps = t.alert_km_steps_list
                        if steps:
                            for threshold in steps:
                                if remaining_km <= int(threshold):
                                    if _mark_sent_pre_safe(v.id, t.id, last.id, int(threshold)):
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
                                            enviados += 1
                                    break

                    except Exception as e:
                        errores_logica += 1
                        ultimo_error = e.__class__.__name__
                        logger.exception("Error lógica flota km veh=%s tipo=%s", v.id, t.id)

                # -----------------------
                # B) ALERTAS POR DÍAS
                # -----------------------
                if has_days:
                    try:
                        due_date = last.service_date + timedelta(days=int(t.interval_days))
                        remaining_days = (due_date - hoy).days

                        if remaining_days <= 0:
                            if t.notify_on_overdue:
                                if _mark_sent_overdue_today_safe(v.id, t.id, last.id, "overdue_days", hoy):
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
                                        enviados += 1
                            continue

                        steps_days = t.alert_days_steps_list
                        if steps_days:
                            for threshold in steps_days:
                                if remaining_days <= int(threshold):
                                    if _mark_sent_pre_days_safe(v.id, t.id, last.id, int(threshold)):
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
                                            enviados += 1
                                    break

                    except Exception as e:
                        errores_logica += 1
                        ultimo_error = e.__class__.__name__
                        logger.exception("Error lógica flota days veh=%s tipo=%s", v.id, t.id)

        ok = (errores_envio == 0 and errores_logica == 0)

        return JsonResponse(
            {
                "status": "ok" if ok else "partial-error",
                "date": str(hoy),
                "method": request.method,
                "force": force_run,
                "sent": enviados,
                "skipped": saltados,
                "send_errors": errores_envio,
                "logic_errors": errores_logica,
                "last_error": ultimo_error,
            },
            status=200,
        )

    finally:
        _pg_advisory_unlock(lock_key)
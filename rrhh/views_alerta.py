from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .models import ContratoAlertaEnviada, ContratoTrabajo, CronDiarioEjecutado


@require_http_methods(["GET", "HEAD"])
def cron_contratos_por_vencer(request):
    """
    Endpoint para ser llamado por UptimeRobot (o similar) varias veces al día.
    - Protegido por token (?token=...).
    - Solo ejecuta el envío una vez por día.
    - Nunca se ejecuta antes de las 08:00 (hora local).
    - Envía correos cuando faltan 20, 15, 10, 5, 3, 2, 1 días.
    """

    # 1) Seguridad: validar token
    token_recibido = request.GET.get("token")
    token_esperado = getattr(settings, "CONTRATOS_CRON_TOKEN", "")

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

    # 3) Ver si ya se ejecutó hoy este cron
    job_name = "contratos_por_vencer"
    if CronDiarioEjecutado.objects.filter(nombre=job_name, fecha=hoy).exists():
        return JsonResponse(
            {"status": "already-run", "detail": "Ya se ejecutó hoy"},
            status=200,
        )

    # Marcar como ejecutado
    CronDiarioEjecutado.objects.create(nombre=job_name, fecha=hoy)

    # 4) Días en los que queremos avisar
    DIAS_ALERTA = {20, 15, 10, 5, 3, 2, 1}

    # Pool de correos SOLO para contratos
    recip_raw = getattr(settings, "CONTRATOS_ALERT_EMAILS", "")
    destinatarios = [e.strip() for e in recip_raw.split(",") if e.strip()]

    if not destinatarios:
        return JsonResponse(
            {
                "status": "no-recipients",
                "detail": "CONTRATOS_ALERT_EMAILS vacío, no se enviaron correos",
            },
            status=200,
        )

    # Logo Planix: misma ruta que usas en el mail de reset
    logo_url = getattr(
        settings,
        "PLANIX_LOGO_URL",
        "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
    )

    # 5) Buscar contratos con fecha_termino definida
    contratos = (
        ContratoTrabajo.objects
        .select_related("tecnico")
        .exclude(fecha_termino__isnull=True)
    )

    enviados = 0
    saltados = 0

    for c in contratos:
        dias_restantes = (c.fecha_termino - hoy).days

        if dias_restantes not in DIAS_ALERTA:
            saltados += 1
            continue

        # Evitar duplicados
        ya_enviada = ContratoAlertaEnviada.objects.filter(
            contrato=c,
            fecha_termino=c.fecha_termino,
            dias_antes=dias_restantes,
        ).exists()

        if ya_enviada:
            saltados += 1
            continue

        # ===== Construir correo =====
        tecnico = c.tecnico
        nombre_tecnico = (
            tecnico.get_full_name()
            if hasattr(tecnico, "get_full_name") else str(tecnico)
        )
        rut_tecnico = getattr(tecnico, "identidad", "")

        subject = (
            f"[GZ Services] Contrato por vencer en {dias_restantes} días - {nombre_tecnico}"
        )

        # Texto plano ( fallback )
        text_body = (
            "Hola,\n\n"
            f"El contrato de trabajo del técnico {nombre_tecnico}"
            f"{f' (RUT {rut_tecnico})' if rut_tecnico else ''} "
            f"vence el día {c.fecha_termino:%Y-%m-%d}.\n\n"
            f"Quedan {dias_restantes} días para su vencimiento.\n\n"
            "Por favor, revisar renovaciones o acciones necesarias en el módulo de "
            "RRHH de GZ Services.\n\n"
            "Este mensaje fue generado automáticamente por el sistema Planix.\n"
        )

        # HTML estilo similar al mail de recuperación
        html_body = f"""\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
</head>
<body style="background-color:#f4f6f8; padding:20px;">
  <div style="
      max-width:600px;
      margin:auto;
      background-color:#ffffff;
      padding:30px;
      border-radius:12px;
      box-shadow:0 5px 15px rgba(0,0,0,0.10);
      font-family:'Segoe UI',system-ui,-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;
  ">
    <div style="text-align:center; margin-bottom:24px;">
      <img src="{logo_url}"
           alt="Logo Planix"
           style="max-width:180px; height:auto;">
    </div>

    <h2 style="font-size:22px; margin:0 0 12px; color:#111827;">
      Aviso de contrato por vencer
    </h2>

    <p style="font-size:14px; color:#374151; margin:0 0 12px;">
      Hola,
    </p>

    <p style="font-size:14px; color:#374151; margin:0 0 12px;">
      El contrato de trabajo del técnico
      <strong>{nombre_tecnico}</strong>
      {f"(RUT <strong>{rut_tecnico}</strong>)" if rut_tecnico else ""} 
      vence el día <strong>{c.fecha_termino:%Y-%m-%d}</strong>.
    </p>

    <p style="font-size:14px; color:#111827; margin:0 0 16px;">
      <strong>Quedan {dias_restantes} día(s)</strong> para su vencimiento.
    </p>

    <div style="
        background-color:#f9fafb;
        border-radius:10px;
        padding:14px 18px;
        font-size:13px;
        color:#374151;
        margin-bottom:20px;
    ">
      <p style="margin:0 0 6px;"><strong>Resumen del contrato:</strong></p>
      <ul style="margin:4px 0 0 18px; padding:0;">
        <li><strong>Técnico:</strong> {nombre_tecnico}</li>
        {f"<li><strong>RUT:</strong> {rut_tecnico}</li>" if rut_tecnico else ""}
        <li><strong>Fecha de término:</strong> {c.fecha_termino:%Y-%m-%d}</li>
        <li><strong>Estado:</strong> Por vencer</li>
      </ul>
    </div>

    <p style="font-size:14px; color:#374151; margin:0 0 24px;">
      Por favor revisa la situación en el módulo de <strong>RRHH</strong> de
      la plataforma para gestionar una eventual renovación, término de contrato
      o actualización de condiciones.
    </p>

    <div style="font-size:12px; color:#9ca3af; margin-top:24px; text-align:center;">
      Enviado automáticamente por el sistema Planix.
      No responder a este correo.
    </div>
  </div>
</body>
</html>
"""

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=destinatarios,
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)

        # Registrar alerta enviada
        ContratoAlertaEnviada.objects.create(
            contrato=c,
            fecha_termino=c.fecha_termino,
            dias_antes=dias_restantes,
        )
        enviados += 1

    return JsonResponse(
        {
            "status": "ok",
            "date": str(hoy),
            "sent": enviados,
            "skipped": saltados,
        },
        status=200,
    )
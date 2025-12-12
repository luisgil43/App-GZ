from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .models import ContratoAlertaEnviada, ContratoTrabajo, CronDiarioEjecutado


def contrato_sigue_siend_ultimo(contrato: ContratoTrabajo) -> bool:
    """
    Devuelve True si este contrato sigue siendo el 'último' contrato relevante
    del técnico para efectos de alertas.

    Si existe otro contrato del mismo técnico con:
      - fecha_termino mayor, o
      - fecha_termino en blanco (indefinido),
    asumimos que ese contrato nuevo reemplaza al actual y por lo tanto
    este contrato ya no debería generar correos (ni pre ni post-vencimiento).
    """
    # ¿Hay algún contrato indefinido más nuevo?
    existe_indefinido_nuevo = ContratoTrabajo.objects.filter(
        tecnico=contrato.tecnico,
        notificar_vencimiento=True,
        fecha_termino__isnull=True,
    ).exclude(pk=contrato.pk).exists()

    if existe_indefinido_nuevo:
        return False

    # ¿Hay algún contrato con fecha_termino mayor que éste?
    if contrato.fecha_termino:
        existe_mas_nuevo = ContratoTrabajo.objects.filter(
            tecnico=contrato.tecnico,
            notificar_vencimiento=True,
            fecha_termino__gt=contrato.fecha_termino,
        ).exclude(pk=contrato.pk).exists()

        if existe_mas_nuevo:
            return False

    return True


@require_http_methods(["GET", "HEAD"])
def cron_contratos_por_vencer(request):
    """
    Endpoint para ser llamado por UptimeRobot (o similar) varias veces al día.
    - Protegido por token (?token=...).
    - Solo ejecuta el envío una vez por día.
    - Nunca se ejecuta antes de las 08:00 (hora local).
    - PRE-vencimiento: envía correos cuando faltan 20, 15, 10, 5, 3, 2, 1 días.
    - POST-vencimiento: envía correos TODOS los días (hasta MAX_DIAS_POST)
      mientras:
        * el contrato tenga notificar_vencimiento=True, y
        * siga siendo el 'último' contrato del técnico.
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

    # 4) Configuración de días
    DIAS_ALERTA_PRE = {20, 15, 10, 5, 3, 2, 1}
    # Máximo de días después del vencimiento que queremos hinchar
    # Si quieres ilimitado, pon MAX_DIAS_POST = None
    MAX_DIAS_POST = 60

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

    # 5) Buscar contratos con fecha_termino definida y alertas activas
    contratos = (
        ContratoTrabajo.objects
        .select_related("tecnico")
        .exclude(fecha_termino__isnull=True)
        .filter(notificar_vencimiento=True)
    )

    enviados = 0
    saltados = 0

    for c in contratos:
        # Si ya existe un contrato nuevo para este técnico, no seguir molestando
        if not contrato_sigue_siend_ultimo(c):
            saltados += 1
            continue

        dias_relativos = (c.fecha_termino - hoy).days
        es_pre = dias_relativos > 0

        # ---------------- PRE-VENCIMIENTO ----------------
        if es_pre:
            if dias_relativos not in DIAS_ALERTA_PRE:
                saltados += 1
                continue

            dias_pasados = 0
            estado_tabla = "Por vencer"
            estado_subject = f"por vencer en {dias_relativos} días"
            texto_linea_plain = (
                f"Quedan {dias_relativos} día(s) para su vencimiento."
            )
            alerta_html = (
                f"<strong>Quedan {dias_relativos} día(s)</strong> para su vencimiento."
            )

        # ---------------- POST-VENCIMIENTO ----------------
        else:
            dias_pasados = -dias_relativos  # 0 => vence hoy, 1 => vencido hace 1 día...

            if MAX_DIAS_POST is not None and dias_pasados > MAX_DIAS_POST:
                saltados += 1
                continue

            estado_tabla = "Vencido"

            if dias_pasados == 0:
                estado_subject = "vence hoy"
                texto_linea_plain = "El contrato vence hoy."
                alerta_html = "<strong>El contrato vence hoy.</strong>"
            else:
                estado_subject = f"vencido hace {dias_pasados} día(s)"
                texto_linea_plain = (
                    f"El contrato se encuentra vencido hace {dias_pasados} día(s)."
                )
                alerta_html = (
                    f"<strong>El contrato se encuentra vencido</strong> "
                    f"hace {dias_pasados} día(s)."
                )

        # Evitar duplicados: usamos el mismo valor relativo (positivo o negativo)
        ya_enviada = ContratoAlertaEnviada.objects.filter(
            contrato=c,
            fecha_termino=c.fecha_termino,
            dias_antes=dias_relativos,
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
            f"[GZ Services] Contrato {estado_subject} - {nombre_tecnico}"
        )

        # Texto plano (fallback)
        text_body = (
            "Hola,\n\n"
            f"El contrato de trabajo del técnico {nombre_tecnico}"
            f"{f' (RUT {rut_tecnico})' if rut_tecnico else ''} "
            f"tiene fecha de término el día {c.fecha_termino:%Y-%m-%d}.\n\n"
            f"{texto_linea_plain}\n\n"
            "Por favor, revisar renovaciones, anexos o término en el módulo de "
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
      Aviso de contrato {estado_tabla.lower()}
    </h2>

    <p style="font-size:14px; color:#374151; margin:0 0 12px;">
      Hola,
    </p>

    <p style="font-size:14px; color:#374151; margin:0 0 12px;">
      El contrato de trabajo del técnico
      <strong>{nombre_tecnico}</strong>
      {f"(RUT <strong>{rut_tecnico}</strong>)" if rut_tecnico else ""} 
      tiene fecha de término el día <strong>{c.fecha_termino:%Y-%m-%d}</strong>.
    </p>

    <p style="font-size:14px; color:#111827; margin:0 0 16px;">
      {alerta_html}
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
        <li><strong>Estado:</strong> {estado_tabla}</li>
      </ul>
    </div>

    <p style="font-size:14px; color:#374151; margin:0 0 24px;">
      Por favor revisa la situación en el módulo de <strong>RRHH</strong> de
      la plataforma para gestionar una eventual renovación, anexo, término de
      contrato o actualización de condiciones.
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

        # Registrar alerta enviada (puede ser positiva o negativa)
        ContratoAlertaEnviada.objects.create(
            contrato=c,
            fecha_termino=c.fecha_termino,
            dias_antes=dias_relativos,
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
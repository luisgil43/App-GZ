from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
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

    # 2) No ejecutar antes de las 08:00 (Chile)
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

    # Marcar como ejecutado (para que si UptimeRobot llama varias veces, solo
    # esta primera pasada haga el trabajo).
    CronDiarioEjecutado.objects.create(nombre=job_name, fecha=hoy)

    # 4) Días en los que queremos avisar
    DIAS_ALERTA = {20, 15, 10, 5, 3, 2, 1}

    # Correos destino (pool de correos SOLO para contratos)
    recip_raw = getattr(settings, "CONTRATOS_ALERT_EMAILS", "")
    destinatarios = [e.strip() for e in recip_raw.split(",") if e.strip()]

    if not destinatarios:
        # No hay correos configurados => salimos sin hacer nada ruidoso
        return JsonResponse(
            {
                "status": "no-recipients",
                "detail": "CONTRATOS_ALERT_EMAILS vacío, no se enviaron correos",
            },
            status=200,
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

        # Solo nos interesan exactamente estos días
        if dias_restantes not in DIAS_ALERTA:
            saltados += 1
            continue

        # Evitar duplicados: ¿ya enviamos para este contrato + fecha + días_restantes?
        ya_enviada = ContratoAlertaEnviada.objects.filter(
            contrato=c,
            fecha_termino=c.fecha_termino,
            dias_antes=dias_restantes,
        ).exists()

        if ya_enviada:
            saltados += 1
            continue

        # 6) Construir correo
        tecnico = c.tecnico
        nombre_tecnico = (
            tecnico.get_full_name()
            if hasattr(tecnico, "get_full_name") else str(tecnico)
        )
        rut_tecnico = getattr(tecnico, "identidad", "")

        subject = (
            f"[GZ] Contrato por vencer en {dias_restantes} días - {nombre_tecnico}"
        )
        body = (
            f"Hola,\n\n"
            f"El contrato de trabajo del técnico {nombre_tecnico}"
            f"{f' (RUT {rut_tecnico})' if rut_tecnico else ''} "
            f"vence el día {c.fecha_termino:%Y-%m-%d}.\n\n"
            f"Quedan {dias_restantes} días para su vencimiento.\n\n"
            f"Por favor, revisar renovaciones o acciones necesarias en el "
            f"módulo de RRHH de GZ Services.\n\n"
            f"Este mensaje fue generado automáticamente.\n"
        )

        send_mail(
            subject,
            body,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            destinatarios,
            fail_silently=False,
        )

        # 7) Registrar que ya se envió esta alerta
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
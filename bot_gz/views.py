# bot_gz/views.py

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import (HttpResponseBadRequest, HttpResponseForbidden,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import BotIntent, BotMessageLog
from .services import handle_telegram_update

logger = logging.getLogger(__name__)


# ========================
#  Webhook & health
# ========================

@require_GET
def telegram_health(request):
    """
    Endpoint simple para comprobar que la app está arriba.
    Útil para probar en navegador o monitores.
    """
    return JsonResponse({"ok": True, "detail": "bot-gz health ok"})


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """
    Endpoint que recibe los updates reales desde Telegram.
    Aquí NO devolvemos texto al usuario, solo procesamos
    y respondemos 200 a Telegram.
    """
    try:
        raw_body = request.body.decode("utf-8")
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("Webhook Telegram: JSON inválido")
        return HttpResponseBadRequest("Invalid JSON")

    try:
        handle_telegram_update(data)
    except Exception:
        # Nunca dejamos que explote hacia Telegram, solo log
        logger.exception("Error procesando update de Telegram")

    return JsonResponse({"ok": True})


# ========================
#  Helpers de permisos
# ========================

def _user_can_train_bot(user) -> bool:
    """
    Solo gente 'fuerte' puede ver la consola de entrenamiento.
    Ajusta esto si quieres incluir más roles.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # Custom roles de tu app
    if getattr(user, "es_admin_general", False):
        return True
    if getattr(user, "es_rrhh", False):
        return True
    if getattr(user, "es_facturacion", False):
        return True
    return False


def _forbidden(request):
    return HttpResponseForbidden("No tienes permisos para entrenar el bot.")


# ========================
#  Consola de entrenamiento
# ========================

@login_required
def training_dashboard(request):
    """
    Vista principal de entrenamiento:
    - Lista mensajes que el bot no entendió bien (fallback/error)
      o marcados para entrenamiento.
    - Permite filtrar y acceder a una vista de edición por mensaje.
    """

    if not _user_can_train_bot(request.user):
        return _forbidden(request)

    # Filtros básicos
    status = request.GET.get("status", "").strip()
    intent_slug = request.GET.get("intent", "").strip()
    solo_entrenamiento = request.GET.get("solo_entrenamiento", "1") == "1"

    logs = BotMessageLog.objects.all().select_related(
        "usuario",
        "intent_detectado",
        "intent_corregido",
    )

    # Por defecto: solo los que necesitan entrenamiento
    if solo_entrenamiento:
        logs = logs.filter(
            Q(marcar_para_entrenamiento=True) | Q(status__in=["fallback", "error"])
        )

    if status:
        logs = logs.filter(status=status)

    if intent_slug:
        logs = logs.filter(
            Q(intent_detectado__slug=intent_slug)
            | Q(intent_corregido__slug=intent_slug)
        )

    logs = logs.order_by("-creado_en")[:200]  # límite razonable para no explotar la página

    intents = BotIntent.objects.filter(activo=True).order_by("slug")

    ctx = {
        "logs": logs,
        "intents": intents,
        "status_actual": status,
        "intent_actual": intent_slug,
        "solo_entrenamiento": solo_entrenamiento,
    }
    return render(request, "bot_gz/training_dashboard.html", ctx)


@login_required
def training_edit_message(request, pk: int):
    """
    Vista detalle para corregir un mensaje concreto:
    - Ver texto original
    - Ver intent detectado
    - Asignar intent corregido
    - Marcar / desmarcar "para entrenamiento"
    """

    if not _user_can_train_bot(request.user):
        return _forbidden(request)

    msg = get_object_or_404(
        BotMessageLog.objects.select_related(
            "usuario",
            "intent_detectado",
            "intent_corregido",
        ),
        pk=pk,
    )

    intents = BotIntent.objects.filter(activo=True).order_by("slug")

    if request.method == "POST":
        intent_corregido_id = request.POST.get("intent_corregido") or ""
        marcar = request.POST.get("marcar_para_entrenamiento") == "on"

        if intent_corregido_id:
            try:
                intent_obj = BotIntent.objects.get(pk=intent_corregido_id)
                msg.intent_corregido = intent_obj
            except BotIntent.DoesNotExist:
                messages.error(request, "El intent seleccionado no existe.")
        else:
            msg.intent_corregido = None

        msg.marcar_para_entrenamiento = marcar
        msg.save(update_fields=["intent_corregido", "marcar_para_entrenamiento"])

        messages.success(request, "Mensaje actualizado para entrenamiento del bot.")
        return redirect("bot_gz:training_dashboard")

    ctx = {
        "msg": msg,
        "intents": intents,
    }
    return render(request, "bot_gz/training_edit_message.html", ctx)
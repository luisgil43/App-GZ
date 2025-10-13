from .models import Notificacion
from django.db.models import Q  # ✅ Esto faltaba
import json


def notificaciones_context(request):
    if request.user.is_authenticated:
        queryset = Notificacion.objects.filter(
            usuario=request.user).order_by('leido', '-fecha')
        return {
            'notificaciones_no_leidas': queryset.filter(leido=False).count(),
            'notificaciones_recientes': queryset[:10]
        }
    return {}

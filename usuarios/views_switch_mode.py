from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse


def _has_admin_role(user) -> bool:
    """
    Consideramos 'admin' si tiene al menos uno de estos roles/flags.
    Ajusta la lista si tienes otros flags.
    """
    flags = [
        "es_admin_general",
        "es_supervisor",
        "es_pm",
        "es_rrhh",
        "es_logistica",
        "es_prevencion",
        "es_facturacion",
        "es_subcontrato",
    ]
    for f in flags:
        if getattr(user, f, False):
            return True

    # Extra: si usas staff para permisos admin, también cuenta
    return bool(getattr(user, "is_staff", False))


def _has_user_role(user) -> bool:
    """
    Usuario (técnico/operativo).

    ✅ REGLA TUYA:
    - SOLO es "usuario" si explícitamente tiene es_tecnico o es_usuario en True.
    - Si esos flags no existen en el modelo, NO inventamos → False.
    """
    if hasattr(user, "es_tecnico") or hasattr(user, "es_usuario"):
        return bool(getattr(user, "es_tecnico", False) or getattr(user, "es_usuario", False))
    return False


@login_required
def switch_mode(request):
    """
    Alterna entre modo 'user' y 'admin' usando sesión.
    Requiere que el usuario tenga rol de usuario + rol admin.
    """
    u = request.user

    if not (_has_user_role(u) and _has_admin_role(u)):
        return HttpResponseForbidden("No autorizado")

    current = (request.session.get("ui_mode") or "user").lower()
    target = "admin" if current != "admin" else "user"
    request.session["ui_mode"] = target

    # ✅ Importante: NO uses next aquí (porque el next puede ser una URL del "otro" modo)
    # y el middleware te lo va a rebotar.
    if target == "admin":
        return redirect("/dashboard_admin/index/")
    return redirect("/dashboard/")
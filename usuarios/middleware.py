# usuarios/middleware.py
import logging
import time
from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin


class SessionExpiryMiddleware:
    """
    - Cierra sesión por INACTIVIDAD si se supera SESSION_IDLE_TIMEOUT (segundos).
    - (Opcional) Cierra sesión por tiempo ABSOLUTO si se supera SESSION_ABSOLUTE_TIMEOUT (segundos).
    Guarda marcas de tiempo en la sesión: 'last_activity' y 'login_time'.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_timeout = int(getattr(settings, "SESSION_IDLE_TIMEOUT", 15 * 60))
        self.absolute_timeout = getattr(settings, "SESSION_ABSOLUTE_TIMEOUT", None)

    def __call__(self, request):
        path = request.path or ""
        if path.startswith(("/static/", "/media/")):
            return self.get_response(request)

        excluded_paths = set()
        try:
            excluded_paths.add(reverse("usuarios:login"))
        except Exception:
            pass
        try:
            excluded_paths.add(reverse("dashboard_admin:logout"))
        except Exception:
            pass

        if (not request.user.is_authenticated) or (path in excluded_paths):
            return self.get_response(request)

        now = int(time.time())
        session = request.session

        if "last_activity" not in session:
            session["last_activity"] = now
        if "login_time" not in session:
            session["login_time"] = now

        if self.idle_timeout and (now - session["last_activity"] > self.idle_timeout):
            self._logout_and_redirect(request, reason="Tu sesión fue cerrada por inactividad.")
            return redirect("usuarios:login")

        if self.absolute_timeout and (now - session["login_time"] > int(self.absolute_timeout)):
            self._logout_and_redirect(request, reason="Tu sesión expiró por tiempo máximo de sesión.")
            return redirect("usuarios:login")

        session["last_activity"] = now
        return self.get_response(request)

    def _logout_and_redirect(self, request, reason: str):
        for k in ("last_activity", "login_time"):
            request.session.pop(k, None)
        try:
            messages.warning(request, reason)
        except Exception:
            pass
        logout(request)


log = logging.getLogger("pagos.monthly")


class LogRedirectsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        if resp.status_code in (301, 302, 303, 307, 308):
            log.warning("REDIRECT %s %s → %s", request.method, request.path, resp.get("Location"))
        return resp


class ChileTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone.activate(ZoneInfo("America/Santiago"))
        resp = self.get_response(request)
        timezone.deactivate()
        return resp


def _get_2fa_enforce_date():
    return getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None)


class Enforce2FAMiddleware(MiddlewareMixin):
    """
    Si la fecha de obligatoriedad ya pasó y el usuario staff
    NO tiene 2FA activado, lo fuerza a ir a la pantalla de setup 2FA.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            return None

        if not user.is_staff:
            return None

        enforce_date = _get_2fa_enforce_date()
        if not enforce_date:
            return None

        today = timezone.localdate()
        if today < enforce_date:
            return None

        if getattr(user, "two_factor_enabled", False):
            return None

        try:
            match = resolve(request.path)
            full_name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
        except Resolver404:
            full_name = None

        allowed_names = {
            "usuarios:two_factor_setup",
            "usuarios:two_factor_verify",
            "usuarios:login_unificado",
            "usuarios:logout",
        }

        static_url = getattr(settings, "STATIC_URL", "/static/")
        media_url = getattr(settings, "MEDIA_URL", "/media/")

        if request.path.startswith(static_url) or request.path.startswith(media_url):
            return None

        if full_name in allowed_names:
            return None

        messages.warning(
            request,
            "Debes configurar el segundo factor de autenticación para continuar usando la plataforma.",
        )
        return redirect("usuarios:two_factor_setup")


# ==========================================================
# ✅ UI MODE SWITCH (USER <-> ADMIN)
# ==========================================================

def _ui_has_admin_role(user) -> bool:
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
    if any(bool(getattr(user, f, False)) for f in flags):
        return True
    return bool(getattr(user, "is_staff", False))


def _ui_has_user_role(user) -> bool:
    """
    IMPORTANTE:
    - Si existen flags (es_tecnico / es_usuario), exige que alguno sea True.
    - Si NO existen en tu modelo, DEVUELVE False (para NO inventar que es usuario).
    """
    if hasattr(user, "es_tecnico") or hasattr(user, "es_usuario"):
        return bool(getattr(user, "es_tecnico", False) or getattr(user, "es_usuario", False))
    return False


class UIModeRedirectMiddleware:
    """
    Reglas:
    - Solo aplica si el usuario tiene rol USER + rol ADMIN.
    - Si entra a /dashboard_admin/index/ => fija ui_mode=admin
    - Si entra a /dashboard/            => fija ui_mode=user
    - Si ui_mode=user y entra a /dashboard_admin/... => lo manda a /dashboard/
    - Si ui_mode=admin y entra a /dashboard/...      => lo manda a /dashboard_admin/index/
    """

    USER_HOME = "/dashboard/"
    ADMIN_HOME = "/dashboard_admin/index/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if not (u and u.is_authenticated):
            return self.get_response(request)

        path = request.path or "/"

        # excluir rutas que no debemos tocar
        if (
            path.startswith("/static/")
            or path.startswith("/media/")
            or path.startswith("/admin/")
            or path.startswith("/usuarios/login")
            or path.startswith("/usuarios/ui/")
            or path.startswith("/logout")
        ):
            return self.get_response(request)

        can_user = _ui_has_user_role(u)
        can_admin = _ui_has_admin_role(u)

        # ✅ si NO tiene ambos perfiles, NO aplicamos modo y limpiamos ui_mode
        if not (can_user and can_admin):
            request.session.pop("ui_mode", None)
            return self.get_response(request)

        # modo actual
        mode = (request.session.get("ui_mode") or "user").lower()

        # ✅ si llega directo a un HOME, eso define el modo (evita que el login te rebote)
        if path == self.ADMIN_HOME:
            request.session["ui_mode"] = "admin"
            return self.get_response(request)

        if path == self.USER_HOME:
            request.session["ui_mode"] = "user"
            return self.get_response(request)

        # ✅ forzar navegación consistente
        if mode == "admin" and path.startswith(self.USER_HOME):
            return redirect(self.ADMIN_HOME)

        if mode != "admin" and path.startswith("/dashboard_admin/"):
            return redirect(self.USER_HOME)

        return self.get_response(request)
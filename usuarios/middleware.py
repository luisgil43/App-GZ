# usuarios/middleware.py
import logging
import time
from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.sessions.middleware import SessionMiddleware
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.deprecation import MiddlewareMixin


class ActivitySessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        # El sondeo puede terminar después de una interacción. Evita que
        # guarde una copia anterior de la sesión por SAVE_EVERY_REQUEST.
        if (
            getattr(request, "is_photo_poll", False)
            and not request.session.modified
            and not request.session.is_empty()
        ):
            patch_vary_headers(response, ("Cookie",))
            return response
        return super().process_response(request, response)


class SessionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_timeout = int(getattr(settings, "IDLE_TIMEOUT_SECONDS", 15 * 60))
        self.user_idle_timeout = settings.USER_IDLE_TIMEOUT_SECONDS
        self.absolute_timeout = getattr(settings, "SESSION_ABSOLUTE_TIMEOUT", None)

    def __call__(self, request):
        path = request.path or ""
        if path.startswith(("/static/", "/media/")):
            return self.get_response(request)

        excluded_paths = set()
        try:
            excluded_paths.add(reverse("usuarios:login_unificado"))
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
        try:
            view_name = resolve(request.path_info).view_name
        except Resolver404:
            view_name = None

        request.is_photo_poll = view_name in {
            "operaciones:fotos_status_json",
            "operaciones:foto_de_requerimiento",
        }
        request.user_panel_session = self._is_user_panel(request)
        idle_timeout = self.user_idle_timeout if request.user_panel_session else self.idle_timeout
        last_activity = session.get("last_activity", now)

        idle_seconds = now - last_activity
        if idle_timeout and (
            idle_seconds > idle_timeout
            or (request.user_panel_session and idle_seconds == idle_timeout)
        ):
            self._logout_and_redirect(
                request, reason="Tu sesión fue cerrada por inactividad."
            )
            return redirect("usuarios:login_unificado")

        if self.absolute_timeout and (
            now - session.get("login_time", now) > int(self.absolute_timeout)
        ):
            self._logout_and_redirect(
                request, reason="Tu sesión expiró por tiempo máximo de sesión."
            )
            return redirect("usuarios:login_unificado")

        if not request.is_photo_poll and view_name != "usuarios:session_activity":
            session.setdefault("login_time", now)
            session["last_activity"] = now
        return self.get_response(request)

    def _is_user_panel(self, request):
        if request.path_info.startswith("/admin/"):
            return False
        mode = request.session.get("ui_mode")
        if mode in ("admin", "user"):
            return mode == "user"
        user = request.user
        return _ui_has_user_role(user) or not (
            _ui_has_admin_role(user) or user.es_flota or user.es_bodeguero
        )

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

    Importante:
    - Permite cerrar sesión aunque el usuario no tenga 2FA configurado.
    - Permite acceder a login, verificación 2FA, setup 2FA, static/media.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            return None

        path = request.path or ""

        static_url = getattr(settings, "STATIC_URL", "/static/")
        media_url = getattr(settings, "MEDIA_URL", "/media/")

        # Archivos estáticos/media siempre permitidos
        if path.startswith(static_url) or path.startswith(media_url):
            return None

        # Rutas por path que deben poder ejecutarse aunque 2FA esté pendiente
        allowed_path_prefixes = (
            "/admin/logout/",
            "/logout/",
            "/usuarios/logout/",
            "/dashboard_admin/logout/",
        )

        if path.startswith(allowed_path_prefixes):
            return None

        # Solo exigir a staff
        if not user.is_staff:
            return None

        enforce_date = _get_2fa_enforce_date()
        if not enforce_date:
            return None

        today = timezone.localdate()
        if today < enforce_date:
            return None

        # Si ya tiene 2FA activo, continúa normal
        if getattr(user, "two_factor_enabled", False):
            return None

        try:
            match = resolve(request.path_info)
            full_name = (
                f"{match.namespace}:{match.url_name}"
                if match.namespace
                else match.url_name
            )
            url_name = match.url_name
        except Resolver404:
            full_name = None
            url_name = None

        allowed_names = {
            "usuarios:two_factor_setup",
            "usuarios:two_factor_verify",
            "usuarios:login_unificado",
            "usuarios:logout",
            "dashboard_admin:logout",
            "logout",
            "two_factor_setup",
            "two_factor_verify",
            "login_unificado",
        }

        if full_name in allowed_names or url_name in allowed_names:
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
        if not (u and u.is_authenticated) or getattr(request, "is_photo_poll", False):
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
            or path.startswith("/usuarios/logout/")
            or path.startswith("/dashboard_admin/logout/")
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

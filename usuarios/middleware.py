# usuarios/middlewares.py
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
        self.idle_timeout = int(
            # 15 min por defecto
            getattr(settings, "SESSION_IDLE_TIMEOUT", 15 * 60))
        self.absolute_timeout = getattr(
            settings, "SESSION_ABSOLUTE_TIMEOUT", None)  # p.ej. 8*60*60

    def __call__(self, request):
        # Rutas excluidas (login, logout, static, etc.)
        path = request.path or ""
        excluded_prefixes = ("/static/", "/media/")
        if path.startswith(excluded_prefixes):
            return self.get_response(request)

        # Resolver reverses en tiempo de request (evita problemas de import)
        excluded_paths = set()
        try:
            excluded_paths.add(reverse("usuarios:login"))
        except Exception:
            pass
        try:
            excluded_paths.add(reverse("dashboard_admin:logout"))
        except Exception:
            pass
        # agrega aquí otras rutas si lo necesitas (healthchecks, webhooks, etc.)

        # Si no está autenticado o la ruta está excluida -> seguir
        if (not request.user.is_authenticated) or (path in excluded_paths):
            return self.get_response(request)

        now = int(time.time())
        session = request.session

        # Inicializar tiempos si no existen
        if "last_activity" not in session:
            session["last_activity"] = now
        if "login_time" not in session:
            session["login_time"] = now

        # 1) Timeout por inactividad
        if self.idle_timeout and (now - session["last_activity"] > self.idle_timeout):
            self._logout_and_redirect(
                request, reason="Tu sesión fue cerrada por inactividad.")
            return redirect("usuarios:login")

        # 2) Timeout absoluto (opcional)
        if self.absolute_timeout and (now - session["login_time"] > int(self.absolute_timeout)):
            self._logout_and_redirect(
                request, reason="Tu sesión expiró por tiempo máximo de sesión.")
            return redirect("usuarios:login")

        # Aún válida → refrescar marca de actividad
        session["last_activity"] = now
        return self.get_response(request)

    def _logout_and_redirect(self, request, reason: str):
        # Limpiar marcas y cerrar sesión
        for k in ("last_activity", "login_time"):
            request.session.pop(k, None)
        try:
            messages.warning(request, reason)
        except Exception:
            pass
        logout(request)


log = logging.getLogger('pagos.monthly')


class LogRedirectsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        if resp.status_code in (301, 302, 303, 307, 308):
            log.warning('REDIRECT %s %s → %s', request.method,
                        request.path, resp.get('Location'))
        return resp


class ChileTimezoneMiddleware:
    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        timezone.activate(ZoneInfo("America/Santiago"))
        resp = self.get_response(request)
        timezone.deactivate()
        return resp





def _get_2fa_enforce_date():
    """
    Fecha en la que 2FA pasa a ser obligatorio.
    Debe venir de settings.TWO_FACTOR_ENFORCE_DATE (objeto date).
    """
    return getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None)


class Enforce2FAMiddleware(MiddlewareMixin):
    """
    Si la fecha de obligatoriedad ya pasó y el usuario staff
    NO tiene 2FA activado, lo fuerza a ir a la pantalla de setup 2FA.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)

        # No autenticado => no aplica
        if not user or not user.is_authenticated:
            return None

        # Solo personal administrativo/staff
        if not user.is_staff:
            return None

        enforce_date = _get_2fa_enforce_date()
        if not enforce_date:
            return None

        today = timezone.localdate()
        if today < enforce_date:
            # Aún estamos en período de gracia
            return None

        # Si ya tiene 2FA activado, nada que hacer
        if getattr(user, "two_factor_enabled", False):
            return None

        # Permitir algunas rutas para que pueda activar 2FA o salir
        try:
            match = resolve(request.path)
            full_name = (
                f"{match.namespace}:{match.url_name}"
                if match.namespace
                else match.url_name
            )
        except Resolver404:
            full_name = None

        allowed_names = {
            "usuarios:two_factor_setup",
            "usuarios:two_factor_verify",
            "usuarios:login_unificado",
            "usuarios:logout",  # si tienes esta vista
        }

        # Permitir también estáticos / media
        static_url = getattr(settings, "STATIC_URL", "/static/")
        media_url = getattr(settings, "MEDIA_URL", "/media/")

        if request.path.startswith(static_url) or request.path.startswith(media_url):
            return None

        if full_name in allowed_names:
            return None

        # Bloquea todo lo demás y redirige a setup 2FA
        messages.warning(
            request,
            "Debes configurar el segundo factor de autenticación para continuar usando la plataforma.",
        )
        return redirect("usuarios:two_factor_setup")
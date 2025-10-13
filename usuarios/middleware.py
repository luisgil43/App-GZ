# usuarios/middlewares.py
from zoneinfo import ZoneInfo
from django.utils import timezone
import logging
import time
from django.conf import settings
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth import logout


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

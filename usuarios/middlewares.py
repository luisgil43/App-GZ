# usuarios/middlewares.py
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.middleware.csrf import CsrfViewMiddleware


class SessionExpiryMiddleware:
    """Detecta sesión expirada o CSRF inválido y redirige al login con mensaje."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Si el CSRF falla y el usuario estaba logueado, asumimos sesión expirada
        if request.method == "POST":
            reason = getattr(request, 'csrf_processing_failed', False)
            if reason:
                messages.warning(
                    request, "Tu sesión ha expirado. Por favor vuelve a iniciar sesión.")
                try:
                    return redirect(reverse('login_unificado'))
                except:
                    return redirect(reverse('usuarios:login'))
        return None

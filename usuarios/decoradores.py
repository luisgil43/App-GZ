# usuarios/decorators.py
from functools import wraps
from django.http import JsonResponse
from django.shortcuts import redirect


def rol_requerido(*roles_esperados, url_redireccion='usuarios:no_autorizado'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            ok = False
            if user.is_authenticated:
                if user.is_superuser:
                    ok = True
                elif hasattr(user, 'roles') and user.roles.filter(nombre__in=roles_esperados).exists():
                    ok = True

            if ok:
                return view_func(request, *args, **kwargs)

            # ⬇⬇⬇ Diferencia clave: si es AJAX/JSON, NO redirijas: responde JSON 403
            wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" \
                or "application/json" in (request.headers.get("Accept") or "")
            if wants_json:
                return JsonResponse({"detail": "forbidden"}, status=403)

            # Flujo normal (no-AJAX): redirige como antes
            return redirect(url_redireccion)
        return _wrapped_view
    return decorator

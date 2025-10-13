# operaciones/utils/http.py
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from functools import wraps


def login_required_json(view):
    @wraps(view)
    def _wrap(request, *a, **kw):
        if not request.user.is_authenticated:
            # Para XHR, no redirigir: status 401 JSON
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": "auth_required"}, status=401)
            # Para no-XHR, comportamiento normal (redirige al login)
            return login_required(view)(request, *a, **kw)
        return view(request, *a, **kw)
    return _wrap

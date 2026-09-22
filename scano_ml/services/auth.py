import secrets

from django.conf import settings
from django.http import JsonResponse


def verify_collector_token(request):
    """
    Valida exclusivamente el Bearer token de Scano ML Collector.

    Retorna:
        None si la autenticación es válida.
        JsonResponse si debe rechazarse la petición.
    """

    if not getattr(settings, "SCANO_ML_ENABLED", False):
        return JsonResponse(
            {"detail": "Service unavailable"},
            status=503,
        )

    expected_token = getattr(
        settings,
        "SCANO_ML_COLLECTOR_TOKEN",
        "",
    ).strip()

    if not expected_token:
        return JsonResponse(
            {"detail": "Service unavailable"},
            status=503,
        )

    authorization = request.headers.get(
        "Authorization",
        "",
    )

    prefix = "Bearer "

    if not authorization.startswith(prefix):
        return JsonResponse(
            {"detail": "Unauthorized"},
            status=401,
        )

    supplied_token = authorization[len(prefix) :].strip()

    if not supplied_token:
        return JsonResponse(
            {"detail": "Unauthorized"},
            status=401,
        )

    if not secrets.compare_digest(
        supplied_token,
        expected_token,
    ):
        return JsonResponse(
            {"detail": "Unauthorized"},
            status=401,
        )

    return None

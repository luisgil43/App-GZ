import secrets

from django.conf import settings
from django.http import JsonResponse


def _verify_bearer_token(
    request,
    *,
    setting_name,
):
    """
    Valida un Bearer token contra un setting de Django.

    Retorna:
        None si la autenticación es válida.
        JsonResponse si debe rechazarse la petición.
    """

    if not getattr(
        settings,
        "SCANO_ML_ENABLED",
        False,
    ):
        return JsonResponse(
            {
                "detail": "Service unavailable",
            },
            status=503,
        )

    expected_token = getattr(
        settings,
        setting_name,
        "",
    ).strip()

    if not expected_token:
        return JsonResponse(
            {
                "detail": "Service unavailable",
            },
            status=503,
        )

    authorization = request.headers.get(
        "Authorization",
        "",
    )

    prefix = "Bearer "

    if not authorization.startswith(prefix):
        return JsonResponse(
            {
                "detail": "Unauthorized",
            },
            status=401,
        )

    supplied_token = authorization[len(prefix) :].strip()

    if not supplied_token:
        return JsonResponse(
            {
                "detail": "Unauthorized",
            },
            status=401,
        )

    if not secrets.compare_digest(
        supplied_token,
        expected_token,
    ):
        return JsonResponse(
            {
                "detail": "Unauthorized",
            },
            status=401,
        )

    return None


def verify_collector_token(request):
    """
    Autenticación exclusiva del Scano ML Collector.
    """

    return _verify_bearer_token(
        request,
        setting_name=("SCANO_ML_COLLECTOR_TOKEN"),
    )


def verify_worker_token(request):
    """
    Autenticación exclusiva del worker de entrenamiento.
    """

    return _verify_bearer_token(
        request,
        setting_name=("SCANO_ML_WORKER_TOKEN"),
    )

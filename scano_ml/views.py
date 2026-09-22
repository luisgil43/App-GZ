import json
from datetime import datetime, timezone
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .services.auth import verify_collector_token
from .services.storage import (ScanoStorageConfigurationError,
                               ScanoStorageError, generate_presigned_put_url,
                               head_object)

ALLOWED_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

PRESIGN_EXPIRES_IN = 900


def _generate_sample_id():
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")

    return f"scano_" f"{timestamp}_" f"{uuid4().hex[:8]}"


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None


@require_GET
def health(request):
    return JsonResponse(
        {
            "status": "ok",
            "service": "scano-ml-collector",
            "version": "2.0.0-gz",
            "bucket": getattr(
                settings,
                "SCANO_ML_WASABI_BUCKET",
                None,
            ),
            "region": getattr(
                settings,
                "SCANO_ML_WASABI_REGION",
                None,
            ),
        }
    )


@csrf_exempt
@require_POST
def presign_training_sample(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    payload = _parse_json_body(request)

    if not isinstance(payload, dict):
        return JsonResponse(
            {"detail": "Invalid JSON body"},
            status=400,
        )

    extension = str(payload.get("extension", "jpg")).strip().lower()

    if extension not in ALLOWED_EXTENSIONS:
        return JsonResponse(
            {"detail": "Unsupported image extension"},
            status=400,
        )

    # Conservamos la normalización histórica jpeg -> jpg.
    if extension == "jpeg":
        extension = "jpg"

    expected_content_type = ALLOWED_EXTENSIONS[extension]

    content_type = (
        str(
            payload.get(
                "content_type",
                expected_content_type,
            )
        )
        .strip()
        .lower()
    )

    if content_type != expected_content_type:
        return JsonResponse(
            {
                "detail": (
                    "content_type does not match " "the requested image extension"
                )
            },
            status=400,
        )

    sample_id = _generate_sample_id()

    image_key = f"raw/{sample_id}.{extension}"
    annotation_key = f"annotations/{sample_id}.json"

    try:
        image_upload_url = generate_presigned_put_url(
            key=image_key,
            content_type=content_type,
            expires_in=PRESIGN_EXPIRES_IN,
        )

        annotation_upload_url = generate_presigned_put_url(
            key=annotation_key,
            content_type="application/json",
            expires_in=PRESIGN_EXPIRES_IN,
        )
    except ScanoStorageConfigurationError:
        return JsonResponse(
            {"detail": "Storage unavailable"},
            status=503,
        )
    except ScanoStorageError:
        return JsonResponse(
            {"detail": "Storage unavailable"},
            status=502,
        )

    return JsonResponse(
        {
            "sample_id": sample_id,
            "image_key": image_key,
            "annotation_key": annotation_key,
            "image_upload_url": image_upload_url,
            "annotation_upload_url": annotation_upload_url,
            "expires_in": PRESIGN_EXPIRES_IN,
        }
    )


@csrf_exempt
@require_POST
def complete_training_sample(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    payload = _parse_json_body(request)

    if not isinstance(payload, dict):
        return JsonResponse(
            {"detail": "Invalid JSON body"},
            status=400,
        )

    sample_id = str(payload.get("sample_id", "")).strip()

    image_key = str(payload.get("image_key", "")).strip()

    annotation_key = str(payload.get("annotation_key", "")).strip()

    if not sample_id:
        return JsonResponse(
            {"detail": "sample_id is required"},
            status=400,
        )

    if not image_key:
        return JsonResponse(
            {"detail": "image_key is required"},
            status=400,
        )

    if not annotation_key:
        return JsonResponse(
            {"detail": "annotation_key is required"},
            status=400,
        )

    expected_image_prefix = f"raw/{sample_id}."
    expected_annotation_key = f"annotations/{sample_id}.json"

    if not image_key.startswith(expected_image_prefix):
        return JsonResponse(
            {"detail": ("image_key does not match sample_id")},
            status=400,
        )

    image_extension = image_key.rsplit(
        ".",
        1,
    )[-1].lower()

    if image_extension not in {
        "jpg",
        "jpeg",
        "png",
        "webp",
    }:
        return JsonResponse(
            {"detail": ("image_key has an unsupported extension")},
            status=400,
        )

    if annotation_key != expected_annotation_key:
        return JsonResponse(
            {"detail": ("annotation_key does not match sample_id")},
            status=400,
        )

    try:
        image_info = head_object(key=image_key)

        annotation_info = head_object(key=annotation_key)
    except ScanoStorageConfigurationError:
        return JsonResponse(
            {"detail": "Storage unavailable"},
            status=503,
        )
    except ScanoStorageError:
        return JsonResponse(
            {"detail": "Storage unavailable"},
            status=502,
        )

    if image_info is None:
        return JsonResponse(
            {"detail": ("Image object does not exist yet")},
            status=409,
        )

    if annotation_info is None:
        return JsonResponse(
            {"detail": ("Annotation object does not exist yet")},
            status=409,
        )

    if image_info["size"] <= 0:
        return JsonResponse(
            {"detail": ("Image object exists but is empty")},
            status=409,
        )

    if annotation_info["size"] <= 0:
        return JsonResponse(
            {"detail": ("Annotation object exists but is empty")},
            status=409,
        )

    return JsonResponse(
        {
            "status": "complete",
            "sample_id": sample_id,
            "image": image_info,
            "annotation": annotation_info,
        }
    )

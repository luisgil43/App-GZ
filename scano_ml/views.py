import json
from datetime import datetime, timezone
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone as django_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import TrainingJob
from .services.auth import verify_collector_token
from .services.storage import (ScanoStorageConfigurationError,
                               ScanoStorageError, generate_presigned_put_url,
                               head_object)
from .services.training_status import scan_cloud_samples

ALLOWED_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

PRESIGN_EXPIRES_IN = 900


# ============================================================
# HELPERS GENERALES
# ============================================================


def _generate_sample_id():
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")

    return f"scano_{timestamp}_{uuid4().hex[:8]}"


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None


# ============================================================
# HEALTH
# ============================================================


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


# ============================================================
# PRESIGN DE MUESTRA
# ============================================================


@csrf_exempt
@require_POST
def presign_training_sample(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    payload = _parse_json_body(request)

    if not isinstance(payload, dict):
        return JsonResponse(
            {
                "detail": "Invalid JSON body",
            },
            status=400,
        )

    extension = (
        str(
            payload.get(
                "extension",
                "jpg",
            )
        )
        .strip()
        .lower()
    )

    if extension not in ALLOWED_EXTENSIONS:
        return JsonResponse(
            {
                "detail": "Unsupported image extension",
            },
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
            {
                "detail": "Storage unavailable",
            },
            status=503,
        )

    except ScanoStorageError:
        return JsonResponse(
            {
                "detail": "Storage unavailable",
            },
            status=502,
        )

    return JsonResponse(
        {
            "sample_id": sample_id,
            "image_key": image_key,
            "annotation_key": annotation_key,
            "image_upload_url": image_upload_url,
            "annotation_upload_url": (annotation_upload_url),
            "expires_in": PRESIGN_EXPIRES_IN,
        }
    )


# ============================================================
# COMPLETE DE MUESTRA
# ============================================================


@csrf_exempt
@require_POST
def complete_training_sample(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    payload = _parse_json_body(request)

    if not isinstance(payload, dict):
        return JsonResponse(
            {
                "detail": "Invalid JSON body",
            },
            status=400,
        )

    sample_id = str(
        payload.get(
            "sample_id",
            "",
        )
    ).strip()

    image_key = str(
        payload.get(
            "image_key",
            "",
        )
    ).strip()

    annotation_key = str(
        payload.get(
            "annotation_key",
            "",
        )
    ).strip()

    if not sample_id:
        return JsonResponse(
            {
                "detail": "sample_id is required",
            },
            status=400,
        )

    if not image_key:
        return JsonResponse(
            {
                "detail": "image_key is required",
            },
            status=400,
        )

    if not annotation_key:
        return JsonResponse(
            {
                "detail": ("annotation_key is required"),
            },
            status=400,
        )

    expected_image_prefix = f"raw/{sample_id}."

    expected_annotation_key = f"annotations/{sample_id}.json"

    if not image_key.startswith(expected_image_prefix):
        return JsonResponse(
            {"detail": ("image_key does not match " "sample_id")},
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
            {"detail": ("image_key has an unsupported " "extension")},
            status=400,
        )

    if annotation_key != expected_annotation_key:
        return JsonResponse(
            {"detail": ("annotation_key does not match " "sample_id")},
            status=400,
        )

    try:
        image_info = head_object(key=image_key)

        annotation_info = head_object(key=annotation_key)

    except ScanoStorageConfigurationError:
        return JsonResponse(
            {
                "detail": "Storage unavailable",
            },
            status=503,
        )

    except ScanoStorageError:
        return JsonResponse(
            {
                "detail": "Storage unavailable",
            },
            status=502,
        )

    if image_info is None:
        return JsonResponse(
            {"detail": ("Image object does not exist yet")},
            status=409,
        )

    if annotation_info is None:
        return JsonResponse(
            {"detail": ("Annotation object does not " "exist yet")},
            status=409,
        )

    if image_info["size"] <= 0:
        return JsonResponse(
            {"detail": ("Image object exists but is empty")},
            status=409,
        )

    if annotation_info["size"] <= 0:
        return JsonResponse(
            {"detail": ("Annotation object exists but " "is empty")},
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


# ============================================================
# TRAINING HELPERS
# ============================================================


def _latest_completed_training():
    return (
        TrainingJob.objects.filter(
            status=TrainingJob.Status.COMPLETED,
        )
        .order_by(
            "-finished_at",
            "-requested_at",
            "-pk",
        )
        .first()
    )


def _active_training():
    active_statuses = [
        TrainingJob.Status.WAITING_FOR_WORKER,
        TrainingJob.Status.PREPARING_DATASET,
        TrainingJob.Status.TRAINING,
    ]

    return (
        TrainingJob.objects.filter(
            status__in=active_statuses,
        )
        .order_by(
            "-requested_at",
            "-pk",
        )
        .first()
    )


def _empty_dataset_snapshot():
    return {
        "available": False,
        "name": None,
        "total": 0,
        "train": 0,
        "val": 0,
        "test": 0,
    }


def _empty_model_snapshot():
    return {
        "available": False,
        "name": None,
        "status": "not_started",
        "status_label": "Sin entrenamiento",
        "dataset_name": None,
        "current_epoch": 0,
        "total_epochs": None,
        "best_epoch": None,
        "best_val_loss": None,
        "best_val_error_pixels": None,
        "test_error_pixels": None,
        "test_error_normalized": None,
    }


def _build_training_status():
    cloud = scan_cloud_samples()

    completed = _latest_completed_training()
    active = _active_training()

    if completed is not None:
        dataset = dict(completed.dataset_snapshot or {})

        model = dict(completed.model_snapshot or {})

        collection_snapshot = completed.collection_snapshot or {}

        trained_total = int(
            dataset.get(
                "total",
                collection_snapshot.get(
                    "trained_total",
                    0,
                ),
            )
            or 0
        )

    else:
        dataset = _empty_dataset_snapshot()
        model = _empty_model_snapshot()
        trained_total = 0

    unique_total = int(
        cloud.get(
            "unique_total",
            0,
        )
        or 0
    )

    pending_training = max(
        unique_total - trained_total,
        0,
    )

    if active is not None:
        pipeline = {
            "status": active.status,
            "status_label": (active.get_status_display()),
            "stage": active.pipeline_stage,
            "message": active.pipeline_message,
            "job_id": active.pk,
            "requested_at": (active.requested_at.isoformat()),
            "started_at": (
                active.started_at.isoformat() if active.started_at else None
            ),
            "updated_at": (active.updated_at.isoformat()),
        }

        if active.dataset_snapshot:
            dataset = dict(active.dataset_snapshot)

        if active.model_snapshot:
            model = dict(active.model_snapshot)

        model["current_epoch"] = int(active.current_epoch or 0)

        model["total_epochs"] = (
            int(active.total_epochs) if active.total_epochs is not None else None
        )

        if active.status == TrainingJob.Status.TRAINING:
            model["status"] = "training"

    elif completed is not None:
        pipeline = {
            "status": "completed",
            "status_label": "Completado",
            "stage": (completed.pipeline_stage or "completed"),
            "message": (completed.pipeline_message),
            "job_id": completed.pk,
            "requested_at": (completed.requested_at.isoformat()),
            "started_at": (
                completed.started_at.isoformat() if completed.started_at else None
            ),
            "finished_at": (
                completed.finished_at.isoformat() if completed.finished_at else None
            ),
            "updated_at": (completed.updated_at.isoformat()),
        }

        model.setdefault(
            "current_epoch",
            int(completed.current_epoch or 0),
        )

        model.setdefault(
            "total_epochs",
            (
                int(completed.total_epochs)
                if completed.total_epochs is not None
                else None
            ),
        )

    else:
        pipeline = {
            "status": "idle",
            "status_label": "Listo",
            "stage": "",
            "message": "",
            "job_id": None,
        }

    return {
        "status": "ok",
        "collection": {
            "raw_annotation_total": int(
                cloud.get(
                    "annotation_total",
                    0,
                )
                or 0
            ),
            "valid_pair_total": int(
                cloud.get(
                    "valid_pair_total",
                    0,
                )
                or 0
            ),
            "unique_total": unique_total,
            "uploaded_total": unique_total,
            "trained_total": trained_total,
            "pending_training": (pending_training),
            "duplicate_total": int(
                cloud.get(
                    "duplicate_total",
                    0,
                )
                or 0
            ),
            "invalid_total": int(
                cloud.get(
                    "invalid_total",
                    0,
                )
                or 0
            ),
        },
        "dataset": dataset,
        "model": model,
        "pipeline": pipeline,
        "updated_at": (django_timezone.now().isoformat()),
    }


# ============================================================
# TRAINING STATUS
# ============================================================


@require_GET
def training_status(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    try:
        return JsonResponse(_build_training_status())

    except ScanoStorageConfigurationError:
        return JsonResponse(
            {"detail": ("No se pudo obtener el estado " "del almacenamiento.")},
            status=503,
        )

    except ScanoStorageError:
        return JsonResponse(
            {"detail": ("No se pudo obtener el estado " "del almacenamiento.")},
            status=502,
        )

    except Exception as exc:
        return JsonResponse(
            {"detail": ("No se pudo obtener el estado " f"del entrenamiento: {exc}")},
            status=500,
        )


# ============================================================
# TRAINING START
# ============================================================


@csrf_exempt
@require_POST
def start_training(request):
    auth_error = verify_collector_token(request)

    if auth_error is not None:
        return auth_error

    try:
        with transaction.atomic():
            active_statuses = [
                TrainingJob.Status.WAITING_FOR_WORKER,
                TrainingJob.Status.PREPARING_DATASET,
                TrainingJob.Status.TRAINING,
            ]

            active = (
                TrainingJob.objects.select_for_update()
                .filter(
                    status__in=active_statuses,
                )
                .order_by(
                    "-requested_at",
                    "-pk",
                )
                .first()
            )

            if active is not None:
                return JsonResponse(
                    {
                        "detail": (
                            "Ya existe un entrenamiento " "pendiente o en curso."
                        ),
                        "job_id": active.pk,
                        "status": active.status,
                    },
                    status=409,
                )

            current = _build_training_status()

            collection = current["collection"]

            invalid_total = int(
                collection.get(
                    "invalid_total",
                    0,
                )
                or 0
            )

            if invalid_total > 0:
                return JsonResponse(
                    {
                        "detail": (
                            "Existen muestras inválidas. "
                            "Revísalas antes de entrenar."
                        ),
                        "invalid_total": (invalid_total),
                    },
                    status=409,
                )

            pending_training = int(
                collection.get(
                    "pending_training",
                    0,
                )
                or 0
            )

            if pending_training <= 0:
                return JsonResponse(
                    {
                        "detail": (
                            "No existen muestras nuevas " "pendientes por entrenar."
                        )
                    },
                    status=409,
                )

            job = TrainingJob.objects.create(
                status=(TrainingJob.Status.WAITING_FOR_WORKER),
                pending_training_at_request=(pending_training),
                pipeline_stage="queued",
                pipeline_message=("Entrenamiento aceptado. " "Esperando al worker."),
                collection_snapshot={
                    "unique_total": (collection["unique_total"]),
                    "trained_total": (collection["trained_total"]),
                    "pending_training": (pending_training),
                    "duplicate_total": (collection["duplicate_total"]),
                    "invalid_total": (collection["invalid_total"]),
                },
            )

        return JsonResponse(
            {
                "status": "accepted",
                "job_id": job.pk,
                "pipeline": {
                    "status": job.status,
                    "status_label": (job.get_status_display()),
                    "stage": (job.pipeline_stage),
                    "message": (job.pipeline_message),
                },
                "collection": (job.collection_snapshot),
            },
            status=202,
        )

    except ScanoStorageConfigurationError:
        return JsonResponse(
            {
                "detail": (
                    "No se pudo revisar el "
                    "almacenamiento antes de iniciar "
                    "el entrenamiento."
                )
            },
            status=503,
        )

    except ScanoStorageError:
        return JsonResponse(
            {
                "detail": (
                    "No se pudo revisar el "
                    "almacenamiento antes de iniciar "
                    "el entrenamiento."
                )
            },
            status=502,
        )

    except Exception as exc:
        return JsonResponse(
            {"detail": ("No se pudo iniciar el " f"entrenamiento: {exc}")},
            status=500,
        )

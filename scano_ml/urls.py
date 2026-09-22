from django.urls import path

from . import views

app_name = "scano_ml"


urlpatterns = [
    # Sin slash final: mantiene exactamente el contrato
    # utilizado por Scano ML Collector.
    path(
        "health",
        views.health,
        name="health",
    ),
    path(
        "v1/training-status",
        views.training_status,
        name="training_status",
    ),
    path(
        "v1/training/start",
        views.start_training,
        name="training_start",
    ),
    path(
        "v1/training-samples/presign",
        views.presign_training_sample,
        name="training_sample_presign",
    ),
    path(
        "v1/training-samples/complete",
        views.complete_training_sample,
        name="training_sample_complete",
    ),
    # API exclusiva del worker de entrenamiento.
    path(
        "v1/worker/training/claim",
        views.worker_claim_training,
        name="worker_training_claim",
    ),
    path(
        "v1/worker/training/<int:job_id>/update",
        views.worker_update_training,
        name="worker_training_update",
    ),
    path(
        "v1/worker/training/<int:job_id>/complete",
        views.worker_complete_training,
        name="worker_training_complete",
    ),
    path(
        "v1/worker/training/<int:job_id>/fail",
        views.worker_fail_training,
        name="worker_training_fail",
    ),
]

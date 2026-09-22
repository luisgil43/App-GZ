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
        "v1/training-samples/presign",
        views.presign_training_sample,
        name="training_sample_presign",
    ),
    path(
        "v1/training-samples/complete",
        views.complete_training_sample,
        name="training_sample_complete",
    ),
]

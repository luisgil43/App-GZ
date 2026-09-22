from django.db import models


class TrainingJob(models.Model):
    class Status(models.TextChoices):
        WAITING_FOR_WORKER = (
            "waiting_for_worker",
            "Waiting for worker",
        )
        PREPARING_DATASET = (
            "preparing_dataset",
            "Preparing dataset",
        )
        TRAINING = (
            "training",
            "Training",
        )
        COMPLETED = (
            "completed",
            "Completed",
        )
        FAILED = (
            "failed",
            "Failed",
        )
        CANCELED = (
            "canceled",
            "Canceled",
        )

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.WAITING_FOR_WORKER,
        db_index=True,
    )

    pending_training_at_request = models.PositiveIntegerField(
        default=0,
    )

    requested_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    worker_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
    )

    pipeline_stage = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )

    pipeline_message = models.TextField(
        blank=True,
        default="",
    )

    quarantined_duplicates = models.PositiveIntegerField(
        default=0,
    )

    dataset_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    model_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    collection_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    current_epoch = models.PositiveIntegerField(
        default=0,
    )

    total_epochs = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    error_message = models.TextField(
        blank=True,
        default="",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-requested_at",
        ]
        indexes = [
            models.Index(
                fields=[
                    "status",
                    "requested_at",
                ],
                name="scano_train_status_req_idx",
            ),
        ]

    def __str__(self):
        return f"Scano ML TrainingJob #{self.pk} " f"({self.status})"

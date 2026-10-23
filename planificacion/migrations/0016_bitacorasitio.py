# Generated for GZ Services — Bitácora por sitio

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "planificacion",
            "0015_alter_disponibilidadcuadrillasemana_modalidad",
        ),
        migrations.swappable_dependency(
            settings.AUTH_USER_MODEL,
        ),
        (
            "operaciones",
            "__first__",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="BitacoraSitio",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "contenido",
                    models.TextField(
                        help_text=(
                            "Anotación operacional relacionada " "con el sitio."
                        ),
                    ),
                ),
                (
                    "creado_en",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "creado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="anotaciones_bitacora_sitios",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "sitio",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bitacora_planificacion",
                        to="operaciones.sitiomovil",
                    ),
                ),
            ],
            options={
                "verbose_name": "Anotación de bitácora",
                "verbose_name_plural": "Anotaciones de bitácora",
                "ordering": [
                    "-creado_en",
                    "-id",
                ],
            },
        ),
        migrations.AddIndex(
            model_name="bitacorasitio",
            index=models.Index(
                fields=[
                    "sitio",
                    "-creado_en",
                ],
                name="idx_bitacora_sitio_fecha",
            ),
        ),
    ]

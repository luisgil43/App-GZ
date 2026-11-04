from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operaciones", "0023_requisitofoto_etapa"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitiomovil",
            name="bitacora_alarma_activa",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="sitiomovil",
            name="bitacora_alarma_activada_en",
            field=models.DateTimeField(
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="sitiomovil",
            name="bitacora_alarma_fecha_salida",
            field=models.DateField(
                null=True,
                blank=True,
            ),
        ),
    ]

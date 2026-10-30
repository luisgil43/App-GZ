from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "operaciones",
            "0022_tarifaactividadtecnico_activa_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="requisitofoto",
            name="etapa",
            field=models.CharField(
                max_length=10,
                choices=[
                    ("", "Sin etapa"),
                    ("antes", "Antes"),
                    ("durante", "Durante"),
                    ("despues", "Después"),
                ],
                default="",
                blank=True,
            ),
        ),
    ]

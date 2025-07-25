# Generated by Django 5.2.1 on 2025-07-10 21:43

import cloudinary_storage.storage
import logistica.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0009_salidamaterial_estado_envio_sii_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='salidamaterial',
            name='archivo_xml',
            field=models.FileField(blank=True, null=True, storage=cloudinary_storage.storage.RawMediaCloudinaryStorage(), upload_to=logistica.models.ruta_xml_firmado, verbose_name='XML firmado'),
        ),
    ]

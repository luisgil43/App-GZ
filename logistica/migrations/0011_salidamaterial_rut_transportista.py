# Generated by Django 5.2.1 on 2025-07-11 20:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0010_salidamaterial_archivo_xml'),
    ]

    operations = [
        migrations.AddField(
            model_name='salidamaterial',
            name='rut_transportista',
            field=models.CharField(default='267246793', max_length=20),
            preserve_default=False,
        ),
    ]

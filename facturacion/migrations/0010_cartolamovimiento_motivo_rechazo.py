# Generated by Django 5.2.1 on 2025-07-28 23:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0009_cartolamovimiento_numero_doc_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cartolamovimiento',
            name='motivo_rechazo',
            field=models.TextField(blank=True, null=True),
        ),
    ]

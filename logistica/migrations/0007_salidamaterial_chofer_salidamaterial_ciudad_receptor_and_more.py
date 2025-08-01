# Generated by Django 5.2.1 on 2025-07-10 02:49

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0006_certificadodigital'),
    ]

    operations = [
        migrations.AddField(
            model_name='salidamaterial',
            name='chofer',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='ciudad_receptor',
            field=models.CharField(default='', max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='comuna_receptor',
            field=models.CharField(default='', max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='destino',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='direccion_receptor',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='giro_receptor',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='nombre_receptor',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='obra',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='observaciones',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='origen',
            field=models.CharField(default='', max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='patente',
            field=models.CharField(default='', max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salidamaterial',
            name='rut_receptor',
            field=models.CharField(default='', max_length=15),
            preserve_default=False,
        ),
    ]

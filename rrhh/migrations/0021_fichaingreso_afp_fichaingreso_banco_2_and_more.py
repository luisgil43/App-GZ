# Generated by Django 5.2.1 on 2025-06-20 19:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rrhh', '0020_fichaingreso_firma_pm_fichaingreso_firma_rrhh'),
    ]

    operations = [
        migrations.AddField(
            model_name='fichaingreso',
            name='afp',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='banco_2',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='bono',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='colacion',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='departamento',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='direccion_emergencia',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='hijos',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='horario_trabajo',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='movilizacion',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='nacionalidad',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='nivel_estudios',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='numero_cuenta_2',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='observaciones',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='profesion_u_oficio',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='region',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='salud',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='sexo',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='sueldo_liquido',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='talla_pantalon',
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='talla_polera',
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='talla_zapato',
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='tipo_contrato',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='fichaingreso',
            name='tipo_cuenta_2',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]

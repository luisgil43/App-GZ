# Generated by Django 5.2.1 on 2025-07-12 02:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logistica', '0013_material_valor_unitario'),
    ]

    operations = [
        migrations.AlterField(
            model_name='material',
            name='codigo_interno',
            field=models.CharField(max_length=50),
        ),
        migrations.AddConstraint(
            model_name='material',
            constraint=models.UniqueConstraint(fields=('codigo_interno', 'bodega'), name='unique_codigo_interno_por_bodega'),
        ),
        migrations.AddConstraint(
            model_name='material',
            constraint=models.UniqueConstraint(fields=('codigo_externo', 'bodega'), name='unique_codigo_externo_por_bodega'),
        ),
    ]

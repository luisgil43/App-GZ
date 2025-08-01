# Generated by Django 5.2.1 on 2025-07-06 16:53

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0008_rename_leida_notificacion_leido'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='encargado_facturacion',
            field=models.ForeignKey(blank=True, help_text='Encargado de Facturación', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_facturacion', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='encargado_flota',
            field=models.ForeignKey(blank=True, help_text='Encargado de Flota', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_flota', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='encargado_subcontrato',
            field=models.ForeignKey(blank=True, help_text='Encargado de Subcontrato', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_subcontrato', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='logistica_encargado',
            field=models.ForeignKey(blank=True, help_text='Encargado de Logística', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_logistica', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='pm',
            field=models.ForeignKey(blank=True, help_text='Project Manager responsable', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_pm', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='prevencionista',
            field=models.ForeignKey(blank=True, help_text='Encargado de Prevención de Riesgo', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_prevencionista', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='rrhh_encargado',
            field=models.ForeignKey(blank=True, help_text='Encargado de Recursos Humanos', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_rrhh', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='customuser',
            name='supervisor',
            field=models.ForeignKey(blank=True, help_text='Supervisor directo del usuario', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='aprueba_como_supervisor', to=settings.AUTH_USER_MODEL),
        ),
    ]

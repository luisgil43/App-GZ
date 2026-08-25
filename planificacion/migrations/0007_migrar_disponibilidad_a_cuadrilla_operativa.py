from django.db import migrations, models


def vincular_cuadrillas_historicas(
    apps,
    schema_editor,
):
    CuadrillaOperativa = apps.get_model(
        "planificacion",
        "CuadrillaOperativa",
    )

    DisponibilidadCuadrillaSemana = apps.get_model(
        "planificacion",
        "DisponibilidadCuadrillaSemana",
    )

    # ========================================================
    # MAPEO LEGACY -> CATÁLOGO MAESTRO
    # ========================================================

    mapeo = {
        "cuadrilla_1": "cuadrilla_c1",
        "cuadrilla_2": "cuadrilla_c2",
        "cuadrilla_3": "cuadrilla_c3",
    }

    for codigo_legacy, codigo_maestro in mapeo.items():

        cuadrilla_operativa = CuadrillaOperativa.objects.filter(
            codigo=codigo_maestro,
        ).first()

        if cuadrilla_operativa is None:
            continue

        DisponibilidadCuadrillaSemana.objects.filter(
            cuadrilla=codigo_legacy,
            cuadrilla_operativa__isnull=True,
        ).update(
            cuadrilla_operativa=cuadrilla_operativa,
        )


def desvincular_cuadrillas_historicas(
    apps,
    schema_editor,
):
    """
    Reverse conservador.

    No eliminamos las cuadrillas maestras.
    Solamente retiramos la FK de los registros históricos
    que correspondan a los códigos conocidos.
    """

    CuadrillaOperativa = apps.get_model(
        "planificacion",
        "CuadrillaOperativa",
    )

    DisponibilidadCuadrillaSemana = apps.get_model(
        "planificacion",
        "DisponibilidadCuadrillaSemana",
    )

    codigos = [
        "cuadrilla_c1",
        "cuadrilla_c2",
        "cuadrilla_c3",
    ]

    cuadrillas = CuadrillaOperativa.objects.filter(
        codigo__in=codigos,
    )

    DisponibilidadCuadrillaSemana.objects.filter(
        cuadrilla_operativa__in=cuadrillas,
    ).update(
        cuadrilla_operativa=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        (
            "planificacion",
            "0006_alter_disponibilidadcuadrillasemana_base_latitud_and_more",
        ),
    ]

    operations = [
        # ====================================================
        # 1. EL CAMPO LEGACY PUEDE QUEDAR VACÍO
        # ====================================================
        migrations.AlterField(
            model_name="disponibilidadcuadrillasemana",
            name="cuadrilla",
            field=models.CharField(
                blank=True,
                choices=[
                    (
                        "cuadrilla_1",
                        "Cuadrilla 1",
                    ),
                    (
                        "cuadrilla_2",
                        "Cuadrilla 2",
                    ),
                    (
                        "cuadrilla_3",
                        "Cuadrilla 3",
                    ),
                ],
                db_index=True,
                default="",
                help_text=(
                    "Campo histórico utilizado antes de "
                    "CuadrillaOperativa. Se conserva "
                    "temporalmente para compatibilidad."
                ),
                max_length=30,
            ),
        ),
        # ====================================================
        # 2. VINCULAR LOS REGISTROS HISTÓRICOS
        # ====================================================
        migrations.RunPython(
            vincular_cuadrillas_historicas,
            desvincular_cuadrillas_historicas,
        ),
        # ====================================================
        # 3. RETIRAR CONSTRAINT LEGACY ANTERIOR
        # ====================================================
        migrations.RemoveConstraint(
            model_name="disponibilidadcuadrillasemana",
            name="uq_disponibilidad_cuadrilla_semana",
        ),
        # ====================================================
        # 4. RETIRAR CONSTRAINT FK ANTERIOR
        # ====================================================
        migrations.RemoveConstraint(
            model_name="disponibilidadcuadrillasemana",
            name="uq_disponibilidad_cuadrilla_operativa_semana",
        ),
        # ====================================================
        # 5. CONSTRAINT LEGACY CONDICIONADA
        # ====================================================
        migrations.AddConstraint(
            model_name="disponibilidadcuadrillasemana",
            constraint=models.UniqueConstraint(
                fields=(
                    "configuracion_semana",
                    "cuadrilla",
                ),
                condition=~models.Q(
                    cuadrilla="",
                ),
                name="uq_disponibilidad_cuadrilla_semana",
            ),
        ),
        # ====================================================
        # 6. CONSTRAINT NUEVA CONDICIONADA
        # ====================================================
        migrations.AddConstraint(
            model_name="disponibilidadcuadrillasemana",
            constraint=models.UniqueConstraint(
                fields=(
                    "configuracion_semana",
                    "cuadrilla_operativa",
                ),
                condition=models.Q(
                    cuadrilla_operativa__isnull=False,
                ),
                name=("uq_disponibilidad_" "cuadrilla_operativa_semana"),
            ),
        ),
    ]

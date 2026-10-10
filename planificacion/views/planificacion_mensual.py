from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.forms.planificacion_mensual import PlanificacionMensualForm
from planificacion.models import (BatchPlanificacionSemanal,
                                  PlanificacionMensual)
from usuarios.decoradores import rol_requerido

# ============================================================
# LISTADO DE PLANIFICACIONES MENSUALES
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def planificaciones_mensuales(request):
    """
    Pantalla principal del módulo de planificación.

    Permite:

    - revisar planificaciones existentes;
    - crear un nuevo mes;
    - abrir una planificación mensual;
    - editar una planificación mensual;
    - eliminar una planificación mensual vacía.

    No ejecuta lógica de rutas.
    """

    planificaciones = (
        PlanificacionMensual.objects.select_related(
            "creado_por",
            "actualizado_por",
        )
        .annotate(
            total_sitios=Count(
                "sitios",
                filter=Q(
                    sitios__activo_en_mes=True,
                ),
                distinct=True,
            ),
            sitios_planificados=Count(
                "sitios",
                filter=Q(
                    sitios__activo_en_mes=True,
                    sitios__fecha_planificada__isnull=False,
                ),
                distinct=True,
            ),
            permisos_aprobados=Count(
                "sitios",
                filter=Q(
                    sitios__activo_en_mes=True,
                    sitios__estado_permiso="aprobado",
                ),
                distinct=True,
            ),
            permisos_pendientes=Count(
                "sitios",
                filter=Q(
                    sitios__activo_en_mes=True,
                    sitios__estado_permiso__in=[
                        "por_solicitar",
                        "solicitado",
                        "en_espera",
                    ],
                ),
                distinct=True,
            ),
        )
        .order_by(
            "-anio",
            "-mes",
        )
    )

    return render(
        request,
        "planificacion/mensual/lista_meses.html",
        {
            "planificaciones": planificaciones,
        },
    )


# ============================================================
# CREAR PLANIFICACIÓN MENSUAL
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def crear_planificacion_mensual(request):
    """
    Crea una nueva planificación mensual.

    La combinación año/mes es única.
    """

    if request.method == "POST":

        form = PlanificacionMensualForm(
            request.POST,
        )

        if form.is_valid():

            planificacion = form.save(
                commit=False,
            )

            planificacion.creado_por = request.user

            planificacion.actualizado_por = request.user

            planificacion.save()

            messages.success(
                request,
                "Planificación mensual creada correctamente.",
            )

            return redirect(
                "planificacion:lista_asignacion_mensual",
                pk=planificacion.pk,
            )

    else:

        form = PlanificacionMensualForm()

    return render(
        request,
        "planificacion/mensual/crear.html",
        {
            "form": form,
            "modo": "crear",
            "planificacion": None,
        },
    )


# ============================================================
# EDITAR PLANIFICACIÓN MENSUAL
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
@transaction.atomic
def editar_planificacion_mensual(
    request,
    pk,
):
    """
    Permite modificar los datos generales de una
    planificación mensual.

    Actualmente el formulario administra:

    - año;
    - mes;
    - observaciones.

    IMPORTANTE
    ==========================================================

    Editar la planificación mensual NO:

    - recrea sitios;
    - elimina sitios;
    - recrea batches;
    - modifica planificación diaria;
    - modifica permisos;
    - modifica Operaciones.

    La misma instancia es actualizada.
    """

    planificacion = get_object_or_404(
        PlanificacionMensual.objects.select_for_update(),
        pk=pk,
    )

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        form = PlanificacionMensualForm(
            request.POST,
            instance=planificacion,
        )

        if form.is_valid():

            planificacion = form.save(
                commit=False,
            )

            planificacion.actualizado_por = request.user

            planificacion.save()

            messages.success(
                request,
                "Planificación mensual actualizada correctamente.",
            )

            return redirect(
                "planificacion:planificaciones_mensuales",
            )

    # ========================================================
    # GET
    # ========================================================

    else:

        form = PlanificacionMensualForm(
            instance=planificacion,
        )

    # ========================================================
    # TEMPLATE
    # ========================================================

    return render(
        request,
        "planificacion/mensual/crear.html",
        {
            "form": form,
            "modo": "editar",
            "planificacion": planificacion,
        },
    )


# ============================================================
# ELIMINAR PLANIFICACIÓN MENSUAL
# ============================================================


# ============================================================
# ELIMINAR PLANIFICACIÓN MENSUAL
# ============================================================


@require_POST
@login_required
@rol_requerido(
    "admin",
)
@transaction.atomic
def eliminar_planificacion_mensual(
    request,
    pk,
):
    """
    Elimina de forma administrativa y forzada una
    planificación mensual y toda la información de
    planificación que dependa de ella.

    IMPORTANTE
    ==========================================================

    Un sitio perteneciente al mes que estamos eliminando puede
    haber sido utilizado para completar una semana operacional
    perteneciente al mes anterior.

    Ejemplo:

        SitioPlanificado -> septiembre
        SitioBatchSemanal -> W35 de agosto
        Salida diaria     -> 28 de agosto

    En ese caso:

        - eliminamos la participación diaria del sitio;
        - eliminamos su SitioBatchSemanal;
        - mantenemos intacto el batch de agosto;
        - mantenemos los otros sitios de esa salida;
        - si la salida queda completamente vacía,
          eliminamos solamente esa salida.

    SE ELIMINA
    ==========================================================

    - participaciones diarias de sitios del mes;
    - salidas diarias propias de batches del mes;
    - salidas externas que queden vacías;
    - SitioBatchSemanal que referencie sitios del mes,
      aunque pertenezca a otro batch;
    - batches semanales propios del mes;
    - configuraciones semanales propias del mes;
    - SitioPlanificado del mes;
    - ImportacionAsignacionMensual del mes;
    - finalmente PlanificacionMensual.

    NO SE ELIMINA
    ==========================================================

    - SitioMovil;
    - ServicioCotizado;
    - técnicos;
    - sesiones;
    - evidencias;
    - Operaciones;
    - batches de otros meses;
    - sitios de otros meses.
    """

    # ========================================================
    # IMPORTACIONES LOCALES
    # ========================================================

    from planificacion.modelos import (SalidaPlanificacionDiaria,
                                       SitioSalidaPlanificacionDiaria)
    from planificacion.models import (BatchPlanificacionSemanal,
                                      ConfiguracionSemana,
                                      ImportacionAsignacionMensual,
                                      SitioBatchSemanal, SitioPlanificado)

    # ========================================================
    # OBTENER PLANIFICACIÓN
    # ========================================================

    planificacion = get_object_or_404(
        PlanificacionMensual.objects.select_for_update(),
        pk=pk,
    )

    # ========================================================
    # NOMBRE PARA MENSAJE
    # ========================================================

    meses = {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }

    nombre_mes = meses.get(
        planificacion.mes,
        str(planificacion.mes),
    )

    nombre = f"{nombre_mes} " f"{planificacion.anio}"

    # ========================================================
    # SITIOS PLANIFICADOS DEL MES
    # ========================================================

    sitios_planificados_qs = SitioPlanificado.objects.filter(
        planificacion=planificacion,
    )

    sitio_planificado_ids = list(
        sitios_planificados_qs.values_list(
            "pk",
            flat=True,
        )
    )

    cantidad_sitios = len(sitio_planificado_ids)

    # ========================================================
    # BATCHES PROPIOS DEL MES
    # ========================================================

    batches_propios_qs = BatchPlanificacionSemanal.objects.filter(
        planificacion=planificacion,
    )

    batch_ids_propios = list(
        batches_propios_qs.values_list(
            "pk",
            flat=True,
        )
    )

    cantidad_batches = len(batch_ids_propios)

    # ========================================================
    # CONFIGURACIONES SEMANALES
    # ========================================================

    configuracion_ids = list(
        ConfiguracionSemana.objects.filter(
            planificacion=planificacion,
        ).values_list(
            "pk",
            flat=True,
        )
    )

    # ========================================================
    # TODOS LOS SITIO-BATCH QUE APUNTAN A SITIOS DEL MES
    # ========================================================
    #
    # CRÍTICO:
    #
    # NO filtramos solamente:
    #
    #     batch__planificacion=planificacion
    #
    # porque un SitioPlanificado de septiembre puede estar
    # dentro de W35 perteneciente a agosto.
    # ========================================================

    sitios_batch_del_mes_qs = SitioBatchSemanal.objects.filter(
        sitio_planificado_id__in=(sitio_planificado_ids),
    )

    sitio_batch_ids_del_mes = list(
        sitios_batch_del_mes_qs.values_list(
            "pk",
            flat=True,
        )
    )

    cantidad_sitios_batch_mes = len(sitio_batch_ids_del_mes)

    # ========================================================
    # SITIOS-BATCH PROPIOS DE LOS BATCHES DEL MES
    # ========================================================
    #
    # Además eliminamos cualquier SitioBatchSemanal que
    # pertenezca directamente a un batch propio del mes.
    # ========================================================

    sitio_batch_ids_batches_propios = list(
        SitioBatchSemanal.objects.filter(
            batch_id__in=batch_ids_propios,
        ).values_list(
            "pk",
            flat=True,
        )
    )

    # ========================================================
    # UNIVERSO TOTAL DE SITIOS-BATCH A ELIMINAR
    # ========================================================

    sitio_batch_ids_eliminar = set(sitio_batch_ids_del_mes)

    sitio_batch_ids_eliminar.update(sitio_batch_ids_batches_propios)

    # ========================================================
    # SALIDAS EXTERNAS AFECTADAS
    # ========================================================
    #
    # Son salidas que pueden pertenecer, por ejemplo, a agosto
    # pero contener uno o más sitios de septiembre.
    #
    # Necesitamos recordarlas antes de eliminar las
    # participaciones para comprobar luego si quedaron vacías.
    # ========================================================

    salida_ids_afectadas = set(
        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch_id__in=(sitio_batch_ids_eliminar),
        ).values_list(
            "salida_id",
            flat=True,
        )
    )

    # ========================================================
    # SALIDAS PROPIAS DE LOS BATCHES DEL MES
    # ========================================================

    salida_ids_propias = set(
        SalidaPlanificacionDiaria.objects.filter(
            batch_id__in=batch_ids_propios,
        ).values_list(
            "pk",
            flat=True,
        )
    )

    # ========================================================
    # CONTADORES
    # ========================================================

    cantidad_participaciones_diarias = SitioSalidaPlanificacionDiaria.objects.filter(
        sitio_batch_id__in=(sitio_batch_ids_eliminar),
    ).count()

    cantidad_salidas_propias = len(salida_ids_propias)

    # ========================================================
    # 1. ELIMINAR PARTICIPACIONES DE LOS SITIOS DEL MES
    # ========================================================
    #
    # Esto permite liberar los SitioBatchSemanal protegidos.
    # ========================================================

    if sitio_batch_ids_eliminar:

        SitioSalidaPlanificacionDiaria.objects.filter(
            sitio_batch_id__in=(sitio_batch_ids_eliminar),
        ).delete()

    # ========================================================
    # 2. ELIMINAR TODAS LAS PARTICIPACIONES DE SALIDAS
    #    PROPIAS DEL MES
    # ========================================================
    #
    # Esta segunda limpieza cubre cualquier inconsistencia
    # histórica en la cual una salida perteneciente al mes
    # contenga una participación que no estuviera dentro del
    # universo anterior.
    # ========================================================

    if salida_ids_propias:

        SitioSalidaPlanificacionDiaria.objects.filter(
            salida_id__in=(salida_ids_propias),
        ).delete()

    # ========================================================
    # 3. ELIMINAR SALIDAS PROPIAS DEL MES
    # ========================================================

    if salida_ids_propias:

        SalidaPlanificacionDiaria.objects.filter(
            pk__in=salida_ids_propias,
        ).delete()

    # ========================================================
    # 4. ELIMINAR SALIDAS EXTERNAS QUE QUEDARON VACÍAS
    # ========================================================
    #
    # Ejemplo:
    #
    # W35 es de agosto.
    #
    # Una salida tenía únicamente:
    #
    #     05_007 septiembre
    #     05_350 septiembre
    #
    # después de retirar ambos sitios la salida no tiene
    # sentido y se elimina.
    #
    # Si todavía conserva un sitio de agosto:
    #
    #     NO se elimina.
    # ========================================================

    salida_ids_externas = salida_ids_afectadas - salida_ids_propias

    salida_ids_externas_vacias = []

    if salida_ids_externas:

        for salida in SalidaPlanificacionDiaria.objects.filter(
            pk__in=salida_ids_externas,
        ):

            tiene_sitios = SitioSalidaPlanificacionDiaria.objects.filter(
                salida=salida,
            ).exists()

            if not tiene_sitios:

                salida_ids_externas_vacias.append(salida.pk)

    cantidad_salidas_externas_vacias = len(salida_ids_externas_vacias)

    if salida_ids_externas_vacias:

        SalidaPlanificacionDiaria.objects.filter(
            pk__in=(salida_ids_externas_vacias),
        ).delete()

    # ========================================================
    # 5. ELIMINAR SITIOS DE BATCH QUE REFERENCIAN EL MES
    # ========================================================
    #
    # Aquí desaparece finalmente la relación PROTECT que
    # estaba causando el error.
    # ========================================================

    if sitio_batch_ids_eliminar:

        SitioBatchSemanal.objects.filter(
            pk__in=(sitio_batch_ids_eliminar),
        ).delete()

    # ========================================================
    # 6. ELIMINAR BATCHES PROPIOS DEL MES
    # ========================================================

    if batch_ids_propios:

        BatchPlanificacionSemanal.objects.filter(
            pk__in=batch_ids_propios,
        ).delete()

    # ========================================================
    # 7. ELIMINAR CONFIGURACIONES SEMANALES DEL MES
    # ========================================================

    if configuracion_ids:

        ConfiguracionSemana.objects.filter(
            pk__in=configuracion_ids,
            planificacion=planificacion,
        ).delete()

    # Seguridad adicional por configuraciones huérfanas.

    ConfiguracionSemana.objects.filter(
        planificacion=planificacion,
    ).delete()

    # ========================================================
    # 8. ELIMINAR SITIOS PLANIFICADOS DEL MES
    # ========================================================

    SitioPlanificado.objects.filter(
        planificacion=planificacion,
    ).delete()

    # ========================================================
    # 9. ELIMINAR AUDITORÍA DE IMPORTACIONES
    # ========================================================

    ImportacionAsignacionMensual.objects.filter(
        planificacion=planificacion,
    ).delete()

    # ========================================================
    # 10. ELIMINAR PLANIFICACIÓN MENSUAL
    # ========================================================

    planificacion.delete()

    # ========================================================
    # MENSAJE FINAL
    # ========================================================

    messages.success(
        request,
        (
            f'La planificación mensual "{nombre}" '
            "fue eliminada administrativamente. "
            f"Se eliminaron {cantidad_sitios} sitio(s) "
            "planificado(s), "
            f"{cantidad_sitios_batch_mes} vínculo(s) "
            "semanal(es) asociados al mes, "
            f"{cantidad_batches} batch(es) propio(s), "
            f"{cantidad_salidas_propias} salida(s) propia(s), "
            f"{cantidad_salidas_externas_vacias} "
            "salida(s) externa(s) que quedaron vacías y "
            f"{cantidad_participaciones_diarias} "
            "participación(es) diaria(s) vinculada(s). "
            "Los sitios maestros, otros meses y la "
            "información de Operaciones permanecen intactos."
        ),
    )

    return redirect(
        "planificacion:planificaciones_mensuales",
    )

# planificacion/views/asignacion_operativa.py

from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from planificacion.modelos import SalidaPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal
from planificacion.services.asignacion_operativa import (
    asignar_dia_completo, asignar_salida_completa,
    construir_preview_asignacion_dia, construir_preview_asignacion_salida)
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================

ROLES_ASIGNACION_OPERATIVA = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# URL DE RETORNO
# ============================================================


def _redirect_detalle_batch(
    batch_id,
):
    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch_id,
    )


# ============================================================
# PREVIEW ASIGNACIÓN DE UNA CUADRILLA / SALIDA
# ============================================================


@require_GET
@rol_requerido(*ROLES_ASIGNACION_OPERATIVA)
def preview_asignacion_salida(
    request,
    salida_id,
):
    """
    Muestra el preview antes de asignar todos los sitios
    elegibles de una salida a los integrantes configurados
    en su CuadrillaOperativa.

    No modifica datos.
    """

    salida = get_object_or_404(
        SalidaPlanificacionDiaria.objects.select_related(
            "batch",
            "batch__planificacion",
            "disponibilidad_cuadrilla",
            "disponibilidad_cuadrilla__cuadrilla_operativa",
        ),
        pk=salida_id,
    )

    preview = construir_preview_asignacion_salida(
        salida,
    )

    return render(
        request,
        "planificacion/diaria/asignacion_salida_preview.html",
        {
            "batch": salida.batch,
            "mensual": salida.batch.planificacion,
            "salida": salida,
            "preview": preview,
        },
    )


# ============================================================
# CONFIRMAR ASIGNACIÓN DE UNA CUADRILLA / SALIDA
# ============================================================


@require_POST
@rol_requerido(*ROLES_ASIGNACION_OPERATIVA)
def confirmar_asignacion_salida(
    request,
    salida_id,
):
    """
    Confirma la asignación de todos los sitios elegibles
    de una salida.

    Un sitio solamente se procesa cuando:

    - pertenece activamente a la salida;
    - posee permiso aprobado o no requiere permiso;
    - existe ServicioCotizado;
    - ServicioCotizado continúa exactamente en
      aprobado_pendiente.

    Los técnicos utilizados son los integrantes activos
    configurados en CuadrillaOperativa.
    """

    salida = get_object_or_404(
        SalidaPlanificacionDiaria.objects.select_related(
            "batch",
        ),
        pk=salida_id,
    )

    batch_id = salida.batch_id

    resultado = asignar_salida_completa(
        salida_id=salida.pk,
        usuario=request.user,
        request=request,
    )

    # ========================================================
    # ERROR GENERAL
    # ========================================================

    error = (
        resultado.get(
            "error",
            "",
        )
        or ""
    ).strip()

    if error:

        messages.error(
            request,
            error,
        )

        return _redirect_detalle_batch(
            batch_id,
        )

    # ========================================================
    # RESULTADOS
    # ========================================================

    cantidad_asignados = int(
        resultado.get(
            "cantidad_asignados",
            0,
        )
        or 0
    )

    cantidad_omitidos = int(
        resultado.get(
            "cantidad_omitidos",
            0,
        )
        or 0
    )

    cuadrilla = resultado.get(
        "cuadrilla",
    )

    nombre_cuadrilla = cuadrilla.nombre if cuadrilla else "Cuadrilla"

    # ========================================================
    # ASIGNADOS
    # ========================================================

    if cantidad_asignados:

        messages.success(
            request,
            (
                f"{nombre_cuadrilla}: "
                f"se asignaron correctamente "
                f"{cantidad_asignados} sitio(s) "
                "a los integrantes configurados "
                "de la cuadrilla."
            ),
        )

    # ========================================================
    # OMITIDOS
    # ========================================================

    if cantidad_omitidos:

        messages.warning(
            request,
            (
                f"{cantidad_omitidos} sitio(s) no fueron "
                "asignados porque ya no se encontraban "
                "en estado Pendiente por asignar o no "
                "cumplían las condiciones operacionales."
            ),
        )

    # ========================================================
    # NINGUNO ASIGNADO
    # ========================================================

    if not cantidad_asignados:

        messages.warning(
            request,
            (
                f"{nombre_cuadrilla}: no existe actualmente "
                "ningún sitio elegible para asignación masiva."
            ),
        )

    return _redirect_detalle_batch(
        batch_id,
    )


# ============================================================
# PREVIEW ASIGNACIÓN DE TODO EL DÍA
# ============================================================


@require_GET
@rol_requerido(*ROLES_ASIGNACION_OPERATIVA)
def preview_asignacion_dia(
    request,
    batch_id,
    fecha,
):
    """
    Muestra un preview de todas las cuadrillas/salidas
    existentes en una fecha.

    Cada cuadrilla muestra:

    - integrantes configurados;
    - sitios asignables;
    - sitios omitidos.

    No modifica datos.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
        ),
        pk=batch_id,
    )

    # ========================================================
    # VALIDAR FECHA
    # ========================================================

    try:

        fecha_objeto = date.fromisoformat(
            fecha,
        )

    except (
        TypeError,
        ValueError,
    ):

        messages.error(
            request,
            "La fecha indicada para la asignación no es válida.",
        )

        return _redirect_detalle_batch(
            batch.pk,
        )

    # ========================================================
    # VALIDAR QUE PERTENEZCA A LA SEMANA
    # ========================================================

    fecha_fin = batch.fecha_inicio + timedelta(
        days=5,
    )

    if not (batch.fecha_inicio <= fecha_objeto <= fecha_fin):

        messages.error(
            request,
            "La fecha indicada no pertenece a la semana de este batch.",
        )

        return _redirect_detalle_batch(
            batch.pk,
        )

    # ========================================================
    # CONSTRUIR PREVIEW
    # ========================================================

    preview = construir_preview_asignacion_dia(
        batch=batch,
        fecha=fecha_objeto,
    )

    return render(
        request,
        "planificacion/diaria/asignacion_dia_preview.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "fecha": fecha_objeto,
            "preview": preview,
        },
    )


# ============================================================
# CONFIRMAR ASIGNACIÓN DE TODO EL DÍA
# ============================================================


@require_POST
@rol_requerido(*ROLES_ASIGNACION_OPERATIVA)
def confirmar_asignacion_dia(
    request,
    batch_id,
    fecha,
):
    """
    Asigna todas las salidas elegibles de una fecha.

    Cada salida utiliza exclusivamente los integrantes
    activos configurados en su propia CuadrillaOperativa.

    Un servicio solamente se procesa cuando continúa
    exactamente en:

        aprobado_pendiente
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
        ),
        pk=batch_id,
    )

    # ========================================================
    # VALIDAR FECHA
    # ========================================================

    try:

        fecha_objeto = date.fromisoformat(
            fecha,
        )

    except (
        TypeError,
        ValueError,
    ):

        messages.error(
            request,
            "La fecha indicada para la asignación no es válida.",
        )

        return _redirect_detalle_batch(
            batch.pk,
        )

    # ========================================================
    # VALIDAR QUE PERTENEZCA A LA SEMANA
    # ========================================================

    fecha_fin = batch.fecha_inicio + timedelta(
        days=5,
    )

    if not (batch.fecha_inicio <= fecha_objeto <= fecha_fin):

        messages.error(
            request,
            "La fecha indicada no pertenece a la semana de este batch.",
        )

        return _redirect_detalle_batch(
            batch.pk,
        )

    # ========================================================
    # EJECUTAR
    # ========================================================

    resultado = asignar_dia_completo(
        batch=batch,
        fecha=fecha_objeto,
        usuario=request.user,
        request=request,
    )

    cantidad_asignados = int(
        resultado.get(
            "cantidad_asignados",
            0,
        )
        or 0
    )

    cantidad_omitidos = int(
        resultado.get(
            "cantidad_omitidos",
            0,
        )
        or 0
    )

    resultados_salidas = (
        resultado.get(
            "resultados",
            [],
        )
        or []
    )

    # ========================================================
    # ERRORES DE CUADRILLAS
    # ========================================================

    errores_cuadrillas = []

    for resultado_salida in resultados_salidas:

        error = (
            resultado_salida.get(
                "error",
                "",
            )
            or ""
        ).strip()

        if error:

            errores_cuadrillas.append(
                error,
            )

    # ========================================================
    # MENSAJE PRINCIPAL
    # ========================================================

    if cantidad_asignados:

        messages.success(
            request,
            (
                f"Asignación del "
                f"{fecha_objeto:%d/%m/%Y} "
                "completada. "
                f"Se asignaron "
                f"{cantidad_asignados} sitio(s) "
                "a los integrantes de sus respectivas "
                "cuadrillas."
            ),
        )

    else:

        messages.warning(
            request,
            (
                f"No existían sitios elegibles para asignar "
                f"el {fecha_objeto:%d/%m/%Y}."
            ),
        )

    # ========================================================
    # OMITIDOS
    # ========================================================

    if cantidad_omitidos:

        messages.warning(
            request,
            (
                f"{cantidad_omitidos} sitio(s) fueron omitidos "
                "porque no se encontraban exactamente en "
                "estado Pendiente por asignar o no cumplían "
                "las validaciones operacionales."
            ),
        )

    # ========================================================
    # CUADRILLAS SIN CONFIGURACIÓN
    # ========================================================

    for error in errores_cuadrillas:

        messages.error(
            request,
            error,
        )

    return _redirect_detalle_batch(
        batch.pk,
    )

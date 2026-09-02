# planificacion/views/mapa_planificacion_diaria.py

from datetime import datetime

from django.conf import settings
from django.shortcuts import get_object_or_404, render

from planificacion.modelos import SalidaPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal
from planificacion.services.mapas_planificacion_diaria import (
    construir_mapa_dia_planificacion, construir_mapa_operacional_semanal,
    construir_mapa_pendientes_planificacion, construir_mapa_salida_diaria)
from planificacion.services.planificacion_diaria import \
    obtener_sitios_pendientes_planificacion_diaria
from usuarios.decoradores import rol_requerido

ROLES_PLANIFICACION_DIARIA = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# MAPA DE UNA SALIDA
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def mapa_salida_planificacion_diaria(
    request,
    salida_id,
):
    salida = get_object_or_404(
        SalidaPlanificacionDiaria.objects.select_related(
            "batch",
            "batch__planificacion",
            "disponibilidad_cuadrilla",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        ),
        pk=salida_id,
    )

    batch = salida.batch

    mapa = construir_mapa_salida_diaria(
        salida,
    )

    return render(
        request,
        "planificacion/diaria/mapa_salida.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "salida": salida,
            "mapa": mapa,
            "google_maps_api_key": getattr(
                settings,
                "GOOGLE_MAPS_API_KEY",
                "",
            ),
        },
    )


# ============================================================
# MAPA COMPLETO DEL DÍA
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def mapa_dia_planificacion_diaria(
    request,
    batch_id,
    fecha,
):
    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    try:

        fecha_objeto = datetime.strptime(
            fecha,
            "%Y-%m-%d",
        ).date()

    except ValueError:

        fecha_objeto = batch.fecha_inicio

    mapa = construir_mapa_dia_planificacion(
        batch=batch,
        fecha=fecha_objeto,
    )

    return render(
        request,
        "planificacion/diaria/mapa_dia.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "fecha": fecha_objeto,
            "mapa": mapa,
            "google_maps_api_key": getattr(
                settings,
                "GOOGLE_MAPS_API_KEY",
                "",
            ),
        },
    )


# ============================================================
# MAPA DE SITIOS PENDIENTES
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def mapa_pendientes_planificacion_diaria(
    request,
    batch_id,
):
    """
    Muestra exclusivamente los sitios aprobados que todavía
    no poseen una salida diaria dentro del batch.

    IMPORTANTE
    ==========================================================

    Utilizamos exactamente el mismo universo de pendientes
    que utiliza la pantalla de Planificación Diaria.

    De esta manera el mapa nunca construye su propia lógica
    para decidir qué sitio está pendiente.

    Si la pantalla muestra 3 pendientes, el mapa debe mostrar
    exactamente esos mismos 3.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    # ========================================================
    # OBTENER PENDIENTES REALES
    # ========================================================

    pendientes = list(
        obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )
    )

    # ========================================================
    # CONSTRUIR MAPA
    # ========================================================

    mapa = construir_mapa_pendientes_planificacion(
        batch=batch,
        pendientes=pendientes,
    )

    # ========================================================
    # RESPUESTA
    # ========================================================

    return render(
        request,
        "planificacion/diaria/mapa_pendientes.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "pendientes": pendientes,
            "cantidad_pendientes": len(pendientes),
            "mapa": mapa,
            "google_maps_api_key": getattr(
                settings,
                "GOOGLE_MAPS_API_KEY",
                "",
            ),
        },
    )


# ============================================================
# MAPA OPERACIONAL SEMANAL
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def mapa_operacional_semanal_planificacion_diaria(
    request,
    batch_id,
):
    """
    Mapa operacional completo del batch semanal.

    Muestra simultáneamente todos los sitios operativos de la
    semana y superpone únicamente las rutas correspondientes
    al día seleccionado.

    La fecha puede recibirse mediante:

        ?fecha=YYYY-MM-DD

    Si no se recibe una fecha válida:

        - si hoy pertenece a la semana del batch, usamos hoy;
        - en caso contrario usamos la fecha de inicio del batch.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    # ========================================================
    # FECHAS DE LA SEMANA
    # ========================================================

    fecha_inicio = batch.fecha_inicio

    fechas_semana = [
        fecha_inicio,
        fecha_inicio.fromordinal(fecha_inicio.toordinal() + 1),
        fecha_inicio.fromordinal(fecha_inicio.toordinal() + 2),
        fecha_inicio.fromordinal(fecha_inicio.toordinal() + 3),
        fecha_inicio.fromordinal(fecha_inicio.toordinal() + 4),
        fecha_inicio.fromordinal(fecha_inicio.toordinal() + 5),
    ]

    # ========================================================
    # FECHA SOLICITADA
    # ========================================================

    fecha_parametro = (
        request.GET.get(
            "fecha",
            "",
        )
        or ""
    ).strip()

    fecha_rutas = None

    if fecha_parametro:

        try:

            fecha_rutas = datetime.strptime(
                fecha_parametro,
                "%Y-%m-%d",
            ).date()

        except ValueError:

            fecha_rutas = None

    # ========================================================
    # FECHA PREDETERMINADA
    # ========================================================

    if fecha_rutas not in fechas_semana:

        hoy = datetime.now().date()

        if hoy in fechas_semana:
            fecha_rutas = hoy

        else:
            fecha_rutas = fecha_inicio

    # ========================================================
    # PENDIENTES
    # ========================================================

    pendientes = list(
        obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )
    )

    # ========================================================
    # MAPA
    # ========================================================

    mapa = construir_mapa_operacional_semanal(
        batch=batch,
        fecha_rutas=fecha_rutas,
        pendientes=pendientes,
    )

    # ========================================================
    # SELECTOR DE DÍAS
    # ========================================================

    dias = []

    for fecha in fechas_semana:

        dias.append(
            {
                "fecha": fecha,
                "fecha_iso": fecha.isoformat(),
                "seleccionado": (fecha == fecha_rutas),
            }
        )

    return render(
        request,
        "planificacion/diaria/mapa_operacional_semanal.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "mapa": mapa,
            "fecha_rutas": fecha_rutas,
            "dias": dias,
            "google_maps_api_key": (settings.GOOGLE_MAPS_API_KEY),
        },
    )

# planificacion/views/planificacion_diaria_manual.py

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from planificacion.forms import ProgramarSitioManualPlanificacionDiariaForm
from planificacion.modelos import PrioridadPlanificacionDiaria
from planificacion.models import BatchPlanificacionSemanal, SitioBatchSemanal
from planificacion.services.planificacion_diaria_manual import (
    analizar_programacion_manual, programar_sitio_manual)
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================


ROLES_PLANIFICACION_DIARIA = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# UTILIDADES
# ============================================================


def _url_detalle_diario(
    batch,
):
    return reverse(
        "planificacion:detalle_planificacion_diaria",
        kwargs={
            "batch_id": batch.pk,
        },
    )


def _identificador_sitio(
    sitio_batch,
):
    sitio = sitio_batch.sitio_planificado.sitio

    return sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"


# ============================================================
# CREAR / REACTIVAR PRIORIDAD
# ============================================================


def _asegurar_prioridad_programacion_manual(
    *,
    sitio_batch,
    fecha,
    disponibilidad,
    usuario,
):
    """
    Crea o reactiva la prioridad diaria solamente cuando
    el usuario marcó explícitamente el checkbox de prioridad.
    """

    cuadrilla = disponibilidad.cuadrilla_operativa if disponibilidad else None

    prioridad, creada = PrioridadPlanificacionDiaria.objects.get_or_create(
        sitio_batch=sitio_batch,
        defaults={
            "prioridad": "alta",
            "estado": "activa",
            "es_ancla": True,
            "fecha_objetivo": fecha,
            "fecha_es_obligatoria": True,
            "cuadrilla_obligatoria": cuadrilla,
            "motivo": (
                "Sitio marcado como prioritario " "durante programación manual."
            ),
            "creado_por": usuario,
            "actualizado_por": usuario,
        },
    )

    if creada:
        return prioridad

    campos_actualizados = []

    # ========================================================
    # ESTADO
    # ========================================================

    if prioridad.estado != "activa":

        prioridad.estado = "activa"

        campos_actualizados.append(
            "estado",
        )

    # ========================================================
    # NIVEL
    # ========================================================

    if prioridad.prioridad == "normal":

        prioridad.prioridad = "alta"

        campos_actualizados.append(
            "prioridad",
        )

    # ========================================================
    # ANCLA
    # ========================================================

    if not prioridad.es_ancla:

        prioridad.es_ancla = True

        campos_actualizados.append(
            "es_ancla",
        )

    # ========================================================
    # FECHA
    # ========================================================

    if prioridad.fecha_objetivo != fecha:

        prioridad.fecha_objetivo = fecha

        campos_actualizados.append(
            "fecha_objetivo",
        )

    if not prioridad.fecha_es_obligatoria:

        prioridad.fecha_es_obligatoria = True

        campos_actualizados.append(
            "fecha_es_obligatoria",
        )

    # ========================================================
    # CUADRILLA
    # ========================================================

    cuadrilla_id = cuadrilla.pk if cuadrilla else None

    if prioridad.cuadrilla_obligatoria_id != cuadrilla_id:

        prioridad.cuadrilla_obligatoria = cuadrilla

        campos_actualizados.append(
            "cuadrilla_obligatoria",
        )

    # ========================================================
    # MOTIVO
    # ========================================================

    nuevo_motivo = prioridad.motivo or (
        "Sitio marcado como prioritario " "durante programación manual."
    )

    if prioridad.motivo != nuevo_motivo:

        prioridad.motivo = nuevo_motivo

        campos_actualizados.append(
            "motivo",
        )

    # ========================================================
    # AUDITORÍA
    # ========================================================

    prioridad.actualizado_por = usuario

    campos_actualizados.append(
        "actualizado_por",
    )

    prioridad.save(
        update_fields=[
            *dict.fromkeys(
                campos_actualizados,
            ),
            "actualizado_en",
        ]
    )

    return prioridad


# ============================================================
# CANCELAR PRIORIDAD SI SE DESMARCA
# ============================================================


def _cancelar_prioridad_si_corresponde(
    *,
    sitio_batch,
    usuario,
):
    """
    Si el usuario deja el checkbox desmarcado y el sitio tenía
    una prioridad activa, la prioridad deja de estar activa.

    No elimina el registro para conservar auditoría.
    """

    try:

        prioridad = sitio_batch.prioridad_diaria

    except ObjectDoesNotExist:

        return None

    if prioridad.estado != "activa":
        return prioridad

    prioridad.estado = "cancelada"

    prioridad.actualizado_por = usuario

    prioridad.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    return prioridad


# ============================================================
# PROGRAMAR SITIO MANUALMENTE
# ============================================================


@require_http_methods(
    [
        "GET",
        "POST",
    ]
)
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
@transaction.atomic
def programar_sitio_manual_planificacion_diaria(
    request,
    batch_id,
    sitio_batch_id,
):
    """
    Programa manualmente un sitio en una fecha/cuadrilla.

    PROGRAMACIÓN MANUAL
    ==========================================================

    La programación manual permite decidir:

        fecha
        cuadrilla
        protección frente a recálculos

    PRIORIDAD
    ==========================================================

    La prioridad es independiente.

    Solamente se crea/reactiva cuando el usuario marca:

        "Marcar este sitio como prioritario"

    Si el checkbox queda desmarcado:

        - no se crea prioridad;
        - si existía una prioridad activa, se cancela;
        - no aparece estrella;
        - la programación manual se conserva normalmente.
    """

    # ========================================================
    # BATCH
    # ========================================================

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    # ========================================================
    # SITIO
    # ========================================================

    sitio_batch = get_object_or_404(
        SitioBatchSemanal.objects.select_related(
            "batch",
            "sitio_planificado",
            "sitio_planificado__sitio",
        ),
        pk=sitio_batch_id,
        batch=batch,
    )

    identificador = _identificador_sitio(
        sitio_batch,
    )

    sitio = sitio_batch.sitio_planificado.sitio

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        form = ProgramarSitioManualPlanificacionDiariaForm(
            request.POST,
            batch=batch,
            sitio_batch=sitio_batch,
        )

        if form.is_valid():

            # =================================================
            # DATOS
            # =================================================

            fecha = form.cleaned_data["fecha"]

            disponibilidad = form.cleaned_data["disponibilidad_cuadrilla"]

            confirmar_excepcion = bool(
                form.cleaned_data.get(
                    "confirmar_excepcion",
                    False,
                )
            )

            bloquear_salida = bool(
                form.cleaned_data.get(
                    "bloquear_salida",
                    True,
                )
            )

            marcar_como_prioridad = bool(
                form.cleaned_data.get(
                    "marcar_como_prioridad",
                    False,
                )
            )

            observaciones = (
                form.cleaned_data.get(
                    "observaciones",
                    "",
                )
                or ""
            ).strip()

            # =================================================
            # ANÁLISIS PREVIO
            # =================================================

            analisis = analizar_programacion_manual(
                batch=batch,
                sitio_batch=sitio_batch,
                disponibilidad_cuadrilla=disponibilidad,
                fecha=fecha,
            )

            # =================================================
            # ERRORES
            # =================================================

            if analisis["errores"]:

                for error in analisis["errores"]:

                    messages.error(
                        request,
                        error,
                    )

            # =================================================
            # CONFIRMACIÓN
            # =================================================

            elif analisis["requiere_confirmacion"] and not confirmar_excepcion:

                messages.warning(
                    request,
                    (
                        "Esta programación necesita "
                        "confirmación manual antes "
                        "de guardarse."
                    ),
                )

                for advertencia in analisis["advertencias"]:

                    messages.warning(
                        request,
                        advertencia,
                    )

            # =================================================
            # GUARDAR
            # =================================================

            else:

                try:

                    # =========================================
                    # PROGRAMACIÓN MANUAL
                    # =========================================

                    resultado = programar_sitio_manual(
                        batch=batch,
                        sitio_batch=sitio_batch,
                        disponibilidad_cuadrilla=disponibilidad,
                        fecha=fecha,
                        usuario=request.user,
                        confirmar_excepcion=confirmar_excepcion,
                        bloquear_salida=bloquear_salida,
                        observaciones=observaciones,
                    )

                    # =========================================
                    # PRIORIDAD INDEPENDIENTE
                    # =========================================

                    if marcar_como_prioridad:

                        _asegurar_prioridad_programacion_manual(
                            sitio_batch=sitio_batch,
                            fecha=fecha,
                            disponibilidad=disponibilidad,
                            usuario=request.user,
                        )

                    else:

                        _cancelar_prioridad_si_corresponde(
                            sitio_batch=sitio_batch,
                            usuario=request.user,
                        )

                except ValidationError as exc:

                    if hasattr(
                        exc,
                        "messages",
                    ):

                        errores = exc.messages

                    else:

                        errores = [
                            str(exc),
                        ]

                    for error in errores:

                        messages.error(
                            request,
                            error,
                        )

                except Exception as exc:

                    messages.error(
                        request,
                        ("No fue posible programar " f"{identificador}: {exc}"),
                    )

                else:

                    # =========================================
                    # RESULTADO
                    # =========================================

                    salida = resultado["salida"]

                    cantidad = resultado["cantidad_sitios"]

                    if resultado["sitio_movido"]:

                        accion = "fue reprogramado"

                    else:

                        accion = "fue programado"

                    # =========================================
                    # MENSAJE
                    # =========================================

                    texto = (
                        f"{identificador} "
                        f"{accion} para el "
                        f"{salida.fecha:%d/%m/%Y} "
                        f"con "
                        f"{salida.cuadrilla_nombre}. "
                        f"La salida posee ahora "
                        f"{cantidad} sitio(s)."
                    )

                    if marcar_como_prioridad:

                        texto += " El sitio quedó marcado " "como prioridad diaria."

                    messages.success(
                        request,
                        texto,
                    )

                    # =========================================
                    # ADVERTENCIAS
                    # =========================================

                    for advertencia in resultado.get(
                        "advertencias",
                        [],
                    ):

                        messages.warning(
                            request,
                            advertencia,
                        )

                    return redirect(
                        "planificacion:" "detalle_planificacion_diaria",
                        batch_id=batch.pk,
                    )

    # ========================================================
    # GET
    # ========================================================

    else:

        form = ProgramarSitioManualPlanificacionDiariaForm(
            batch=batch,
            sitio_batch=sitio_batch,
        )

    # ========================================================
    # CONTEXTO
    # ========================================================

    return render(
        request,
        "planificacion/diaria/programar_manual.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "sitio_batch": sitio_batch,
            "sitio_planificado": (sitio_batch.sitio_planificado),
            "sitio": sitio,
            "identificador": identificador,
            "form": form,
            "volver_url": _url_detalle_diario(
                batch,
            ),
        },
    )

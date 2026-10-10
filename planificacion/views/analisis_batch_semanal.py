from django.conf import settings
from django.contrib import messages
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.models import BatchPlanificacionSemanal
from planificacion.services.analisis_batch_semanal import (
    ANALISIS_BATCH_VERSION, aplicar_propuesta_batch,
    construir_resultado_serializable)
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================

ROLES_PLANIFICACION = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# CLAVE DE SESSION
# ============================================================


def _session_key(
    batch_id,
):
    return f"analisis_batch_semanal_" f"{batch_id}"


# ============================================================
# VALIDAR CACHE DEL ANÁLISIS
# ============================================================


def _resultado_session_es_valido(
    *,
    resultado,
    batch,
):
    """
    Determina si podemos reutilizar el análisis guardado
    previamente en session.

    Esto evita reutilizar análisis antiguos generados cuando:

    - todavía existían reservas automáticas;
    - cambió la lógica del motor;
    - pertenece a otro batch.
    """

    if not resultado:
        return False

    if resultado.get("batch_id") != batch.pk:
        return False

    if resultado.get("version") != ANALISIS_BATCH_VERSION:
        return False

    return True


# ============================================================
# ANALIZAR / COMPARAR PROPUESTAS
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
def analizar_batch_semanal_view(
    request,
    batch_id,
):
    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ).prefetch_related(
            (
                "configuracion_semana__"
                "disponibilidades_cuadrillas__"
                "cuadrilla_operativa"
            ),
        ),
        pk=batch_id,
    )

    recalcular = request.GET.get("recalcular") == "1"

    key = _session_key(batch.pk)

    resultado = request.session.get(key)

    # ========================================================
    # RECALCULAR
    # ========================================================

    if recalcular or not _resultado_session_es_valido(
        resultado=resultado,
        batch=batch,
    ):

        resultado = construir_resultado_serializable(
            batch=batch,
            cantidad_reserva=0,
        )

        request.session[key] = resultado

        request.session.modified = True

    # ========================================================
    # GOOGLE MAPS
    # ========================================================

    google_maps_api_key = getattr(
        settings,
        "GOOGLE_MAPS_API_KEY",
        "",
    )

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        ("planificacion/semanal/" "analisis.html"),
        {
            "batch": batch,
            "mensual": (batch.planificacion),
            "analisis": resultado,
            "propuestas": (
                resultado.get(
                    "propuestas",
                    [],
                )
            ),
            "google_maps_api_key": (google_maps_api_key),
        },
    )


# ============================================================
# USAR PROPUESTA DEL MOTOR
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def aplicar_propuesta_batch_view(
    request,
    batch_id,
    posicion,
):
    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
        ),
        pk=batch_id,
    )

    # ========================================================
    # SOLO BORRADOR
    # ========================================================

    if batch.estado != "borrador":

        messages.error(
            request,
            (
                "Solo puedes aplicar una propuesta "
                "del motor mientras el batch "
                "se encuentre en borrador."
            ),
        )

        return redirect(
            ("planificacion:" "detalle_planificacion_semanal"),
            batch_id=batch.pk,
        )

    # ========================================================
    # SESSION
    # ========================================================

    key = _session_key(batch.pk)

    resultado = request.session.get(key)

    if not _resultado_session_es_valido(
        resultado=resultado,
        batch=batch,
    ):

        request.session.pop(
            key,
            None,
        )

        request.session.modified = True

        messages.error(
            request,
            (
                "El análisis ya no está disponible "
                "o pertenece a una versión anterior "
                "del motor. Ejecuta nuevamente "
                "el análisis."
            ),
        )

        return redirect(
            ("planificacion:" "analizar_batch_semanal"),
            batch_id=batch.pk,
        )

    # ========================================================
    # LOCALIZAR PROPUESTA
    # ========================================================

    propuestas = resultado.get(
        "propuestas",
        [],
    )

    propuesta = next(
        (item for item in propuestas if item.get("posicion") == posicion),
        None,
    )

    if propuesta is None:

        messages.error(
            request,
            "La propuesta seleccionada no existe.",
        )

        return redirect(
            ("planificacion:" "analizar_batch_semanal"),
            batch_id=batch.pk,
        )

    # ========================================================
    # APLICAR
    # ========================================================

    try:

        resultado_aplicacion = aplicar_propuesta_batch(
            batch=batch,
            propuesta_serializada=propuesta,
            usuario=request.user,
        )

    except ProtectedError:

        # ====================================================
        # PROTECCIÓN DE PLANIFICACIÓN DIARIA EXISTENTE
        # ====================================================
        #
        # La propuesta intenta retirar uno o más
        # SitioBatchSemanal que ya poseen participaciones
        # dentro de la planificación diaria.
        #
        # La FK PROTECT está funcionando correctamente.
        #
        # No eliminamos esas participaciones ni debilitamos
        # la protección. Solamente convertimos la excepción
        # técnica en un mensaje comprensible para el usuario.
        # ====================================================

        messages.warning(
            request,
            (
                "No se pudo aplicar esta propuesta porque "
                "la semana ya contiene sitios que forman parte "
                "de una planificación diaria existente. "
                "Para proteger esas salidas, el sistema no permite "
                "eliminar automáticamente esos sitios del batch. "
                "La planificación actual no fue modificada."
            ),
        )

        return redirect(
            ("planificacion:" "analizar_batch_semanal"),
            batch_id=batch.pk,
        )

    except ValueError as exc:

        # Si el universo cambió después del análisis,
        # eliminamos el cache para obligar a recalcular.
        request.session.pop(
            key,
            None,
        )

        request.session.modified = True

        messages.error(
            request,
            str(exc),
        )

        return redirect(
            ("planificacion:" "analizar_batch_semanal"),
            batch_id=batch.pk,
        )

    # ========================================================
    # ÉXITO
    # ========================================================

    messages.success(
        request,
        (
            f"Propuesta "
            f"{propuesta['codigo']} "
            f"aplicada correctamente: "
            f"{resultado_aplicacion['principales']} "
            "sitio(s) principal(es) incorporado(s) "
            "al batch semanal."
        ),
    )

    # ========================================================
    # LIMPIAR CACHE
    # ========================================================

    request.session.pop(
        key,
        None,
    )

    request.session.modified = True

    return redirect(
        ("planificacion:" "detalle_planificacion_semanal"),
        batch_id=batch.pk,
    )

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from planificacion.models import SitioBatchSemanal, SitioPlanificado
from planificacion.services.permisos_planificacion import \
    actualizar_permiso_sitio
from usuarios.decoradores import rol_requerido


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
@require_POST
@transaction.atomic
def actualizar_permiso_inline(
    request,
    pk,
):
    """
    Actualiza el permiso de un sitio directamente desde
    la tabla de planificación mensual.

    SINCRONIZACIÓN
    ==========================================================

    Además de actualizar SitioPlanificado, mantiene
    sincronizadas las participaciones SitioBatchSemanal
    todavía pertenecientes al flujo de permisos.

    REGLAS
    ==========================================================

    aprobado / no_requiere
        SitioPlanificado -> listo_planificar
        SitioBatchSemanal:
            gestion_permiso -> disponible
            seleccionado    -> disponible
            candidato       -> disponible

    rechazado
        SitioBatchSemanal:
            gestion_permiso -> rechazado
            disponible      -> rechazado
            seleccionado    -> rechazado
            candidato       -> rechazado

    solicitado / en_espera / por_solicitar
        SitioBatchSemanal:
            disponible      -> gestion_permiso
            seleccionado    -> gestion_permiso
            candidato       -> gestion_permiso

    IMPORTANTE
    ==========================================================

    No modificamos automáticamente estados más avanzados como:

        confirmado
        excluido
        reemplazado

    porque pueden representar decisiones posteriores dentro
    del flujo semanal/diario.

    El estado general del BatchPlanificacionSemanal tampoco
    se modifica aquí.

    Un batch puede continuar globalmente en:

        gestion_permisos

    aunque algunos de sus sitios individualmente ya estén:

        disponible
        confirmado

    Eso es válido.
    """

    # ========================================================
    # OBTENER SITIO
    # ========================================================

    sitio_planificado = get_object_or_404(
        SitioPlanificado.objects.select_for_update().select_related(
            "sitio",
            "planificacion",
        ),
        pk=pk,
        activo_en_mes=True,
    )

    # ========================================================
    # NUEVO PERMISO
    # ========================================================

    nuevo_estado = (
        request.POST.get(
            "estado_permiso",
        )
        or ""
    ).strip()

    # ========================================================
    # ACTUALIZAR SITIO PLANIFICADO
    # ========================================================

    try:

        resultado = actualizar_permiso_sitio(
            sitio_planificado=sitio_planificado,
            nuevo_estado=nuevo_estado,
            user=request.user,
        )

    except ValueError as exc:

        return JsonResponse(
            {
                "ok": False,
                "mensaje": str(exc),
            },
            status=400,
        )

    sitio_planificado = resultado["sitio_planificado"]

    # ========================================================
    # BLOQUEAR PARTICIPACIONES DEL SITIO EN BATCHES
    # ========================================================
    #
    # Un mismo SitioPlanificado podría aparecer en más de un
    # registro histórico de batch.
    #
    # Trabajamos solamente sobre estados todavía compatibles
    # con la etapa de permisos.
    # ========================================================

    items_batch = list(
        SitioBatchSemanal.objects.select_for_update()
        .filter(
            sitio_planificado=sitio_planificado,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .order_by(
            "id",
        )
    )

    items_batch_actualizados = []

    # ========================================================
    # SINCRONIZAR CADA PARTICIPACIÓN
    # ========================================================

    for item in items_batch:

        estado_anterior_batch = item.estado

        nuevo_estado_batch = None

        # ====================================================
        # APROBADO / NO REQUIERE
        # ====================================================

        if nuevo_estado in {
            "aprobado",
            "no_requiere",
        }:

            if item.estado in {
                "candidato",
                "seleccionado",
                "gestion_permiso",
            }:

                nuevo_estado_batch = "disponible"

        # ====================================================
        # RECHAZADO
        # ====================================================

        elif nuevo_estado == "rechazado":

            if item.estado in {
                "candidato",
                "seleccionado",
                "gestion_permiso",
                "disponible",
            }:

                nuevo_estado_batch = "rechazado"

        # ====================================================
        # TODAVÍA EN GESTIÓN
        # ====================================================

        elif nuevo_estado in {
            "por_solicitar",
            "solicitado",
            "en_espera",
        }:

            if item.estado in {
                "candidato",
                "seleccionado",
                "disponible",
                "gestion_permiso",
            }:

                nuevo_estado_batch = "gestion_permiso"

        # ====================================================
        # SIN GESTIÓN
        # ====================================================

        elif nuevo_estado == "sin_gestion":

            if item.estado in {
                "candidato",
                "seleccionado",
                "gestion_permiso",
                "disponible",
            }:

                nuevo_estado_batch = "seleccionado"

        # ====================================================
        # APLICAR CAMBIO
        # ====================================================

        if nuevo_estado_batch and item.estado != nuevo_estado_batch:

            item.estado = nuevo_estado_batch

            item.save(
                update_fields=[
                    "estado",
                    "actualizado_en",
                ]
            )

            items_batch_actualizados.append(
                {
                    "id": item.pk,
                    "estado_anterior": (estado_anterior_batch),
                    "estado": item.estado,
                    "estado_display": (item.get_estado_display()),
                }
            )

    # ========================================================
    # REFRESCAR SITIO
    # ========================================================

    sitio_planificado.refresh_from_db()

    # ========================================================
    # USUARIO
    # ========================================================

    actualizado_por = request.user.get_full_name() or request.user.username

    # ========================================================
    # RESPUESTA
    # ========================================================

    return JsonResponse(
        {
            "ok": True,
            "cambio": resultado["cambio"],
            "sitio_planificado": {
                "id": sitio_planificado.pk,
                "estado_permiso": (sitio_planificado.estado_permiso),
                "estado_permiso_display": (
                    sitio_planificado.get_estado_permiso_display()
                ),
                "estado": (sitio_planificado.estado),
                "estado_display": (sitio_planificado.get_estado_display()),
                "actualizado_por": actualizado_por,
            },
            "batches_sincronizados": (len(items_batch_actualizados)),
            "items_batch_actualizados": (items_batch_actualizados),
        }
    )

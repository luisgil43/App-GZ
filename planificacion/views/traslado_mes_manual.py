"""
Acción masiva para trasladar sitios entre meses.

Independiente del movimiento a semana existente.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from planificacion.models import (
    PlanificacionMensual,
    SitioPlanificado,
)
from planificacion.services.traslado_mes_manual import (
    trasladar_sitio_mes_manual,
)
from usuarios.decoradores import rol_requerido


@login_required
@require_POST
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def mover_sitios_mes_masivo(request, pk):

    planificacion = get_object_or_404(
        PlanificacionMensual,
        pk=pk,
    )

    def regresar():
        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=planificacion.pk,
        )

    ids_recibidos = request.POST.getlist("sitio_ids")

    ids = []

    for valor in ids_recibidos:

        try:
            identificador = int(valor)
        except (TypeError, ValueError):
            messages.error(
                request,
                "La selección contiene identificadores inválidos.",
            )
            return regresar()

        if identificador <= 0:
            messages.error(
                request,
                "La selección contiene identificadores inválidos.",
            )
            return regresar()

        if identificador not in ids:
            ids.append(identificador)

    if not ids:
        messages.warning(
            request,
            "Selecciona al menos un sitio para trasladar.",
        )
        return regresar()

    try:
        anio = int(
            request.POST.get("anio_destino", "")
        )

        mes = int(
            request.POST.get("mes_destino", "")
        )

    except (TypeError, ValueError):
        messages.error(
            request,
            "Debes indicar un mes y año de destino válidos.",
        )
        return regresar()

    if not (2000 <= anio <= 2100 and 1 <= mes <= 12):
        messages.error(
            request,
            "El mes o año de destino no es válido.",
        )
        return regresar()

    respuesta_permiso = request.POST.get(
        "mantener_permiso",
        "",
    )

    if respuesta_permiso not in {"si", "no"}:
        messages.error(
            request,
            "Debes indicar si deseas conservar "
            "el estado actual de los permisos.",
        )
        return regresar()

    mantener_permiso = respuesta_permiso == "si"

    if (
        planificacion.anio == anio
        and planificacion.mes == mes
    ):
        messages.error(
            request,
            "El mes de destino debe ser diferente al de origen.",
        )
        return regresar()

    if not PlanificacionMensual.objects.filter(
        anio=anio,
        mes=mes,
    ).exists():
        messages.error(
            request,
            "Primero debes crear la planificación mensual "
            f"{mes:02d}/{anio}.",
        )
        return regresar()

    sitios_validos = set(
        SitioPlanificado.objects.filter(
            pk__in=ids,
            planificacion=planificacion,
            activo_en_mes=True,
        ).values_list("pk", flat=True)
    )

    trasladados = 0
    rechazados = []

    for identificador in ids:

        if identificador not in sitios_validos:
            rechazados.append(
                f"ID {identificador}: no pertenece "
                "a los sitios activos de este mes."
            )
            continue

        try:
            with transaction.atomic():

                trasladar_sitio_mes_manual(
                    sitio_planificado_id=identificador,
                    planificacion_origen_id=planificacion.pk,
                    anio_destino=anio,
                    mes_destino=mes,
                    mantener_permiso=mantener_permiso,
                    usuario=request.user,
                )

            trasladados += 1

        except ValidationError as error:
            rechazados.append(
                f"ID {identificador}: "
                f"{'; '.join(error.messages)}"
            )

        except SitioPlanificado.DoesNotExist:
            rechazados.append(
                f"ID {identificador}: el sitio "
                "ya no está disponible."
            )

    if trasladados:
        messages.success(
            request,
            f"Se trasladaron {trasladados} sitio(s) "
            f"a {mes:02d}/{anio}.",
        )

    if rechazados:
        muestra = rechazados[:8]

        detalle = " | ".join(muestra)

        if len(rechazados) > len(muestra):
            detalle += (
                f" | Y {len(rechazados) - len(muestra)} "
                "rechazo(s) adicionales."
            )

        messages.warning(
            request,
            f"No se trasladaron {len(rechazados)} sitio(s). "
            f"{detalle}",
        )

    return regresar()

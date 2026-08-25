from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.forms.cuadrillas import CuadrillaOperativaForm
from planificacion.modelos import CuadrillaOperativa
from planificacion.services.geocodificacion_cuadrillas import (
    ErrorGeocodificacionCuadrilla, geocodificar_direccion_cuadrilla)
from usuarios.decoradores import rol_requerido

ROLES_PLANIFICACION = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# LISTA
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
def listar_cuadrillas(
    request,
):
    cuadrillas = CuadrillaOperativa.objects.prefetch_related(
        "integrantes",
    ).order_by(
        "orden",
        "nombre",
        "id",
    )

    return render(
        request,
        "planificacion/cuadrillas/lista.html",
        {
            "cuadrillas": cuadrillas,
        },
    )


# ============================================================
# CREAR
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
@transaction.atomic
def crear_cuadrilla(
    request,
):
    if request.method == "POST":

        form = CuadrillaOperativaForm(
            request.POST,
        )

        if form.is_valid():

            cuadrilla = form.save(commit=False)

            cuadrilla.creado_por = request.user

            cuadrilla.actualizado_por = request.user

            direccion = (cuadrilla.direccion_base or "").strip()

            if direccion:

                try:

                    geocodificacion = geocodificar_direccion_cuadrilla(direccion)

                except ErrorGeocodificacionCuadrilla as exc:

                    form.add_error(
                        "direccion_base",
                        str(exc),
                    )

                else:

                    cuadrilla.base_latitud = geocodificacion["latitud"]

                    cuadrilla.base_longitud = geocodificacion["longitud"]

            if not form.errors:

                cuadrilla.save()

                form.save_m2m()

                messages.success(
                    request,
                    "Cuadrilla creada correctamente.",
                )

                return redirect("planificacion:listar_cuadrillas")

    else:

        form = CuadrillaOperativaForm(
            initial={
                "activa": True,
                "permite_urbano": True,
                "minutos_jornada_default": 540,
                "minutos_trabajo_sitio_default": 165,
            }
        )

    return render(
        request,
        "planificacion/cuadrillas/formulario.html",
        {
            "form": form,
            "titulo": "Nueva cuadrilla",
            "modo": "crear",
            "cuadrilla": None,
        },
    )


# ============================================================
# EDITAR
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
@transaction.atomic
def editar_cuadrilla(
    request,
    pk,
):
    cuadrilla = get_object_or_404(
        CuadrillaOperativa,
        pk=pk,
    )

    direccion_anterior = (cuadrilla.direccion_base or "").strip()

    if request.method == "POST":

        form = CuadrillaOperativaForm(
            request.POST,
            instance=cuadrilla,
        )

        if form.is_valid():

            objeto = form.save(commit=False)

            objeto.actualizado_por = request.user

            direccion_nueva = (objeto.direccion_base or "").strip()

            direccion_cambio = direccion_nueva != direccion_anterior

            requiere_geocodificacion = bool(direccion_nueva) and (
                direccion_cambio
                or objeto.base_latitud is None
                or objeto.base_longitud is None
            )

            if requiere_geocodificacion:

                try:

                    geocodificacion = geocodificar_direccion_cuadrilla(direccion_nueva)

                except ErrorGeocodificacionCuadrilla as exc:

                    form.add_error(
                        "direccion_base",
                        str(exc),
                    )

                else:

                    objeto.base_latitud = geocodificacion["latitud"]

                    objeto.base_longitud = geocodificacion["longitud"]

            elif not direccion_nueva:

                objeto.base_latitud = None

                objeto.base_longitud = None

            if not form.errors:

                objeto.save()

                form.save_m2m()

                messages.success(
                    request,
                    "Cuadrilla actualizada correctamente.",
                )

                return redirect("planificacion:listar_cuadrillas")

    else:

        form = CuadrillaOperativaForm(
            instance=cuadrilla,
        )

    return render(
        request,
        "planificacion/cuadrillas/formulario.html",
        {
            "form": form,
            "titulo": "Editar cuadrilla",
            "modo": "editar",
            "cuadrilla": cuadrilla,
        },
    )


# ============================================================
# ACTIVAR / DESACTIVAR
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def cambiar_estado_cuadrilla(
    request,
    pk,
):
    cuadrilla = get_object_or_404(
        CuadrillaOperativa,
        pk=pk,
    )

    cuadrilla.activa = not cuadrilla.activa

    cuadrilla.actualizado_por = request.user

    cuadrilla.save(
        update_fields=[
            "activa",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    if cuadrilla.activa:

        mensaje = "Cuadrilla activada correctamente."

    else:

        mensaje = "Cuadrilla desactivada correctamente."

    messages.success(
        request,
        mensaje,
    )

    return redirect("planificacion:listar_cuadrillas")


# ============================================================
# REGEOCODIFICAR
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def regeocodificar_cuadrilla(
    request,
    pk,
):
    cuadrilla = get_object_or_404(
        CuadrillaOperativa,
        pk=pk,
    )

    if not cuadrilla.direccion_base:

        messages.error(
            request,
            "La cuadrilla no posee una dirección base.",
        )

        return redirect("planificacion:listar_cuadrillas")

    try:

        resultado = geocodificar_direccion_cuadrilla(cuadrilla.direccion_base)

    except ErrorGeocodificacionCuadrilla as exc:

        messages.error(
            request,
            str(exc),
        )

    else:

        cuadrilla.base_latitud = resultado["latitud"]

        cuadrilla.base_longitud = resultado["longitud"]

        cuadrilla.actualizado_por = request.user

        cuadrilla.save(
            update_fields=[
                "base_latitud",
                "base_longitud",
                "actualizado_por",
                "actualizado_en",
            ]
        )

        messages.success(
            request,
            "Coordenadas actualizadas correctamente.",
        )

    return redirect("planificacion:listar_cuadrillas")

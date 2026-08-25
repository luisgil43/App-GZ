from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.models import (BatchPlanificacionSemanal,
                                  PlanificacionMensual)
from planificacion.services.completar_semana_anterior import (
    confirmar_sitios_para_completar_semana,
    generar_recomendacion_completar_semana, obtener_batches_completables)
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
# ELEGIR SEMANAS
# ============================================================


@rol_requerido(*ROLES_PLANIFICACION)
def completar_semana_anterior(
    request,
    mensual_id,
):
    """
    Presenta todas las semanas operacionales que todavía
    pueden recibir sitios desde la nueva asignación mensual.

    El usuario puede:

    - seleccionar una semana;
    - seleccionar varias;
    - no seleccionar ninguna y continuar.
    """

    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    opciones = obtener_batches_completables(
        planificacion_nueva=mensual,
    )

    return render(
        request,
        ("planificacion/" "completar_semana_anterior/" "seleccionar_semana.html"),
        {
            "mensual": mensual,
            "opciones": opciones,
        },
    )


# ============================================================
# ANALIZAR SEMANAS SELECCIONADAS
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def analizar_completar_semana(
    request,
    mensual_id,
):
    """
    Analiza todas las semanas seleccionadas por el usuario.

    Las recomendaciones se generan secuencialmente para que
    un mismo sitio del nuevo mes nunca sea recomendado para
    dos semanas distintas.
    """

    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    # ========================================================
    # CONTINUAR SIN COMPLETAR
    # ========================================================

    accion = (
        request.POST.get(
            "accion",
        )
        or ""
    )

    if accion == "continuar":

        messages.info(
            request,
            (
                "La asignación mensual se mantiene sin "
                "incorporar sitios a semanas anteriores."
            ),
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=mensual.pk,
        )

    # ========================================================
    # IDS SOLICITADOS
    # ========================================================

    batch_ids_raw = request.POST.getlist(
        "batch_ids",
    )

    batch_ids = []

    for valor in batch_ids_raw:

        try:

            batch_id = int(
                valor,
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if batch_id not in batch_ids:

            batch_ids.append(batch_id)

    # ========================================================
    # NINGUNA SEMANA SELECCIONADA
    # ========================================================

    if not batch_ids:

        messages.info(
            request,
            (
                "No seleccionaste ninguna semana. "
                "La asignación mensual continuará sin "
                "incorporar sitios a semanas anteriores."
            ),
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=mensual.pk,
        )

    # ========================================================
    # SEMANAS QUE REALMENTE SIGUEN SIENDO COMPLETABLES
    # ========================================================

    opciones_validas = obtener_batches_completables(
        planificacion_nueva=mensual,
    )

    opciones_por_batch = {opcion["batch"].pk: opcion for opcion in opciones_validas}

    # ========================================================
    # VALIDAR SELECCIÓN
    # ========================================================

    ids_invalidos = [
        batch_id for batch_id in batch_ids if batch_id not in opciones_por_batch
    ]

    if ids_invalidos:

        messages.error(
            request,
            (
                "Una de las semanas seleccionadas ya no "
                "está disponible para recibir sitios. "
                "La planificación fue actualizada; revisa "
                "nuevamente las opciones."
            ),
        )

        return redirect(
            "planificacion:completar_semana_anterior",
            mensual_id=mensual.pk,
        )

    # ========================================================
    # RESPETAR ORDEN OPERACIONAL
    # ========================================================
    #
    # Primero semana actual.
    # Después semana siguiente.
    #
    # Esto es importante porque la primera recomendación
    # reserva candidatos antes de generar la siguiente.
    # ========================================================

    opciones_seleccionadas = [opciones_por_batch[batch_id] for batch_id in batch_ids]

    opciones_seleccionadas.sort(
        key=lambda opcion: (
            opcion["batch"].fecha_inicio,
            opcion["batch"].pk,
        )
    )

    # ========================================================
    # GENERAR RECOMENDACIONES SIN REPETIR SITIOS
    # ========================================================

    resultados = []

    sitios_reservados = set()

    for opcion in opciones_seleccionadas:

        batch = opcion["batch"]

        resultado = generar_recomendacion_completar_semana(
            planificacion_nueva=mensual,
            batch_destino=batch,
            excluir_sitio_planificado_ids=(sitios_reservados),
        )

        recomendados = (
            resultado.get(
                "recomendados",
                [],
            )
            or []
        )

        for sitio in recomendados:

            sitio_id = sitio.get("sitio_planificado_id")

            if sitio_id:

                sitios_reservados.add(int(sitio_id))

        resultados.append(
            {
                "batch": batch,
                "resultado": resultado,
                "diagnostico": (resultado["diagnostico"]),
                "recomendados": recomendados,
            }
        )

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        ("planificacion/" "completar_semana_anterior/" "recomendacion.html"),
        {
            "mensual": mensual,
            "resultados": resultados,
        },
    )


# ============================================================
# CONFIRMAR MÚLTIPLES SEMANAS
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION)
def confirmar_completar_semanas(
    request,
    mensual_id,
):
    """
    Confirma todas las selecciones realizadas en la pantalla
    de recomendaciones.

    Una semana puede quedar sin sitios seleccionados.

    Si el usuario desmarca todos los sitios de todas las
    semanas, simplemente continúa sin incorporar ninguno.
    """

    mensual = get_object_or_404(
        PlanificacionMensual,
        pk=mensual_id,
    )

    # ========================================================
    # BATCHES INCLUIDOS EN EL FORMULARIO
    # ========================================================

    batch_ids_raw = request.POST.getlist(
        "batch_ids",
    )

    batch_ids = []

    for valor in batch_ids_raw:

        try:

            batch_id = int(
                valor,
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if batch_id not in batch_ids:

            batch_ids.append(batch_id)

    if not batch_ids:

        messages.info(
            request,
            "No se seleccionaron semanas para completar.",
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=mensual.pk,
        )

    # ========================================================
    # VALIDAR SEMANAS ACTUALES
    # ========================================================

    opciones_validas = obtener_batches_completables(
        planificacion_nueva=mensual,
    )

    batches_validos = {
        opcion["batch"].pk: (opcion["batch"]) for opcion in opciones_validas
    }

    # ========================================================
    # RECOGER SELECCIONES
    # ========================================================

    selecciones = []

    ids_globales = set()

    for batch_id in batch_ids:

        if batch_id not in batches_validos:

            messages.error(
                request,
                (
                    "Una de las semanas seleccionadas "
                    "ya no puede recibir sitios. "
                    "Vuelve a revisar la recomendación."
                ),
            )

            return redirect(
                "planificacion:completar_semana_anterior",
                mensual_id=mensual.pk,
            )

        campo = f"sitio_ids_{batch_id}"

        ids_raw = request.POST.getlist(
            campo,
        )

        ids = []

        for valor in ids_raw:

            try:

                sitio_id = int(
                    valor,
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            # ================================================
            # EVITAR REPETIR SITIO ENTRE SEMANAS
            # ================================================

            if sitio_id in ids_globales:

                messages.error(
                    request,
                    (
                        "Un mismo sitio fue seleccionado "
                        "para más de una semana. "
                        "Actualiza la recomendación y "
                        "vuelve a intentarlo."
                    ),
                )

                return redirect(
                    "planificacion:completar_semana_anterior",
                    mensual_id=mensual.pk,
                )

            ids_globales.add(sitio_id)

            ids.append(sitio_id)

        if not ids:
            continue

        selecciones.append(
            {
                "batch": (batches_validos[batch_id]),
                "ids": ids,
            }
        )

    # ========================================================
    # USUARIO DESMARCÓ TODO
    # ========================================================

    if not selecciones:

        messages.info(
            request,
            ("No se incorporó ningún sitio a las " "semanas anteriores."),
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=mensual.pk,
        )

    # ========================================================
    # CONFIRMAR TODO DE FORMA ATÓMICA
    # ========================================================

    resultados_confirmacion = []

    try:

        with transaction.atomic():

            for seleccion in selecciones:

                batch = seleccion["batch"]

                resultado = confirmar_sitios_para_completar_semana(
                    planificacion_nueva=mensual,
                    batch_destino=batch,
                    sitio_planificado_ids=(seleccion["ids"]),
                    usuario=request.user,
                )

                resultados_confirmacion.append(
                    {
                        "batch": batch,
                        "resultado": resultado,
                    }
                )

    except ValueError as exc:

        messages.error(
            request,
            str(exc),
        )

        return redirect(
            "planificacion:completar_semana_anterior",
            mensual_id=mensual.pk,
        )

    # ========================================================
    # MENSAJE FINAL
    # ========================================================

    partes = []

    total_incorporados = 0

    for item in resultados_confirmacion:

        batch = item["batch"]

        resultado = item["resultado"]

        cantidad = resultado["cantidad_creados"]

        total_incorporados += cantidad

        partes.append((f"{batch.codigo_semana}: " f"{cantidad} sitio(s)"))

    messages.success(
        request,
        (
            f"Se incorporaron "
            f"{total_incorporados} sitio(s) "
            "a la planificación operacional. "
            + " · ".join(partes)
            + ". Los sitios continúan perteneciendo "
            f"a {mensual}."
        ),
    )

    # ========================================================
    # VOLVER AL MES NUEVO
    # ========================================================

    return redirect(
        "planificacion:lista_asignacion_mensual",
        pk=mensual.pk,
    )

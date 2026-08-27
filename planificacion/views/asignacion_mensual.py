import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from planificacion.forms.asignacion_mensual import \
    ImportarAsignacionMensualForm
from planificacion.models import (BatchPlanificacionSemanal, ContactoSitio,
                                  PlanificacionMensual, SitioBatchSemanal,
                                  SitioPlanificado)
from planificacion.services.asignacion_mensual_importer import (
    aplicar_importacion_asignacion, generar_preview_asignacion,
    leer_excel_asignacion)
from planificacion.services.completar_semana_anterior import \
    obtener_batches_completables
from planificacion.services.mover_semana import \
    asignar_o_mover_sitio_planificado_a_semana
from usuarios.decoradores import rol_requerido

CACHE_TIMEOUT = 60 * 30


def _preview_cache_key(
    user_id,
    token,
):
    return "gz:planificacion:" f"asignacion_mensual:" f"{user_id}:{token}"


# ============================================================
# LISTA DE ASIGNACIÓN MENSUAL
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def lista_asignacion_mensual(
    request,
    pk,
):
    planificacion = get_object_or_404(
        PlanificacionMensual,
        pk=pk,
    )

    # ========================================================
    # CONTACTOS ACTIVOS
    # ========================================================

    contactos_prefetch = Prefetch(
        "sitio__contactos_planificacion",
        queryset=(
            ContactoSitio.objects.filter(
                activo=True,
            )
            .select_related(
                "actualizado_por",
            )
            .order_by(
                "prioridad_contacto",
                "id",
            )
        ),
        to_attr="contactos_planificacion_cargados",
    )

    # ========================================================
    # SITIOS DEL MES
    # ========================================================

    sitios = (
        SitioPlanificado.objects.filter(
            planificacion=planificacion,
            activo_en_mes=True,
        )
        .select_related(
            "sitio",
        )
        .prefetch_related(
            contactos_prefetch,
        )
        .order_by(
            "sitio__region",
            "sitio__comuna",
            "sitio__id_claro",
        )
    )

    # ========================================================
    # SEMANAS ANTERIORES / OPERACIONALES COMPLETABLES
    # ========================================================

    opciones_completar = obtener_batches_completables(
        planificacion_nueva=planificacion,
    )

    # ========================================================
    # SEMANAS DESCARTADAS MANUALMENTE
    # ========================================================
    #
    # La X de continuidad solamente oculta la recomendación
    # para ESTA planificación mensual.
    #
    # No elimina:
    #
    # - el batch;
    # - sus sitios;
    # - la planificación diaria;
    # - Operaciones.
    #
    # Guardamos los PK de los batches como strings para que
    # funcione igual aunque existan datos antiguos guardados
    # como enteros.
    # ========================================================

    semanas_descartadas = {
        str(valor) for valor in (planificacion.continuidad_semanal_descartada or [])
    }

    opciones_completar = [
        opcion
        for opcion in opciones_completar
        if str(opcion["batch"].pk) not in semanas_descartadas
    ]

    # ========================================================
    # OPORTUNIDAD PRINCIPAL
    # ========================================================

    oportunidad_principal = opciones_completar[0] if opciones_completar else None

    # ========================================================
    # SEMANAS DISPONIBLES PARA MOVIMIENTO MASIVO
    # ========================================================
    #
    # La semana es GLOBAL.
    #
    # Por tanto mostramos cualquier batch semanal existente,
    # independientemente del mes que lo creó originalmente.
    #
    # Solo excluimos semanas canceladas.
    # ========================================================

    batches_destino_movimiento = BatchPlanificacionSemanal.objects.exclude(
        estado="cancelado",
    ).order_by(
        "-fecha_inicio",
        "-id",
    )

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "planificacion/asignacion_mensual/lista.html",
        {
            "planificacion": planificacion,
            "sitios": sitios,
            "opciones_completar": opciones_completar,
            "oportunidad_completar": oportunidad_principal,
            "batches_destino_movimiento": (batches_destino_movimiento),
        },
    )

# ============================================================
# DESCARTAR RECOMENDACIÓN DE CONTINUIDAD SEMANAL
# ============================================================


@login_required
@require_POST
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def descartar_continuidad_semanal(
    request,
    pk,
    batch_id,
):
    """
    Descarta de forma persistente una recomendación de
    continuidad operacional para una planificación mensual.

    IMPORTANTE
    ==========================================================

    Esta acción NO modifica:

    - BatchPlanificacionSemanal;
    - SitioBatchSemanal;
    - SitioPlanificado;
    - planificación diaria;
    - Operaciones;
    - permisos;
    - rutas;
    - sesiones;
    - evidencias.

    Únicamente registra que esta planificación mensual ya no
    desea volver a mostrar la recomendación de completar ese
    batch.
    """

    # ========================================================
    # PLANIFICACIÓN MENSUAL
    # ========================================================

    planificacion = get_object_or_404(
        PlanificacionMensual,
        pk=pk,
    )

    # ========================================================
    # BATCH
    # ========================================================

    batch = get_object_or_404(
        BatchPlanificacionSemanal,
        pk=batch_id,
    )

    # ========================================================
    # LISTA ACTUAL DE DESCARTADAS
    # ========================================================

    descartadas = list(planificacion.continuidad_semanal_descartada or [])

    batch_id_normalizado = str(
        batch.pk,
    )

    descartadas_normalizadas = {str(valor) for valor in descartadas}

    # ========================================================
    # AGREGAR SOLO SI TODAVÍA NO ESTÁ DESCARTADA
    # ========================================================

    if batch_id_normalizado not in descartadas_normalizadas:

        descartadas.append(
            batch_id_normalizado,
        )

        planificacion.continuidad_semanal_descartada = descartadas

        planificacion.actualizado_por = request.user

        planificacion.save(
            update_fields=[
                "continuidad_semanal_descartada",
                "actualizado_por",
                "actualizado_en",
            ]
        )

    # ========================================================
    # MENSAJE
    # ========================================================

    messages.success(
        request,
        (
            f"La recomendación para completar "
            f"{batch.codigo_semana} fue descartada. "
            "La semana y sus sitios no fueron modificados."
        ),
    )

    # ========================================================
    # VOLVER AL MES
    # ========================================================

    return redirect(
        "planificacion:lista_asignacion_mensual",
        pk=planificacion.pk,
    )


# ============================================================
# MOVER SITIOS SELECCIONADOS A OTRA SEMANA
# ============================================================


@login_required
@require_POST
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def mover_sitios_semana_masivo(
    request,
    pk,
):
    """
    Asigna o mueve varios sitios seleccionados desde la
    pantalla de planificación mensual hacia una semana
    operacional global.

    SOPORTA DOS ESCENARIOS
    ==========================================================

    1. SITIO SIN SEMANA ACTIVA

        SitioPlanificado
            ->
        Semana destino

    Se crea su primera participación semanal.

    2. SITIO CON SEMANA ACTIVA

        Semana origen
            ->
        Semana destino

    Se mueve utilizando toda la lógica operacional existente.

    IMPORTANTE
    ==========================================================

    Esta vista NO implementa nuevamente la lógica de
    asignación/movimiento.

    Cada sitio pasa individualmente por:

        asignar_o_mover_sitio_planificado_a_semana()

    Por tanto el servicio decide si debe:

    - crear su primera participación semanal;
    - reutilizar una participación histórica;
    - mover una participación semanal activa;
    - desmontar planificación diaria anterior;
    - reiniciar Operaciones cuando corresponde;
    - conservar estados protegidos;
    - mantener la planificación mensual de origen;
    - registrar planificaciones_origen;
    - prevenir duplicados.

    La semana operacional es GLOBAL y no está restringida
    por el mes original del sitio.
    """

    # ========================================================
    # 1. PLANIFICACIÓN MENSUAL
    # ========================================================

    planificacion = get_object_or_404(
        PlanificacionMensual,
        pk=pk,
    )

    # ========================================================
    # 2. SITIOS SELECCIONADOS
    # ========================================================

    sitio_ids = request.POST.getlist(
        "sitio_ids",
    )

    if not sitio_ids:

        messages.warning(
            request,
            "Debes seleccionar al menos un sitio.",
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=planificacion.pk,
        )

    # ========================================================
    # 3. SEMANA DESTINO
    # ========================================================

    batch_destino_id = request.POST.get(
        "batch_destino_id",
    )

    try:

        batch_destino_id = int(
            batch_destino_id,
        )

    except (
        TypeError,
        ValueError,
    ):

        messages.error(
            request,
            "Debes seleccionar una semana destino válida.",
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=planificacion.pk,
        )

    batch_destino = get_object_or_404(
        BatchPlanificacionSemanal.objects.exclude(
            estado="cancelado",
        ),
        pk=batch_destino_id,
    )

    # ========================================================
    # 4. SITIOS VÁLIDOS DE ESTA PLANIFICACIÓN MENSUAL
    # ========================================================

    sitios_planificados = list(
        SitioPlanificado.objects.filter(
            pk__in=sitio_ids,
            planificacion=planificacion,
            activo_en_mes=True,
        )
        .select_related(
            "sitio",
        )
        .order_by(
            "id",
        )
    )

    if not sitios_planificados:

        messages.warning(
            request,
            "No se encontraron sitios válidos para asignar.",
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=planificacion.pk,
        )

    # ========================================================
    # 5. CONTADORES
    # ========================================================

    procesados = 0

    asignados = 0

    movidos = 0

    omitidos = 0

    errores = []

    # ========================================================
    # 6. PROCESAR CADA SITIO
    # ========================================================

    for sitio_planificado in sitios_planificados:

        identificador = (
            sitio_planificado.sitio.id_claro
            or sitio_planificado.sitio.id_sites
            or f"Sitio {sitio_planificado.sitio_id}"
        )

        try:

            resultado = asignar_o_mover_sitio_planificado_a_semana(
                sitio_planificado_id=sitio_planificado.pk,
                batch_destino_id=batch_destino.pk,
                usuario=request.user,
            )

        except ValidationError as exc:

            omitidos += 1

            for mensaje in exc.messages:

                errores.append(f"{identificador}: {mensaje}")

            continue

        except (
            SitioPlanificado.DoesNotExist,
            SitioBatchSemanal.DoesNotExist,
            BatchPlanificacionSemanal.DoesNotExist,
        ):

            omitidos += 1

            errores.append(
                (
                    f"{identificador}: la planificación "
                    "cambió mientras se realizaba la "
                    "asignación."
                )
            )

            continue

        except Exception as exc:

            omitidos += 1

            errores.append(
                (f"{identificador}: no fue posible " f"asignarlo. Detalle: {exc}")
            )

            continue

        # ====================================================
        # 7. CLASIFICAR RESULTADO
        # ====================================================

        procesados += 1

        if resultado.get(
            "era_primera_asignacion_semanal",
            False,
        ):

            asignados += 1

        else:

            movidos += 1

    # ========================================================
    # 8. MENSAJE PRINCIPAL
    # ========================================================

    if procesados:

        partes = []

        if asignados:

            partes.append(f"{asignados} asignado(s)")

        if movidos:

            partes.append(f"{movidos} movido(s)")

        detalle = " y ".join(
            partes,
        )

        messages.success(
            request,
            (
                f"{procesados} sitio(s) procesado(s) "
                f"correctamente hacia "
                f"{batch_destino.codigo_semana}: "
                f"{detalle}."
            ),
        )

    # ========================================================
    # 9. SITIOS OMITIDOS
    # ========================================================

    if omitidos:

        messages.warning(
            request,
            (f"{omitidos} sitio(s) no pudieron " "ser asignados o movidos."),
        )

    # ========================================================
    # 10. DETALLE DE ERRORES
    # ========================================================

    for error in errores[:10]:

        messages.warning(
            request,
            error,
        )

    if len(errores) > 10:

        messages.warning(
            request,
            (f"Existen {len(errores) - 10} " "advertencia(s) adicional(es)."),
        )

    # ========================================================
    # 11. VOLVER A PLANIFICACIÓN MENSUAL
    # ========================================================

    return redirect(
        "planificacion:lista_asignacion_mensual",
        pk=planificacion.pk,
    )


# ============================================================
# IMPORTAR ASIGNACIÓN MENSUAL
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def importar_asignacion_mensual(
    request,
    pk,
):
    planificacion = get_object_or_404(
        PlanificacionMensual,
        pk=pk,
    )

    accion = request.POST.get("accion") or ""

    token = request.POST.get("token") or ""

    # ========================================================
    # CONFIRMAR
    # ========================================================

    if request.method == "POST" and accion == "confirmar":

        if not token:

            messages.error(
                request,
                "No se encontró el preview.",
            )

            return redirect(
                "planificacion:importar_asignacion_mensual",
                pk=planificacion.pk,
            )

        cache_key = _preview_cache_key(
            request.user.id,
            token,
        )

        payload = cache.get(
            cache_key,
        )

        if not payload:

            messages.error(
                request,
                ("El preview expiró. " "Vuelve a subir el archivo."),
            )

            return redirect(
                "planificacion:importar_asignacion_mensual",
                pk=planificacion.pk,
            )

        try:

            # =================================================
            # APLICAR IMPORTACIÓN
            # =================================================

            resultado = aplicar_importacion_asignacion(
                preview=payload["preview"],
                planificacion=planificacion,
                user=request.user,
                nombre_archivo=payload.get(
                    "nombre_archivo",
                    "",
                ),
                nombre_hoja=payload.get(
                    "nombre_hoja",
                    "",
                ),
                columna_id_detectada=payload.get(
                    "columna_id_detectada",
                    "",
                ),
            )

            # =================================================
            # LIMPIAR PREVIEW
            # =================================================

            cache.delete(
                cache_key,
            )

            # =================================================
            # MENSAJE DE IMPORTACIÓN
            # =================================================

            messages.success(
                request,
                (
                    "Asignación mensual aplicada. "
                    f"Nuevos: {resultado['creados']}. "
                    f"Ya existentes: "
                    f"{resultado['ya_existentes']}. "
                    f"No encontrados: "
                    f"{resultado['no_encontrados']}."
                ),
            )

            # =================================================
            # DETECTAR SEMANA OPERACIONAL VIGENTE
            # QUE PUEDA COMPLETARSE
            # =================================================
            #
            # REGLA:
            #
            # No movemos todavía ningún sitio.
            #
            # Solamente comprobamos si existe una semana
            # operacional ACTUAL que:
            #
            # - todavía esté abierta;
            # - corresponda a la semana real vigente;
            # - tenga capacidad por completar;
            # - pueda recibir sitios de esta nueva
            #   planificación mensual.
            #
            # Si existe, enviamos al usuario al flujo donde
            # podrá decidir si desea o no complementar esa
            # semana.
            # =================================================

            opciones_completar = obtener_batches_completables(
                planificacion_nueva=planificacion,
            )

            # =================================================
            # EXISTE SEMANA PARA COMPLETAR
            # =================================================

            if opciones_completar:

                messages.info(
                    request,
                    (
                        "Existe una semana operacional vigente "
                        "que todavía puede completarse con sitios "
                        "de esta nueva asignación. "
                        "Puedes revisar la recomendación antes "
                        "de incorporar cualquier sitio."
                    ),
                )

                return redirect(
                    "planificacion:completar_semana_anterior",
                    mensual_id=planificacion.pk,
                )

            # =================================================
            # NO EXISTE SEMANA PARA COMPLETAR
            # =================================================

            return redirect(
                "planificacion:lista_asignacion_mensual",
                pk=planificacion.pk,
            )

        except Exception as exc:

            messages.error(
                request,
                ("No fue posible aplicar " f"la asignación: {exc}"),
            )

            return redirect(
                "planificacion:importar_asignacion_mensual",
                pk=planificacion.pk,
            )

    # ========================================================
    # CANCELAR
    # ========================================================

    if request.method == "POST" and accion == "cancelar":

        if token:

            cache.delete(
                _preview_cache_key(
                    request.user.id,
                    token,
                )
            )

        messages.info(
            request,
            "Importación cancelada.",
        )

        return redirect(
            "planificacion:lista_asignacion_mensual",
            pk=planificacion.pk,
        )

    # ========================================================
    # ARCHIVO
    # ========================================================

    if request.method == "POST":

        form = ImportarAsignacionMensualForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():

            archivo = form.cleaned_data["archivo"]

            try:

                (
                    df,
                    hoja,
                    columna_id,
                ) = leer_excel_asignacion(
                    archivo,
                )

                (
                    preview,
                    resumen,
                    errores,
                    repetidos,
                ) = generar_preview_asignacion(
                    df,
                    columna_id,
                    planificacion,
                )

                token = uuid.uuid4().hex

                cache_key = _preview_cache_key(
                    request.user.id,
                    token,
                )

                cache.set(
                    cache_key,
                    {
                        "preview": preview,
                        "resumen": resumen,
                        "errores": errores,
                        "repetidos": repetidos,
                        "nombre_archivo": (archivo.name),
                        "nombre_hoja": hoja,
                        "columna_id_detectada": (str(columna_id)),
                    },
                    timeout=CACHE_TIMEOUT,
                )

                return render(
                    request,
                    ("planificacion/" "asignacion_mensual/" "importar.html"),
                    {
                        "planificacion": (planificacion),
                        "form": form,
                        "preview": preview,
                        "resumen": resumen,
                        "errores": errores,
                        "repetidos": repetidos,
                        "token": token,
                        "nombre_archivo": (archivo.name),
                        "nombre_hoja": hoja,
                        "columna_id_detectada": (columna_id),
                    },
                )

            except Exception as exc:

                messages.error(
                    request,
                    ("No fue posible analizar " f"el archivo: {exc}"),
                )

    else:

        form = ImportarAsignacionMensualForm()

    # ========================================================
    # RENDER NORMAL
    # ========================================================

    return render(
        request,
        ("planificacion/" "asignacion_mensual/" "importar.html"),
        {
            "planificacion": planificacion,
            "form": form,
        },
    )

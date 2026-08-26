import uuid
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect, render

from planificacion.forms.contactos import ImportarContactosForm
from planificacion.models import ImportacionContactosSitios
from planificacion.services.contactos_importer import (
    aplicar_importacion_contactos, generar_preview_contactos,
    leer_excel_contactos)
from usuarios.decoradores import rol_requerido

# ============================================================
# CONFIGURACIÓN
# ============================================================


CACHE_TIMEOUT_PREVIEW = 60 * 30
PREVIEW_FILAS_POR_PAGINA = 100

# ============================================================
# CACHE
# ============================================================


def _contactos_preview_cache_key(
    user_id,
    token,
):
    """
    Clave única del preview de importación por usuario.

    Evita que un usuario pueda confirmar accidentalmente
    el preview generado por otro.
    """

    return f"gz:planificacion:" f"contactos_preview:" f"{user_id}:{token}"


# ============================================================
# ÚLTIMA IMPORTACIÓN
# ============================================================


def _ultima_importacion_contactos():
    """
    Retorna la última importación que fue efectivamente aplicada.
    """

    return (
        ImportacionContactosSitios.objects.filter(
            estado="aplicada",
        )
        .select_related(
            "creado_por",
        )
        .order_by(
            "-aplicado_en",
            "-id",
        )
        .first()
    )


# ============================================================
# RESUMEN DE IDS NO VINCULADOS
# ============================================================


def _obtener_ids_no_vinculados(preview):
    """
    Analiza TODO el preview y devuelve los IDs que no encontraron
    coincidencia en SitioMovil.

    IMPORTANTE:
    Un ID puede aparecer varias veces en la planilla porque un sitio
    puede tener varios contactos.

    Ejemplo:

        08_899 -> fila 100
        08_899 -> fila 101
        08_899 -> fila 102
        07_024 -> fila 150

    Resultado:

        filas_no_vinculadas = 4
        ids_unicos_no_vinculados = 2

        [
            {
                "id_origen": "08_899",
                "cantidad": 3,
                "filas": [100, 101, 102],
            },
            {
                "id_origen": "07_024",
                "cantidad": 1,
                "filas": [150],
            },
        ]

    No modifica SitioMovil.
    Solo trabaja con el preview ya generado.
    """

    agrupados = OrderedDict()

    for item in preview:
        if item.get("vinculado"):
            continue

        id_origen = str(item.get("id_origen") or "").strip()

        if not id_origen:
            id_origen = "ID vacío"

        fila = item.get("fila")

        if id_origen not in agrupados:
            agrupados[id_origen] = {
                "id_origen": id_origen,
                "cantidad": 0,
                "filas": [],
            }

        agrupados[id_origen]["cantidad"] += 1

        if fila is not None:
            agrupados[id_origen]["filas"].append(fila)

    ids = list(agrupados.values())

    # Ordenamos alfabéticamente por ID para facilitar revisión.
    ids.sort(key=lambda item: item["id_origen"].lower())

    filas_no_vinculadas = sum(item["cantidad"] for item in ids)

    ids_unicos_no_vinculados = len(ids)

    return {
        "ids": ids,
        "filas_no_vinculadas": (filas_no_vinculadas),
        "ids_unicos_no_vinculados": (ids_unicos_no_vinculados),
    }


# ============================================================
# CONTEXTO COMÚN DE PREVIEW
# ============================================================


def _construir_contexto_preview(
    *,
    form,
    preview,
    resumen,
    errores,
    sheet_name,
    token,
    nombre_archivo,
    pagina=1,
):
    """
    Construye el contexto visual del preview.

    IMPORTANTE
    ==========================================================

    El archivo completo se analiza y permanece disponible
    para confirmar la importación.

    Sin embargo, para evitar consumir memoria innecesaria
    renderizando miles de filas HTML simultáneamente,
    solamente mostramos una página del preview.

    Esto NO limita:

        - el análisis;
        - los totales;
        - la detección de cambios;
        - los IDs sin vincular;
        - la confirmación final.

    Solamente limita las filas visibles simultáneamente.
    """

    preview = list(preview or [])

    total_registros = len(
        preview,
    )

    # ========================================================
    # IDS SIN VINCULAR
    # ========================================================

    no_vinculados = _obtener_ids_no_vinculados(
        preview,
    )

    # ========================================================
    # PAGINACIÓN
    # ========================================================

    try:
        pagina = int(
            pagina,
        )

    except (
        TypeError,
        ValueError,
    ):
        pagina = 1

    pagina = max(
        pagina,
        1,
    )

    if total_registros > 0:

        total_paginas = (
            total_registros + PREVIEW_FILAS_POR_PAGINA - 1
        ) // PREVIEW_FILAS_POR_PAGINA

    else:

        total_paginas = 1

    pagina = min(
        pagina,
        total_paginas,
    )

    indice_inicio = (pagina - 1) * PREVIEW_FILAS_POR_PAGINA

    indice_fin = min(
        indice_inicio + PREVIEW_FILAS_POR_PAGINA,
        total_registros,
    )

    preview_pagina = preview[indice_inicio:indice_fin]

    # ========================================================
    # NAVEGACIÓN
    # ========================================================

    pagina_anterior = pagina - 1 if pagina > 1 else None

    pagina_siguiente = pagina + 1 if pagina < total_paginas else None

    # Ventana de páginas.
    #
    # Ejemplo:
    #
    #   1 2 3 4 5
    #
    # o:
    #
    #   24 25 26 27 28
    #
    radio_paginas = 2

    pagina_desde = max(
        pagina - radio_paginas,
        1,
    )

    pagina_hasta = min(
        pagina + radio_paginas,
        total_paginas,
    )

    paginas_visibles = list(
        range(
            pagina_desde,
            pagina_hasta + 1,
        )
    )

    # ========================================================
    # FILAS VISIBLES
    # ========================================================

    mostrando_desde = indice_inicio + 1 if total_registros else 0

    mostrando_hasta = indice_fin

    # ========================================================
    # CONTEXTO
    # ========================================================

    return {
        "form": form,
        # ----------------------------------------------------
        # PREVIEW VISUAL PAGINADO
        # ----------------------------------------------------
        "preview": preview_pagina,
        "preview_total": total_registros,
        "preview_mostrados": len(
            preview_pagina,
        ),
        "preview_limitado": (total_registros > PREVIEW_FILAS_POR_PAGINA),
        # ----------------------------------------------------
        # PAGINACIÓN
        # ----------------------------------------------------
        "pagina_actual": pagina,
        "total_paginas": total_paginas,
        "pagina_anterior": pagina_anterior,
        "pagina_siguiente": pagina_siguiente,
        "paginas_visibles": paginas_visibles,
        "mostrando_desde": mostrando_desde,
        "mostrando_hasta": mostrando_hasta,
        # ----------------------------------------------------
        # RESUMEN
        # ----------------------------------------------------
        "resumen": resumen,
        # ----------------------------------------------------
        # IDS QUE NO VINCULARON
        # ----------------------------------------------------
        "ids_no_vinculados": (no_vinculados["ids"]),
        "filas_no_vinculadas": (no_vinculados["filas_no_vinculadas"]),
        "ids_unicos_no_vinculados": (no_vinculados["ids_unicos_no_vinculados"]),
        # ----------------------------------------------------
        # ERRORES
        # ----------------------------------------------------
        "errores": errores[:100],
        "errores_total": len(
            errores,
        ),
        # ----------------------------------------------------
        # ARCHIVO
        # ----------------------------------------------------
        "sheet_name": sheet_name,
        "token": token,
        "nombre_archivo": nombre_archivo,
        # ----------------------------------------------------
        # AUDITORÍA
        # ----------------------------------------------------
        "ultima_importacion": (_ultima_importacion_contactos()),
    }


# ============================================================
# IMPORTAR CONTACTOS
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor")
def importar_contactos(request):
    """
    Flujo completo:

    1. Subir Excel.
    2. Analizar todas las filas.
    3. Buscar coincidencias contra SitioMovil.
    4. Guardar el preview completo temporalmente.
    5. Mostrar el preview paginado.
    6. Informar IDs no vinculados.
    7. Confirmar o cancelar.
    8. Aplicar todos los registros del preview.

    IMPORTANTE
    ==========================================================

    SitioMovil se utiliza únicamente como tabla maestra
    de consulta/vinculación.

    Esta vista NO modifica SitioMovil.

    La paginación afecta solamente la visualización.

    Aunque solamente mostremos 100 registros simultáneamente,
    la confirmación procesa TODO el preview.
    """

    accion = (
        request.POST.get(
            "accion",
        )
        or ""
    )

    token = (
        request.POST.get(
            "token",
        )
        or request.GET.get(
            "token",
        )
        or ""
    )

    # ========================================================
    # CONFIRMAR IMPORTACIÓN
    # ========================================================

    if request.method == "POST" and accion == "confirmar":

        if not token:

            messages.error(
                request,
                ("No se encontró el preview " "de importación."),
            )

            return redirect("planificacion:importar_contactos")

        cache_key = _contactos_preview_cache_key(
            request.user.id,
            token,
        )

        payload = cache.get(
            cache_key,
        )

        if not payload:

            messages.error(
                request,
                ("El preview expiró o ya no existe. " "Vuelve a subir el archivo."),
            )

            return redirect("planificacion:importar_contactos")

        preview_completo = (
            payload.get(
                "preview",
            )
            or []
        )

        try:

            resultado = aplicar_importacion_contactos(
                preview=preview_completo,
                user=request.user,
                nombre_archivo=(
                    payload.get(
                        "nombre_archivo",
                        "",
                    )
                ),
            )

            cache.delete(
                cache_key,
            )

            messages.success(
                request,
                (
                    "Base de contactos actualizada correctamente. "
                    f"Nuevos: {resultado['creados']}. "
                    f"Actualizados: {resultado['actualizados']}. "
                    f"Sin cambios: {resultado['sin_cambios']}. "
                    f"Filas sin vincular: "
                    f"{resultado['no_vinculados']}."
                ),
            )

            return redirect("planificacion:listar_contactos")

        except Exception as exc:

            messages.error(
                request,
                ("No fue posible aplicar " f"la importación: {exc}"),
            )

            return redirect("planificacion:importar_contactos")

    # ========================================================
    # CANCELAR PREVIEW
    # ========================================================

    if request.method == "POST" and accion == "cancelar":

        if token:

            cache.delete(
                _contactos_preview_cache_key(
                    request.user.id,
                    token,
                )
            )

        messages.info(
            request,
            "Importación cancelada.",
        )

        return redirect("planificacion:listar_contactos")

    # ========================================================
    # NAVEGAR ENTRE PÁGINAS DEL PREVIEW
    # ========================================================

    if request.method == "GET" and token:

        cache_key = _contactos_preview_cache_key(
            request.user.id,
            token,
        )

        payload = cache.get(
            cache_key,
        )

        if not payload:

            messages.error(
                request,
                ("El preview expiró o ya no existe. " "Vuelve a subir el archivo."),
            )

            return redirect("planificacion:importar_contactos")

        try:

            pagina = int(
                request.GET.get(
                    "pagina",
                    1,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            pagina = 1

        form = ImportarContactosForm()

        return render(
            request,
            ("planificacion/" "contactos/" "importar.html"),
            _construir_contexto_preview(
                form=form,
                preview=(
                    payload.get(
                        "preview",
                    )
                    or []
                ),
                resumen=(
                    payload.get(
                        "resumen",
                    )
                    or {}
                ),
                errores=(
                    payload.get(
                        "errores",
                    )
                    or []
                ),
                sheet_name=(
                    payload.get(
                        "sheet_name",
                        "",
                    )
                ),
                token=token,
                nombre_archivo=(
                    payload.get(
                        "nombre_archivo",
                        "",
                    )
                ),
                pagina=pagina,
            ),
        )

    # ========================================================
    # SUBIR ARCHIVO / GENERAR PREVIEW
    # ========================================================

    if request.method == "POST":

        form = ImportarContactosForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():

            archivo = form.cleaned_data["archivo"]

            try:

                # =============================================
                # LEER EXCEL
                # =============================================

                df, sheet_name = leer_excel_contactos(
                    archivo,
                )

                # =============================================
                # ANALIZAR TODO EL ARCHIVO
                # =============================================

                (
                    preview,
                    resumen,
                    errores,
                ) = generar_preview_contactos(
                    df,
                )

                # El DataFrame ya no es necesario.
                #
                # Permitimos que Python pueda liberar esa
                # memoria antes de renderizar el template.
                del df

                # =============================================
                # GENERAR TOKEN
                # =============================================

                token = uuid.uuid4().hex

                cache_key = _contactos_preview_cache_key(
                    request.user.id,
                    token,
                )

                # =============================================
                # GUARDAR PREVIEW COMPLETO
                # =============================================
                #
                # Guardamos todos los registros porque
                # Confirmar importación debe procesarlos todos.
                #
                # El template, en cambio, recibirá únicamente
                # una página.
                # =============================================

                payload = {
                    "preview": preview,
                    "resumen": resumen,
                    "errores": errores,
                    "sheet_name": sheet_name,
                    "nombre_archivo": (archivo.name),
                }

                cache.set(
                    cache_key,
                    payload,
                    timeout=(CACHE_TIMEOUT_PREVIEW),
                )

                # =============================================
                # RENDER PRIMERA PÁGINA
                # =============================================

                return render(
                    request,
                    ("planificacion/" "contactos/" "importar.html"),
                    _construir_contexto_preview(
                        form=form,
                        preview=preview,
                        resumen=resumen,
                        errores=errores,
                        sheet_name=sheet_name,
                        token=token,
                        nombre_archivo=(archivo.name),
                        pagina=1,
                    ),
                )

            except Exception as exc:

                messages.error(
                    request,
                    ("No fue posible leer " f"el archivo: {exc}"),
                )

    else:

        form = ImportarContactosForm()

    # ========================================================
    # VISTA INICIAL
    # ========================================================

    return render(
        request,
        ("planificacion/" "contactos/" "importar.html"),
        {
            "form": form,
            "ultima_importacion": (_ultima_importacion_contactos()),
        },
    )

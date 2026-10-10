from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect, render

from planificacion.forms.contactos import ImportarContactosForm
from planificacion.models import (FilaImportacionContacto,
                                  ImportacionContactosSitios)
from planificacion.services.contactos_importer import (
    aplicar_importacion_contactos, generar_preview_contactos,
    leer_excel_contactos)
from usuarios.decoradores import rol_requerido

# ============================================================
# CONFIGURACIÓN
# ============================================================


PREVIEW_FILAS_POR_PAGINA = 100

TOKEN_PREVIEW_MAX_AGE = 60 * 30

TOKEN_PREVIEW_SALT = "gz-planificacion-contactos-preview"


# ============================================================
# TOKEN FIRMADO
# ============================================================


def _crear_token_preview(
    *,
    importacion,
    user,
    sheet_name,
):
    return signing.dumps(
        {
            "importacion_id": (importacion.pk),
            "user_id": (user.pk),
            "sheet_name": (sheet_name or ""),
        },
        salt=TOKEN_PREVIEW_SALT,
        compress=True,
    )


def _leer_token_preview(
    *,
    token,
    user,
):
    if not token:

        return None

    try:

        payload = signing.loads(
            token,
            salt=TOKEN_PREVIEW_SALT,
            max_age=TOKEN_PREVIEW_MAX_AGE,
        )

    except (
        signing.BadSignature,
        signing.SignatureExpired,
    ):

        return None

    if int(
        payload.get(
            "user_id",
            0,
        )
        or 0
    ) != int(
        user.pk,
    ):

        return None

    return payload


# ============================================================
# IMPORTACIÓN DESDE TOKEN
# ============================================================


def _obtener_importacion_preview(
    *,
    token,
    user,
):
    payload = _leer_token_preview(
        token=token,
        user=user,
    )

    if not payload:

        return (
            None,
            None,
        )

    importacion_id = payload.get(
        "importacion_id",
    )

    if not importacion_id:

        return (
            None,
            None,
        )

    importacion = ImportacionContactosSitios.objects.filter(
        pk=importacion_id,
        creado_por=user,
        estado="preview",
    ).first()

    if not importacion:

        return (
            None,
            None,
        )

    return (
        importacion,
        payload,
    )


# ============================================================
# ÚLTIMA IMPORTACIÓN
# ============================================================


def _ultima_importacion_contactos():
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
# CONVERTIR FILA BD -> FILA VISUAL
# ============================================================


def _fila_preview_visual(
    fila,
):
    return {
        "fila": (fila.numero_fila),
        "estado": (fila.estado),
        "contacto_id": (fila.contacto_id),
        "sitio_id": (fila.sitio_id),
        "vinculado": (fila.vinculado),
        "vinculo_por": (fila.vinculo_por),
        "region": (fila.region),
        "id_origen": (fila.id_origen),
        "nombre_sitio": (fila.nombre_sitio),
        "propietario": (fila.propietario),
        "telefono": (fila.telefono),
        "correo": (fila.correo),
        "fecha_informacion": (fila.fecha_informacion),
        "responsable": (fila.responsable),
        "observaciones": (fila.observaciones),
        "accion": (fila.accion),
        "cambios": (fila.cambios or []),
    }


# ============================================================
# IDS SIN VINCULAR
# ============================================================


def _obtener_ids_no_vinculados(
    importacion,
):
    agrupados = OrderedDict()

    queryset = (
        FilaImportacionContacto.objects.filter(
            importacion=importacion,
            vinculado=False,
        )
        .exclude(
            estado="error",
        )
        .order_by(
            "id_origen",
            "numero_fila",
        )
        .values_list(
            "id_origen",
            "numero_fila",
        )
    )

    for (
        id_origen,
        numero_fila,
    ) in queryset.iterator(
        chunk_size=500,
    ):

        id_origen = str(id_origen or "").strip()

        if not id_origen:

            id_origen = "ID vacío"

        if id_origen not in agrupados:

            agrupados[id_origen] = {
                "id_origen": (id_origen),
                "cantidad": 0,
                "filas": [],
            }

        agrupados[id_origen]["cantidad"] += 1

        agrupados[id_origen]["filas"].append(
            numero_fila,
        )

    ids = list(agrupados.values())

    return {
        "ids": ids,
        "filas_no_vinculadas": sum(item["cantidad"] for item in ids),
        "ids_unicos_no_vinculados": len(
            ids,
        ),
    }


# ============================================================
# RESUMEN
# ============================================================


def _resumen_importacion(
    importacion,
):
    return {
        "total_filas": (importacion.total_filas),
        "nuevos": (importacion.nuevos),
        "actualizados": (importacion.actualizados),
        "sin_cambios": (importacion.sin_cambios),
        "no_vinculados": (importacion.no_vinculados),
        "errores": (importacion.errores),
    }


# ============================================================
# CONTEXTO DEL PREVIEW
# ============================================================


def _construir_contexto_preview(
    *,
    form,
    importacion,
    token,
    sheet_name,
    pagina,
):
    filas_validas_queryset = (
        FilaImportacionContacto.objects.filter(
            importacion=importacion,
        )
        .exclude(
            estado="error",
        )
        .order_by(
            "numero_fila",
            "id",
        )
    )

    paginator = Paginator(
        filas_validas_queryset,
        PREVIEW_FILAS_POR_PAGINA,
    )

    try:

        pagina_obj = paginator.page(
            pagina,
        )

    except Exception:

        pagina_obj = paginator.page(
            1,
        )

    preview = [
        _fila_preview_visual(
            fila,
        )
        for fila in pagina_obj.object_list
    ]

    pagina_actual = pagina_obj.number

    total_paginas = max(
        paginator.num_pages,
        1,
    )

    pagina_desde = max(
        pagina_actual - 2,
        1,
    )

    pagina_hasta = min(
        pagina_actual + 2,
        total_paginas,
    )

    paginas_visibles = list(
        range(
            pagina_desde,
            pagina_hasta + 1,
        )
    )

    if paginator.count:

        mostrando_desde = pagina_obj.start_index()

        mostrando_hasta = pagina_obj.end_index()

    else:

        mostrando_desde = 0

        mostrando_hasta = 0

    no_vinculados = _obtener_ids_no_vinculados(
        importacion,
    )

    errores_queryset = FilaImportacionContacto.objects.filter(
        importacion=importacion,
        estado="error",
    ).order_by("numero_fila",)[:100]

    errores = [
        {
            "fila": (fila.numero_fila),
            "error": (fila.error),
        }
        for fila in errores_queryset
    ]

    return {
        "form": form,
        "preview": preview,
        "preview_total": (paginator.count),
        "preview_mostrados": len(
            preview,
        ),
        "preview_limitado": (paginator.count > PREVIEW_FILAS_POR_PAGINA),
        "pagina_actual": (pagina_actual),
        "total_paginas": (total_paginas),
        "pagina_anterior": (
            pagina_obj.previous_page_number() if pagina_obj.has_previous() else None
        ),
        "pagina_siguiente": (
            pagina_obj.next_page_number() if pagina_obj.has_next() else None
        ),
        "paginas_visibles": (paginas_visibles),
        "mostrando_desde": (mostrando_desde),
        "mostrando_hasta": (mostrando_hasta),
        "resumen": (
            _resumen_importacion(
                importacion,
            )
        ),
        "ids_no_vinculados": (no_vinculados["ids"]),
        "filas_no_vinculadas": (no_vinculados["filas_no_vinculadas"]),
        "ids_unicos_no_vinculados": (no_vinculados["ids_unicos_no_vinculados"]),
        "errores": errores,
        "errores_total": (importacion.errores),
        "sheet_name": (sheet_name or ""),
        "token": token,
        "nombre_archivo": (importacion.nombre_archivo),
        "ultima_importacion": (_ultima_importacion_contactos()),
    }


# ============================================================
# IMPORTAR CONTACTOS
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def importar_contactos(
    request,
):
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
    # CONFIRMAR
    # ========================================================

    if request.method == "POST" and accion == "confirmar":

        (
            importacion,
            payload,
        ) = _obtener_importacion_preview(
            token=token,
            user=request.user,
        )

        if not importacion:

            messages.error(
                request,
                (
                    "El preview expiró, "
                    "no existe o ya fue procesado. "
                    "Vuelve a subir el archivo."
                ),
            )

            return redirect("planificacion:importar_contactos")

        try:

            resultado = aplicar_importacion_contactos(
                importacion=importacion,
                user=request.user,
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
    # CANCELAR
    # ========================================================

    if request.method == "POST" and accion == "cancelar":

        (
            importacion,
            payload,
        ) = _obtener_importacion_preview(
            token=token,
            user=request.user,
        )

        if importacion:

            with transaction.atomic():

                importacion.estado = "cancelada"

                importacion.save(
                    update_fields=[
                        "estado",
                    ]
                )

                FilaImportacionContacto.objects.filter(
                    importacion=importacion,
                ).delete()

        messages.info(
            request,
            "Importación cancelada.",
        )

        return redirect("planificacion:listar_contactos")

    # ========================================================
    # PAGINACIÓN DE PREVIEW EXISTENTE
    # ========================================================

    if request.method == "GET" and token:

        (
            importacion,
            payload,
        ) = _obtener_importacion_preview(
            token=token,
            user=request.user,
        )

        if not importacion:

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
                or 1
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
                importacion=importacion,
                token=token,
                sheet_name=(
                    payload.get(
                        "sheet_name",
                        "",
                    )
                ),
                pagina=pagina,
            ),
        )

    # ========================================================
    # SUBIR ARCHIVO
    # ========================================================

    if request.method == "POST":

        form = ImportarContactosForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():

            archivo = form.cleaned_data["archivo"]

            importacion = None

            try:

                # =============================================
                # CREAR CABECERA DE PREVIEW
                # =============================================

                importacion = ImportacionContactosSitios.objects.create(
                    nombre_archivo=(archivo.name),
                    estado="preview",
                    creado_por=(request.user),
                )

                # =============================================
                # LEER EXCEL
                # =============================================

                (
                    df,
                    sheet_name,
                ) = leer_excel_contactos(
                    archivo,
                )

                # =============================================
                # GENERAR PREVIEW DIRECTAMENTE EN POSTGRESQL
                # =============================================

                generar_preview_contactos(
                    df,
                    importacion,
                )

                # DataFrame ya no participa en ningún render.
                del df

                # =============================================
                # TOKEN SEGURO
                # =============================================

                token = _crear_token_preview(
                    importacion=importacion,
                    user=request.user,
                    sheet_name=sheet_name,
                )

                # =============================================
                # REDIRECT
                # =============================================
                #
                # MUY IMPORTANTE:
                #
                # NO renderizamos directamente después del
                # análisis.
                #
                # Terminamos la request pesada y hacemos otra
                # request GET que solamente traerá 100 filas.
                # =============================================

                return redirect(
                    (f"/planificacion/contactos/importar/" f"?token={token}&pagina=1")
                )

            except Exception as exc:

                if importacion:

                    ImportacionContactosSitios.objects.filter(
                        pk=importacion.pk,
                    ).update(
                        estado="error",
                        observaciones=str(
                            exc,
                        ),
                    )

                    FilaImportacionContacto.objects.filter(
                        importacion_id=(importacion.pk),
                    ).delete()

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

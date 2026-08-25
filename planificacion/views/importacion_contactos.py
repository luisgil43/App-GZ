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
):
    """
    Construye en un único lugar toda la información necesaria
    para mostrar el preview.

    IMPORTANTE:
    El preview se muestra COMPLETO.

    Si el archivo tiene 5.125 registros:
    - se analizan los 5.125;
    - se guardan los 5.125 en caché;
    - se muestran los 5.125;
    - se procesan los 5.125 al confirmar.
    """

    no_vinculados = _obtener_ids_no_vinculados(preview)

    return {
        "form": form,
        # ----------------------------------------------------
        # PREVIEW VISUAL COMPLETO
        # ----------------------------------------------------
        "preview": preview,
        "preview_total": len(preview),
        "preview_mostrados": len(preview),
        "preview_limitado": False,
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
        # ERRORES REALES DE ARCHIVO
        # ----------------------------------------------------
        "errores": errores[:100],
        "errores_total": len(errores),
        # ----------------------------------------------------
        # ARCHIVO
        # ----------------------------------------------------
        "sheet_name": sheet_name,
        "token": token,
        "nombre_archivo": (nombre_archivo),
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
    "supervisor",
)
def importar_contactos(request):
    """
    Flujo completo:

    1. Subir Excel.
    2. Analizar todas las filas.
    3. Buscar coincidencias contra SitioMovil.
    4. Mostrar preview completo.
    5. Informar IDs no vinculados.
    6. Confirmar o cancelar.
    7. Aplicar únicamente cambios sobre ContactoSitio.

    IMPORTANTE:

    SitioMovil se utiliza únicamente como tabla maestra
    de consulta/vinculación.

    Esta vista NO modifica SitioMovil.
    """

    accion = request.POST.get("accion") or ""

    token = request.POST.get("token") or ""

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

        payload = cache.get(cache_key)

        if not payload:
            messages.error(
                request,
                ("El preview expiró o ya no existe. " "Vuelve a subir el archivo."),
            )

            return redirect("planificacion:importar_contactos")

        preview_completo = payload.get("preview") or []

        try:
            resultado = aplicar_importacion_contactos(
                # Se procesa TODO el preview.
                preview=preview_completo,
                user=request.user,
                nombre_archivo=(
                    payload.get(
                        "nombre_archivo",
                        "",
                    )
                ),
            )

            cache.delete(cache_key)

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

                df, sheet_name = leer_excel_contactos(archivo)

                # =============================================
                # ANALIZAR TODO EL ARCHIVO
                # =============================================

                (
                    preview,
                    resumen,
                    errores,
                ) = generar_preview_contactos(df)

                # =============================================
                # GENERAR TOKEN
                # =============================================

                token = uuid.uuid4().hex

                cache_key = _contactos_preview_cache_key(
                    request.user.id,
                    token,
                )

                # =============================================
                # GUARDAR TODO EL PREVIEW
                #
                # Ejemplo:
                #
                # 5.125 registros:
                # - 5.125 en caché
                # - 5.125 visibles
                # - 5.125 al confirmar
                # =============================================

                cache.set(
                    cache_key,
                    {
                        "preview": preview,
                        "resumen": resumen,
                        "errores": errores,
                        "sheet_name": (sheet_name),
                        "nombre_archivo": (archivo.name),
                    },
                    timeout=(CACHE_TIMEOUT_PREVIEW),
                )

                # =============================================
                # RENDER
                # =============================================

                return render(
                    request,
                    ("planificacion/" "contactos/" "importar.html"),
                    _construir_contexto_preview(
                        form=form,
                        preview=preview,
                        resumen=resumen,
                        errores=errores,
                        sheet_name=(sheet_name),
                        token=token,
                        nombre_archivo=(archivo.name),
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
    # GET NORMAL
    # ========================================================

    return render(
        request,
        ("planificacion/" "contactos/" "importar.html"),
        {
            "form": form,
            "ultima_importacion": (_ultima_importacion_contactos()),
        },
    )

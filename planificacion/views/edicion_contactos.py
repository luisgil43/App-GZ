from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from planificacion.forms.edicion_contactos import EditarContactoInlineForm
from planificacion.models import ContactoSitio
from usuarios.decoradores import rol_requerido

# ============================================================
# CAMPOS EDITABLES INLINE
# ============================================================


CAMPOS_INLINE_CONTACTO = {
    "propietario",
    "telefono",
    "correo",
    "responsable",
    "observaciones",
    "accion",
}


# ============================================================
# HELPERS
# ============================================================


def _valor_actual_contacto(
    contacto,
    campo,
):
    return str(
        getattr(
            contacto,
            campo,
            "",
        )
        or ""
    ).strip()


def _respuesta_contacto(
    contacto,
    *,
    campo=None,
    actualizado_por="",
):
    return {
        "id": contacto.pk,
        "campo": campo,
        "valor": (
            _valor_actual_contacto(
                contacto,
                campo,
            )
            if campo
            else ""
        ),
        "propietario": contacto.propietario or "",
        "telefono": contacto.telefono or "",
        "correo": contacto.correo or "",
        "responsable": contacto.responsable or "",
        "observaciones": contacto.observaciones or "",
        "accion": contacto.accion or "",
        "fecha": (
            contacto.fecha_informacion.strftime("%d/%m/%Y")
            if contacto.fecha_informacion
            else "—"
        ),
        "actualizado_por": actualizado_por,
    }


# ============================================================
# EDICIÓN INLINE DE UN CAMPO
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
@require_POST
def editar_contacto_inline(
    request,
    pk,
):
    """
    Modifica UN SOLO CAMPO de ContactoSitio.

    El frontend envía:

        campo=telefono
        valor=987654321

    o:

        campo=observaciones
        valor=Avisar con 24 horas...

    IMPORTANTE:

    Esta vista NO modifica:
    - operaciones.SitioMovil
    - contacto.sitio
    - contacto.id_origen
    - región
    - nombre maestro del sitio

    Al existir una modificación real:
    - actualiza solamente ese campo;
    - fecha_informacion pasa a hoy;
    - actualizado_por queda con el usuario actual;
    - requiere_reanalisis pasa a True si existe en el modelo.
    """

    contacto = get_object_or_404(
        ContactoSitio,
        pk=pk,
        activo=True,
    )

    # ========================================================
    # CAMPO SOLICITADO
    # ========================================================

    campo = str(
        request.POST.get(
            "campo",
            "",
        )
        or ""
    ).strip()

    if campo not in CAMPOS_INLINE_CONTACTO:
        return JsonResponse(
            {
                "ok": False,
                "mensaje": (
                    "El campo solicitado no puede editarse " "desde esta tabla."
                ),
            },
            status=400,
        )

    valor_recibido = request.POST.get(
        "valor",
        "",
    )

    # ========================================================
    # VALIDAR / NORMALIZAR CON EL FORM EXISTENTE
    # ========================================================

    form = EditarContactoInlineForm(
        data={
            campo: valor_recibido,
        }
    )

    if not form.is_valid():

        errores_campo = form.errors.get(
            campo,
            [],
        )

        return JsonResponse(
            {
                "ok": False,
                "mensaje": ("Revisa la información ingresada."),
                "errores": {campo: [str(error) for error in errores_campo]},
            },
            status=400,
        )

    nuevo_valor = (
        form.cleaned_data.get(
            campo,
            "",
        )
        or ""
    )

    if isinstance(
        nuevo_valor,
        str,
    ):
        nuevo_valor = nuevo_valor.strip()

    anterior = _valor_actual_contacto(
        contacto,
        campo,
    )

    nuevo_comparable = str(nuevo_valor or "").strip()

    # ========================================================
    # SIN CAMBIOS
    # ========================================================

    if anterior == nuevo_comparable:

        return JsonResponse(
            {
                "ok": True,
                "sin_cambios": True,
                "mensaje": ("No existen modificaciones para guardar."),
                "contacto": _respuesta_contacto(
                    contacto,
                    campo=campo,
                ),
            }
        )

    # ========================================================
    # GUARDAR ÚNICAMENTE EL CAMPO SOLICITADO
    # ========================================================

    setattr(
        contacto,
        campo,
        nuevo_valor,
    )

    contacto.fecha_informacion = timezone.localdate()

    contacto.actualizado_por = request.user

    update_fields = [
        campo,
        "fecha_informacion",
        "actualizado_por",
        "actualizado_en",
    ]

    if hasattr(
        contacto,
        "requiere_reanalisis",
    ):
        contacto.requiere_reanalisis = True

        update_fields.append("requiere_reanalisis")

    contacto.save(update_fields=update_fields)

    actualizado_por = request.user.get_full_name() or request.user.username

    # ========================================================
    # RESPUESTA
    # ========================================================

    return JsonResponse(
        {
            "ok": True,
            "sin_cambios": False,
            "mensaje": ("Información actualizada correctamente."),
            "contacto": _respuesta_contacto(
                contacto,
                campo=campo,
                actualizado_por=actualizado_por,
            ),
        }
    )

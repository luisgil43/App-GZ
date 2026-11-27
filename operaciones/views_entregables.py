import logging
from urllib.parse import urlencode

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import DatabaseError, transaction
from django.db.models.deletion import ProtectedError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods, require_safe

from usuarios.decoradores import rol_requerido

from .models import (
    EntregableServicio, FormatoEntregable, PerfilRequerimientoEntregable,
    RequerimientoEntregable, ServicioCotizado, SesionFotoTecnico,
)
from .vista_previa_excel import vista_previa_excel

logger = logging.getLogger(__name__)
CONTENIDO_ENTREGABLE = "application/octet-stream"


class ArchivoEntregableForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo", max_length=255,
        validators=[FileExtensionValidator(EntregableServicio.EXTENSIONES_ADMITIDAS)],
    )


class EntregableForm(ArchivoEntregableForm):
    titulo = forms.CharField(
        label="Nombre del entregable", max_length=EntregableServicio.TITULO_MAX_LENGTH,
    )


class FormatoEntregableForm(ArchivoEntregableForm):
    nombre = forms.CharField(
        label="Nombre del formato", max_length=FormatoEntregable._meta.get_field("nombre").max_length,
    )
    field_order = ["nombre", "archivo"]


class RequerimientoEntregableForm(forms.ModelForm):
    extensiones = forms.MultipleChoiceField(
        label="Formatos admitidos", required=False, widget=forms.CheckboxSelectMultiple,
        choices=[
            (extension, extension.upper()) for extension in EntregableServicio.EXTENSIONES_DOCUMENTALES
        ],
    )
    ejemplo = forms.FileField(
        label="Nuevo archivo de ejemplo", required=False, max_length=255,
        validators=[FileExtensionValidator(EntregableServicio.EXTENSIONES_ADMITIDAS)],
    )

    class Meta:
        model = RequerimientoEntregable
        fields = ["titulo", "instrucciones", "extensiones", "obligatorio", "formato"]
        labels = {"titulo": "Nombre del requerimiento", "formato": "Formato reutilizable"}
        widgets = {"instrucciones": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        datos = super().clean()
        if datos.get("formato") and datos.get("ejemplo"):
            self.add_error("ejemplo", "Selecciona un formato existente o adjunta un nuevo ejemplo.")
        return datos


def _almacen():
    return EntregableServicio._meta.get_field("archivo").storage


def _error(mensaje, status=400):
    return JsonResponse({"error": mensaje}, status=status)


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
@never_cache
def adjuntar_entregable(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, pk=servicio_id)
    requisito_id = request.POST.get("requerimiento")
    requisito = None
    if requisito_id:
        if not requisito_id.isascii() or not requisito_id.isdecimal() or len(requisito_id) > 18:
            return _error("El requerimiento seleccionado no es válido.")
        requisito = get_object_or_404(servicio.requerimientos_entregables, pk=requisito_id)
        return _adjuntar_a_requerimiento(request, servicio, requisito, administrador=True)
    form = EntregableForm(request.POST, request.FILES)
    if not form.is_valid():
        return _error(" ".join(
            f"{campo.label}. {error}" for campo in form for error in campo.errors
        ))

    archivo = form.cleaned_data["archivo"]
    entregable = EntregableServicio(
        servicio=servicio, titulo=form.cleaned_data["titulo"],
        nombre_original=archivo.name, adjuntado_por=request.user,
    )
    if not _guardar_entregable(entregable, archivo):
        return _error("No se pudo guardar el archivo en Wasabi. Inténtalo nuevamente.", 503)

    fila = render_to_string("operaciones/_entregable_fila.html", {"entregable": entregable})
    return JsonResponse({"id": entregable.pk, "fila": fila}, status=201)


def _limpiar_archivo(campo):
    if campo.name:
        try:
            campo.delete(save=False)
        except (OSError, BotoCoreError, ClientError):
            logger.exception("Error al limpiar un archivo de entregables.")


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_http_methods(["GET", "HEAD", "POST"])
@never_cache
def formatos_entregables(request):
    form = FormatoEntregableForm()
    if request.method == "POST":
        form = FormatoEntregableForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data["archivo"]
            formato = FormatoEntregable(
                nombre=form.cleaned_data["nombre"], nombre_original=archivo.name,
            )
            try:
                formato.ejemplo.save(archivo.name, archivo, save=False)
                formato.save()
            except (OSError, BotoCoreError, ClientError, S3UploadFailedError, DatabaseError):
                logger.exception("Error al guardar un formato de entregable.")
                _limpiar_archivo(formato.ejemplo)
                form.add_error(None, "No se pudo guardar el formato. Inténtalo nuevamente.")
            else:
                messages.success(request, "Formato guardado.")
                return redirect("operaciones:formatos_entregables")

    return render(request, "operaciones/formatos_entregables.html", {
        "form": form,
        "formatos": FormatoEntregable.objects.all(),
    })


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_safe
@never_cache
def descargar_formato_entregable(request, formato_id):
    return _descargar_formato(get_object_or_404(FormatoEntregable, pk=formato_id))


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_safe
@never_cache
def ver_formato_entregable(request, formato_id):
    formato = get_object_or_404(FormatoEntregable, pk=formato_id)
    if formato.es_planilla_xlsx:
        return vista_previa_excel(
            request, formato, formato.ejemplo.storage,
            archivo_fuente=formato.ejemplo, titulo=formato.nombre,
        )
    if not formato.tipo_visualizacion:
        raise Http404("Este formato se puede descargar desde el catálogo.")
    return _descargar_formato(formato, adjunto=False)


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def eliminar_formato_entregable(request, formato_id):
    try:
        with transaction.atomic():
            formato = get_object_or_404(FormatoEntregable.objects.select_for_update(), pk=formato_id)
            archivo = formato.ejemplo
            formato.delete()
            transaction.on_commit(lambda: _limpiar_archivo(archivo))
    except ProtectedError:
        messages.error(request, "El formato está vinculado a un requerimiento. Quita el vínculo antes de eliminarlo.")
    else:
        messages.success(request, "Formato eliminado.")
    return redirect("operaciones:formatos_entregables")


def _guardar_entregable(entregable, archivo):
    try:
        entregable.archivo.save(archivo.name, archivo, save=False)
        with transaction.atomic():
            entregable.save()
    except (OSError, BotoCoreError, ClientError, S3UploadFailedError, DatabaseError):
        logger.exception("Error al adjuntar un entregable al servicio %s", entregable.servicio_id)
        _limpiar_archivo(entregable.archivo)
        return False
    return True


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_safe
@never_cache
def sugerir_requerimientos_entregables(request):
    clave = RequerimientoEntregable.clave_titulo(request.GET.get("q", "")[:500])
    perfiles = PerfilRequerimientoEntregable.objects.filter(
        clave__contains=clave,
    ).order_by("titulo", "pk")[:15]
    return JsonResponse({"resultados": [
        {
            "titulo": perfil.titulo, "instrucciones": perfil.instrucciones,
            "extensiones": perfil.extension.split(",") if perfil.extension else [],
            "obligatorio": perfil.obligatorio, "formato": perfil.formato_id,
        }
        for perfil in perfiles
    ]})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def solicitar_entregable(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, pk=servicio_id)
    url = reverse("operaciones:fotos_configurar_requisitos", args=[servicio_id])
    siguiente = request.POST.get("next", "")
    if siguiente and url_has_allowed_host_and_scheme(
        siguiente, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        url += "?" + urlencode({"next": siguiente})
    destino = redirect(url + "#requerimientos-entregables")
    datos = request.POST
    if datos.get("extension") and "extensiones" not in datos:
        datos = datos.copy()
        datos.setlist("extensiones", datos["extension"].split(","))
    form = RequerimientoEntregableForm(datos, request.FILES)
    if not form.is_valid():
        for campo in form:
            for error in campo.errors:
                messages.error(request, f"{campo.label}. {error}")
        return destino

    requisito = form.save(commit=False)
    requisito.servicio = servicio
    requisito.extension = ",".join(form.cleaned_data["extensiones"])
    archivo = form.cleaned_data.get("ejemplo")
    formato = None
    try:
        if archivo:
            formato = FormatoEntregable(nombre=requisito.titulo, nombre_original=archivo.name)
            formato.ejemplo.save(archivo.name, archivo, save=False)
        with transaction.atomic():
            ServicioCotizado.objects.select_for_update().get(pk=servicio.pk)
            clave = requisito.clave_titulo(requisito.titulo)
            existente = next((
                item for item in servicio.requerimientos_entregables.all()
                if item.clave_titulo(item.titulo) == clave
            ), None)
            if formato:
                formato.save()
                requisito.formato = formato
            if existente:
                requisito.pk = existente.pk
            requisito.save()
            PerfilRequerimientoEntregable.guardar_desde(requisito)
    except (OSError, BotoCoreError, ClientError, S3UploadFailedError, DatabaseError):
        logger.exception("Error al solicitar un entregable al servicio %s", servicio_id)
        if formato:
            _limpiar_archivo(formato.ejemplo)
        messages.error(request, "No se pudo guardar el requerimiento de entregable.")
    else:
        messages.success(
            request,
            "Requerimiento de entregable actualizado." if existente else "Requerimiento de entregable creado.",
        )
    return destino


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
@transaction.atomic
def quitar_requerimiento_entregable(request, servicio_id, requisito_id):
    servicio = get_object_or_404(ServicioCotizado.objects.select_for_update(), pk=servicio_id)
    requisito = get_object_or_404(servicio.requerimientos_entregables, pk=requisito_id)
    requisito.delete()
    return JsonResponse({"ok": True})


def _servicio_del_tecnico(request, servicio_id):
    return get_object_or_404(
        ServicioCotizado, pk=servicio_id, trabajadores_asignados=request.user,
    )


@login_required
@rol_requerido("usuario")
@require_safe
@never_cache
def entregables_del_tecnico(request, servicio_id):
    servicio = _servicio_del_tecnico(request, servicio_id)
    return redirect("operaciones:ir_a_upload_fotos", servicio_id=servicio.pk)


def puede_adjuntar_entregables(servicio, asignacion):
    return servicio.estado == "en_progreso" and asignacion.estado == "en_proceso"


def _asignacion_entregables(servicio, usuario):
    return get_object_or_404(SesionFotoTecnico, sesion__servicio=servicio, tecnico=usuario)


def _fila_entrega_tecnico(request, requisito):
    requisito = RequerimientoEntregable.objects.select_related("formato", "entrega").get(pk=requisito.pk)
    return render_to_string("operaciones/_entregable_tecnico.html", {
        "servicio": requisito.servicio, "requisito": requisito,
        "puede_adjuntar_entregables": True,
        "extensiones_documentales": EntregableServicio.EXTENSIONES_DOCUMENTALES,
    }, request=request)


@login_required
@rol_requerido("usuario")
@require_POST
@never_cache
def entregar_requerimiento(request, servicio_id, requisito_id):
    servicio = _servicio_del_tecnico(request, servicio_id)
    requisito = get_object_or_404(servicio.requerimientos_entregables, pk=requisito_id)
    if not puede_adjuntar_entregables(servicio, _asignacion_entregables(servicio, request.user)):
        return _error("Puedes adjuntar entregables mientras tu asignación esté en ejecución.", 409)
    return _adjuntar_a_requerimiento(request, servicio, requisito)


def _adjuntar_a_requerimiento(request, servicio, requisito, *, administrador=False):
    if EntregableServicio.objects.filter(requerimiento=requisito).exists():
        return _error("Este requerimiento ya tiene un archivo adjunto.", 409)

    form = ArchivoEntregableForm(request.POST, request.FILES)
    if not form.is_valid():
        return _error(" ".join(form.errors.get("archivo", [])))
    archivo = form.cleaned_data["archivo"]
    extensiones = requisito.extensiones or EntregableServicio.EXTENSIONES_DOCUMENTALES
    try:
        FileExtensionValidator(extensiones)(archivo)
    except ValidationError:
        formatos = ", ".join(extension.upper() for extension in extensiones)
        return _error(f"Adjunta un archivo en uno de los formatos solicitados: {formatos}.")

    entregable = EntregableServicio(
        servicio=servicio, requerimiento=requisito, titulo=requisito.titulo,
        nombre_original=archivo.name, adjuntado_por=request.user,
    )
    if not _guardar_entregable(entregable, archivo):
        if EntregableServicio.objects.filter(requerimiento=requisito).exists():
            return _error("Este requerimiento ya tiene un archivo adjunto.", 409)
        return _error("No se pudo guardar el archivo en Wasabi. Inténtalo nuevamente.", 503)
    respuesta = {"id": entregable.pk, "archivo": entregable.nombre_original}
    if administrador:
        respuesta["fila"] = render_to_string("operaciones/_entregable_fila.html", {"entregable": entregable})
        respuesta["requerimiento"] = requisito.pk
    else:
        respuesta["fila"] = _fila_entrega_tecnico(request, requisito)
    return JsonResponse(respuesta, status=201)


@login_required
@rol_requerido("usuario")
@require_safe
@never_cache
def ejemplo_entregable(request, servicio_id, requisito_id):
    servicio = _servicio_del_tecnico(request, servicio_id)
    requisito = get_object_or_404(
        servicio.requerimientos_entregables.select_related("formato"),
        pk=requisito_id, formato__isnull=False,
    )
    return _descargar_formato(requisito.formato)


def _descargar_formato(formato, *, adjunto=True):
    try:
        url = formato.ejemplo.storage.url(
            formato.ejemplo.name, expire=300, http_method="GET",
            parameters={
                "ResponseContentDisposition": content_disposition_header(adjunto, formato.nombre_original),
                "ResponseContentType": CONTENIDO_ENTREGABLE if adjunto else formato.tipo_visualizacion,
                "ResponseCacheControl": "private, no-store",
            },
        )
    except (BotoCoreError, ClientError):
        logger.exception("Error al preparar la descarga del formato %s", formato.pk)
        return _error("No se pudo descargar el ejemplo. Inténtalo nuevamente.", 503)
    respuesta = redirect(url)
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
@transaction.atomic
def eliminar_entregable(request, servicio_id, entregable_id):
    entregable = get_object_or_404(
        EntregableServicio.objects.select_for_update(),
        servicio_id=servicio_id,
        pk=entregable_id,
    )
    return _eliminar_archivo_entregable(entregable)


def _eliminar_archivo_entregable(entregable):
    entregable_id = entregable.pk
    try:
        entregable.archivo.delete(save=False)
    except (OSError, BotoCoreError, ClientError):
        logger.exception("Error al eliminar el entregable %s", entregable_id)
        return _error("No se pudo eliminar el archivo. Inténtalo nuevamente.", 503)
    entregable.delete()
    return JsonResponse({"ok": True})


@login_required
@rol_requerido("usuario")
@require_POST
@transaction.atomic
def quitar_entrega_tecnico(request, servicio_id, entregable_id):
    servicio = _servicio_del_tecnico(request, servicio_id)
    asignacion = _asignacion_entregables(servicio, request.user)
    entregable = get_object_or_404(
        servicio.entregables.select_for_update(),
        pk=entregable_id, requerimiento__isnull=False, adjuntado_por=request.user,
    )
    if not puede_adjuntar_entregables(servicio, asignacion):
        return _error("Puedes quitar tu archivo mientras tu asignación esté en ejecución.", 409)
    requisito = entregable.requerimiento
    respuesta = _eliminar_archivo_entregable(entregable)
    if respuesta.status_code != 200:
        return respuesta
    return JsonResponse({"ok": True, "fila": _fila_entrega_tecnico(request, requisito)})


@login_required
@rol_requerido("usuario")
@require_safe
@never_cache
def abrir_entrega_tecnico(request, servicio_id, entregable_id, accion):
    servicio = _servicio_del_tecnico(request, servicio_id)
    entregable = get_object_or_404(
        servicio.entregables, pk=entregable_id, requerimiento__isnull=False,
    )
    if accion == "descargar":
        return _enlace_entregable(entregable, adjunto=True, redirigir=True)
    if accion != "visualizar":
        raise Http404
    if entregable.es_planilla_xlsx:
        return vista_previa_excel(request, entregable, _almacen())
    return _enlace_entregable(entregable, adjunto=False)


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_safe
@never_cache
def descargar_entregable(request, servicio_id, entregable_id):
    entregable = get_object_or_404(EntregableServicio, servicio_id=servicio_id, pk=entregable_id)
    return _enlace_entregable(entregable, adjunto=True)


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_safe
@never_cache
def visualizar_entregable(request, servicio_id, entregable_id):
    entregable = get_object_or_404(EntregableServicio, servicio_id=servicio_id, pk=entregable_id)
    if entregable.es_planilla_xlsx:
        return vista_previa_excel(request, entregable, _almacen())
    return _enlace_entregable(entregable, adjunto=False)


def _enlace_entregable(entregable, *, adjunto, redirigir=False):
    tipo = CONTENIDO_ENTREGABLE if adjunto else entregable.tipo_visualizacion
    if tipo is None:
        raise Http404("No hay vista previa para este formato.")
    try:
        url = _almacen().url(
            entregable.archivo.name,
            parameters={
                "ResponseContentDisposition": content_disposition_header(
                    adjunto, entregable.nombre_original,
                ),
                "ResponseContentType": tipo,
                "ResponseCacheControl": "private, no-store",
            },
            expire=300,
            http_method="GET",
        )
    except (BotoCoreError, ClientError):
        logger.exception("Error al preparar el enlace del entregable %s", entregable.pk)
        return _error("No se pudo abrir el archivo. Inténtalo nuevamente.", 503)
    if adjunto and not redirigir:
        return JsonResponse({"url": url})
    respuesta = redirect(url)
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta

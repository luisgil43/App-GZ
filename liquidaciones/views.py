from django.urls import NoReverseMatch
import logging
from urllib.parse import urljoin
import os
import uuid
import base64
import requests
import fitz  # PyMuPDF
from io import BytesIO
from PIL import Image
from django.http import Http404
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import HttpResponse, FileResponse, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.core.exceptions import ValidationError
from . import views
from django.core.files.storage import default_storage
from .models import Liquidacion, ruta_archivo_firmado, ruta_archivo_sin_firmar
from .forms import LiquidacionForm
from django_select2.views import AutoResponseView
from dal import autocomplete
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from usuarios.decoradores import rol_requerido
from django.utils.http import urlencode
from django.urls import reverse
from .forms import CargaMasivaLiquidacionesForm
from usuarios.models import CustomUser
import os
User = get_user_model()
logger = logging.getLogger(__name__)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def admin_lista_liquidaciones(request):
    try:
        liquidaciones = Liquidacion.objects.select_related('tecnico').all()

        nombres = sorted(set(l.tecnico.get_full_name() for l in liquidaciones))
        meses = sorted(set(l.mes for l in liquidaciones))
        años = sorted(set(l.año for l in liquidaciones))

        return render(request, 'liquidaciones/admin_lista.html', {
            'liquidaciones': liquidaciones,
            'nombres': nombres,
            'meses': meses,
            'años': años,
        })

    except Exception as e:
        logger.error(f"[admin_lista_liquidaciones] Error: {e}")
        return HttpResponse(f"Error 500: {e}", status=500)


@login_required
def listar_liquidaciones(request):
    usuario = request.user
    liquidaciones = Liquidacion.objects.filter(tecnico=usuario)
    return render(request, 'liquidaciones/listar.html', {
        'liquidaciones': liquidaciones
    })


@login_required
def ver_pdf_liquidacion(request, pk):
    usuario = request.user

    try:
        liquidacion = Liquidacion.objects.get(pk=pk, tecnico=usuario)
    except Liquidacion.DoesNotExist:
        messages.error(
            request, "La liquidación solicitada no existe o no te pertenece.")
        return redirect('liquidaciones:listar')

    archivo = liquidacion.archivo_pdf_liquidacion

    if archivo and archivo.name:
        try:
            return FileResponse(archivo.open('rb'), content_type='application/pdf')
        except Exception as e:
            logger.error(f"[ver_pdf_liquidacion] Error al abrir PDF: {e}")
            messages.error(request, "No se pudo abrir el archivo PDF.")
            return redirect('liquidaciones:listar')
    else:
        messages.error(request, "El archivo ya no está disponible.")
        return redirect('liquidaciones:listar')


@login_required
def firmar_liquidacion(request, pk):
    usuario = request.user
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=usuario)

    if not usuario.firma_digital or not usuario.firma_digital.name:
        messages.warning(
            request, "Debes registrar tu firma digital primero para poder firmar.")
        return redirect('liquidaciones:registrar_firma')

    try:
        usuario.firma_digital.open()
    except Exception as e:
        logger.warning(f"[firmar_liquidacion] Firma digital no accesible: {e}")
        messages.warning(
            request, "Tu firma registrada ya no está disponible. Por favor, vuelve a subirla.")
        return redirect('liquidaciones:registrar_firma')

    if request.method == 'POST':
        try:
            if not liquidacion.archivo_pdf_liquidacion or not liquidacion.archivo_pdf_liquidacion.name:
                logger.warning(
                    f"[firmar_liquidacion] PDF no encontrado para liquidación {liquidacion.pk}")
                return HttpResponseBadRequest("No se encontró el archivo PDF.")

            # Leer PDF original
            with liquidacion.archivo_pdf_liquidacion.open('rb') as f:
                original_pdf = BytesIO(f.read())

            # Leer firma
            with usuario.firma_digital.open('rb') as f:
                firma_data = BytesIO(f.read())

            img = Image.open(firma_data)
            if img.format not in ['PNG', 'JPEG']:
                raise ValueError("Formato de imagen no compatible")

            firma_img_io = BytesIO()
            img.save(firma_img_io, format='PNG')
            firma_img_io.seek(0)

            # Insertar imagen en PDF
            doc = fitz.open(stream=original_pdf, filetype='pdf')
            page = doc[-1]
            rect = fitz.Rect(400, 700, 550, 750)
            page.insert_image(rect, stream=firma_img_io)

            pdf_firmado_io = BytesIO()
            doc.save(pdf_firmado_io)
            doc.close()
            pdf_firmado_io.seek(0)

            # ✅ Usar mismo nombre base del PDF original
            nombre_base = Path(liquidacion.archivo_pdf_liquidacion.name).name
            nombre_firmado = nombre_base

            content = ContentFile(pdf_firmado_io.read())
            liquidacion.pdf_firmado.save(nombre_firmado, content, save=False)

            liquidacion.firmada = True
            liquidacion.fecha_firma = now()
            liquidacion.save()

            print("✅ Ruta final del PDF firmado en Cloudinary:",
                  liquidacion.pdf_firmado.name)

            messages.success(
                request, "La liquidación fue firmada correctamente. Puedes descargarla ahora.")
            return redirect('liquidaciones:listar')

        except Exception as e:
            logger.error(f"[firmar_liquidacion] Error general al firmar: {e}")
            return HttpResponseBadRequest(f"Error al firmar el PDF: {e}")

    return render(request, 'liquidaciones/firmar.html', {
        'liquidacion': liquidacion,
        'tecnico': usuario,
        'solo_lectura': True,  # esto ya lo estás usando
        'firmar_documento': True  # 🔧 esto es lo que faltaba
    })


@login_required
def registrar_firma(request):
    usuario = request.user
    try:
        redireccion = request.GET.get(
            'next') or reverse('liquidaciones:listar')
    except NoReverseMatch:
        redireccion = '/'  # Fallback seguro si algo falla

    # Eliminar firma
    if request.method == 'POST' and 'eliminar_firma' in request.POST:
        if usuario.firma_digital:
            usuario.firma_digital.delete(save=True)
            messages.success(request, "Firma digital eliminada correctamente.")
        else:
            messages.info(request, "No había firma registrada para eliminar.")
        return redirect(request.path)

    # Guardar firma
    if request.method == 'POST' and 'firma_digital' in request.POST:
        data_url = request.POST.get('firma_digital')

        if not data_url:
            messages.error(request, "No se recibió ninguna firma.")
            return redirect(request.path)

        try:
            if not data_url.startswith('data:image/png;base64,'):
                raise ValueError("El formato de la firma no es PNG válido.")

            # Decodificar imagen base64
            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)

            nombre_archivo = f"firmas/usuario_{usuario.id}_firma.png"

            # Eliminar anterior si existe
            if usuario.firma_digital and usuario.firma_digital.storage.exists(usuario.firma_digital.name):
                usuario.firma_digital.delete(save=False)

            usuario.firma_digital.save(nombre_archivo, content, save=True)

            messages.success(
                request, "Tu firma digital ha sido guardada correctamente.")
            return redirect(redireccion)

        except Exception as e:
            messages.error(
                request,
                f"Error al guardar la firma. Verifica que sea una imagen PNG válida. Detalles: {e}"
            )
            return redirect(request.path)

    # Determinar base html según si es admin
    base_template = 'dashboard_admin/base.html' if usuario.is_staff else 'dashboard/base.html'

    return render(request, 'liquidaciones/registrar_firma.html', {
        'tecnico': usuario,
        'base_template': base_template
    })


@login_required
def liquidaciones_pdf(request):
    usuario = request.user
    liquidaciones = Liquidacion.objects.filter(tecnico=usuario)

    html_string = render_to_string('liquidaciones/liquidaciones_pdf.html', {
        'liquidaciones': liquidaciones,
        # si tu template usa {{ tecnico }}, esto se mantiene
        'tecnico': usuario,
    })

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="liquidaciones.pdf"'

    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response


@login_required
def descargar_pdf(request, pk):
    usuario = request.user
    print("en descargar")
    try:
        liquidacion = Liquidacion.objects.get(pk=pk, tecnico=usuario)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe o no te pertenece.")
        return redirect('liquidaciones:listar')

    archivo = liquidacion.pdf_firmado
    # archivo = liquidacion.archivo_pdf_liquidacion
    print(archivo)
    if archivo and archivo.name:

        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"  # formato 01, 02, ..., 12
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}.pdf"
            # return FileResponse(archivo.open('rb'), content_type='application/pdf')
            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf',
                as_attachment=False,
                filename=nombre_archivo
            )
        except Exception as e:
            print("ERROR: ", e)
            logger.error(
                f"[descargar_pdf] Error al abrir archivo firmado: {e}")
            messages.error(request, "No se pudo abrir el archivo firmado.")
            return redirect('liquidaciones:listar')
    else:
        messages.error(request, "El archivo firmado ya no está disponible.")
        return redirect('liquidaciones:listar')


@login_required
def confirmar_firma(request, pk):
    user = request.user

    if user.is_staff:
        messages.error(
            request, "Solo los técnicos pueden confirmar una firma.")
        return redirect('liquidaciones:listar')

    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=user)

    if not liquidacion.firmada:
        liquidacion.firmada = True
        liquidacion.fecha_firma = timezone.now()
        liquidacion.save()
        logger.info(
            f"[confirmar_firma] Liquidación {pk} firmada por el técnico {user.pk}")
        messages.success(request, "Firma confirmada correctamente.")
    else:
        logger.info(
            f"[confirmar_firma] Liquidación {pk} ya estaba firmada por el técnico {user.pk}")
        messages.info(request, "La liquidación ya estaba firmada.")

    return redirect('liquidaciones:listar')


@staff_member_required
@csrf_protect
def confirmar_reemplazo(request):
    if request.method == 'POST':
        if '_reemplazar' in request.POST:
            data = request.session.get('duplicado_data')
            archivo_binario = request.session.get('archivo_temporal_bytes')
            archivo_nombre = request.session.get('archivo_temporal_nombre')

            if data and archivo_binario and archivo_nombre:
                tecnico_id = data.get('tecnico')
                mes = data.get('mes')
                año = data.get('año')

                anterior = Liquidacion.objects.filter(
                    tecnico_id=tecnico_id, mes=mes, año=año
                ).first()

                if anterior:
                    # Intentar eliminar archivos sin usar storage.exists()
                    try:
                        if anterior.pdf_firmado:
                            anterior.pdf_firmado.delete(save=False)
                    except Exception as e:
                        logger.warning(
                            f"[confirmar_reemplazo] No se pudo eliminar pdf_firmado: {e}")

                    try:
                        if anterior.archivo_pdf_liquidacion:
                            anterior.archivo_pdf_liquidacion.delete(save=False)
                    except Exception as e:
                        logger.warning(
                            f"[confirmar_reemplazo] No se pudo eliminar archivo_pdf_liquidacion: {e}")

                    anterior.delete()

                # Guardar nuevo archivo en Cloudinary
                nombre_final = f"liquidaciones_sin_firmar/{uuid.uuid4()}_{archivo_nombre}"
                ruta_guardada = default_storage.save(
                    nombre_final, ContentFile(archivo_binario))

                nueva = Liquidacion(
                    tecnico_id=tecnico_id,
                    mes=mes,
                    año=año,
                    firmada=False
                )
                nueva.archivo_pdf_liquidacion.name = ruta_guardada
                nueva.save()

                messages.success(
                    request, "✅ Liquidación reemplazada correctamente.")

                # Limpiar sesión
                request.session.pop('duplicado_data', None)
                request.session.pop('archivo_temporal_bytes', None)
                request.session.pop('archivo_temporal_nombre', None)

                return redirect('admin:liquidaciones_liquidacion_changelist')

        elif '_cancelar' in request.POST:
            request.session.pop('duplicado_data', None)
            request.session.pop('archivo_temporal_bytes', None)
            request.session.pop('archivo_temporal_nombre', None)

            messages.info(
                request, "❌ Se canceló el reemplazo de la liquidación.")
            return redirect('admin:liquidaciones_liquidacion_changelist')

    # GET
    data = request.session.get('duplicado_data', {})
    archivo_nombre = request.session.get('archivo_temporal_nombre', '')

    tecnico_nombre = ''
    if data.get('tecnico'):
        obj = Liquidacion.objects.filter(tecnico_id=data['tecnico']).first()
        tecnico_nombre = obj.tecnico if obj else ''

    return render(request, 'liquidaciones/confirmar_reemplazo.html', {
        'tecnico_id': data.get('tecnico'),
        'tecnico': tecnico_nombre,
        'mes': data.get('mes'),
        'año': data.get('año'),
        'pdf_name': archivo_nombre,
    })


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def carga_masiva_liquidaciones(request):
    resumen = None

    if request.method == 'POST':
        form = CargaMasivaLiquidacionesForm(request.POST, request.FILES)

        if form.is_valid():
            mes = form.cleaned_data['mes']
            año = form.cleaned_data['año']
            archivos = request.FILES.getlist('archivos')

            errores = []
            cargadas = 0

            for archivo in archivos:
                nombre = os.path.splitext(archivo.name)[0]

                if not archivo.name.lower().endswith('.pdf'):
                    errores.append(f"{archivo.name} (no es un PDF)")
                    continue

                rut = nombre.strip()
                usuario = CustomUser.objects.filter(identidad=rut).first()

                if not usuario:
                    errores.append(f"{rut} (usuario no existe)")
                    continue

                # Sobrescribir si ya existe
                liquidacion, creada = Liquidacion.objects.update_or_create(
                    tecnico=usuario,
                    mes=mes,
                    año=año,
                    defaults={'archivo_pdf_liquidacion': archivo}
                )

                cargadas += 1

            resumen = {
                'exitosas': cargadas,
                'fallidas': errores,
            }

            messages.success(request, "Carga finalizada")
            form = CargaMasivaLiquidacionesForm()  # resetea el form

        else:
            # Mostrar mensajes de error individuales del formulario
            for campo, errores in form.errors.items():
                for error in errores:
                    messages.error(request, f"{campo.capitalize()}: {error}")

    else:
        form = CargaMasivaLiquidacionesForm()

    return render(request, 'liquidaciones/carga_masiva_liquidaciones.html', {
        'form': form,
        'resumen': resumen,
    })


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def crear_liquidacion(request):

    if request.method == 'POST':
        form = LiquidacionForm(request.POST, request.FILES)
        if form.is_valid():
            # fields=(tecnico;mes;año;monto;archivo_pdf_liquidacion;pdf_firmado;fecha_firma;firmada)
            form.save()
            messages.success(request, "Liquidación creada con éxito.")
            # return redirect('liquidaciones:lista_liquidaciones')
            return redirect('liquidaciones:admin_lista')
        else:
            print("🔴 Errores en el formulario:", form.errors)
    else:
        form = LiquidacionForm()

    return render(request, 'liquidaciones/crear_liquidacion.html', {'form': form})


@method_decorator(login_required, name='dispatch')
class UsuarioAutocomplete(AutoResponseView):
    def get_queryset(self):
        qs = User.objects.filter(is_active=True)
        if self.q:
            qs = qs.filter(
                Q(identidad__icontains=self.q) |
                Q(first_name__icontains=self.q) |
                Q(last_name__icontains=self.q)
            )
        return qs

    def get_result_label(self, item):
        return f"{item.identidad} - {item.first_name} {item.last_name}"

    def get_result_value(self, item):
        return str(item.pk)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)

    if request.method == "POST":
        liquidacion.delete()
        messages.success(request, "Liquidación eliminada correctamente.")
        return redirect("liquidaciones:admin_lista")

    return render(request, "liquidaciones/eliminar_confirmacion.html", {"liquidacion": liquidacion})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def editar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)
    antigua_ruta_pdf = (
        liquidacion.archivo_pdf_liquidacion.name if liquidacion.archivo_pdf_liquidacion else None
    )

    if request.method == 'POST':
        form = LiquidacionForm(
            request.POST, request.FILES, instance=liquidacion)
        if form.is_valid():
            nueva_ruta_pdf = request.FILES.get('archivo_pdf_liquidacion')

            # 🧠 Si se reemplaza el archivo, eliminamos la firma anterior
            if nueva_ruta_pdf and nueva_ruta_pdf.name != antigua_ruta_pdf:
                if liquidacion.pdf_firmado and default_storage.exists(liquidacion.pdf_firmado.name):
                    default_storage.delete(liquidacion.pdf_firmado.name)
                    messages.info(
                        request, "La firma anterior fue eliminada porque se reemplazó la liquidación.")

                liquidacion.pdf_firmado = None
                liquidacion.fecha_firma = None
                liquidacion.firmada = False

            form.save()
            messages.success(
                request, '✅ Liquidación actualizada correctamente.')
            return redirect('liquidaciones:admin_lista')
        else:
            messages.error(
                request, '❌ Hubo errores al actualizar la liquidación. Revisa los campos.')
    else:
        form = LiquidacionForm(instance=liquidacion)

    return render(request, 'liquidaciones/editar_liquidacion.html', {
        'form': form,
        'liquidacion': liquidacion,
    })


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def ver_pdf_firmado_admin(request, pk):
    try:
        liquidacion = Liquidacion.objects.get(pk=pk)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe.")
        return redirect('liquidaciones:admin_lista')

    archivo = liquidacion.pdf_firmado

    if archivo and archivo.name:
        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}.pdf"

            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf'
            )
        except Exception as e:
            logger.error(
                f"[ver_pdf_firmado_admin] Error al abrir PDF firmado: {e}")
            messages.error(request, "No se pudo abrir el archivo firmado.")
            return redirect('liquidaciones:admin_lista')
    else:
        messages.error(request, "El archivo firmado ya no está disponible.")
        return redirect('liquidaciones:admin_lista')


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def ver_pdf_admin(request, pk):
    try:
        liquidacion = Liquidacion.objects.get(pk=pk)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe.")
        return redirect('liquidaciones:admin_lista')

    archivo = liquidacion.archivo_pdf_liquidacion

    if archivo and archivo.name:
        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}_sin_firma.pdf"

            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf'
            )
        except Exception as e:
            logger.error(f"[ver_pdf_admin] Error al abrir archivo PDF: {e}")
            messages.error(request, "No se pudo abrir el archivo.")
            return redirect('liquidaciones:admin_lista')
    else:
        messages.error(request, "El archivo ya no está disponible.")
        return redirect('liquidaciones:admin_lista')


def verificar_storage(request):
    return JsonResponse({
        "USE_CLOUDINARY": getattr(settings, 'USE_CLOUDINARY', False),
        "STORAGE_BACKEND": getattr(settings, 'DEFAULT_FILE_STORAGE', 'No definido')
    })

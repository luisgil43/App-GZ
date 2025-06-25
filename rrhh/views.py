from django.urls import NoReverseMatch
from rrhh.forms import CronogramaPagoForm
from rrhh.models import CronogramaPago
from django.utils import timezone
from .models import CronogramaPago
from .forms import CronogramaPagoForm
from django.core.exceptions import ValidationError
from rrhh.utils import generar_pdf_solicitud_vacaciones
from rrhh.models import SolicitudVacaciones
from django.shortcuts import get_object_or_404, redirect
from rrhh.utils import generar_pdf_solicitud_vacaciones  # ⬅️ al inicio del archivo
from io import BytesIO
from rrhh.models import FichaIngreso
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import uuid
import os
from .models import ContratoTrabajo
from .forms import ContratoTrabajoForm
from django.shortcuts import get_object_or_404
import logging
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import FileResponse, Http404
from .forms import FichaIngresoForm
from .forms import SolicitudVacacionesForm
from datetime import date
from django.http import HttpResponseForbidden
from usuarios.decoradores import rol_requerido
from .forms import RevisionVacacionesForm
from rrhh.models import Feriado
import json
from rrhh.models import DiasVacacionesTomadosManualmente
from django.urls import reverse
from django.db.models import Q
from .utils import contar_dias_habiles
from .forms import DocumentoTrabajadorForm, TipoDocumentoForm
from .models import DocumentoTrabajador, TipoDocumento, CustomUser
from django.db.models import OuterRef, Subquery
from .forms import ReemplazoDocumentoForm
import cloudinary.uploader
from django.core.files.base import ContentFile
from django.utils.text import slugify
import openpyxl
from datetime import datetime
from openpyxl.utils import get_column_letter
from django.http import HttpResponse
from django.utils.encoding import smart_str
from .utils import calcular_estado_documento
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import io
from .models import FichaIngreso
from rrhh.utils import generar_ficha_ingreso_pdf
# from rrhh.utils import agregar_firma_trabajador_a_ficha
import requests
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
# from .utils import agregar_firma_pm_a_ficha
from usuarios.models import CustomUser
from PIL import Image
from .forms import FirmaForm
import base64
User = get_user_model()


logger = logging.getLogger(__name__)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def listar_contratos_admin(request):
    contratos = ContratoTrabajo.objects.select_related('tecnico')

    identidades = contratos.values_list(
        'tecnico__identidad', flat=True).distinct()
    nombres = contratos.values_list(
        'tecnico__first_name', 'tecnico__last_name').distinct()
    fechas_inicio = contratos.values_list('fecha_inicio', flat=True).distinct()

    fechas_termino_raw = contratos.values_list('fecha_termino', flat=True)
    fechas_termino = []
    for fecha in fechas_termino_raw:
        if fecha:
            fechas_termino.append(str(fecha))
        else:
            fechas_termino.append("Indefinido")
    fechas_termino = sorted(set(fechas_termino))

    nombres_completos = sorted(set(
        f"{n[0]} {n[1]}" for n in nombres if n[0] and n[1]
    ))

    return render(request, 'rrhh/listar_contratos_admin.html', {
        'contratos': contratos,
        'identidades': identidades,
        'nombres': nombres_completos,
        'fechas_inicio': fechas_inicio,
        'fechas_termino': fechas_termino,
    })


@login_required
def listar_contratos_usuario(request):
    try:
        usuario = request.user
        logger.info(f"🧪 Usuario: {usuario} - ID: {usuario.id}")

        contratos = ContratoTrabajo.objects.filter(tecnico=usuario)
        logger.info(f"🧪 Total contratos: {contratos.count()}")

        return render(request, 'rrhh/contratos_trabajo.html', {
            'contratos': contratos
        })
    except Exception as e:
        logger.error(f"❌ Error al cargar contratos usuario: {e}")
        raise e  # Deja que falle para ver en los logs de Render


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def crear_contrato(request):
    if request.method == 'POST':
        archivo = request.FILES.get('archivo')

        # Validar archivo antes de crear el formulario
        if not archivo:
            messages.error(request, '❌ Debes subir un archivo PDF.')
            return render(request, 'rrhh/crear_contrato.html', {'form': ContratoTrabajoForm(request.POST)})

        if archivo.content_type != 'application/pdf':
            messages.error(
                request, '❌ Estás intentando subir un documento no válido. El archivo debe estar en formato PDF.')
            return render(request, 'rrhh/crear_contrato.html', {'form': ContratoTrabajoForm(request.POST)})

        form = ContratoTrabajoForm(request.POST, request.FILES)

        if form.is_valid():
            contrato = form.save(commit=False)

            if request.POST.get('indefinido-check'):
                contrato.fecha_termino = None

            contrato.archivo = archivo
            contrato.save()

            messages.success(request, '✅ Contrato creado correctamente.')
            return redirect('rrhh:contratos_trabajo')
        else:
            messages.error(
                request, '❌ Error al crear el contrato. Revisa los campos.')
    else:
        form = ContratoTrabajoForm()

    return render(request, 'rrhh/crear_contrato.html', {'form': form})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def editar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)

    form = ContratoTrabajoForm(
        request.POST or None, request.FILES or None, instance=contrato)

    if request.method == 'POST' and form.is_valid():
        contrato = form.save(commit=False)

        if request.POST.get('indefinido-check'):
            contrato.fecha_termino = None

        reemplazar = form.cleaned_data.get('reemplazar_archivo')
        archivo_nuevo = request.FILES.get('archivo')

        if reemplazar and archivo_nuevo:
            if archivo_nuevo.content_type != 'application/pdf':
                messages.error(request, '❌ El archivo debe ser un PDF válido.')
                return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})

            try:
                if contrato.archivo and contrato.archivo.name:
                    nombre_original = contrato.archivo.name.split(
                        '/')[-1]  # 🔁 Guardamos antes
                    # 🗑️ Eliminar archivo existente
                    contrato.archivo.delete(save=False)

                    archivo_nuevo.seek(0)
                    contenido = archivo_nuevo.read()
                    contrato.archivo.save(
                        nombre_original, ContentFile(contenido), save=False)
            except Exception as e:
                messages.error(
                    request, f"❌ Error al subir el nuevo archivo: {e}")
                return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})

        contrato.save()
        messages.success(request, '✅ Contrato actualizado correctamente.')
        return redirect('rrhh:contratos_trabajo')

    return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)

    if request.method == 'POST':
        try:
            if contrato.archivo and contrato.archivo.name:
                contrato.archivo.delete(save=False)
            contrato.delete()
            messages.success(request, "✅ Contrato eliminado correctamente.")
        except Exception as e:
            messages.error(
                request, f"❌ Ocurrió un error al eliminar el contrato: {e}")
        return redirect('rrhh:contratos_trabajo')

    return render(request, 'rrhh/eliminar_contrato.html', {'contrato': contrato})


@staff_member_required
def ver_contrato(request, contrato_id):
    try:
        contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)

        try:
            archivo = contrato.archivo.open()
        except Exception as e:
            messages.error(request, f"❌ No se pudo acceder al archivo: {e}")
            return redirect('rrhh:contratos_trabajo')

        # ✅ Mostramos el PDF directamente en navegador
        return FileResponse(contrato.archivo.open(), content_type='application/pdf')

    except Exception as e:
        messages.error(request, f"❌ Error al mostrar el contrato: {e}")
        return redirect('rrhh:contratos_trabajo')


@login_required
def listar_fichas_ingreso_usuario(request):
    fichas = FichaIngreso.objects.filter(usuario=request.user)
    return render(request, 'rrhh/listar_fichas_ingreso_usuario.html', {
        'fichas': fichas
    })

# Vista admin para listar fichas de ingreso (reutiliza ContratoTrabajo)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def listar_fichas_ingreso_admin(request):
    fichas = FichaIngreso.objects.all()
    return render(request, 'rrhh/listar_fichas_ingreso_admin.html', {'fichas': fichas})

# Crear ficha (reutilizando formulario)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def crear_ficha_ingreso(request):
    if request.method == 'POST':
        form = FichaIngresoForm(request.POST)
        if form.is_valid():
            ficha = form.save(commit=False)
            ficha.creado_por = request.user

            # Buscar usuario por RUT
            rut_limpio = ficha.rut.replace('.', '').replace('-', '')
            try:
                usuario = CustomUser.objects.get(
                    identidad__icontains=rut_limpio)
                ficha.usuario = usuario
            except CustomUser.DoesNotExist:
                messages.warning(
                    request, "⚠️ No se encontró ningún usuario con el RUT ingresado. Se guardará sin asignación de usuario.")

            ficha.save()
            generar_ficha_ingreso_pdf(ficha)
            messages.success(
                request, "Ficha de ingreso guardada exitosamente.")
            return redirect('rrhh:listar_fichas_ingreso_admin')
        else:
            messages.error(request, "Corrige los errores del formulario.")
    else:
        form = FichaIngresoForm()

    return render(request, 'rrhh/crear_ficha_ingreso.html', {'form': form})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def editar_ficha_ingreso(request, pk):
    ficha = get_object_or_404(FichaIngreso, pk=pk)

    if request.method == 'POST':
        form = FichaIngresoForm(request.POST, request.FILES, instance=ficha)
        if form.is_valid():
            ficha = form.save(commit=False)

            # Si la ficha ya estaba firmada o rechazada, al editarla se reinicia
            if ficha.estado in ['rechazada_pm', 'rechazada_usuario', 'aprobada']:
                ficha.estado = 'pendiente_pm'
                ficha.firma_rrhh = None
                ficha.firma_pm = None
                ficha.firma_trabajador = None
                ficha.pm = None  # para que se asigne nuevamente el nuevo aprobador

            ficha.save()

            # Regenerar el PDF limpio sin firmas
            generar_ficha_ingreso_pdf(ficha)

            messages.success(
                request, "Ficha actualizada correctamente y reiniciada para aprobación.")
            return redirect('rrhh:listar_fichas_ingreso_admin')
        else:
            print("❌ Formulario no válido:", form.errors)
    else:
        form = FichaIngresoForm(instance=ficha)

    return render(request, 'rrhh/editar_ficha_ingreso.html', {'form': form})


@login_required
@rol_requerido('admin', 'pm')
def rechazar_ficha_ingreso_pm(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id)

    if request.method == 'POST':
        motivo = request.POST.get('motivo', '').strip()

        if not motivo:
            messages.error(
                request, "Debes ingresar un motivo para rechazar la ficha.")
            return redirect('rrhh:listar_fichas_ingreso_admin')

        ficha.estado = 'rechazada_pm'
        ficha.motivo_rechazo_pm = motivo
        ficha.save()

        messages.warning(
            request, "❌ Has rechazado la ficha correctamente. RRHH ha sido notificado.")
        return redirect('rrhh:listar_fichas_ingreso_admin')

    # Si no es POST, redirecciona sin acción
    return redirect('rrhh:listar_fichas_ingreso_admin')


@login_required
@rol_requerido('admin', 'usuario')
def aprobar_ficha_ingreso_trabajador(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id, usuario=request.user)

    if ficha.firma_trabajador:
        messages.info(request, "Ya has firmado esta ficha.")
        return redirect('dashboard:mis_fichas_ingreso')

    if not (ficha.firma_pm and ficha.firma_rrhh):
        messages.error(
            request, "No puedes firmar todavía. Aún falta la aprobación del PM o de RRHH.")
        return redirect('dashboard:mis_fichas_ingreso')

    ficha.firma_trabajador = request.user.firma_digital
    ficha.estado = 'completada'
    ficha.save()

    # Reemplaza el PDF con las tres firmas insertadas
    generar_ficha_ingreso_pdf(ficha)

    messages.success(request, "✅ Has aprobado y firmado tu ficha de ingreso.")
    return redirect('dashboard:mis_fichas_ingreso')


@login_required
@rol_requerido('usuario')
def rechazar_ficha_ingreso_trabajador(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id, usuario=request.user)

    if request.method == 'POST':
        motivo = request.POST.get('motivo', '').strip()

        if not motivo:
            messages.error(
                request, "Debes ingresar un motivo para rechazar la ficha.")
            return redirect('rrhh:mis_fichas_ingreso')

        ficha.estado = 'rechazada_usuario'
        ficha.motivo_rechazo_usuario = motivo
        ficha.save()

        messages.warning(
            request, "❌ Has rechazado la ficha correctamente. RRHH ha sido notificado.")
        return redirect('rrhh:mis_fichas_ingreso')

    return redirect('rrhh:mis_fichas_ingreso')


@rol_requerido('admin', 'pm')
def revisar_ficha_pm(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id)

    if request.method == 'POST':
        accion = request.POST.get('accion')

        if accion == 'aprobar':
            ficha.estado = 'pendiente'  # pasa a validación del trabajador
            ficha.save()
            messages.success(
                request, "Ficha aprobada y enviada al trabajador para su validación.")
        elif accion == 'rechazar':
            ficha.estado = 'rechazada'
            ficha.save()
            messages.warning(
                request, "Ficha rechazada. Recursos Humanos ha sido notificado.")
        return redirect('rrhh:listar_fichas_pm')  # o a donde desees redirigir

    return render(request, 'rrhh/revision_ficha_pm.html', {
        'ficha': ficha
    })


@login_required
@rol_requerido('pm', 'admin')
def firmar_ficha_pm(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id)

    # No permitir firmar si ya está finalizada o rechazada
    if ficha.estado in ['rechazada_pm', 'rechazada_usuario', 'aprobada']:
        messages.error(
            request, "No se puede firmar una ficha ya finalizada o rechazada.")
        return redirect('rrhh:listar_fichas_ingreso_admin')

    # Validar que RRHH tenga firma digital
    if not ficha.creado_por or not ficha.creado_por.firma_digital:
        messages.error(
            request, "El responsable de RRHH aún no ha registrado su firma. No se puede continuar.")
        return redirect('rrhh:listar_fichas_ingreso_admin')

    # Validar que el PM actual tenga firma
    if not request.user.firma_digital:
        messages.error(
            request, "Debes tener una firma digital registrada para aprobar la ficha.")
        return redirect('rrhh:listar_fichas_ingreso_admin')

    # Verificar si ya fue firmada por el PM
    if ficha.firma_pm:
        messages.info(request, "Ya has firmado esta ficha.")
        return redirect('rrhh:listar_fichas_ingreso_admin')

    # Guardar firma, PM y actualizar estado
    ficha.firma_pm = request.user.firma_digital
    ficha.pm = request.user
    ficha.estado = 'pendiente_usuario'
    ficha.motivo_rechazo = None  # Limpiar rechazo anterior si lo hubo
    ficha.save()

    messages.success(
        request, "✅ Ficha firmada correctamente. Ahora debe revisarla el trabajador.")
    return redirect('rrhh:listar_fichas_ingreso_admin')


@login_required
@rol_requerido('usuario', 'admin')
def firmar_ficha_ingreso(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id, usuario=request.user)

    # Validar que el PM haya aprobado
    if not ficha.firma_pm:
        messages.error(
            request, "No puedes firmar esta ficha aún. Falta la aprobación del PM.")
        return redirect('rrhh:listar_fichas_ingreso_usuario')

    # Validar si falta alguna firma
    errores = []

    if not request.user.firma_digital or not hasattr(request.user.firma_digital, 'url'):
        errores.append(
            "Debes registrar tu firma digital antes de poder firmar la ficha.")
    if not ficha.pm or not ficha.pm.firma_digital:
        errores.append("El PM asignado aún no ha registrado su firma.")
    if not ficha.creado_por or not ficha.creado_por.firma_digital:
        errores.append("El encargado de RRHH aún no ha registrado su firma.")

    if errores:
        for e in errores:
            messages.error(request, e)

        # Reenviar fichas a la misma plantilla con errores
        fichas = FichaIngreso.objects.filter(
            usuario=request.user).order_by('-id')
        return render(request, 'rrhh/listar_fichas_ingreso_usuario.html', {'fichas': fichas})

    # Si es POST, firmamos
    if request.method == 'POST':
        ficha.firma_trabajador = request.user.firma_digital
        ficha.firma_rrhh = ficha.creado_por.firma_digital
        ficha.firma_pm = ficha.pm.firma_digital

        try:
            from rrhh.utils import firmar_ficha_ingreso_pdf
            firmar_ficha_ingreso_pdf(ficha)

            ficha.estado = 'aprobada'
            ficha.save()

            messages.success(request, "✅ Ficha firmada correctamente.")
        except Exception as e:
            messages.error(
                request, f"No se pudo completar la firma del PDF: {e}")

        return redirect('rrhh:listar_fichas_ingreso_usuario')

    return redirect('rrhh:listar_fichas_ingreso_usuario')


@login_required
@rol_requerido('usuario', 'admin')
def rechazar_ficha_ingreso(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id, usuario=request.user)

    if ficha.firma_pm and not ficha.firma_trabajador:
        ficha.estado = 'rechazada_usuario'
        ficha.save()
        messages.warning(
            request, "Has rechazado la ficha. Será revisada nuevamente por RRHH.")
    else:
        messages.error(
            request, "No puedes rechazar esta ficha en su estado actual.")

    return redirect('rrhh:listar_fichas_ingreso_usuario')


"""
@login_required
@rol_requerido('usuario')
def firmar_ficha_ingreso_trabajador(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id)

    if ficha.firma_trabajador:
        messages.warning(request, "Ya has firmado esta ficha.")
        return redirect('rrhh:listar_fichas_ingreso_usuario')

    if not request.user.firma_digital:
        messages.error(
            request, "Debes registrar tu firma digital antes de firmar.")
        return redirect('rrhh:listar_fichas_ingreso_usuario')

    ficha.usuario = request.user
    ficha.firma_trabajador = request.user.firma_digital

    try:
        agregar_firma_trabajador_a_ficha(ficha)
        messages.success(request, "Ficha firmada correctamente.")
    except Exception as e:
        messages.error(request, f"Ocurrió un error al firmar: {e}")

    return redirect('rrhh:listar_fichas_ingreso_usuario')"""


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_ficha_ingreso(request, pk):
    ficha = get_object_or_404(FichaIngreso, pk=pk)

    if request.method == 'POST':
        try:
            if ficha.archivo and ficha.archivo.name:
                # ✅ Elimina el PDF de Cloudinary
                ficha.archivo.delete(save=False)

            ficha.delete()
            messages.success(request, "Ficha eliminada correctamente.")
        except Exception as e:
            messages.error(
                request, f"Ocurrió un error al eliminar la ficha: {e}")

        return redirect('rrhh:listar_fichas_ingreso_admin')

    return render(request, 'rrhh/eliminar_ficha_ingreso.html', {'ficha': ficha})


@login_required
@rol_requerido('admin', 'pm', 'rrhh')
def generar_ficha_pdf(request, ficha_id):
    ficha = get_object_or_404(FichaIngreso, id=ficha_id)

    # Generar PDF temporal
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("FICHA DE INGRESO DE PERSONAL", styles['Heading1']),
        Spacer(1, 12),
        Paragraph(
            f"Nombre: {ficha.nombres} {ficha.apellidos}", styles['Normal']),
        Paragraph(f"RUT: {ficha.rut}", styles['Normal']),
        Paragraph(f"Cargo: {ficha.cargo}", styles['Normal']),
        Paragraph(f"Proyecto: {ficha.faena}", styles['Normal']),
        # ... más campos según corresponda ...
    ]
    doc.build(elements)
    buffer.seek(0)

    # Nombre de archivo lógico para descarga
    nombre_archivo = f"FichaIngreso_{ficha.rut.replace('.', '').replace('-', '')}.pdf"
    return FileResponse(buffer, as_attachment=True, filename=nombre_archivo)


@login_required
@rol_requerido('usuario')
def mis_vacaciones(request):
    usuario = request.user

    # Llamar al método del modelo para obtener días disponibles
    dias_disponibles = usuario.obtener_dias_vacaciones_disponibles()

    # Obtener los días cargados manualmente si existen
    try:
        dias_manuales = usuario.vacaciones_manuales.cantidad_dias
    except DiasVacacionesTomadosManualmente.DoesNotExist:
        dias_manuales = 0

    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST, usuario=usuario)
        if form.is_valid():
            dias_solicitados = usuario.calcular_dias_habiles(
                form.cleaned_data['fecha_inicio'],
                form.cleaned_data['fecha_fin']
            )
            if dias_solicitados > dias_disponibles:
                form.add_error(
                    None, "Los días solicitados superan los días disponibles.")
            else:
                solicitud = form.save(commit=False)
                solicitud.usuario = usuario
                solicitud.dias_solicitados = dias_solicitados
                solicitud.save()
                messages.success(request, "Solicitud enviada correctamente.")
                return redirect('rrhh:mis_vacaciones')
    else:
        form = SolicitudVacacionesForm(usuario=usuario)

    solicitudes = SolicitudVacaciones.objects.filter(
        usuario=usuario).order_by('-fecha_solicitud')

    feriados = Feriado.objects.values_list('fecha', flat=True)
    feriados_json = json.dumps([f.strftime("%Y-%m-%d") for f in feriados])

    context = {
        'dias_disponibles': dias_disponibles,
        'form': form,
        'solicitudes': solicitudes,
        'feriados_json': feriados_json,
        'dias_manuales': dias_manuales
    }

    return render(request, 'rrhh/solicitud_vacaciones.html', context)


@login_required
def editar_solicitud_vacaciones(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        mensajes_estado = {
            'rechazada_supervisor': "No puedes editar una solicitud que fue rechazada por el supervisor.",
            'pendiente_pm': "No puedes editar una solicitud que ya fue enviada al PM.",
            'rechazada_pm': "No puedes editar una solicitud que fue rechazada por el PM.",
            'pendiente_rrhh': "No puedes editar una solicitud que ya fue enviada a RRHH.",
            'rechazada_rrhh': "No puedes editar una solicitud que fue rechazada por RRHH.",
            'rechazada_admin': "No puedes editar una solicitud que fue rechazada por administración.",
            'aprobada': "No puedes editar una solicitud que ya fue aprobada.",
        }
        mensaje = mensajes_estado.get(
            solicitud.estatus, "No puedes editar esta solicitud.")
        messages.warning(request, mensaje)
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST, instance=solicitud)
        if form.is_valid():
            solicitud = form.save(commit=False)

            # Recalcular los días hábiles
            fecha_inicio = form.cleaned_data['fecha_inicio']
            fecha_fin = form.cleaned_data['fecha_fin']
            dias = contar_dias_habiles(fecha_inicio, fecha_fin)

            solicitud.dias_solicitados = dias
            solicitud.save()
            messages.success(request, "Solicitud actualizada correctamente.")
            return redirect('rrhh:mis_vacaciones')
    else:
        form = SolicitudVacacionesForm(instance=solicitud)

    return render(request, 'rrhh/editar_solicitud_vacaciones.html', {
        'form': form,
        'solicitud': solicitud
    })


@login_required
def eliminar_solicitud_vacaciones(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        mensajes_estado = {
            'rechazada_supervisor': "No puedes eliminar una solicitud rechazada por el supervisor.",
            'pendiente_pm': "No puedes eliminar una solicitud que ya fue enviada al PM.",
            'rechazada_pm': "No puedes eliminar una solicitud rechazada por el PM.",
            'pendiente_rrhh': "No puedes eliminar una solicitud que ya fue enviada a RRHH.",
            'rechazada_rrhh': "No puedes eliminar una solicitud rechazada por RRHH.",
            'rechazada_admin': "No puedes eliminar una solicitud rechazada por administración.",
            'aprobada': "No puedes eliminar una solicitud que ya fue aprobada.",
        }
        mensaje = mensajes_estado.get(
            solicitud.estatus, "No puedes eliminar esta solicitud.")
        messages.warning(request, mensaje)
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        solicitud.delete()
        messages.success(request, "Solicitud eliminada correctamente.")
        return redirect('rrhh:mis_vacaciones')

    return render(request, 'rrhh/confirmar_eliminacion.html', {
        'solicitud': solicitud
    })

# --- Vista para Supervisor ---


@staff_member_required
@rol_requerido('supervisor', 'admin')
def revisar_solicitudes_supervisor(request):
    if not request.user.es_supervisor and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.filter(
        estatus='pendiente_supervisor').order_by('fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_supervisor.html', {
        'solicitudes': solicitudes,
        'titulo': "Solicitudes Pendientes - Supervisor",
        'rol': 'supervisor',
    })

# --- Vista para PM ---


@staff_member_required
@rol_requerido('pm', 'admin')
def revisar_solicitudes_pm(request):
    if not request.user.es_pm and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.filter(
        estatus='pendiente_pm').order_by('fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_pm.html', {
        'solicitudes': solicitudes,
        'titulo': "Solicitudes Pendientes - PM",
        'rol': 'pm',
    })

# --- Vista para RRHH ---


@staff_member_required
@rol_requerido('rrhh', 'admin')
def revisar_solicitudes_rrhh(request):
    if not request.user.es_rrhh and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    # Recoger filtros desde GET
    identidad = request.GET.get('identidad', '').strip()
    nombre = request.GET.get('nombre', '').strip()
    estatus = request.GET.get('estatus', '').strip()

    # Filtros base
    solicitudes = SolicitudVacaciones.objects.all()

    if identidad:
        solicitudes = solicitudes.filter(
            usuario__identidad__icontains=identidad)

    if nombre:
        solicitudes = solicitudes.filter(
            Q(usuario__first_name__icontains=nombre) |
            Q(usuario__last_name__icontains=nombre)
        )

    if estatus:
        solicitudes = solicitudes.filter(estatus=estatus)

    solicitudes = solicitudes.order_by('-fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_rrhh.html', {
        'solicitudes': solicitudes,
        'filtros': {
            'identidad': identidad,
            'nombre': nombre,
            'estatus': estatus,
        }
    })


@staff_member_required
@rol_requerido('supervisor', 'pm', 'rrhh', 'admin')
def revisar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)

    # Detectar rol activo
    if request.user.es_supervisor:
        rol = 'supervisor'
    elif request.user.es_pm:
        rol = 'pm'
    elif request.user.es_rrhh or request.user.es_admin_general:
        rol = 'rrhh'
    else:
        return HttpResponseForbidden("No tienes permiso para revisar esta solicitud.")

    transiciones_validas = {
        'supervisor': 'pendiente_supervisor',
        'pm': 'pendiente_pm',
        'rrhh': 'pendiente_rrhh',
    }

    if transiciones_validas.get(rol) != solicitud.estatus:
        return HttpResponseForbidden("No puedes revisar esta solicitud.")

    if request.method == 'POST':
        form = RevisionVacacionesForm(request.POST)
        if form.is_valid():
            accion = request.POST.get('accion')
            observacion = form.cleaned_data['observacion']
            solicitud.observacion = observacion

            if accion == 'aprobar':
                if rol == 'supervisor':
                    solicitud.estatus = 'pendiente_pm'
                elif rol == 'pm':
                    solicitud.estatus = 'pendiente_rrhh'
                elif rol == 'rrhh':
                    solicitud.estatus = 'aprobada'
            elif accion == 'rechazar':
                solicitud.estatus = f'rechazada_{rol}'

            solicitud.save()
            messages.success(
                request, f"Solicitud {'aprobada' if accion == 'aprobar' else 'rechazada'} exitosamente.")
            return redirect('rrhh:revisar_' + rol)
    else:
        form = RevisionVacacionesForm()

    return render(request, 'rrhh/revisar_solicitud_vacaciones.html', {
        'solicitud': solicitud,
        'form': form
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')  # ✅ RRHH añadido
def revisar_todas_vacaciones(request):
    if not request.user.es_admin_general and not request.user.es_rrhh:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.all().order_by('-fecha_solicitud')

    return render(request, 'rrhh/revisar_todas_vacaciones.html', {
        'solicitudes': solicitudes,
        'titulo': "Todas las Solicitudes de Vacaciones",
        'rol': 'rrhh' if request.user.es_rrhh else 'admin',
    })


@staff_member_required
@rol_requerido('supervisor', 'pm', 'rrhh', 'admin')
def rechazar_solicitud_vacaciones(request):
    if request.method == 'POST':
        solicitud_id = request.POST.get('solicitud_id')
        observacion = request.POST.get('observacion')
        solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)

        # Detectar el rol de mayor privilegio permitido
        if request.user.es_supervisor:
            rol_usuario = 'supervisor'
        elif request.user.es_pm:
            rol_usuario = 'pm'
        elif request.user.es_rrhh:
            rol_usuario = 'rrhh'
        elif request.user.es_admin_general:
            rol_usuario = 'admin'
        else:
            return HttpResponseForbidden("Rol no válido para esta acción.")

        estatus_rechazo = {
            'supervisor': 'rechazada_supervisor',
            'pm': 'rechazada_pm',
            'rrhh': 'rechazada_rrhh',
            'admin': 'rechazada_admin'
        }[rol_usuario]

        solicitud.estatus = estatus_rechazo
        solicitud.observacion = observacion

        # ✅ Asignar quién rechazó la solicitud según el rol
        if rol_usuario == 'supervisor':
            solicitud.aprobado_por_supervisor = request.user
        elif rol_usuario == 'pm':
            solicitud.aprobado_por_pm = request.user
        elif rol_usuario == 'rrhh':
            solicitud.aprobado_por_rrhh = request.user

        solicitud.save()

        messages.success(
            request, f"Solicitud rechazada por {rol_usuario.upper()}.")

        redirecciones = {
            'supervisor': 'rrhh:revisar_supervisor',
            'pm': 'rrhh:revisar_pm',
            'rrhh': 'rrhh:revisar_rrhh',
            'admin': 'rrhh:revisar_todas_vacaciones'
        }

        return redirect(reverse(redirecciones[rol_usuario]))

    return HttpResponseForbidden("Acceso denegado.")


@rol_requerido('supervisor')
def aprobar_vacacion_supervisor(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)
    if solicitud.estatus == 'pendiente_supervisor':
        solicitud.estatus = 'pendiente_pm'
        solicitud.aprobado_por_supervisor = request.user  # Guarda quién aprobó
        solicitud.save()
        messages.success(request, "Solicitud aprobada y enviada al PM.")
    else:
        messages.warning(
            request, "La solicitud no está pendiente para Supervisor.")
    return redirect('rrhh:revisar_supervisor')


@rol_requerido('pm')
def aprobar_vacacion_pm(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)
    if solicitud.estatus == 'pendiente_pm':
        solicitud.estatus = 'pendiente_rrhh'
        solicitud.aprobado_por_pm = request.user  # Guarda quién aprobó
        solicitud.save()
        messages.success(request, "Solicitud aprobada y enviada a RRHH.")
    else:
        messages.warning(request, "La solicitud no está pendiente para PM.")
    return redirect('rrhh:revisar_pm')


@staff_member_required
@rol_requerido('admin', 'rrhh')
def aprobar_vacacion_rrhh(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)

    if solicitud.estatus != 'pendiente_rrhh':
        messages.error(request, "Esta solicitud ya fue revisada.")
        return redirect('rrhh:revisar_rrhh')

    trabajador = solicitud.usuario
    pm = solicitud.aprobado_por_pm
    rrhh = request.user

    # Validación de firmas
    faltantes = []
    if not trabajador.firma_digital:
        faltantes.append("del trabajador")
    if not pm or not pm.firma_digital:
        faltantes.append("del jefe directo")
    if not rrhh.firma_digital:
        faltantes.append("de Recursos Humanos")

    if faltantes:
        mensaje = "❌ No se puede completar la aprobación. Faltan las firmas " + \
            ", ".join(faltantes) + "."
        messages.error(request, mensaje)
        return redirect('rrhh:revisar_rrhh')

    # Aprobación y cambio de estado
    solicitud.estatus = 'aprobada'
    solicitud.aprobado_por_rrhh = rrhh
    solicitud.save()

    try:
        generar_pdf_solicitud_vacaciones(solicitud)
        messages.success(
            request, "✅ Solicitud aprobada y documento generado correctamente.")
    except Exception as e:
        print(f"⚠️ Error al generar el PDF: {e}")
        messages.warning(
            request, f"Solicitud aprobada, pero hubo un error al generar el documento PDF: {e}")

    return redirect('rrhh:revisar_rrhh')


@staff_member_required
@rol_requerido('admin', 'rrhh')
def eliminar_solicitud_vacaciones_admin(request, pk):
    # Seguridad extra por si acaso alguien sin rol llega hasta aquí
    if not request.user.es_rrhh and not request.user.es_admin_general:
        messages.error(
            request, "No tienes permisos para eliminar esta solicitud.")
        return redirect('dashboard_admin:vacaciones_admin')

    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)

    if request.method == 'POST':
        try:
            # Elimina el archivo PDF si existe
            if solicitud.archivo_pdf and solicitud.archivo_pdf.name:
                solicitud.archivo_pdf.delete(save=False)

            # Elimina la solicitud
            solicitud.delete()
            messages.success(request, "Solicitud eliminada correctamente.")
        except Exception as e:
            messages.error(request, f"Ocurrió un error al eliminar: {e}")

        return redirect('dashboard_admin:vacaciones_admin')

    messages.warning(request, "La eliminación debe hacerse mediante POST.")
    return redirect('dashboard_admin:vacaciones_admin')


@rol_requerido('admin', 'rrhh')
def subir_documento_trabajador(request):
    if request.method == 'POST':
        form = DocumentoTrabajadorForm(request.POST, request.FILES)
        if form.is_valid():
            trabajador = form.cleaned_data['trabajador']
            tipo = form.cleaned_data['tipo_documento']
            archivo = form.cleaned_data['archivo']
            fecha_emision = form.cleaned_data['fecha_emision']
            fecha_vencimiento = form.cleaned_data['fecha_vencimiento']

            # Verificar si ya existe
            existente = DocumentoTrabajador.objects.filter(
                trabajador=trabajador, tipo_documento=tipo).first()
            if existente:
                # Reemplazar el archivo
                existente.archivo = archivo
                existente.fecha_emision = fecha_emision
                existente.fecha_vencimiento = fecha_vencimiento
                existente.save()
                messages.success(
                    request, '📄 Documento reemplazado correctamente.')
            else:
                form.save()
                messages.success(request, '📄 Documento subido correctamente.')

            return redirect('rrhh:listado_documentos')
    else:
        form = DocumentoTrabajadorForm()

    return render(request, 'rrhh/subir_documento_trabajador.html', {'form': form})


def calcular_estado_documento(doc):
    if not doc or not doc.archivo or not doc.archivo.name:
        return "Faltante"

    if not doc.fecha_vencimiento:
        return "Faltante"

    hoy = date.today()

    if doc.fecha_vencimiento < hoy:
        return "Vencido"
    elif (doc.fecha_vencimiento - hoy).days <= 7:
        return "Por vencer"
    return "Vigente"


@staff_member_required
@rol_requerido('admin', 'rrhh')
def listado_documentos_trabajador(request):
    filtro_nombre = request.GET.get('trabajador', '').strip()
    filtro_tipo = request.GET.get('tipo', '')
    filtro_fecha = request.GET.get('fecha', '')
    filtro_estado = request.GET.get('estado', '')

    documentos = DocumentoTrabajador.objects.select_related(
        'trabajador', 'tipo_documento').all()

    # Aplicar filtros
    if filtro_nombre:
        documentos = documentos.filter(
            Q(trabajador__nombres__icontains=filtro_nombre) |
            Q(trabajador__apellidos__icontains=filtro_nombre)
        )
    if filtro_tipo:
        documentos = documentos.filter(tipo_documento__id=filtro_tipo)
    if filtro_fecha:
        documentos = documentos.filter(fecha_vencimiento=filtro_fecha)

    # Cargar todos los tipos de documentos para los filtros
    tipos = TipoDocumento.objects.all()

    data = []
    trabajadores_vistos = set()

    for doc in documentos:
        estado = calcular_estado_documento(doc)

        # Filtrar por estado después de calcularlo
        if filtro_estado and estado != filtro_estado:
            continue

        trabajador_id = doc.trabajador.id

        # Agrupar documentos por trabajador
        if trabajador_id not in trabajadores_vistos:
            trabajadores_vistos.add(trabajador_id)
            data.append({
                "trabajador": doc.trabajador,
                "documentos": []
            })

        # Agregar documento al trabajador correspondiente
        for entrada in data:
            if entrada["trabajador"].id == trabajador_id:
                entrada["documentos"].append({
                    "tipo": doc.tipo_documento,
                    "doc": doc,
                    "estado": estado
                })

    return render(request, 'rrhh/listado_documentos_trabajador.html', {
        "data": data,
        "tipos": tipos,
        "filtro_nombre": filtro_nombre,
        "filtro_tipo": filtro_tipo,
        "filtro_fecha": filtro_fecha,
        "filtro_estado": filtro_estado,
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')
def crear_tipo_documento(request):
    tipo_id = request.GET.get('editar')
    tipo_a_editar = None

    if tipo_id:
        tipo_a_editar = get_object_or_404(TipoDocumento, pk=tipo_id)
        form = TipoDocumentoForm(request.POST or None, instance=tipo_a_editar)
    else:
        form = TipoDocumentoForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            form.save()
            messages.success(
                request, "Tipo de documento actualizado correctamente." if tipo_a_editar else "Tipo de documento creado correctamente.")
            return redirect('rrhh:crear_tipo_documento')
        else:
            messages.error(request, "Error al guardar el tipo de documento.")

    tipos = TipoDocumento.objects.all()
    return render(request, 'rrhh/crear_tipo_documento.html', {
        'form': form,
        'tipos': tipos,
        'editando': tipo_a_editar
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')
def reemplazar_documento(request, documento_id):
    documento = get_object_or_404(DocumentoTrabajador, pk=documento_id)

    if request.method == 'POST':
        form = ReemplazoDocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            nuevo_archivo = form.cleaned_data['archivo']

            # Eliminar archivo anterior en Cloudinary
            if documento.archivo:
                public_id = documento.archivo.name.rsplit('.', 1)[0]
                cloudinary.uploader.destroy(public_id, invalidate=True)

            # Guardar nuevo archivo
            identidad = documento.trabajador.identidad
            tipo_slug = slugify(documento.tipo_documento.nombre)
            filename = f"{tipo_slug}.pdf"
            path = f"Documentos de los trabajadores/{identidad}/{filename}"
            documento.archivo.save(path, ContentFile(nuevo_archivo.read()))

            # Actualizar fechas
            documento.fecha_emision = form.cleaned_data['fecha_emision']
            documento.fecha_vencimiento = form.cleaned_data['fecha_vencimiento']
            documento.save()

            messages.success(request, "Documento reemplazado correctamente.")
            return redirect('rrhh:listado_documentos')
        else:
            messages.error(request, "Hubo un error al subir el documento.")
    else:
        form = ReemplazoDocumentoForm()

    return render(request, 'rrhh/reemplazar_documento.html', {
        'form': form,
        'documento': documento
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')
def exportar_documentos_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos Trabajadores"

    # Cabeceras
    headers = [
        "Trabajador",
        "Identidad",
        "Correo",
        "Tipo de documento",
        "Fecha de carga",
        "Fecha de emisión",
        "Fecha de expiración",
        "Estado"
    ]
    ws.append(headers)

    documentos = DocumentoTrabajador.objects.select_related(
        'trabajador', 'tipo_documento')

    for doc in documentos:
        estado = calcular_estado_documento(doc)
        ws.append([
            doc.trabajador.get_full_name(),
            doc.trabajador.identidad,
            doc.trabajador.email,
            doc.tipo_documento.nombre,
            doc.creado.strftime('%Y-%m-%d') if doc.creado else "—",
            doc.fecha_emision.strftime(
                '%Y-%m-%d') if doc.fecha_emision else "—",
            doc.fecha_vencimiento.strftime(
                '%Y-%m-%d') if doc.fecha_vencimiento else "—",
            estado
        ])

    # Ajustar el ancho de columnas automáticamente
    for col_num, _ in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col_num)].width = 25

    # Preparar archivo Excel para descarga
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename={smart_str("documentos_trabajadores.xlsx")}'
    wb.save(response)
    return response


@login_required
def mis_documentos(request):
    documentos = DocumentoTrabajador.objects.filter(trabajador=request.user)
    documentos_info = []

    for doc in documentos:
        documentos_info.append({
            "tipo": doc.tipo_documento.nombre,
            "archivo": doc.archivo,
            "fecha_emision": doc.fecha_emision,
            "fecha_vencimiento": doc.fecha_vencimiento,
            "estado": calcular_estado_documento(doc)
        })

    return render(request, 'rrhh/mis_documentos.html', {
        "documentos": documentos_info
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')
def eliminar_documento(request, id):
    documento = get_object_or_404(DocumentoTrabajador, id=id)

    if request.method == 'POST':
        try:
            if documento.archivo and documento.archivo.name:
                documento.archivo.delete(save=False)
            documento.delete()
            messages.success(
                request, "El documento fue eliminado correctamente.")
        except Exception as e:
            messages.error(
                request, f"Ocurrió un error al eliminar el documento: {e}")

        return redirect('rrhh:listado_documentos_trabajador')

    # Evita eliminar por GET accidental
    messages.error(request, "La eliminación debe hacerse mediante POST.")
    return redirect('rrhh:listado_documentos_trabajador')


@staff_member_required
@rol_requerido('admin', 'rrhh')
def eliminar_tipo_documento(request, pk):
    tipo = get_object_or_404(TipoDocumento, pk=pk)

    if request.method == 'POST':
        try:
            tipo.delete()
            messages.success(
                request, "✅ Tipo de documento eliminado correctamente.")
        except Exception as e:
            messages.error(request, f"❌ Error al eliminar: {e}")
        return redirect('rrhh:crear_tipo_documento')

    messages.warning(request, "⚠️ La eliminación debe hacerse mediante POST.")
    return redirect('rrhh:crear_tipo_documento')


@rol_requerido('rrhh', 'admin')
def listar_firmas(request):
    usuarios = CustomUser.objects.all()
    return render(request, 'rrhh/listar_firmas.html', {'usuarios': usuarios})


@rol_requerido('rrhh', 'admin')
def eliminar_firma(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)

    if user.firma_digital and user.firma_digital.name:
        try:
            # Imprimir ruta solo para depurar (puedes quitar esto después)
            print("Eliminando firma:", user.firma_digital.name)

            # Eliminar solo el archivo exacto
            user.firma_digital.delete(save=False)
            user.firma_digital = None
            user.save(update_fields=['firma_digital'])

            messages.success(
                request, f"Firma eliminada para {user.get_full_name()}.")

        except Exception as e:
            messages.error(request, f"No se pudo eliminar la firma: {e}")
    else:
        messages.info(request, "Este usuario no tiene una firma registrada.")

    return redirect('rrhh:listar_firmas')


@login_required
def registrar_firma_admin(request, user_id):
    from usuarios.models import CustomUser  # Asegúrate de importar el modelo
    if not request.user.is_staff:
        return redirect('no_autorizado')  # O cualquier lógica de permiso

    usuario = CustomUser.objects.get(id=user_id)
    try:
        redireccion = request.GET.get('next') or reverse('rrhh:listar_firmas')
    except NoReverseMatch:
        redireccion = '/dashboard_admin/'  # Fallback por si algo falla

    if request.method == 'POST':
        if 'eliminar_firma' in request.POST:
            if usuario.firma_digital:
                usuario.firma_digital.delete(save=True)
                messages.success(request, "Firma eliminada correctamente.")
            return redirect(request.path)

        data_url = request.POST.get('firma_digital')
        if not data_url:
            messages.error(request, "No se recibió ninguna firma.")
            return redirect(request.path)

        try:
            if not data_url.startswith('data:image/png;base64,'):
                raise ValueError("Formato inválido.")

            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)
            nombre_archivo = f"firmas/usuario_{usuario.id}_firma.png"

            if usuario.firma_digital and usuario.firma_digital.storage.exists(usuario.firma_digital.name):
                usuario.firma_digital.delete(save=False)

            usuario.firma_digital.save(nombre_archivo, content, save=True)
            messages.success(request, "Firma registrada correctamente.")
            return redirect(redireccion)

        except Exception as e:
            messages.error(request, f"Error al guardar firma: {e}")
            return redirect(request.path)

    # 👇 ESTA LÍNEA ES CLAVE PARA EVITAR TU ERROR
    return render(request, 'liquidaciones/registrar_firma.html', {
        'tecnico': usuario,
        'base_template': 'dashboard_admin/base.html'  # asegúrate que siempre se pase
    })


@login_required
@rol_requerido('rrhh', 'admin', 'pm')
def editar_cronograma_pago(request, usuario_id):
    usuario = get_object_or_404(CustomUser, id=usuario_id)
    cronograma, _ = CronogramaPago.objects.get_or_create(usuario=usuario)

    if request.method == 'POST':
        form = CronogramaPagoForm(request.POST, instance=cronograma)
        if form.is_valid():
            form.save()
            # Ajusta esta URL si es necesario
            return redirect('dashboard_admin:listar_usuarios')
    else:
        form = CronogramaPagoForm(instance=cronograma)

    return render(request, 'rrhh/editar_cronograma_pago.html', {
        'form': form,
        'usuario': usuario
    })


def ver_cronograma_pago(request):
    cronograma = CronogramaPago.objects.first()

    cronograma_mensual = []
    meses = [
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'
    ]

    for mes in meses:
        texto = getattr(cronograma, f"{mes}_texto")
        fecha = getattr(cronograma, f"{mes}_fecha")

        # Convertir texto a fecha si es string válido
        if texto:
            try:
                desde = datetime.strptime(texto, "%Y-%m-%d").date()
            except ValueError:
                desde = None
        else:
            desde = None

        cronograma_mensual.append({
            "mes": mes.capitalize(),
            "desde": desde,
            "hasta": fecha
        })

    context = {
        "cronograma": cronograma,
        "cronograma_mensual": cronograma_mensual
    }
    return render(request, "rrhh/ver_cronograma_pago.html", context)


@rol_requerido('admin', 'rrhh', 'pm')
def cronograma_pago_admin(request):
    cronograma, _ = CronogramaPago.objects.get_or_create(id=1)
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    if request.method == "POST":
        for mes in meses:
            texto = request.POST.get(f"{mes}_texto", "").strip()
            fecha_str = request.POST.get(f"{mes}_fecha", "").strip()

            setattr(cronograma, f"{mes}_texto", texto)

            if fecha_str:
                try:
                    fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                except ValueError:
                    fecha = None
            else:
                fecha = None

            setattr(cronograma, f"{mes}_fecha", fecha)

        cronograma.save()
        messages.success(request, "Cronograma actualizado correctamente.")
        return redirect('rrhh:cronograma_pago_admin')

    datos_meses = []
    for mes in meses:
        datos_meses.append({
            'nombre': mes,
            'texto': getattr(cronograma, f"{mes}_texto") or '',
            'fecha': getattr(cronograma, f"{mes}_fecha").strftime('%Y-%m-%d') if getattr(cronograma, f"{mes}_fecha") else '',
        })

    return render(request, 'rrhh/cronograma_pago_admin.html', {
        'cronograma': cronograma,
        'datos_meses': datos_meses
    })

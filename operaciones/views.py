# operaciones/views.py

import calendar
import csv
import io
import json
import locale
import logging
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import requests
import xlwt
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import (Case, Count, F, FloatField, IntegerField, Q, Sum,
                              Value, When)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.html import escape
from django.utils.timezone import is_aware, now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from facturacion.models import CartolaMovimiento
from notificaciones.services import notificar_asignacion_servicio_tecnicos
from operaciones.forms import AsignarTrabajadoresForm
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser
from usuarios.utils import \
    crear_notificacion  # asegúrate de tener esta función

from .forms import MovimientoUsuarioForm  # crearemos este form
from .forms import (ServicioCotizadoForm, SitioMovilForm, validar_rut_chileno,
                    verificar_rut_sii)
from .models import (RequisitoFoto, ServicioCotizado, SesionFotoTecnico,
                     SitioMovil)
from .views_fotos import _get_or_create_sesion, _norm_title

# Configurar locale para nombres de meses en español
try:
    locale.setlocale(locale.LC_TIME, 'es_CL.utf8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'es_ES.utf8')
    except locale.Error:
        locale.setlocale(locale.LC_TIME, '')  # Usa el del sistema



logger = logging.getLogger(__name__)

@login_required
@rol_requerido('usuario')
def buscar_mi_sitio(request):
    id_sitio = request.GET.get("id")
    sitio = None
    buscado = False

    if id_sitio:
        buscado = True
        try:
            obj = SitioMovil.objects.get(id_claro=id_sitio)

            sitio = {}
            for field in obj._meta.fields:
                if field.name != 'id':
                    valor = getattr(obj, field.name)
                    # Normalizar coordenadas si fueran string (por seguridad)
                    if field.name.lower() in ['latitud', 'longitud'] and isinstance(valor, str):
                        valor = valor.replace(",", ".")
                    sitio[field.verbose_name] = str(valor)

        except SitioMovil.DoesNotExist:
            sitio = None

    return render(request, 'operaciones/buscar_mi_sitio.html', {
        'sitio': sitio,
        'buscado': buscado
    })


@login_required
@rol_requerido('pm', 'admin', 'facturacion', 'supervisor')
def listar_sitios(request):
    id_claro = request.GET.get("id_claro", "")
    id_new = request.GET.get("id_new", "")

    # --- Manejo de cantidad con tope 100 ---
    raw_cantidad = request.GET.get("cantidad", "10")

    if raw_cantidad == "todos":
        per_page = 100
        cantidad = "100"
    else:
        try:
            per_page = int(raw_cantidad)
        except (TypeError, ValueError):
            per_page = 10
            cantidad = "10"
        else:
            if per_page < 1:
                per_page = 10
                cantidad = "10"
            elif per_page > 100:
                per_page = 100
                cantidad = "100"
            else:
                cantidad = raw_cantidad

    page_number = request.GET.get("page", 1)

    sitios = SitioMovil.objects.all()

    if id_claro:
        sitios = sitios.filter(id_claro__icontains=id_claro)
    if id_new:
        sitios = sitios.filter(id_sites_new__icontains=id_new)

    paginator = Paginator(sitios, per_page)
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/listar_sitios.html', {
        'sitios': pagina,
        'id_claro': id_claro,
        'id_new': id_new,
        'cantidad': cantidad,  # <- ya normalizado (máx 100)
        'pagina': pagina,
    })

@login_required
@rol_requerido('pm', 'admin', 'facturacion', 'supervisor')
def editar_sitio(request, pk: int):
    """
    Edita un Sitio Móvil. Soporta `next` en query para volver a la lista con filtros.
    """
    sitio = get_object_or_404(SitioMovil, pk=pk)
    # ej: ?next=/operaciones/sitios/?page=2&id_new=ABC
    next_url = request.GET.get("next")

    if request.method == "POST":
        form = SitioMovilForm(request.POST, instance=sitio)
        if form.is_valid():
            form.save()
            messages.success(request, "Sitio actualizado correctamente.")
            return redirect(next_url or reverse('operaciones:listar_sitios'))
        else:
            messages.error(request, "Revisa los campos del formulario.")
    else:
        form = SitioMovilForm(instance=sitio)

    return render(request, "operaciones/editar_sitio.html", {
        "form": form,
        "sitio": sitio,
        "next": next_url,
    })


# (Opcional) Eliminar


@login_required
@rol_requerido('admin')
def eliminar_sitio(request, pk: int):
    sitio = get_object_or_404(SitioMovil, pk=pk)
    next_url = request.GET.get("next")
    if request.method == "POST":
        sitio.delete()
        messages.success(request, "Sitio eliminado correctamente.")
        return redirect(next_url or reverse('operaciones:listar_sitios'))
    return render(request, "operaciones/eliminar_sitio.html", {"sitio": sitio, "next": next_url})


# operaciones/views.py

@login_required
@rol_requerido('admin')
def importar_sitios_excel(request):
    if request.method == 'POST' and request.FILES.get('archivo'):
        archivo = request.FILES['archivo']

        try:
            df = pd.read_excel(archivo)

            sitios_creados = 0
            for _, row in df.iterrows():
                # Normalizamos coordenadas reemplazando ',' por '.'
                latitud = float(str(row.get('Latitud')).replace(
                    ',', '.')) if pd.notna(row.get('Latitud')) else None
                longitud = float(str(row.get('Longitud')).replace(
                    ',', '.')) if pd.notna(row.get('Longitud')) else None

                sitio, created = SitioMovil.objects.update_or_create(
                    id_sites=row.get('ID Sites'),
                    defaults={
                        'id_claro': row.get('ID Claro'),
                        'id_sites_new': row.get('ID Sites NEW'),
                        'region': row.get('Región'),
                        'nombre': row.get('Nombre'),
                        'direccion': row.get('Direccion'),
                        'latitud': latitud,
                        'longitud': longitud,
                        'comuna': row.get('Comuna'),
                        'tipo_construccion': row.get('Tipo de contruccion'),
                        'altura': row.get('Altura'),
                        'candado_bt': row.get('Candado BT'),
                        'condiciones_acceso': row.get('Condiciones de acceso'),
                        'claves': row.get('Claves'),
                        'llaves': row.get('Llaves'),
                        'cantidad_llaves': row.get('Cantidad de Llaves'),
                        'observaciones_generales': row.get('Observaciones Generales'),
                        'zonas_conflictivas': row.get('Sitios zonas conflictivas'),
                        'alarmas': row.get('Alarmas'),
                        'guardias': row.get('Guardias'),
                        'nivel': row.get('Nivel'),
                        'descripcion': row.get('Descripción'),
                    }
                )
                if created:
                    sitios_creados += 1

            messages.success(
                request, f'Se importaron correctamente {sitios_creados} sitios.')
            return redirect('operaciones:listar_sitios')

        except Exception as e:
            messages.error(request, f'Ocurrió un error al importar: {str(e)}')

    return render(request, 'operaciones/importar_sitios.html')


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def listar_servicios_pm(request):
    # prioridad para PM
    estado_prioridad = Case(
        When(estado='cotizado', then=Value(1)),
        When(estado='en_ejecucion', then=Value(2)),
        # pendiente por asignar
        When(estado='aprobado_pendiente', then=Value(3)),
        default=Value(4),
        output_field=IntegerField()
    )

    # queryset base (excluye bonos/adelantos/descuentos)
    servicios = (
        ServicioCotizado.objects
        .exclude(estado__in=['ajuste_bono', 'ajuste_adelanto', 'ajuste_descuento'])
        .annotate(prioridad=estado_prioridad)
        .order_by('prioridad', '-fecha_creacion')
    )

    # Filtros
    du = request.GET.get('du', '')
    id_claro = request.GET.get('id_claro', '')
    id_new = request.GET.get('id_new', '')
    mes_produccion = request.GET.get('mes_produccion', '')
    estado = request.GET.get('estado', '')

    if du:
        du = du.strip().upper().replace("DU", "")
        servicios = servicios.filter(du__iexact=du)
    if id_claro:
        servicios = servicios.filter(id_claro__icontains=id_claro)
    if mes_produccion:
        servicios = servicios.filter(mes_produccion__icontains=mes_produccion)
    if id_new:
        servicios = servicios.filter(id_new__icontains=id_new)
    if estado:
        # si piden un estado de ajuste, no aparecerá por el exclude del queryset base
        servicios = servicios.filter(estado=estado)

   # ========= Paginación (máx. 100) =========
    cantidad_param = request.GET.get("cantidad", "10")

    if cantidad_param == "todos":
        # "todos" se interpreta como máximo 100
        per_page = 100
    else:
        try:
            # mínimo 5, máximo 100
            per_page = max(5, min(int(cantidad_param), 100))
        except ValueError:
            per_page = 10
            cantidad_param = "10"

    paginator = Paginator(servicios, per_page)
    page_number = request.GET.get("page") or 1
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/listar_servicios_pm.html', {
        'pagina': pagina,
        'cantidad': request.GET.get("cantidad", "10"),
        'filtros': {
            'du': du,
            'id_claro': id_claro,
            'mes_produccion': mes_produccion,
            'id_new': id_new,
            'estado': estado,
        },
        'estado_choices': ServicioCotizado.ESTADOS
    })


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def crear_servicio_cotizado(request):
    if request.method == 'POST':
        form = ServicioCotizadoForm(request.POST)
        if form.is_valid():
            print(form.cleaned_data)
            servicio = form.save(commit=False)
            servicio.creado_por = request.user
            servicio.estado = 'cotizado'
            servicio.save()
            return redirect('operaciones:listar_servicios_pm')
    else:
        form = ServicioCotizadoForm()
    return render(request, 'operaciones/crear_servicio_cotizado.html', {'form': form})


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def editar_servicio_cotizado(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    # --- Permitir edición siempre a PM, Admin y Facturación ---
    if servicio.estado not in ['cotizado', 'aprobado_pendiente'] and not (
        request.user.is_superuser or request.user.es_facturacion or request.user.es_pm
    ):
        messages.error(
            request, "No puedes editar esta cotización porque ya fue asignada.")
        return redirect('operaciones:listar_servicios_pm')

    if request.method == 'POST':
        form = ServicioCotizadoForm(request.POST, instance=servicio)
        if form.is_valid():
            servicio = form.save(commit=False)
            if servicio.id_claro:
                sitio = SitioMovil.objects.filter(
                    id_claro=servicio.id_claro).first()
                if sitio:
                    servicio.id_new = sitio.id_sites_new
                    servicio.region = sitio.region
            servicio.save()
            messages.success(request, "Cotización actualizada correctamente.")
            return redirect('operaciones:listar_servicios_pm')
        else:
            messages.error(request, "Corrige los errores en el formulario.")
    else:
        form = ServicioCotizadoForm(instance=servicio)

    return render(request, 'operaciones/editar_servicio_cotizado.html', {
        'form': form,
        'servicio': servicio
    })


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def eliminar_servicio_cotizado(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    # Validar estado permitido
    if servicio.estado not in ['cotizado', 'aprobado_pendiente'] and not (request.user.is_superuser or request.user.es_facturacion):
        messages.error(
            request, "No puedes eliminar esta cotización porque ya fue asignada.")
        return redirect('operaciones:listar_servicios_pm')

    servicio.delete()
    messages.success(request, "Cotización eliminada correctamente.")
    return redirect('operaciones:listar_servicios_pm')


def obtener_datos_sitio(request):
    id_claro = request.GET.get('id_claro')
    try:
        sitio = SitioMovil.objects.get(id_claro=id_claro)
        data = {
            'region': sitio.region,
            'id_new': sitio.id_sites_new  # <- nombre correcto del campo
        }
        return JsonResponse(data)
    except SitioMovil.DoesNotExist:
        return JsonResponse({'error': 'No encontrado'}, status=404)


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
@require_POST
def aprobar_cotizacion(request, pk):
    cotizacion = get_object_or_404(ServicioCotizado, pk=pk)

    # solo permite aprobar si está en 'cotizado'
    if cotizacion.estado != 'cotizado':
        messages.warning(
            request, "Esta cotización ya no está en estado 'cotizado'.")
        return redirect('operaciones:listar_servicios_pm')

    cotizacion.estado = 'aprobado_pendiente'
    cotizacion.pm_aprueba = request.user
    cotizacion.save()

    du_formateado = f"DU{str(cotizacion.du).zfill(8)}"

    # Notificar supervisores reales
    from usuarios.models import CustomUser
    supervisores = CustomUser.objects.filter(
        roles__nombre='supervisor', is_active=True)
    for supervisor in supervisores:
        crear_notificacion(
            usuario=supervisor,
            mensaje=f"Se ha aprobado una nueva cotización {du_formateado}.",
            url=reverse('operaciones:asignar_cotizacion', args=[cotizacion.pk])
        )

    messages.success(
        request, f"Cotización {du_formateado} aprobada correctamente.")
    return redirect('operaciones:listar_servicios_pm')


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def importar_cotizaciones(request):
    if request.method == 'POST' and request.FILES.get('archivo'):
        archivo = request.FILES['archivo']

        try:
            # Cargar archivo
            if archivo.name.endswith('.csv'):
                df = pd.read_csv(archivo)
            else:
                df = pd.read_excel(archivo)

            encabezados_validos = {
                'ID CLARO': 'id_claro',
                'Id Claro': 'id_claro',
                'REGION': 'region',
                'REGIÓN': 'region',
                'MES PRODUCCION': 'mes_produccion',
                'Mes Producción': 'mes_produccion',
                'ID NEW': 'id_new',
                'DETALLE TAREA': 'detalle_tarea',
                'MONTO COTIZADO': 'monto_cotizado',
                'MONTO MMOO': 'monto_mmoo',
            }
            df.rename(columns=encabezados_validos, inplace=True)

            columnas_requeridas = [
                'id_claro', 'mes_produccion', 'detalle_tarea', 'monto_cotizado']
            for col in columnas_requeridas:
                if col not in df.columns:
                    messages.error(
                        request, f'Falta la columna requerida: {col}')
                    return redirect('operaciones:listar_servicios_pm')

            # Lista para almacenar conflictos
            cotizaciones_omitidas = []
            cotizaciones_creadas = []

            for _, row in df.iterrows():
                id_claro = str(row['id_claro']).strip()

                # REGION
                region = row['region'] if 'region' in row and not pd.isna(row['region']) else (
                    id_claro.split('_')[0] if '_' in id_claro else '13'
                )

                # ID NEW
                if 'id_new' in row and not pd.isna(row['id_new']):
                    id_new = row['id_new']
                else:
                    try:
                        sitio = SitioMovil.objects.get(id_claro=id_claro)
                        id_new = sitio.id_sites_new
                    except SitioMovil.DoesNotExist:
                        messages.warning(
                            request, f"No se encontró ID NEW para ID CLARO {id_claro}. Se omitió.")
                        continue

                # MES PRODUCCIÓN
                valor = row['mes_produccion']
                if isinstance(valor, (datetime, pd.Timestamp)):
                    mes_produccion = valor.strftime('%B %Y').capitalize()
                else:
                    try:
                        fecha_parseada = pd.to_datetime(
                            str(valor), dayfirst=True, errors='coerce')
                        mes_produccion = (
                            fecha_parseada.strftime('%B %Y').capitalize()
                            if not pd.isna(fecha_parseada) else str(valor).capitalize()
                        )
                    except:
                        mes_produccion = str(valor).capitalize()

                # Verificar si ya existe cotización
                existente = ServicioCotizado.objects.filter(
                    mes_produccion=mes_produccion
                ).filter(models.Q(id_claro=id_claro) | models.Q(id_new=id_new)).first()

                if existente:
                    cotizaciones_omitidas.append({
                        'id_claro': id_claro,
                        'id_new': id_new,
                        'mes_produccion': mes_produccion,
                        'du': existente.du,
                        'estado': existente.get_estado_display()
                    })
                    continue

                # Crear nueva cotización
                ServicioCotizado.objects.create(
                    id_claro=id_claro,
                    region=region,
                    mes_produccion=mes_produccion,
                    id_new=id_new,
                    detalle_tarea=row['detalle_tarea'],
                    monto_cotizado=row['monto_cotizado'],
                    monto_mmoo=row['monto_mmoo'],
                    estado='cotizado',
                    creado_por=request.user
                )
                cotizaciones_creadas.append(f"{id_claro} - {mes_produccion}")

            # ¿Hay conflictos?
            if cotizaciones_omitidas:
                request.session['cotizaciones_omitidas'] = cotizaciones_omitidas
                messages.warning(
                    request, "Se detectaron cotizaciones ya registradas.")
                return redirect('operaciones:advertencia_cotizaciones_omitidas')

            messages.success(
                request, f'Se importaron correctamente {len(cotizaciones_creadas)} cotizaciones.')
            return redirect('operaciones:listar_servicios_pm')

        except Exception as e:
            messages.error(request, f'Error al importar: {e}')
            return redirect('operaciones:listar_servicios_pm')

    return render(request, 'operaciones/importar_cotizaciones.html')


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def advertencia_cotizaciones_omitidas(request):
    cotizaciones = request.session.get('cotizaciones_omitidas', [])

    if request.method == 'POST':
        if 'continuar' in request.POST:
            del request.session['cotizaciones_omitidas']
            messages.info(
                request, "Las cotizaciones omitidas fueron ignoradas. Las demás se importaron correctamente.")
            return redirect('operaciones:listar_servicios_pm')
        else:
            del request.session['cotizaciones_omitidas']
            messages.warning(request, "La importación fue cancelada.")
            return redirect('operaciones:listar_servicios_pm')

    return render(request, 'operaciones/advertencia_duplicados.html', {
        'cotizaciones': cotizaciones
    })


@login_required
@rol_requerido('supervisor', 'admin', 'facturacion', 'pm')
def listar_servicios_supervisor(request):
    # prioridad para ordenar
    estado_prioridad = Case(
        When(estado='aprobado_pendiente', then=Value(1)),
        When(estado__in=['asignado', 'en_progreso'], then=Value(2)),
        When(estado__in=['en_revision_supervisor',
             'finalizado_trabajador'], then=Value(3)),
        When(estado__in=['informe_subido', 'finalizado',
             'aprobado_supervisor', 'rechazado_supervisor'], then=Value(4)),
        default=Value(5),
        output_field=IntegerField()
    )

    # queryset base (excluye bonos/adelantos/descuentos)
    servicios = (
        ServicioCotizado.objects
        .filter(estado__in=[
            'aprobado_pendiente',
            'asignado',
            'en_progreso',
            'finalizado_trabajador',
            'en_revision_supervisor',
            'aprobado_supervisor',
            'rechazado_supervisor',
            'informe_subido',
            'finalizado',
        ])
        .exclude(estado__in=['ajuste_bono', 'ajuste_adelanto', 'ajuste_descuento'])
        .annotate(prioridad=estado_prioridad)
        .order_by('prioridad', '-du')
    )

    # Filtros
    du = request.GET.get('du', '')
    id_claro = request.GET.get('id_claro', '')
    id_new = request.GET.get('id_new', '')
    mes_produccion = request.GET.get('mes_produccion', '')
    estado = request.GET.get('estado', '')

    if du:
        du = du.strip().upper().replace('DU', '')
        servicios = servicios.filter(du__iexact=du)
    if id_claro:
        servicios = servicios.filter(id_claro__icontains=id_claro)
    if id_new:
        servicios = servicios.filter(id_new__icontains=id_new)
    if mes_produccion:
        servicios = servicios.filter(mes_produccion__icontains=mes_produccion)
    if estado:
        # aunque elijan ajuste_*, igual no aparecerán porque ya están excluidos arriba
        servicios = servicios.filter(estado=estado)

    # ========= Paginación (máx. 100) =========
    cantidad_param = request.GET.get("cantidad", "10")

    if cantidad_param == "todos":
        # "todos" se interpreta como máximo 100
        per_page = 100
    else:
        try:
            # mínimo 5, máximo 100
            per_page = max(5, min(int(cantidad_param), 100))
        except ValueError:
            per_page = 10
            cantidad_param = "10"

    paginator = Paginator(servicios, per_page)
    page_number = request.GET.get("page") or 1
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/listar_servicios_supervisor.html', {
        'pagina': pagina,
        'cantidad': request.GET.get("cantidad", "10"),
        'filtros': {
            'du': du,
            'id_claro': id_claro,
            'id_new': id_new,
            'mes_produccion': mes_produccion,
            'estado': estado,
        },
        'estado_choices': ServicioCotizado.ESTADOS
    })

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@require_POST
def reabrir_servicio(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    if servicio.estado != 'aprobado_supervisor':
        messages.error(
            request, "Solo se pueden reabrir servicios aprobados por el supervisor.")
        return redirect('operaciones:listar_servicios_supervisor')

    motivo = (request.POST.get('motivo') or "").strip()
    if not motivo:
        messages.error(
            request, "Debes indicar un motivo para reabrir el servicio.")
        return redirect('operaciones:listar_servicios_supervisor')

    with transaction.atomic():
        servicio.motivo_rechazo = motivo
        servicio.supervisor_aprobo = None
        servicio.supervisor_rechazo = None
        servicio.tecnico_finalizo = None
        servicio.tecnico_aceptado = None
        servicio.estado = 'asignado'
        servicio.save(update_fields=[
            'motivo_rechazo', 'supervisor_aprobo', 'supervisor_rechazo',
            'tecnico_finalizo', 'tecnico_aceptado', 'estado',
        ])

        sesion = _get_or_create_sesion(servicio)

        qs = SesionFotoTecnico.objects.filter(sesion=sesion)
        update_vals = {'estado': 'asignado'}
        if hasattr(SesionFotoTecnico, 'aceptado_en'):
            update_vals['aceptado_en'] = None
        if hasattr(SesionFotoTecnico, 'finalizado_en'):
            update_vals['finalizado_en'] = None
        if hasattr(SesionFotoTecnico, 'rechazado_en'):
            update_vals['rechazado_en'] = None
        qs.update(**update_vals)

    messages.success(
        request, f"Servicio DU{servicio.du} reabierto. Motivo: {motivo}")
    return redirect('operaciones:listar_servicios_supervisor')


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@csrf_exempt
def actualizar_motivo_rechazo(request, pk):
    if request.method == 'POST':
        servicio = get_object_or_404(ServicioCotizado, pk=pk)
        nuevo_motivo = request.POST.get('motivo', '').strip()
        servicio.motivo_rechazo = nuevo_motivo
        servicio.save()
        return JsonResponse({'success': True, 'motivo': nuevo_motivo})
    return JsonResponse({'success': False}, status=400)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def asignar_trabajadores(request, pk):
    cotizacion = get_object_or_404(ServicioCotizado, pk=pk)

    if request.method == 'POST':
        form = AsignarTrabajadoresForm(request.POST)
        if form.is_valid():
            trabajadores = form.cleaned_data['trabajadores']

            # Detectamos si ya tenía trabajadores antes del POST
            tenia_asignados = cotizacion.trabajadores_asignados.exists()

            # Actualizamos las relaciones M2M
            cotizacion.trabajadores_asignados.set(trabajadores)

            # Primera asignación:
            #  - venimos de "aprobado_pendiente"
            #  - y antes no tenía trabajadores
            #
            # Reasignación:
            #  - ya tenía trabajadores; NO tocamos el estado
            if not tenia_asignados and cotizacion.estado == 'aprobado_pendiente':
                cotizacion.estado = 'asignado'

            cotizacion.supervisor_asigna = request.user
            cotizacion.save()

            # 🔔 Notificar a los trabajadores seleccionados (campanita)
            for trabajador in trabajadores:
                crear_notificacion(
                    usuario=trabajador,
                    mensaje=f"Se te ha asignado una nueva tarea: DU{str(cotizacion.du).zfill(8)}.",
                    url=reverse('operaciones:mis_servicios_tecnico'),
                )

            # 📲 Notificación por Telegram usando el helper centralizado
            try:
                enlace_app = request.build_absolute_uri(
                    reverse('operaciones:mis_servicios_tecnico')
                )

                logs = notificar_asignacion_servicio_tecnicos(
                    servicio=cotizacion,
                    actor=request.user,
                    url=enlace_app,
                    extra={
                        "du": cotizacion.du,
                        "id_claro": cotizacion.id_claro,
                    },
                )

                # Log para debug: ver qué pasó con cada envío
                for log in logs:
                    logger.info(
                        "Telegram asignación servicio DU%s -> usuario_id=%s status=%s error=%s",
                        str(cotizacion.du).zfill(8),
                        log.usuario_id,
                        log.status,
                        getattr(log, "error", ""),
                    )

            except Exception:
                logger.exception("Error enviando notificación Telegram de asignación")

            messages.success(request, "Trabajadores asignados correctamente.")
            return redirect('operaciones:listar_servicios_supervisor')
    else:
        # Precargamos los técnicos que ya estaban asignados → se ve como REASIGNACIÓN
        form = AsignarTrabajadoresForm(
            initial={
                "trabajadores": cotizacion.trabajadores_asignados.all()
            }
        )

    return render(request, 'operaciones/asignar_trabajadores.html', {
        'cotizacion': cotizacion,
        'form': form
    })

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def exportar_servicios_supervisor(request):
    servicios = ServicioCotizado.objects.filter(
        estado__in=[
            'aprobado_pendiente', 'asignado', 'en_proceso',
            'en_revision_supervisor',
            'rechazado_supervisor', 'aprobado_supervisor'
        ]
    )

    data = []
    for s in servicios:
        asignados = ', '.join(
            [f"{u.first_name} {u.last_name}" for u in s.trabajadores_asignados.all()]
        )
        data.append({
            'DU': f'DU{s.du}',
            'ID Claro': s.id_claro,
            'Región': s.region,
            'Mes Producción': s.mes_produccion or '',
            'ID NEW': s.id_new,
            'Detalle Tarea': s.detalle_tarea,
            'Monto MMOO': float(s.monto_mmoo) if s.monto_mmoo else 0,
            'Asignados': asignados,
            'Fecha Fin': s.fecha_aprobacion_supervisor.strftime("%d-%m-%Y") if s.fecha_aprobacion_supervisor else '',
            'Estado': dict(s.ESTADOS).get(s.estado, s.estado),
        })

    df = pd.DataFrame(data)
    columnas = [
        'DU', 'ID Claro', 'Región', 'Mes Producción',
        'ID NEW', 'Detalle Tarea', 'Monto MMOO',
        'Asignados', 'Fecha Fin', 'Estado'
    ]
    df = df[columnas]

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=servicios_supervisor.xlsx'
    df.to_excel(response, index=False)
    return response


@login_required
@rol_requerido('usuario')
def mis_servicios_tecnico(request):
    usuario = request.user

    # Estados de ajustes que NO deben aparecer aquí
    AJUSTES_SET = {'ajuste_bono', 'ajuste_adelanto', 'ajuste_descuento'}

    estado_prioridad = Case(
        When(estado='en_progreso', then=Value(1)),
        When(estado='finalizado_trabajador', then=Value(2)),
        When(estado='asignado', then=Value(3)),
        default=Value(4),
        output_field=IntegerField()
    )

    servicios = (
        ServicioCotizado.objects
        .filter(trabajadores_asignados=usuario)
        # Oculta cotizados, aprobados por supervisor y TODOS los ajustes
        .exclude(estado__in=['cotizado', 'aprobado_supervisor'] + list(AJUSTES_SET))
        .annotate(prioridad=estado_prioridad)
        .order_by('prioridad', '-du')
    )

    servicios_info = []
    for servicio in servicios:
        # ===== Monto MMOO por técnico con decimales =====
        monto_total = (
            servicio.monto_mmoo
            or servicio.monto_cotizado
            or Decimal("0")
        )

        if not isinstance(monto_total, Decimal):
            try:
                monto_total = Decimal(str(monto_total))
            except Exception:
                monto_total = Decimal("0")

        total_tecnicos = servicio.trabajadores_asignados.count() or 1

        try:
            monto_tecnico = (monto_total / Decimal(total_tecnicos)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        except Exception:
            monto_tecnico = Decimal("0.00")

        # string para el template (ej: "1.50")
        monto_str = f"{monto_tecnico:.2f}"
        # ===============================================

        sesion = _get_or_create_sesion(servicio)
        a = sesion.asignaciones.filter(tecnico=usuario).first()
        if not a:
            # si entro por primera vez, me creo mi asignación en "asignado"
            a = SesionFotoTecnico.objects.create(
                sesion=sesion, tecnico=usuario, estado='asignado'
            )

        # yo acepté ⇢ mi asignación en_proceso
        yo_acepte = (a.estado == 'en_proceso')
        # puedo aceptar ⇢ mi asignación aún está en "asignado"
        puedo_aceptar = (a.estado == 'asignado')

        # 🔧 ARREGLO DE CONSISTENCIA:
        # Si YO ya acepté pero el servicio sigue en 'asignado',
        # lo promovemos a 'en_progreso' y marcamos tecnico_aceptado si falta.
        if yo_acepte and servicio.estado == 'asignado':
            servicio.estado = 'en_progreso'
            if not servicio.tecnico_aceptado_id:
                servicio.tecnico_aceptado = usuario
            servicio.save(update_fields=['estado', 'tecnico_aceptado'])

        # Solo contamos aceptados / total entre los técnicos actualmente asignados
        assigned_ids = list(
            servicio.trabajadores_asignados.values_list("id", flat=True)
        )

        if assigned_ids:
            aceptados = sesion.asignaciones.filter(
                aceptado_en__isnull=False,
                tecnico_id__in=assigned_ids,
            ).count()
            total = sesion.asignaciones.filter(
                tecnico_id__in=assigned_ids,
            ).count()
        else:
            aceptados = 0
            total = 0

        servicios_info.append({
            'servicio': servicio,
            'monto_tecnico': monto_tecnico,   # por si lo quieres usar después
            'monto_str': monto_str,           # 👈 este es el que usa tu template
            'yo_acepte': yo_acepte,
            'puedo_aceptar': puedo_aceptar,
            'aceptados': aceptados,
            'total': total,
        })

    return render(request, 'operaciones/mis_servicios_tecnico.html', {
        'servicios_info': servicios_info
    })


@login_required
@rol_requerido('usuario')
def ir_a_upload_fotos(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "No tienes permiso en este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    sesion = _get_or_create_sesion(servicio)
    a = sesion.asignaciones.filter(tecnico=request.user).first()
    if not a:
        a = SesionFotoTecnico.objects.create(
            sesion=sesion, tecnico=request.user, estado='asignado'
        )

    # ✅ Sólo puede entrar si ya aceptó (en_proceso) o si fue rechazado con reintento
    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        messages.info(
            request, "Debes aceptar tu asignación antes de subir fotos.")
        return redirect('operaciones:mis_servicios_tecnico')

    return redirect('operaciones:fotos_upload', pk=a.pk)


from django.utils import timezone


@login_required
@rol_requerido('usuario')
def aceptar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    # Debe ser un técnico asignado a este servicio
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "No tienes permiso para aceptar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Estados donde ya no corresponde aceptar
    estados_bloqueados = {
        'finalizado_trabajador',
        'en_revision_supervisor',
        'aprobado_supervisor',
        'informe_subido',
        'finalizado',
    }
    if servicio.estado in estados_bloqueados:
        messages.warning(request, "Este servicio ya no está disponible para aceptar.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Estados desde los que SÍ se puede aceptar
    estados_permitidos = {'asignado', 'en_progreso', 'rechazado_supervisor'}
    if servicio.estado not in estados_permitidos:
        messages.warning(request, "Este servicio no se puede aceptar en su estado actual.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Crear/obtener sesión de fotos del servicio
    sesion = _get_or_create_sesion(servicio)

    # Mi asignación individual dentro de la sesión
    asignacion, _ = SesionFotoTecnico.objects.get_or_create(
        sesion=sesion,
        tecnico=request.user,
        defaults={'estado': 'asignado'}
    )

    # Si ya estaba en otro estado distinto de 'asignado'
    # solo la "reiniciamos" a 'asignado' cuando venimos de un rechazo.
    if asignacion.estado != 'asignado':
        if servicio.motivo_rechazo and servicio.estado in ['asignado', 'en_progreso', 'rechazado_supervisor']:
            asignacion.estado = 'asignado'
            if hasattr(asignacion, 'aceptado_en'):
                asignacion.aceptado_en = None
            asignacion.save(update_fields=['estado'] + (['aceptado_en'] if hasattr(asignacion, 'aceptado_en') else []))
        else:
            messages.info(request, "Ya habías aceptado esta asignación.")
            return redirect('operaciones:mis_servicios_tecnico')

    # Marcar mi aceptación
    asignacion.estado = 'en_proceso'
    if hasattr(asignacion, 'aceptado_en'):
        asignacion.aceptado_en = timezone.now()
        asignacion.save(update_fields=['estado', 'aceptado_en'])
    else:
        asignacion.save(update_fields=['estado'])

    # Pasar el servicio a EN PROGRESO si aún no lo está (incluye caso rechazado_supervisor)
    if servicio.estado != 'en_progreso':
        servicio.estado = 'en_progreso'
        servicio.tecnico_aceptado = request.user
        servicio.save(update_fields=['estado', 'tecnico_aceptado'])

    messages.success(request, "Has aceptado el servicio. Ya puedes subir fotos.")
    return redirect('operaciones:mis_servicios_tecnico')

@login_required
@rol_requerido('usuario')
def finalizar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    # Debe ser un técnico asignado
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "Solo los técnicos asignados pueden finalizar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    if servicio.estado != 'en_progreso':
        messages.warning(request, "Este servicio no está en progreso.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Asegurar sesión y la asignación del usuario
    sesion = _get_or_create_sesion(servicio)
    a = sesion.asignaciones.filter(tecnico=request.user).first()
    if not a:
        a = SesionFotoTecnico.objects.create(
            sesion=sesion,
            tecnico=request.user,
            estado='asignado'
        )

    # 🔧 Imports locales
    import re
    import unicodedata

    from django.db import transaction

    from .models import EvidenciaFoto, RequisitoFoto

    # ==================== Helpers locales (activos + norma) ====================
    def _norm_title(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = re.sub(r"\s+", " ", s)
        return s

    def _canon_requisitos_por_norma():
        """
        Reúne TODOS los requisitos ACTIVO=True de la sesión por título normalizado (norm):
          norm -> {"id","titulo","obligatorio","orden","ids": set(ids_equivalentes)}
        """
        canon_by_norm = {}
        qs = (RequisitoFoto.objects
              .filter(tecnico_sesion__sesion=sesion, activo=True)
              .values("id", "titulo", "obligatorio", "orden"))
        for r in qs:
            norm = _norm_title(r["titulo"])
            b = canon_by_norm.get(norm)
            if not b:
                canon_by_norm[norm] = {
                    "id": r["id"], "titulo": r["titulo"], "obligatorio": r["obligatorio"],
                    "orden": r["orden"], "ids": {r["id"]}
                }
            else:
                b["ids"].add(r["id"])
                if (r["orden"], r["id"]) < (b["orden"], b["id"]):
                    b["id"] = r["id"]
                    b["titulo"] = r["titulo"]
                    b["obligatorio"] = r["obligatorio"]
                    b["orden"] = r["orden"]
        return canon_by_norm

    def _global_done_por_norma(canon_by_norm: dict):
        """
        True si existe al menos UNA evidencia para cualquiera de los IDs del bloque (norma).
        """
        done = {norm: False for norm in canon_by_norm.keys()}
        if not canon_by_norm:
            return done
        all_ids = [rid for b in canon_by_norm.values() for rid in b["ids"]]
        ids_with_ev = set(
            EvidenciaFoto.objects
            .filter(requisito_id__in=all_ids)
            .values_list("requisito_id", flat=True)
        )
        for norm, b in canon_by_norm.items():
            if any(rid in ids_with_ev for rid in b["ids"]):
                done[norm] = True
        return done
    # ==========================================================================

    # 1) Validar fotos requeridas a nivel proyecto (solo ACTIVO=True y por NORMA)
    canon = _canon_requisitos_por_norma()
    done_by_norm = _global_done_por_norma(canon)

    missing_titles = []
    for norm, b in sorted(canon.items(), key=lambda x: (x[1]["orden"], x[1]["id"])):
        if b["obligatorio"] and not done_by_norm.get(norm, False):
            missing_titles.append(b["titulo"])

    if missing_titles:
        messages.error(
            request,
            "No puedes finalizar: faltan fotos requeridas de " + ", ".join(missing_titles) +
            ". Carga las evidencias para continuar."
        )
        return redirect('operaciones:fotos_upload', pk=a.pk)

    # 2) Validar que TODOS los técnicos asignados (actualmente asignados) hayan aceptado
    assigned_ids = list(servicio.trabajadores_asignados.values_list('id', flat=True))
    for asg in sesion.asignaciones.filter(tecnico_id__in=assigned_ids):
        # si está todavía en "asignado" y sin aceptado_en ⇒ NO ha aceptado
        if asg.estado == "asignado" and not getattr(asg, "aceptado_en", None):
            messages.error(request, "Aún hay técnicos sin aceptar la asignación. No se puede finalizar.")
            return redirect('operaciones:fotos_upload', pk=a.pk)

    # 3) Si todo ok, mover a revisión de supervisor
    now_ = timezone.now()
    with transaction.atomic():
        sesion.asignaciones.update(estado="en_revision_supervisor", finalizado_en=now_)
        sesion.estado = "en_revision_supervisor"
        sesion.save(update_fields=["estado"])

        servicio.estado = "en_revision_supervisor"
        servicio.tecnico_finalizo = request.user
        servicio.save(update_fields=["estado", "tecnico_finalizo"])

    messages.success(request, "Enviado a revisión del supervisor (proyecto completo).")
    return redirect('operaciones:mis_servicios_tecnico')

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def aprobar_asignacion(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    if servicio.estado == 'asignado':
        servicio.estado = 'en_progreso'
    elif servicio.estado == 'finalizado_trabajador':
        servicio.estado = 'aprobado_supervisor'
        servicio.supervisor_aprobo = request.user
        servicio.fecha_aprobacion_supervisor = now()
    else:
        messages.warning(
            request, "Este servicio no está en un estado aprobable.")
        return redirect('operaciones:listar_servicios_supervisor')

    servicio.save()
    messages.success(request, "Aprobación realizada correctamente.")
    return redirect('operaciones:listar_servicios_supervisor')


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def rechazar_asignacion(request, pk):
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo', '').strip()
        servicio = get_object_or_404(ServicioCotizado, pk=pk)

        if servicio.estado in ['asignado', 'finalizado_trabajador']:
            servicio.estado = 'rechazado_supervisor'
            servicio.motivo_rechazo = motivo
            servicio.supervisor_rechazo = request.user
            servicio.save()

            messages.error(
                request, f"Asignación rechazada correctamente. Motivo: {motivo}")
        else:
            messages.warning(
                request, "Este servicio no está en un estado válido para rechazo.")
    else:
        messages.error(request, "Acceso inválido al rechazo.")

    return redirect('operaciones:listar_servicios_supervisor')


from datetime import datetime

import xlwt
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import is_aware

from facturacion.models import CartolaMovimiento

# ==========================================================
# Helpers KM (declaración inmediata + aprobación posterior)
# ==========================================================

def _cartola_tiene_campo_km():
    return any(f.name == "kilometraje" for f in CartolaMovimiento._meta.fields)


def _normalizar_km(valor):
    """
    Convierte kilometraje a int.
    Acepta: 1200, "1.200", "1,200", "1200 km"
    """
    if valor in (None, ""):
        return None
    try:
        s = str(valor).strip()
        s = "".join(ch for ch in s if ch.isdigit())
        if not s:
            return None
        km = int(s)
        return km if km >= 0 else None
    except Exception:
        return None


def _validar_km_no_regresivo(usuario, fecha_transaccion, km_nuevo, exclude_mov_id=None):
    """
    Regla:
    - Para una fecha >= anterior, el km no puede ser menor al último km conocido antes/igual a esa fecha.
    - Para una fecha <= posterior, el km no puede ser mayor al primer km conocido después/igual a esa fecha.
    Esto evita casos tipo: ayer 1200 / hoy 900.
    """
    if km_nuevo is None or not _cartola_tiene_campo_km():
        return True, None

    qs = CartolaMovimiento.objects.filter(
        usuario=usuario,
        fecha_transaccion__isnull=False,
    ).exclude(tipo__categoria='abono')

    # Solo comparar contra movimientos que sí tengan km
    qs = qs.exclude(kilometraje__isnull=True)

    if exclude_mov_id:
        qs = qs.exclude(pk=exclude_mov_id)

    # vecino anterior (misma fecha o menor)
    anterior = (
        qs.filter(fecha_transaccion__lte=fecha_transaccion)
        .order_by('-fecha_transaccion', '-id')
        .first()
    )

    if anterior and anterior.kilometraje is not None and km_nuevo < int(anterior.kilometraje):
        return False, (
            f"El kilometraje ({km_nuevo}) no puede ser menor al último registrado "
            f"({int(anterior.kilometraje)}) del {anterior.fecha_transaccion.strftime('%d-%m-%Y')}."
        )

    # vecino posterior (misma fecha o mayor)
    posterior = (
        qs.filter(fecha_transaccion__gte=fecha_transaccion)
        .order_by('fecha_transaccion', 'id')
        .first()
    )

    if posterior and posterior.kilometraje is not None and km_nuevo > int(posterior.kilometraje):
        return False, (
            f"El kilometraje ({km_nuevo}) no puede ser mayor a un registro posterior "
            f"({int(posterior.kilometraje)}) del {posterior.fecha_transaccion.strftime('%d-%m-%Y')}."
        )

    return True, None


def _registrar_km_en_flota_pendiente(movimiento, request_user=None):
    """
    Hook para flota al MOMENTO DE DECLARAR (pendiente de aprobación).
    Si no existe integración aún, no rompe el flujo.
    """
    if not _cartola_tiene_campo_km():
        return

    km = getattr(movimiento, "kilometraje", None)
    if km in (None, ""):
        return

    # 🔌 Integra aquí con tu app de flota (cuando tengas el servicio listo)
    # Ideal: guardar registro "pendiente_aprobacion=True"
    try:
        # Ejemplo (descomenta cuando exista):
        # from flota.services import registrar_kilometraje_desde_rendicion
        # registrar_kilometraje_desde_rendicion(
        #     usuario=movimiento.usuario,
        #     fecha=movimiento.fecha_transaccion or movimiento.fecha.date(),
        #     kilometraje=int(km),
        #     cartola_movimiento=movimiento,
        #     aprobado=False,
        #     registrado_por=request_user,
        # )
        pass
    except Exception:
        # No bloqueamos la rendición por un error de integración de flota
        pass


def _confirmar_km_en_flota_si_aplica(movimiento, request_user=None):
    """
    Hook para flota cuando finanzas APRUEBA.
    Ideal: marcar el km pendiente como aprobado/confirmado.
    """
    if not _cartola_tiene_campo_km():
        return

    km = getattr(movimiento, "kilometraje", None)
    if km in (None, ""):
        return

    try:
        # Ejemplo (descomenta cuando exista):
        # from flota.services import confirmar_kilometraje_rendicion
        # confirmar_kilometraje_rendicion(
        #     cartola_movimiento=movimiento,
        #     aprobado_por=request_user,
        # )
        pass
    except Exception:
        # No bloqueamos aprobación financiera por integración externa
        pass


# ==========================================================
# Usuario
# ==========================================================

@login_required
def mis_rendiciones(request):
    from datetime import datetime

    from django.core.exceptions import ValidationError
    from django.core.paginator import Paginator
    from django.db import transaction
    from django.db.models import Sum
    from django.utils import timezone

    from flota.models import VehicleService

    user = request.user

    def _map_legacy_service_type(tipo_servicio_obj):
        """
        Mapea el tipo configurable de flota al choice legacy de VehicleService.service_type.
        (VehicleService.service_type sigue siendo obligatorio)
        """
        if not tipo_servicio_obj:
            return "otro"

        n = (getattr(tipo_servicio_obj, "name", "") or "").strip().lower()

        if "combustible" in n:
            return "combustible"
        if "aceite" in n:
            return "aceite"
        if "neumatic" in n or "neumát" in n:
            return "neumaticos"
        if "revision tecnica" in n or "revisión técnica" in n or "revision_tecnica" in n:
            return "revision_tecnica"
        if "permiso" in n and "circul" in n:
            return "permiso_circulacion"

        return "otro"

    def _es_tipo_servicios(tipo_obj):
        """
        Verifica si el tipo de movimiento corresponde a 'Servicios'
        usando SOLO el nombre del TipoGasto.
        """
        if not tipo_obj:
            return False

        nombre = (getattr(tipo_obj, "nombre", None) or getattr(tipo_obj, "name", None) or "").strip().lower()
        return "servicio" in nombre

    def _validar_no_futuro(fecha_tx, hora_servicio=None, es_servicio=False):
        """
        Valida que:
        - fecha_transaccion no sea futura
        - si es servicio (flota), fecha+hora del servicio no sea futura
        Devuelve: (ok: bool, mensaje: str|None)
        """
        if not fecha_tx:
            return True, None

        now_local = timezone.localtime(timezone.now())
        hoy_local = now_local.date()

        if fecha_tx > hoy_local:
            return False, "No puedes registrar una rendición con fecha futura."

        if es_servicio and hora_servicio:
            try:
                dt_servicio = datetime.combine(fecha_tx, hora_servicio)
                tz = timezone.get_current_timezone()
                dt_servicio = timezone.make_aware(dt_servicio, tz) if timezone.is_naive(dt_servicio) else dt_servicio

                if dt_servicio > now_local:
                    return False, "No puedes registrar una rendición con una hora de servicio futura."
            except Exception:
                pass

        return True, None

    def _render_con_error(form_obj):
        movimientos_error = (
            CartolaMovimiento.objects
            .filter(usuario=user)
            .select_related(
                "proyecto", "tipo",
                "vehiculo_flota", "tipo_servicio_flota", "servicio_flota"
            )
            .order_by('-fecha')
        )

        paginator_error = Paginator(movimientos_error, 10)
        pagina_error = paginator_error.get_page(1)

        saldo_disponible_error = (
            (movimientos_error.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
             .aggregate(total=Sum('abonos'))['total'] or 0)
            -
            (movimientos_error.exclude(tipo__categoria="abono")
             .filter(status="aprobado_finanzas")
             .aggregate(total=Sum('cargos'))['total'] or 0)
        )

        saldo_pendiente_error = (
            movimientos_error.filter(tipo__categoria="abono")
            .exclude(status="aprobado_abono_usuario")
            .aggregate(total=Sum('abonos'))['total'] or 0
        )

        saldo_rendido_error = (
            movimientos_error.exclude(tipo__categoria="abono")
            .exclude(status="aprobado_finanzas")
            .aggregate(total=Sum('cargos'))['total'] or 0
        )

        return render(request, 'operaciones/mis_rendiciones.html', {
            'pagina': pagina_error,
            'cantidad': '10',
            'saldo_disponible': saldo_disponible_error,
            'saldo_pendiente': saldo_pendiente_error,
            'saldo_rendido': saldo_rendido_error,
            'form': form_obj,
        })

    # --- Crear nueva rendición ---
    if request.method == 'POST':
        # ✅ IMPORTANTE: pasar POST + FILES + user
        form = MovimientoUsuarioForm(request.POST, request.FILES, user=request.user)

        if form.is_valid():
            cd = form.cleaned_data

            last_mov = (
                CartolaMovimiento.objects
                .filter(usuario=user)
                .order_by('-id')
                .first()
            )

            def norm(value):
                return (value or "").strip()

            is_duplicate = False
            if last_mov:
                is_duplicate = (
                    getattr(last_mov, "proyecto_id", None) == getattr(cd.get("proyecto"), "id", None) and
                    getattr(last_mov, "tipo_id", None) == getattr(cd.get("tipo"), "id", None) and
                    getattr(last_mov, "numero_doc", None) == cd.get("numero_doc") and
                    getattr(last_mov, "cargos", None) == cd.get("cargos") and
                    norm(getattr(last_mov, "rut_factura", "")) == norm(cd.get("rut_factura")) and
                    norm(getattr(last_mov, "observaciones", "")) == norm(cd.get("observaciones")) and
                    getattr(last_mov, "fecha_transaccion", None) == cd.get("fecha_transaccion")
                )

            if is_duplicate:
                messages.warning(
                    request,
                    "Esta rendición ya fue registrada hace unos instantes. No se creó un duplicado."
                )
                return redirect('operaciones:mis_rendiciones')

            try:
                with transaction.atomic():
                    mov = form.save(commit=False)
                    mov.usuario = user
                    mov.fecha = timezone.now()
                    mov.status = 'pendiente_supervisor'
                    mov.comprobante = cd.get("comprobante")

                    # ✅ Validación de kilometraje no regresivo (campo antiguo, si existe)
                    if _cartola_tiene_campo_km():
                        km_nuevo = _normalizar_km(cd.get("kilometraje"))
                        if km_nuevo is not None:
                            ok_km, msg_km = _validar_km_no_regresivo(
                                usuario=user,
                                fecha_transaccion=cd.get("fecha_transaccion"),
                                km_nuevo=km_nuevo,
                            )
                            if not ok_km:
                                form.add_error('kilometraje', msg_km)
                                return _render_con_error(form)

                            setattr(mov, "kilometraje", km_nuevo)

                    tipo_mov = cd.get("tipo")

                    es_rendicion_flota = bool(
                        _es_tipo_servicios(tipo_mov) and
                        cd.get("vehiculo_flota") and
                        cd.get("tipo_servicio_flota") and
                        cd.get("fecha_servicio_flota") and
                        (cd.get("hora_servicio_flota") is not None)
                    )

                    # ✅ Validación fecha/hora no futura
                    ok_no_futuro, msg_no_futuro = _validar_no_futuro(
                        fecha_tx=cd.get("fecha_transaccion"),
                        hora_servicio=cd.get("hora_servicio_flota"),
                        es_servicio=es_rendicion_flota,
                    )
                    if not ok_no_futuro:
                        if es_rendicion_flota and cd.get("hora_servicio_flota"):
                            form.add_error("hora_servicio_flota", msg_no_futuro)
                        else:
                            form.add_error("fecha_transaccion", msg_no_futuro)
                        raise ValidationError("Fecha/hora futura no permitida.")

                    # ✅ Guardar rendición primero (para tener PK)
                    mov.save()

                    # ✅ Crear servicio en FLOTA automáticamente
                    if es_rendicion_flota:
                        vehiculo = cd.get("vehiculo_flota")
                        tipo_servicio_flota = cd.get("tipo_servicio_flota")
                        fecha_servicio = cd.get("fecha_servicio_flota")
                        hora_servicio = cd.get("hora_servicio_flota")
                        km_servicio = cd.get("kilometraje_servicio_flota")
                        monto_servicio = cd.get("cargos") or 0

                        ok_flota_km, msg_flota_km, ultimo_ref, servicio_conflicto = _validar_km_servicio_flota_vs_ultimo(
                            vehicle_id=vehiculo.id,
                            fecha_servicio=fecha_servicio,
                            hora_servicio=hora_servicio,
                            km_nuevo=km_servicio,
                        )

                        if not ok_flota_km:
                            try:
                                rendicion_conflicto = (
                                    CartolaMovimiento.objects
                                    .filter(servicio_flota=servicio_conflicto)
                                    .only("id", "status")
                                    .first()
                                ) if servicio_conflicto else None
                            except Exception:
                                rendicion_conflicto = None

                            if rendicion_conflicto and getattr(rendicion_conflicto, "status", None) in {
                                "aprobado_supervisor",
                                "aprobado_finanzas",
                                "aprobado_abono_usuario",
                                "aprobado",
                            }:
                                form.add_error(
                                    "kilometraje_servicio_flota",
                                    (
                                        f"{msg_flota_km} La rendición anterior ya fue aprobada y no se puede editar. "
                                        f"Debes solicitar que la rechacen para corregirla."
                                    )
                                )
                            else:
                                form.add_error(
                                    "kilometraje_servicio_flota",
                                    f"{msg_flota_km} Edita el registro anterior o modifica la hora del servicio."
                                )

                            raise ValidationError("Kilometraje de flota inválido.")

                        legacy_type = _map_legacy_service_type(tipo_servicio_flota)

                        servicio = VehicleService.objects.create(
                            vehicle=vehiculo,
                            service_type=legacy_type,
                            service_type_obj=tipo_servicio_flota,
                            title=f"Rendición #{mov.pk}",
                            service_date=fecha_servicio,
                            service_time=hora_servicio,
                            kilometraje_declarado=km_servicio if km_servicio not in (None, "") else None,
                            monto=monto_servicio,
                            notes=(
                                f"Creado desde rendición #{mov.pk} por {user.get_full_name() or user.username}. "
                                f"Obs: {cd.get('observaciones') or ''}"
                            ).strip(),
                        )

                        mov.servicio_flota = servicio
                        mov.vehiculo_flota = vehiculo
                        mov.tipo_servicio_flota = tipo_servicio_flota
                        mov.fecha_servicio_flota = fecha_servicio
                        mov.hora_servicio_flota = hora_servicio
                        mov.kilometraje_servicio_flota = km_servicio if km_servicio not in (None, "") else None
                        mov.tipo_servicio_flota_snapshot = getattr(tipo_servicio_flota, "name", None)

                        mov.save(update_fields=[
                            "servicio_flota",
                            "vehiculo_flota",
                            "tipo_servicio_flota",
                            "fecha_servicio_flota",
                            "hora_servicio_flota",
                            "kilometraje_servicio_flota",
                            "tipo_servicio_flota_snapshot",
                        ])

                    _registrar_km_en_flota_pendiente(mov, request_user=request.user)

                messages.success(request, "Rendición registrada correctamente.")
                return redirect('operaciones:mis_rendiciones')

            except ValidationError as e:
                try:
                    if hasattr(e, "message_dict"):
                        for field, errs in e.message_dict.items():
                            for err in errs:
                                form.add_error(field if field in form.fields else None, str(err))
                    else:
                        if not form.non_field_errors() and not any(form.errors.values()):
                            form.add_error(None, str(e))
                except Exception:
                    form.add_error(None, str(e))

                return _render_con_error(form)

    else:
        # ✅ IMPORTANTE: pasar user también en GET para filtrar vehículos asignados
        form = MovimientoUsuarioForm(user=request.user)

    # --- Filtros y Paginación ---
    raw_cantidad = request.GET.get('cantidad', '10')

    if raw_cantidad == 'todos':
        per_page = 100
        cantidad = '100'
    else:
        try:
            per_page = int(raw_cantidad)
        except (TypeError, ValueError):
            per_page = 10
            cantidad = '10'
        else:
            if per_page < 1:
                per_page = 10
                cantidad = '10'
            elif per_page > 100:
                per_page = 100
                cantidad = '100'
            else:
                cantidad = raw_cantidad

    movimientos = (
        CartolaMovimiento.objects
        .filter(usuario=user)
        .select_related(
            "proyecto", "tipo",
            "vehiculo_flota", "tipo_servicio_flota", "servicio_flota"
        )
        .order_by('-fecha')
    )

    paginator = Paginator(movimientos, per_page)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    saldo_disponible = (
        (movimientos.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
         .aggregate(total=Sum('abonos'))['total'] or 0)
        -
        (movimientos.exclude(tipo__categoria="abono")
         .filter(status="aprobado_finanzas")
         .aggregate(total=Sum('cargos'))['total'] or 0)
    )

    saldo_pendiente = (
        movimientos.filter(tipo__categoria="abono")
        .exclude(status="aprobado_abono_usuario")
        .aggregate(total=Sum('abonos'))['total'] or 0
    )

    saldo_rendido = (
        movimientos.exclude(tipo__categoria="abono")
        .exclude(status="aprobado_finanzas")
        .aggregate(total=Sum('cargos'))['total'] or 0
    )

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad,
        'saldo_disponible': saldo_disponible,
        'saldo_pendiente': saldo_pendiente,
        'saldo_rendido': saldo_rendido,
        'form': form,
    })

@login_required
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Abono aprobado correctamente.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def rechazar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if request.method == "POST":
        motivo = request.POST.get("motivo", "")
        mov.status = "rechazado_abono_usuario"
        mov.motivo_rechazo = motivo
        mov.save()
        messages.error(request, "Abono rechazado y enviado a Finanzas para revisión.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def editar_rendicion(request, pk):
    from datetime import datetime

    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    # ✅ No permitir edición si ya fue aprobada
    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_supervisor', 'aprobado_finanzas', 'aprobado']:
        messages.error(
            request,
            "Esta rendición ya fue aprobada y no se puede editar. Debes solicitar que la rechacen."
        )
        return redirect('operaciones:mis_rendiciones')

    # Helpers locales (mismos criterios que usas en mis_rendiciones)
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone

    from flota.models import VehicleService

    def _map_legacy_service_type(tipo_servicio_obj):
        if not tipo_servicio_obj:
            return "otro"

        n = (getattr(tipo_servicio_obj, "name", "") or "").strip().lower()

        if "combustible" in n:
            return "combustible"
        if "aceite" in n:
            return "aceite"
        if "neumatic" in n or "neumát" in n:
            return "neumaticos"
        if "revision tecnica" in n or "revisión técnica" in n or "revision_tecnica" in n:
            return "revision_tecnica"
        if "permiso" in n and "circul" in n:
            return "permiso_circulacion"

        return "otro"

    def _es_tipo_servicios(tipo_obj):
        if not tipo_obj:
            return False
        nombre = (getattr(tipo_obj, "nombre", None) or getattr(tipo_obj, "name", None) or "").strip().lower()
        categoria = (getattr(tipo_obj, "categoria", None) or "").strip().lower()
        return ("servicio" in nombre) or (categoria == "servicios")

    def _validar_no_futuro(fecha_tx, hora_servicio=None, es_servicio=False):
        """
        Valida que:
        - fecha_transaccion no sea futura
        - si es servicio (flota), fecha+hora del servicio no sea futura
        Devuelve: (ok: bool, mensaje: str|None)
        """
        if not fecha_tx:
            return True, None

        now_local = timezone.localtime(timezone.now())
        hoy_local = now_local.date()

        # 1) Fecha futura (cualquier rendición)
        if fecha_tx > hoy_local:
            return False, "No puedes registrar una rendición con fecha futura."

        # 2) Fecha/Hora futura (solo servicios flota)
        if es_servicio and hora_servicio:
            try:
                dt_servicio = datetime.combine(fecha_tx, hora_servicio)
                tz = timezone.get_current_timezone()
                dt_servicio = timezone.make_aware(dt_servicio, tz) if timezone.is_naive(dt_servicio) else dt_servicio

                if dt_servicio > now_local:
                    return False, "No puedes registrar una rendición con una hora de servicio futura."
            except Exception:
                pass

        return True, None

    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES, instance=rendicion)

        if form.is_valid():
            # --- Detectar cambios ---
            campos_editados = []
            for field in form.changed_data:
                if field not in ['status', 'actualizado']:
                    campos_editados.append(field)

            # Si cambió algo y estaba rechazado, restablecer estado
            if campos_editados and rendicion.status in [
                'rechazado_abono_usuario', 'rechazado_supervisor', 'rechazado_pm', 'rechazado_finanzas'
            ]:
                rendicion.status = 'pendiente_supervisor'

            obj = form.save(commit=False)

            # ✅ Mantener/reemplazar comprobante (acepta cualquiera de los 3 inputs)
            nuevo_comprobante = (
                request.FILES.get("comprobante")
                or request.FILES.get("comprobante_archivo")
                or request.FILES.get("comprobante_foto")
            )
            if nuevo_comprobante:
                obj.comprobante = nuevo_comprobante
            # else: se mantiene el actual

            # ✅ Validación de kilometraje "legacy" (si existe campo kilometraje en Cartola)
            if _cartola_tiene_campo_km():
                km_nuevo = _normalizar_km(form.cleaned_data.get("kilometraje"))
                if km_nuevo is not None:
                    ok_km, msg_km = _validar_km_no_regresivo(
                        usuario=request.user,
                        fecha_transaccion=form.cleaned_data.get("fecha_transaccion"),
                        km_nuevo=km_nuevo,
                        exclude_mov_id=rendicion.pk,
                    )
                    if not ok_km:
                        form.add_error('kilometraje', msg_km)
                        return render(
                            request,
                            'operaciones/editar_rendicion.html',
                            {'form': form, 'rendicion': rendicion}
                        )

                    setattr(obj, "kilometraje", km_nuevo)

            # ==========================================================
            # ✅ Sync edición con FLOTA (si tipo = Servicios)
            # ==========================================================
            cd = form.cleaned_data
            tipo_mov = cd.get("tipo")
            es_rendicion_flota = _es_tipo_servicios(tipo_mov)

            # ✅ Validación fecha/hora no futura (también para no-flota valida fecha)
            ok_no_futuro, msg_no_futuro = _validar_no_futuro(
                fecha_tx=cd.get("fecha_transaccion"),
                hora_servicio=cd.get("hora_servicio_flota"),
                es_servicio=es_rendicion_flota,
            )
            if not ok_no_futuro:
                if es_rendicion_flota and cd.get("hora_servicio_flota"):
                    form.add_error("hora_servicio_flota", msg_no_futuro)
                else:
                    form.add_error("fecha_transaccion", msg_no_futuro)

                return render(
                    request,
                    'operaciones/editar_rendicion.html',
                    {'form': form, 'rendicion': rendicion}
                )

            # Si es servicio, la fecha de servicio SIEMPRE se toma desde fecha_transaccion
            if es_rendicion_flota and cd.get("fecha_transaccion"):
                obj.fecha_servicio_flota = cd.get("fecha_transaccion")

            if es_rendicion_flota:
                vehiculo = cd.get("vehiculo_flota")
                tipo_servicio_flota = cd.get("tipo_servicio_flota")
                fecha_servicio = cd.get("fecha_transaccion")  # <- misma fecha real del gasto
                hora_servicio = cd.get("hora_servicio_flota")
                km_servicio = cd.get("kilometraje_servicio_flota")
                monto_servicio = cd.get("cargos") or 0

                # Validación básica (por si algo raro pasa)
                if not vehiculo:
                    form.add_error("vehiculo_flota", "Selecciona un vehículo.")
                if not tipo_servicio_flota:
                    form.add_error("tipo_servicio_flota", "Selecciona un tipo de servicio.")
                if not hora_servicio:
                    form.add_error("hora_servicio_flota", "Ingresa la hora del servicio.")
                if km_servicio in (None, ""):
                    form.add_error("kilometraje_servicio_flota", "Ingresa el kilometraje del servicio.")

                if form.errors:
                    return render(
                        request,
                        'operaciones/editar_rendicion.html',
                        {'form': form, 'rendicion': rendicion}
                    )

                # ✅ Validación cronológica KM de FLOTA (por vehículo, fecha+hora)
                exclude_service_id = obj.servicio_flota_id if getattr(obj, "servicio_flota_id", None) else None
                ok_flota_km, msg_flota_km, ultimo_ref, servicio_conflicto = _validar_km_servicio_flota_vs_ultimo(
                    vehicle_id=vehiculo.id,
                    fecha_servicio=fecha_servicio,
                    hora_servicio=hora_servicio,
                    km_nuevo=km_servicio,
                    exclude_service_id=exclude_service_id,
                )

                if not ok_flota_km:
                    # ✅ Si el conflicto viene de una rendición ya aprobada, mostrar mensaje especial
                    rendicion_conflicto = None
                    try:
                        if servicio_conflicto:
                            rendicion_conflicto = (
                                CartolaMovimiento.objects
                                .filter(servicio_flota=servicio_conflicto)
                                .only("id", "status")
                                .first()
                            )
                    except Exception:
                        rendicion_conflicto = None

                    if rendicion_conflicto and getattr(rendicion_conflicto, "status", None) in {
                        "aprobado_supervisor", "aprobado_finanzas", "aprobado_abono_usuario", "aprobado"
                    }:
                        form.add_error(
                            "kilometraje_servicio_flota",
                            (
                                f"{msg_flota_km} La rendición anterior ya fue aprobada y no se puede editar. "
                                f"Debes solicitar que la rechacen."
                            )
                        )
                    else:
                        form.add_error(
                            "kilometraje_servicio_flota",
                            f"{msg_flota_km} Edita el registro anterior o modifica la hora del servicio."
                        )

                    return render(
                        request,
                        'operaciones/editar_rendicion.html',
                        {'form': form, 'rendicion': rendicion}
                    )

            # Guardar todo
            try:
                with transaction.atomic():
                    # Guardar el status si lo tocamos arriba
                    obj.status = rendicion.status
                    obj.save()

                    # --- Si es servicio flota, crear/actualizar VehicleService vinculado ---
                    if es_rendicion_flota:
                        vehiculo = cd.get("vehiculo_flota")
                        tipo_servicio_flota = cd.get("tipo_servicio_flota")
                        fecha_servicio = cd.get("fecha_transaccion")
                        hora_servicio = cd.get("hora_servicio_flota")
                        km_servicio = cd.get("kilometraje_servicio_flota")
                        monto_servicio = cd.get("cargos") or 0

                        legacy_type = _map_legacy_service_type(tipo_servicio_flota)

                        # Si ya existe servicio flota vinculado, se actualiza; si no, se crea
                        servicio = getattr(obj, "servicio_flota", None)

                        if servicio:
                            servicio.vehicle = vehiculo
                            servicio.service_type = legacy_type
                            servicio.service_type_obj = tipo_servicio_flota
                            servicio.title = f"Rendición #{obj.pk}"
                            servicio.service_date = fecha_servicio
                            servicio.service_time = hora_servicio
                            servicio.kilometraje_declarado = km_servicio if km_servicio not in (None, "") else None
                            servicio.monto = monto_servicio
                            servicio.notes = (
                                f"Editado desde rendición #{obj.pk} por "
                                f"{request.user.get_full_name() or request.user.username}. "
                                f"Obs: {cd.get('observaciones') or ''}"
                            ).strip()
                            servicio.save()
                        else:
                            servicio = VehicleService.objects.create(
                                vehicle=vehiculo,
                                service_type=legacy_type,
                                service_type_obj=tipo_servicio_flota,
                                title=f"Rendición #{obj.pk}",
                                service_date=fecha_servicio,
                                service_time=hora_servicio,
                                kilometraje_declarado=km_servicio if km_servicio not in (None, "") else None,
                                monto=monto_servicio,
                                notes=(
                                    f"Creado desde edición de rendición #{obj.pk} por "
                                    f"{request.user.get_full_name() or request.user.username}. "
                                    f"Obs: {cd.get('observaciones') or ''}"
                                ).strip(),
                            )

                        # Snapshot + vínculo
                        obj.servicio_flota = servicio
                        obj.vehiculo_flota = vehiculo
                        obj.tipo_servicio_flota = tipo_servicio_flota
                        obj.fecha_servicio_flota = fecha_servicio
                        obj.hora_servicio_flota = hora_servicio
                        obj.kilometraje_servicio_flota = km_servicio if km_servicio not in (None, "") else None
                        obj.tipo_servicio_flota_snapshot = getattr(tipo_servicio_flota, "name", None)

                        obj.save(update_fields=[
                            "servicio_flota",
                            "vehiculo_flota",
                            "tipo_servicio_flota",
                            "fecha_servicio_flota",
                            "hora_servicio_flota",
                            "kilometraje_servicio_flota",
                            "tipo_servicio_flota_snapshot",
                        ])

                    else:
                        # Si dejó de ser tipo Servicios, limpiamos datos flota para evitar basura
                        campos_limpiar = []
                        for fld in [
                            "vehiculo_flota",
                            "tipo_servicio_flota",
                            "fecha_servicio_flota",
                            "hora_servicio_flota",
                            "kilometraje_servicio_flota",
                            "tipo_servicio_flota_snapshot",
                        ]:
                            if hasattr(obj, fld):
                                setattr(obj, fld, None)
                                campos_limpiar.append(fld)

                        # OJO: no borro servicio_flota histórico aquí (por auditoría), solo desvinculo si quieres
                        # Si prefieres mantener vínculo histórico, comenta estas 2 líneas
                        if hasattr(obj, "servicio_flota"):
                            obj.servicio_flota = None
                            campos_limpiar.append("servicio_flota")

                        if campos_limpiar:
                            obj.save(update_fields=campos_limpiar)

                    # ✅ Mantener tu lógica existente de km pendiente
                    _registrar_km_en_flota_pendiente(obj, request_user=request.user)

                messages.success(request, "Rendición actualizada correctamente.")
                return redirect('operaciones:mis_rendiciones')

            except ValidationError as e:
                try:
                    if hasattr(e, "message_dict"):
                        for field, errs in e.message_dict.items():
                            for err in errs:
                                form.add_error(field if field in form.fields else None, str(err))
                    else:
                        form.add_error(None, str(e))
                except Exception:
                    form.add_error(None, str(e))

            except Exception as e:
                form.add_error(None, f"No se pudo actualizar la rendición/servicio de flota: {e}")

    else:
        form = MovimientoUsuarioForm(instance=rendicion)

    return render(request, 'operaciones/editar_rendicion.html', {
        'form': form,
        'rendicion': rendicion,
    })


@login_required
def eliminar_rendicion(request, pk):
    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "No puedes eliminar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        # (Opcional futuro) eliminar o anular km pendiente en flota si aplica
        rendicion.delete()
        messages.success(request, "Rendición eliminada correctamente.")
        return redirect('operaciones:mis_rendiciones')

    return render(request, 'operaciones/eliminar_rendicion.html', {'rendicion': rendicion})


# ==========================================================
# Supervisor / PM / Finanzas
# ==========================================================







@login_required
def vista_rendiciones(request):
    user = request.user

    # Base queryset con relaciones para evitar N+1 al pintar la tabla
    base_qs = CartolaMovimiento.objects.select_related(
        'usuario',
        'proyecto',
        'tipo',
        'vehiculo_flota',
        'tipo_servicio_flota',
        'servicio_flota',
        'aprobado_por_supervisor',
        'aprobado_por_pm',
        'aprobado_por_finanzas',
    )

    # --- Qué ve cada rol
    if user.is_superuser:
        movimientos_qs = base_qs.all()
    elif getattr(user, 'es_supervisor', False):
        movimientos_qs = (
            base_qs
            .filter(Q(status__startswith='pendiente') | Q(status__startswith='rechazado'))
            .exclude(tipo__categoria='abono')
        )
    elif getattr(user, 'es_pm', False):
        movimientos_qs = base_qs.all()
    elif getattr(user, 'es_facturacion', False):
        movimientos_qs = base_qs.all()
    else:
        movimientos_qs = base_qs.none()

    # --- Qué estado es "pendiente para mí"
    if getattr(user, 'es_supervisor', False):
        role_pending_status = 'pendiente_supervisor'
    elif getattr(user, 'es_pm', False):
        role_pending_status = 'aprobado_supervisor'
    elif getattr(user, 'es_facturacion', False):
        role_pending_status = 'aprobado_pm'
    else:
        role_pending_status = None

    if role_pending_status:
        prioridad_rol_expr = Case(
            When(status=role_pending_status, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    else:
        prioridad_rol_expr = Value(1, output_field=IntegerField())

    movimientos_qs = movimientos_qs.annotate(
        prioridad_rol=prioridad_rol_expr,
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado', then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        ),
    ).order_by(
        'prioridad_rol',
        'orden_status',
        '-fecha_transaccion',
        '-fecha'
    )

    # ==========================================================
    # ✅ Helpers para filtros Excel (valores iguales a la tabla)
    # ==========================================================
    def _fmt_fecha(d):
        return d.strftime('%d-%m-%Y') if d else '—'

    def _fmt_hora(h):
        # h es time
        if not h:
            return ''
        # 12h con "a. m." / "p. m." similar al template
        try:
            return h.strftime('%I:%M %p').lower().replace('am', 'a. m.').replace('pm', 'p. m.')
        except Exception:
            return ''

    def _fmt_monto(m):
        # En la tabla visual tú muestras: ${{ mov.cargos|floatformat:0|formato_clp }}
        # Para filtro Excel usamos una versión estable tipo "$20.000"
        try:
            n = int(m or 0)
        except Exception:
            n = 0
        return f"${n:,}".replace(',', '.')

    def _fmt_km(km):
        if km in [None, '']:
            return '—'
        try:
            n = int(km)
            return f"{n:,}".replace(',', '.') + " KM"
        except Exception:
            return '—'

    def _estado_filtro(mov):
        # Texto compacto para filtrar por estado (consistente)
        mapa = {
            'pendiente_supervisor': 'Pendiente aprobación del Supervisor',
            'aprobado_supervisor': 'Pendiente aprobación del PM',
            'rechazado_supervisor': 'Rechazado por Supervisor',
            'aprobado_pm': 'Pendiente aprobación de Finanzas',
            'rechazado_pm': 'Rechazado por PM',
            'aprobado_finanzas': 'Aprobado por Finanzas',
            'rechazado_finanzas': 'Rechazado por Finanzas',
            'pendiente_abono_usuario': 'Pendiente aprobación del Usuario',
            'aprobado_abono_usuario': 'Abono aprobado por Usuario',
            'rechazado_abono_usuario': 'Abono rechazado por Usuario',
        }
        return mapa.get(mov.status, getattr(mov, 'get_status_display', lambda: str(mov.status))())

    def _row_values(mov):
        usuario_nombre = (mov.usuario.get_full_name() or getattr(mov.usuario, 'username', '') or '').strip() or '—'

        fecha_real = mov.fecha_transaccion if mov.fecha_transaccion else mov.fecha

        if mov.vehiculo_flota:
            vehiculo_txt = f"{mov.vehiculo_flota.patente}"
            if getattr(mov.vehiculo_flota, 'marca', None):
                vehiculo_txt += f" · {mov.vehiculo_flota.marca} {mov.vehiculo_flota.modelo or ''}".rstrip()
        else:
            vehiculo_txt = '—'

        if mov.fecha_servicio_flota:
            fh_serv = _fmt_fecha(mov.fecha_servicio_flota)
            hora_txt = _fmt_hora(mov.hora_servicio_flota)
            if hora_txt:
                fh_serv = f"{fh_serv} {hora_txt}"
        else:
            fh_serv = '—'

        servicio_flota_txt = f"#{getattr(mov.servicio_flota, 'service_code', None) or mov.servicio_flota.id}" if mov.servicio_flota else '—'

        return {
            # índices = columnas de la tabla (0..14)
            "0": usuario_nombre,
            "1": _fmt_fecha(mov.fecha),
            "2": _fmt_fecha(fecha_real),
            "3": str(mov.proyecto) if mov.proyecto else '—',
            "4": _fmt_monto(mov.cargos),
            "5": str(mov.tipo) if mov.tipo else '—',
            "6": vehiculo_txt,
            "7": getattr(mov, 'tipo_servicio_flota_nombre', None) or '—',
            "8": fh_serv,
            "9": _fmt_km(getattr(mov, 'kilometraje_servicio_flota', None)),
            "10": servicio_flota_txt,
            "11": (mov.observaciones or '—').strip() if getattr(mov, 'observaciones', None) else '—',
            "12": 'Ver' if getattr(mov, 'comprobante', None) else '—',
            "13": _estado_filtro(mov),
            "14": 'Acciones',
        }

    # Convertimos a lista una vez (para poder filtrar global y paginar después)
    movimientos_list = list(movimientos_qs)

    # --- Valores globales para los dropdown tipo Excel (antes de aplicar filtro)
    excel_global = {}
    for mov in movimientos_list:
        vals = _row_values(mov)
        for k, v in vals.items():
            # No incluir Acciones
            if k == "14":
                continue
            excel_global.setdefault(k, set()).add(v or '—')

    excel_global_json = json.dumps({
        k: sorted(list(v), key=lambda x: (str(x).lower()))
        for k, v in excel_global.items()
    }, ensure_ascii=False)

    # --- Aplicar filtros Excel (globales, backend) ANTES de paginar
    excel_filters_raw = request.GET.get('excel_filters', '').strip()
    excel_filters = {}
    if excel_filters_raw:
        try:
            parsed = json.loads(excel_filters_raw)
            if isinstance(parsed, dict):
                # normalizamos a sets de strings
                for k, arr in parsed.items():
                    if isinstance(arr, list):
                        excel_filters[str(k)] = set(str(x) for x in arr)
        except Exception:
            excel_filters = {}

    if excel_filters:
        filtrados = []
        for mov in movimientos_list:
            vals = _row_values(mov)
            ok = True
            for col_idx, allowed in excel_filters.items():
                if not allowed:
                    continue
                if vals.get(col_idx, '—') not in allowed:
                    ok = False
                    break
            if ok:
                filtrados.append(mov)
        movimientos_list = filtrados

    # --- Totales (sobre lista ya filtrada)
    total = sum((m.cargos or Decimal('0')) for m in movimientos_list)
    pendientes = sum((m.cargos or Decimal('0')) for m in movimientos_list if str(m.status).startswith('pendiente'))
    rechazados = sum((m.cargos or Decimal('0')) for m in movimientos_list if str(m.status).startswith('rechazado'))

    # --- Paginación
    cantidad_param = request.GET.get('cantidad', '10')
    try:
        if cantidad_param == 'todos':
            page_size = 100
        else:
            page_size = max(5, min(int(cantidad_param), 100))
    except ValueError:
        cantidad_param = '10'
        page_size = 10

    paginator = Paginator(movimientos_list, page_size)
    page_number = request.GET.get('page') or 1
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_param,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
        'excel_global_json': excel_global_json,   # ✅ para dropdown global
        'excel_filters_raw': excel_filters_raw,   # ✅ para preservar en paginación
    })


@login_required
def aprobar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    user = request.user
    changed = False

    # Flujo de aprobaciones
    if getattr(user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
        mov.status = 'aprobado_supervisor'
        mov.aprobado_por_supervisor = user
        changed = True

    elif getattr(user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
        mov.status = 'aprobado_pm'
        mov.aprobado_por_pm = user
        changed = True

    elif getattr(user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
        mov.status = 'aprobado_finanzas'
        mov.aprobado_por_finanzas = user

        # ✅ Si tu modelo ya tiene historial/campos, los dejamos listos aquí también
        if hasattr(mov, 'aprobado_finanzas_en'):
            mov.aprobado_finanzas_en = timezone.now()
        if hasattr(mov, 'en_historial'):
            mov.en_historial = True
        if hasattr(mov, 'historial_enviado_el'):
            mov.historial_enviado_el = timezone.now()
        if hasattr(mov, 'historial_enviado_por'):
            mov.historial_enviado_por = user

        changed = True

        # ✅ Confirmar KM en flota cuando finanzas aprueba
        _confirmar_km_en_flota_si_aplica(mov, request_user=user)

    if changed:
        mov.motivo_rechazo = ''  # limpiar rechazo previo si lo hubiera
        mov.save()
        messages.success(request, "Movimiento aprobado correctamente.")
    else:
        messages.warning(request, "No puedes aprobar este movimiento en su estado actual.")

    next_url = (
        request.POST.get('next')
        or request.GET.get('next')
        or request.META.get('HTTP_REFERER')
        or reverse('operaciones:vista_rendiciones')
    )
    return redirect(next_url)


@login_required
def rechazar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)

    if request.method == 'POST':
        motivo = (request.POST.get('motivo_rechazo') or '').strip()
        if not motivo:
            messages.error(request, "Debe ingresar el motivo del rechazo.")
        else:
            changed = False

            if getattr(request.user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
                changed = True
            elif getattr(request.user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
                changed = True
            elif getattr(request.user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                mov.aprobado_por_finanzas = request.user
                changed = True

            if changed:
                mov.motivo_rechazo = motivo
                mov.save()
                messages.success(request, "Movimiento rechazado correctamente.")
            else:
                messages.warning(request, "No puedes rechazar este movimiento en su estado actual.")

    next_url = (
        request.POST.get('next')
        or request.GET.get('next')
        or request.META.get('HTTP_REFERER')
        or reverse('operaciones:vista_rendiciones')
    )
    return redirect(next_url)


# ==========================================================
# AJAX RUT
# ==========================================================

@csrf_exempt
def validar_rut_ajax(request):
    """Valida el RUT desde AJAX y devuelve estado."""
    rut = request.POST.get("rut", "")
    if not validar_rut_chileno(rut):
        return JsonResponse({"ok": False, "error": "El RUT ingresado no es válido."})
    razon_social = verificar_rut_sii(rut)
    if not razon_social:
        return JsonResponse({"ok": False, "error": "El RUT no está registrado en el SII."})
    return JsonResponse({"ok": True, "mensaje": "RUT válido"})


# ==========================================================
# Exports
# ==========================================================


@login_required
@rol_requerido('pm')
def exportar_rendiciones_pm(request):
    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="rendiciones_pm.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Rendiciones PM')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = [
        "Nombre",
        "Fecha",
        "Fecha real del gasto",
        "Vehículo",               # ✅ nuevo
        "Hora servicio",          # ✅ nuevo
        "Kilometraje servicio",   # ✅ nuevo
        "Proyecto",
        "Monto",
        "Tipo",
        "Tipo servicio (Flota)",  # ✅ nuevo
        "Observaciones",
        "Estado",
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        fecha_real_excel = getattr(mov, "fecha_transaccion", None)
        if isinstance(fecha_real_excel, datetime):
            if is_aware(fecha_real_excel):
                fecha_real_excel = fecha_real_excel.astimezone().replace(tzinfo=None)
            fecha_real_excel = fecha_real_excel.date()

        hora_servicio = getattr(mov, "hora_servicio_flota", None)
        hora_servicio_txt = hora_servicio.strftime("%H:%M") if hora_servicio else ""

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)
        ws.write(row_num, 2, fecha_real_excel if fecha_real_excel else "", date_style)

        ws.write(row_num, 3, str(getattr(mov, "vehiculo_flota", "") or ""))
        ws.write(row_num, 4, hora_servicio_txt)
        ws.write(row_num, 5, float(getattr(mov, "kilometraje_servicio_flota", 0) or 0))

        ws.write(row_num, 6, str(mov.proyecto))
        ws.write(row_num, 7, float(mov.cargos or 0))
        ws.write(row_num, 8, str(mov.tipo or ""))
        ws.write(row_num, 9, str(getattr(mov, "tipo_servicio_flota", "") or ""))
        ws.write(row_num, 10, str(mov.observaciones or ""))
        ws.write(row_num, 11, mov.get_status_display())

    wb.save(response)
    return response


@login_required
def exportar_mis_rendiciones(request):
    user = request.user
    movimientos = CartolaMovimiento.objects.filter(usuario=user).order_by('-fecha')

    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="mis_rendiciones.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Mis Rendiciones')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = [
        "Nombre",
        "Fecha registro",
        "Fecha real del gasto",
        "Vehículo",               # ✅ nuevo
        "Hora servicio",          # ✅ nuevo
        "Kilometraje servicio",   # ✅ nuevo
        "Proyecto",
        "Tipo",
        "Tipo servicio (Flota)",  # ✅ nuevo
        "RUT Factura",
        "Monto",
        "Estado",
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        fecha_registro = mov.fecha
        if isinstance(fecha_registro, datetime):
            if is_aware(fecha_registro):
                fecha_registro = fecha_registro.astimezone().replace(tzinfo=None)
            fecha_registro = fecha_registro.date()

        fecha_real = getattr(mov, "fecha_transaccion", None)
        if isinstance(fecha_real, datetime):
            if is_aware(fecha_real):
                fecha_real = fecha_real.astimezone().replace(tzinfo=None)
            fecha_real = fecha_real.date()

        hora_servicio = getattr(mov, "hora_servicio_flota", None)
        hora_servicio_txt = hora_servicio.strftime("%H:%M") if hora_servicio else ""

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_registro, date_style)
        ws.write(row_num, 2, fecha_real if fecha_real else "", date_style)

        ws.write(row_num, 3, str(getattr(mov, "vehiculo_flota", "") or ""))
        ws.write(row_num, 4, hora_servicio_txt)
        ws.write(row_num, 5, float(getattr(mov, "kilometraje_servicio_flota", 0) or 0))

        ws.write(row_num, 6, str(getattr(mov, "proyecto", "") or ""))
        ws.write(row_num, 7, str(getattr(mov, "tipo", "") or ""))
        ws.write(row_num, 8, str(getattr(mov, "tipo_servicio_flota", "") or ""))
        ws.write(row_num, 9, str(getattr(mov, "rut_factura", "") or ""))
        ws.write(row_num, 10, float(getattr(mov, "cargos", 0) or 0))
        ws.write(row_num, 11, mov.get_status_display())

    wb.save(response)
    return response
# ==========================================================
# Helpers KM Flota (por vehículo + fecha/hora de servicio)
# ==========================================================

def _dt_servicio_naive(fecha_servicio, hora_servicio):
    """
    Construye datetime naive para comparar cronológicamente.
    Si hora viene vacía, usa 00:00.
    """
    if not fecha_servicio:
        return None
    h = hora_servicio or time(0, 0)
    return datetime.combine(fecha_servicio, h)


def _ultimo_servicio_flota_vehicle(vehicle_id, exclude_service_id=None):
    """
    Devuelve el último servicio registrado para un vehículo,
    ordenado por fecha + hora + PK (para referencia visual).
    """
    from flota.models import VehicleService

    qs = VehicleService.objects.filter(vehicle_id=vehicle_id)

    if exclude_service_id:
        qs = qs.exclude(pk=exclude_service_id)

    return qs.order_by("-service_date", "-service_time", "-pk").first()


def _validar_km_servicio_flota_vs_ultimo(vehicle_id, fecha_servicio, hora_servicio, km_nuevo, exclude_service_id=None):
    """
    Valida kilometraje de flota en orden cronológico (por vehículo):
    - No puede ser menor que el registro anterior (<= fecha/hora nueva)
    - No puede ser mayor que el registro posterior (>= fecha/hora nueva)

    Retorna:
      (ok, msg, ultimo_abs, servicio_conflicto)
    """
    from flota.models import VehicleService

    if not vehicle_id or km_nuevo in (None, "") or fecha_servicio is None:
        return True, None, None, None

    try:
        km_nuevo = int(km_nuevo)
    except (TypeError, ValueError):
        return False, "El kilometraje debe ser numérico.", None, None

    if km_nuevo < 0:
        return False, "El kilometraje no puede ser negativo.", None, None

    dt_nuevo = _dt_servicio_naive(fecha_servicio, hora_servicio)
    if dt_nuevo is None:
        return True, None, None, None

    qs = VehicleService.objects.filter(vehicle_id=vehicle_id)

    if exclude_service_id:
        qs = qs.exclude(pk=exclude_service_id)

    # Solo comparar contra servicios que tengan km
    qs = qs.exclude(kilometraje_declarado__isnull=True)

    # Referencia visual: último absoluto del vehículo (para mostrar en UI)
    ultimo_abs = qs.order_by("-service_date", "-service_time", "-pk").first()

    # Vecino anterior (cronológicamente <= nuevo)
    anterior = (
        qs.filter(
            Q(service_date__lt=fecha_servicio) |
            Q(service_date=fecha_servicio, service_time__lte=(hora_servicio or time(0, 0)))
        )
        .order_by("-service_date", "-service_time", "-pk")
        .first()
    )

    # Vecino posterior (cronológicamente >= nuevo)
    posterior = (
        qs.filter(
            Q(service_date__gt=fecha_servicio) |
            Q(service_date=fecha_servicio, service_time__gte=(hora_servicio or time(0, 0)))
        )
        .order_by("service_date", "service_time", "pk")
        .first()
    )

    # Regla 1: no regresivo respecto al anterior
    if anterior and anterior.kilometraje_declarado is not None:
        if km_nuevo < int(anterior.kilometraje_declarado):
            hora_txt = anterior.service_time.strftime("%I:%M %p").lower() if anterior.service_time else "12:00 a.m."
            return (
                False,
                (
                    f"El kilometraje ({km_nuevo}) no puede ser menor al registro anterior "
                    f"({int(anterior.kilometraje_declarado)}) del "
                    f"{anterior.service_date.strftime('%d-%m-%Y')} {hora_txt}."
                ),
                ultimo_abs,
                anterior,  # 👈 servicio en conflicto
            )

    # Regla 2: no puede superar un registro posterior
    if posterior and posterior.kilometraje_declarado is not None:
        dt_posterior = _dt_servicio_naive(posterior.service_date, posterior.service_time)
        if dt_posterior and dt_nuevo and dt_posterior > dt_nuevo:
            if km_nuevo > int(posterior.kilometraje_declarado):
                hora_txt = posterior.service_time.strftime("%I:%M %p").lower() if posterior.service_time else "12:00 a.m."
                return (
                    False,
                    (
                        f"El kilometraje ({km_nuevo}) no puede ser mayor a un registro posterior "
                        f"({int(posterior.kilometraje_declarado)}) del "
                        f"{posterior.service_date.strftime('%d-%m-%Y')} {hora_txt}."
                    ),
                    ultimo_abs,
                    posterior,  # 👈 servicio en conflicto
                )

    return True, None, ultimo_abs, None
@login_required
@require_GET
def validar_km_servicio_flota_ajax(request):
    """
    Valida en vivo el KM del servicio de flota contra la línea de tiempo del vehículo.
    Usa fecha_transaccion como fecha de servicio (porque fecha_servicio_flota va oculta).

    Además, si hay conflicto de KM, devuelve el ID de la rendición asociada al servicio
    en conflicto (si existe) para que el frontend pueda mostrar link directo a editar.
    """
    from facturacion.models import CartolaMovimiento
    from flota.models import Vehicle

    vehicle_id = request.GET.get("vehiculo_id")
    fecha_txt = (request.GET.get("fecha") or "").strip()   # viene de fecha_transaccion
    hora_txt = (request.GET.get("hora") or "").strip()
    km_txt = (request.GET.get("km") or "").strip()

    if not vehicle_id:
        return JsonResponse({"ok": True, "skip": True, "msg": ""})

    # Validar vehículo existente
    try:
        vehicle = Vehicle.objects.get(pk=vehicle_id)
    except Vehicle.DoesNotExist:
        return JsonResponse({"ok": False, "msg": "Vehículo inválido."}, status=400)

    # Parse fecha
    fecha_servicio = None
    if fecha_txt:
        try:
            fecha_servicio = datetime.strptime(fecha_txt, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"ok": False, "msg": "Fecha inválida."}, status=400)

    # Parse hora
    hora_servicio = None
    if hora_txt:
        try:
            hora_servicio = datetime.strptime(hora_txt, "%H:%M").time()
        except ValueError:
            return JsonResponse({"ok": False, "msg": "Hora inválida."}, status=400)

    # Parse km
    km_nuevo = None
    if km_txt:
        km_nuevo = _normalizar_km(km_txt)
        if km_nuevo is None and km_txt.strip():
            return JsonResponse({"ok": False, "msg": "Kilometraje inválido."}, status=400)

    ultimo = _ultimo_servicio_flota_vehicle(vehicle.id)

    def _fmt_hora_ampm(t):
        if not t:
            return "12:00 a.m."
        return t.strftime("%I:%M %p").lower()

    # Si aún faltan datos para validar, devolvemos solo referencia del último
    if fecha_servicio is None or hora_servicio is None or km_nuevo is None:
        data = {"ok": True, "skip": True, "msg": ""}
        if ultimo and ultimo.kilometraje_declarado is not None:
            data["ultimo"] = {
                "km": int(ultimo.kilometraje_declarado),
                "fecha": ultimo.service_date.strftime("%d-%m-%Y") if ultimo.service_date else "",
                "hora": _fmt_hora_ampm(ultimo.service_time),
            }
        return JsonResponse(data)

    # 👇 OJO: este helper ahora debe retornar 4 valores
    # (ok, msg, ultimo_ref, servicio_conflicto)
    ok, msg, ultimo_ref, servicio_conflicto = _validar_km_servicio_flota_vs_ultimo(
        vehicle_id=vehicle.id,
        fecha_servicio=fecha_servicio,
        hora_servicio=hora_servicio,
        km_nuevo=km_nuevo,
    )

    resp = {
        "ok": ok,
        "msg": msg or "",
        "vehicle": vehicle.patente,
    }

    if ultimo_ref and ultimo_ref.kilometraje_declarado is not None:
        resp["ultimo"] = {
            "km": int(ultimo_ref.kilometraje_declarado),
            "fecha": ultimo_ref.service_date.strftime("%d-%m-%Y") if ultimo_ref.service_date else "",
            "hora": _fmt_hora_ampm(ultimo_ref.service_time),
        }

    # ✅ Si hubo conflicto, devolver mov_id de la rendición asociada al servicio en conflicto
    if not ok and servicio_conflicto:
        mov_conflicto = (
            CartolaMovimiento.objects
            .filter(servicio_flota_id=servicio_conflicto.id)
            .only("id")
            .first()
        )

        if mov_conflicto:
            if "ultimo" not in resp:
                resp["ultimo"] = {}
            resp["ultimo"]["mov_id"] = mov_conflicto.id

    return JsonResponse(resp)

def _validar_no_futuro(fecha_tx, hora_servicio=None, es_servicio=False):
    """
    Valida que:
    - fecha_transaccion no sea futura
    - si es servicio (flota), fecha+hora del servicio no sea futura
    Devuelve: (ok: bool, mensaje: str|None)
    """
    from datetime import datetime

    from django.utils import timezone

    if not fecha_tx:
        return True, None

    now_local = timezone.localtime(timezone.now())
    hoy_local = now_local.date()

    # 1) Fecha futura (cualquier rendición)
    if fecha_tx > hoy_local:
        return False, "No puedes registrar una rendición con fecha futura."

    # 2) Fecha/Hora futura (solo servicios flota)
    if es_servicio and hora_servicio:
        try:
            dt_servicio = datetime.combine(fecha_tx, hora_servicio)
            tz = timezone.get_current_timezone()
            dt_servicio = timezone.make_aware(dt_servicio, tz) if timezone.is_naive(dt_servicio) else dt_servicio

            if dt_servicio > now_local:
                return False, "No puedes registrar una rendición con una hora de servicio futura."
        except Exception:
            # Si por algún motivo falla el parse/combine, no rompemos la operación aquí.
            # La validación de formulario ya debería cubrir formato de hora.
            pass

    return True, None
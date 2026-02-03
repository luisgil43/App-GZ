# operaciones/views.py

import calendar
import csv
import io
import locale
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import requests
import xlwt
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
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
from django.views.decorators.http import require_POST
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


@login_required
def mis_rendiciones(request):
    user = request.user

    # --- Crear nueva rendición ---
    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)
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
                    getattr(last_mov, "proyecto_id", None) == cd["proyecto"].id and
                    getattr(last_mov, "tipo_id", None) == cd["tipo"].id and
                    getattr(last_mov, "numero_doc", None) == cd.get("numero_doc") and
                    getattr(last_mov, "cargos", None) == cd.get("cargos") and
                    norm(getattr(last_mov, "rut_factura", "")) == norm(cd.get("rut_factura")) and
                    norm(getattr(last_mov, "observaciones", "")) == norm(cd.get("observaciones")) and
                    # ✅ NUEVO: misma fecha real
                    getattr(last_mov, "fecha_transaccion", None) == cd.get("fecha_transaccion")
                )

            if is_duplicate:
                messages.warning(
                    request,
                    "Esta rendición ya fue registrada hace unos instantes. "
                    "No se creó un duplicado."
                )
                return redirect('operaciones:mis_rendiciones')

            mov = form.save(commit=False)
            mov.usuario = user

            # fecha de registro (declaración)
            mov.fecha = now()

            mov.status = 'pendiente_supervisor'
            mov.comprobante = cd.get("comprobante")
            mov.save()

            messages.success(request, "Rendición registrada correctamente.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm()

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

    movimientos = CartolaMovimiento.objects.filter(
        usuario=user
    ).order_by('-fecha')

    paginator = Paginator(movimientos, per_page)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Cálculo de saldos ---
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
        messages.error(
            request, "Abono rechazado y enviado a Finanzas para revisión.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def editar_rendicion(request, pk):
    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "No puedes editar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

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

            # ✅ Mantener comprobante si no vienen nuevos archivos
            nuevo_archivo = request.FILES.get("comprobante_archivo")
            nueva_foto = request.FILES.get("comprobante_foto")

            if nuevo_archivo:
                obj.comprobante = nuevo_archivo
            elif nueva_foto:
                obj.comprobante = nueva_foto
            # else: NO tocar obj.comprobante (se mantiene el actual)

            # Guardar el status si lo tocamos arriba
            obj.status = rendicion.status

            obj.save()
            messages.success(request, "Rendición actualizada correctamente.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm(instance=rendicion)

    return render(request, 'operaciones/editar_rendicion.html', {'form': form})


@login_required
def eliminar_rendicion(request, pk):
    rendicion = get_object_or_404(
        CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(
            request, "No puedes eliminar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        rendicion.delete()
        messages.success(request, "Rendición eliminada correctamente.")
        return redirect('operaciones:mis_rendiciones')

    return render(request, 'operaciones/eliminar_rendicion.html', {'rendicion': rendicion})


from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Sum, Value, When


@login_required
def vista_rendiciones(request):
    user = request.user

    # --- Qué ve cada rol
    if user.is_superuser:
        movimientos = CartolaMovimiento.objects.all()
    elif getattr(user, 'es_supervisor', False):
        movimientos = (
            CartolaMovimiento.objects
            .filter(Q(status__startswith='pendiente') | Q(status__startswith='rechazado'))
            .exclude(tipo__categoria='abono')
        )
    elif getattr(user, 'es_pm', False):
        movimientos = CartolaMovimiento.objects.all()
    elif getattr(user, 'es_facturacion', False):
        movimientos = CartolaMovimiento.objects.all()
    else:
        movimientos = CartolaMovimiento.objects.none()

    # --- Qué estado es "pendiente para mí"
    if getattr(user, 'es_supervisor', False):
        role_pending_status = 'pendiente_supervisor'
    elif getattr(user, 'es_pm', False):
        role_pending_status = 'aprobado_supervisor'   # esperando aprobación del PM
    elif getattr(user, 'es_facturacion', False):
        role_pending_status = 'aprobado_pm'           # esperando finanzas
    else:
        role_pending_status = None

    # --- Orden:
    # 1) prioridad_rol = 0 si es "pendiente para mí", 1 si no
    # 2) luego pendientes -> rechazados -> aprobados
    # 3) luego por fecha desc (y opcional: fecha real del gasto desc)
    if role_pending_status:
        prioridad_rol_expr = Case(
            When(status=role_pending_status, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    else:
        prioridad_rol_expr = Value(1)

    movimientos = movimientos.annotate(
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
        '-fecha_transaccion',  # ✅ fecha real del gasto (si existe)
        '-fecha'               # fecha registro como fallback
    )

    # --- Totales
    total = movimientos.aggregate(total=Sum('cargos'))['total'] or 0
    pendientes = movimientos.filter(status__startswith='pendiente').aggregate(total=Sum('cargos'))['total'] or 0
    rechazados = movimientos.filter(status__startswith='rechazado').aggregate(total=Sum('cargos'))['total'] or 0

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

    paginator = Paginator(movimientos, page_size)
    page_number = request.GET.get('page') or 1
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_param,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
    })


@login_required
def aprobar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    user = request.user

    # Flujo de aprobaciones
    if getattr(user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
        mov.status = 'aprobado_supervisor'
        mov.aprobado_por_supervisor = user
    elif getattr(user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
        mov.status = 'aprobado_pm'
        mov.aprobado_por_pm = user
    elif getattr(user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
        mov.status = 'aprobado_finanzas'
        mov.aprobado_por_finanzas = user

    mov.motivo_rechazo = ''  # limpiar rechazo previo si lo hubiera
    mov.save()
    messages.success(request, "Movimiento aprobado correctamente.")

    # ← Redirige de vuelta a donde estaba (conserva page & cantidad)
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
            if getattr(request.user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
            elif getattr(request.user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
            elif getattr(request.user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                mov.aprobado_por_finanzas = request.user

            mov.motivo_rechazo = motivo
            mov.save()
            messages.success(request, "Movimiento rechazado correctamente.")

    # ← Redirige de vuelta a donde estaba (conserva page & cantidad)
    next_url = (
        request.POST.get('next')
        or request.GET.get('next')
        or request.META.get('HTTP_REFERER')
        or reverse('operaciones:vista_rendiciones')
    )
    return redirect(next_url)


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


@login_required
@rol_requerido('pm')  # Solo PM
def exportar_rendiciones_pm(request):
    # Traer TODAS las rendiciones que el PM puede ver (sin filtrar por status)
    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="rendiciones_pm.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Rendiciones PM')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # Columnas (✅ se agrega "Fecha real del gasto")
    columns = [
        "Nombre",
        "Fecha",                 # fecha registro
        "Fecha real del gasto",  # ✅ nueva
        "Proyecto",
        "Monto",
        "Tipo",
        "Observaciones",
        "Estado",
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Filas
    for row_num, mov in enumerate(movimientos, start=1):
        # --- Fecha registro (mov.fecha)
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        # --- Fecha real del gasto (mov.fecha_transaccion)
        fecha_real_excel = getattr(mov, "fecha_transaccion", None)
        if isinstance(fecha_real_excel, datetime):
            if is_aware(fecha_real_excel):
                fecha_real_excel = fecha_real_excel.astimezone().replace(tzinfo=None)
            fecha_real_excel = fecha_real_excel.date()

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)

        # Si viene vacía, dejamos celda vacía
        if fecha_real_excel:
            ws.write(row_num, 2, fecha_real_excel, date_style)
        else:
            ws.write(row_num, 2, "")

        ws.write(row_num, 3, str(mov.proyecto))
        ws.write(row_num, 4, float(mov.cargos or 0))
        ws.write(row_num, 5, str(mov.tipo or ""))
        ws.write(row_num, 6, str(mov.observaciones or ""))
        ws.write(row_num, 7, mov.get_status_display())

    wb.save(response)
    return response

@login_required
def exportar_mis_rendiciones(request):
    user = request.user
    movimientos = CartolaMovimiento.objects.filter(usuario=user).order_by('-fecha')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="mis_rendiciones.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Mis Rendiciones')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # ✅ 8 columnas (manteniendo el set) + agregando fecha real del gasto
    columns = [
        "Nombre",
        "Fecha registro",
        "Fecha real del gasto",
        "Proyecto",
        "Tipo",
        "RUT Factura",
        "Monto",
        "Estado",
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        # ---- Fecha registro (mov.fecha) ----
        fecha_registro = mov.fecha
        if isinstance(fecha_registro, datetime):
            if is_aware(fecha_registro):
                fecha_registro = fecha_registro.astimezone().replace(tzinfo=None)
            fecha_registro = fecha_registro.date()

        # ---- ✅ Fecha real del gasto (mov.fecha_transaccion) ----
        fecha_real = getattr(mov, "fecha_transaccion", None)
        if isinstance(fecha_real, datetime):
            if is_aware(fecha_real):
                fecha_real = fecha_real.astimezone().replace(tzinfo=None)
            fecha_real = fecha_real.date()

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_registro, date_style)

        if fecha_real:
            ws.write(row_num, 2, fecha_real, date_style)
        else:
            ws.write(row_num, 2, "")

        ws.write(row_num, 3, str(getattr(mov, "proyecto", "") or ""))
        ws.write(row_num, 4, str(getattr(mov, "tipo", "") or ""))
        ws.write(row_num, 5, str(getattr(mov, "rut_factura", "") or ""))
        ws.write(row_num, 6, float(getattr(mov, "cargos", 0) or 0))
        ws.write(row_num, 7, mov.get_status_display())

    wb.save(response)
    return response
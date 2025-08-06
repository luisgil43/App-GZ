# operaciones/views.py
from django.utils.timezone import is_aware
from .forms import validar_rut_chileno, verificar_rut_sii
from django.db.models import Sum
from .forms import MovimientoUsuarioForm  # crearemos este form
from django.shortcuts import redirect
from facturacion.models import CartolaMovimiento
from django.db.models import Sum, Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from django.utils.html import escape
from django.utils.encoding import force_str
from django.core.paginator import Paginator
import calendar
from decimal import Decimal
import requests
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image
import io
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from django.db.models.functions import Coalesce
from django.db.models import Sum, F, Count, Value, FloatField
from django.db.models import Case, When, Value, IntegerField
from django.utils.timezone import now
from django.http import HttpResponseServerError
import logging
import xlwt
from django.http import HttpResponse
import csv
from usuarios.models import CustomUser
from django.urls import reverse
from usuarios.utils import crear_notificacion  # asegúrate de tener esta función
from datetime import datetime
import locale
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from .models import ServicioCotizado
from .forms import ServicioCotizadoForm
import pandas as pd
from django.db import models
from django.contrib import messages
from django.shortcuts import render, redirect
from django.shortcuts import render
from .models import SitioMovil
from django.contrib.auth.decorators import login_required
from usuarios.decoradores import rol_requerido
from operaciones.forms import AsignarTrabajadoresForm

# Configurar locale para nombres de meses en español
try:
    locale.setlocale(locale.LC_TIME, 'es_CL.utf8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'es_ES.utf8')
    except locale.Error:
        locale.setlocale(locale.LC_TIME, '')  # Usa el del sistema


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
    cantidad = request.GET.get("cantidad", "10")  # Cantidad por página
    page_number = request.GET.get("page", 1)

    sitios = SitioMovil.objects.all()

    if id_claro:
        sitios = sitios.filter(id_claro__icontains=id_claro)
    if id_new:
        sitios = sitios.filter(id_sites_new__icontains=id_new)

    # Si selecciona "todos", mostramos todo
    if cantidad == "todos":
        paginator = Paginator(sitios, sitios.count() or 1)
    else:
        paginator = Paginator(sitios, int(cantidad))

    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/listar_sitios.html', {
        'sitios': pagina,
        'id_claro': id_claro,
        'id_new': id_new,
        'cantidad': cantidad,
        'pagina': pagina
    })


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
    # Definir prioridad: 1 = cotizado, 2 = en_ejecucion, 3 = pendiente_por_asignar, 4 = otros
    estado_prioridad = Case(
        When(estado='cotizado', then=Value(1)),
        When(estado='en_ejecucion', then=Value(2)),
        # pendiente por asignar
        When(estado='aprobado_pendiente', then=Value(3)),
        default=Value(4),
        output_field=IntegerField()
    )

    servicios = ServicioCotizado.objects.annotate(
        prioridad=estado_prioridad
    ).order_by('prioridad', '-fecha_creacion')

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
        servicios = servicios.filter(estado=estado)

    # Paginación
    cantidad = request.GET.get("cantidad", "10")
    if cantidad == "todos":
        cantidad = 999999
    else:
        cantidad = int(cantidad)
    paginator = Paginator(servicios, cantidad)
    page_number = request.GET.get("page")
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
def aprobar_cotizacion(request, pk):
    cotizacion = get_object_or_404(ServicioCotizado, pk=pk)
    cotizacion.estado = 'aprobado_pendiente'
    cotizacion.pm_aprueba = request.user
    cotizacion.save()

    # Formatear DU con ceros a la izquierda
    du_formateado = f"DU{str(cotizacion.du).zfill(8)}"

    # ✅ Notificar a los supervisores REALES
    from usuarios.models import CustomUser
    supervisores = CustomUser.objects.filter(
        roles__nombre='supervisor', is_active=True)

    for supervisor in supervisores:
        crear_notificacion(
            usuario=supervisor,
            mensaje=f"Se ha aprobado una nueva cotización {du_formateado}.",
            url=reverse('operaciones:asignar_cotizacion', args=[cotizacion.pk])
        )

    messages.success(request, "Cotización aprobada correctamente.")
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
    estado_prioridad = Case(
        When(estado='aprobado_pendiente', then=Value(1)),
        When(estado__in=['asignado', 'en_progreso'], then=Value(2)),
        When(estado='en_revision_supervisor', then=Value(3)),
        When(estado__in=[
            'finalizado_trabajador',
            'informe_subido',
            'finalizado',
            'aprobado_supervisor',
            'rechazado_supervisor'
        ], then=Value(4)),
        default=Value(5),
        output_field=IntegerField()
    )

    servicios = ServicioCotizado.objects.filter(
        estado__in=[
            'aprobado_pendiente',
            'asignado',
            'en_progreso',
            'finalizado_trabajador',
            'en_revision_supervisor',
            'aprobado_supervisor',
            'rechazado_supervisor',
            'informe_subido',
            'finalizado'
        ]
    ).annotate(
        prioridad=estado_prioridad
    ).order_by('prioridad', '-du')

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
        servicios = servicios.filter(estado=estado)

    # Paginación
    cantidad = request.GET.get("cantidad", "10")
    if cantidad == "todos":
        cantidad = 999999
    else:
        cantidad = int(cantidad)
    paginator = Paginator(servicios, cantidad)
    page_number = request.GET.get("page")
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
            print("Trabajadores asignados:", trabajadores)
            cotizacion.trabajadores_asignados.set(trabajadores)
            cotizacion.estado = 'asignado'
            cotizacion.supervisor_asigna = request.user
            cotizacion.save()

            # Notificar a los trabajadores
            for trabajador in trabajadores:
                crear_notificacion(
                    usuario=trabajador,
                    mensaje=f"Se te ha asignado una nueva tarea: DU{str(cotizacion.du).zfill(8)}.",
                    # Ajusta si usas otra vista
                    url=reverse('operaciones:mis_servicios_tecnico')
                )

            messages.success(request, "Trabajadores asignados correctamente.")
            return redirect('operaciones:listar_servicios_supervisor')
    else:
        form = AsignarTrabajadoresForm()

    return render(request, 'operaciones/asignar_trabajadores.html', {
        'cotizacion': cotizacion,
        'form': form
    })


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def exportar_servicios_supervisor(request):
    servicios = ServicioCotizado.objects.filter(
        estado__in=[
            'aprobado_pendiente', 'asignado', 'en_ejecucion',
            'finalizado_tecnico', 'en_revision_supervisor',
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
            'ID Claro': s.id_claro,  # Cambia a s.id_claro.valor si es ForeignKey
            'Región': s.region,       # Cambia si es ForeignKey
            # Evita usar .strftime si es CharField
            'Mes Producción': s.mes_produccion or '',
            'ID NEW': s.id_new,
            'Detalle Tarea': s.detalle_tarea,
            'Monto MMOO': float(s.monto_mmoo) if s.monto_mmoo else 0,
            'Asignados': asignados,
            # Usa el display si tienes choices
            'Estado': dict(s.ESTADOS).get(s.estado, s.estado),
        })

    df = pd.DataFrame(data)
    columnas = [
        'DU', 'ID Claro', 'Región', 'Mes Producción',
        'ID NEW', 'Detalle Tarea', 'Monto MMOO',
        'Asignados', 'Estado'
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

    # Orden personalizado:
    # 1 = aceptado, 2 = en_ejecucion, 3 = finalizado, 4 = pendiente_aceptar, 5 = otros
    estado_prioridad = Case(
        When(estado='aceptado', then=Value(1)),
        When(estado='en_ejecucion', then=Value(2)),
        When(estado='finalizado', then=Value(3)),
        When(estado='pendiente_aceptar', then=Value(4)),
        default=Value(5),
        output_field=IntegerField()
    )

    # Filtrar: excluir cotizado y aprobado_supervisor
    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario
    ).exclude(
        estado__in=['cotizado', 'aprobado_supervisor']
    ).annotate(
        prioridad=estado_prioridad
    ).order_by('prioridad', '-du')

    print("Servicios encontrados:", servicios.count())

    # Prepara los montos personalizados (manteniendo la lógica de MMOO dividido entre técnicos)
    servicios_info = []
    for servicio in servicios:
        total_mmoo = servicio.monto_mmoo or 0  # Monto total de mano de obra
        # Número de técnicos asignados
        total_tecnicos = servicio.trabajadores_asignados.count()
        monto_tecnico = total_mmoo / total_tecnicos if total_tecnicos else 0
        servicios_info.append({
            'servicio': servicio,
            'monto_tecnico': round(monto_tecnico, 2)
        })

    return render(request, 'operaciones/mis_servicios_tecnico.html', {
        'servicios_info': servicios_info
    })


@login_required
@rol_requerido('usuario')
def aceptar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(
            request, "No tienes permiso para aceptar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    if servicio.estado not in ['asignado', 'rechazado_supervisor']:
        messages.warning(
            request, "Este servicio no está disponible para aceptar.")
        return redirect('operaciones:mis_servicios_tecnico')

    servicio.estado = 'en_progreso'
    servicio.tecnico_aceptado = request.user
    servicio.save()

    messages.success(
        request, "Has aceptado el servicio. Ahora está en progreso.")
    return redirect('operaciones:mis_servicios_tecnico')


@login_required
@rol_requerido('usuario')
def finalizar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    # Ahora cualquier trabajador asignado puede finalizar
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(
            request, "Solo los técnicos asignados pueden finalizar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    if servicio.estado != 'en_progreso':
        messages.warning(request, "Este servicio no está en progreso.")
        return redirect('operaciones:mis_servicios_tecnico')

    servicio.estado = 'finalizado_trabajador'
    servicio.tecnico_finalizo = request.user
    servicio.save()

    messages.success(request, "Has marcado este servicio como finalizado.")
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
@rol_requerido('usuario')
def produccion_tecnico(request):
    usuario = request.user
    id_claro = request.GET.get("id_claro", "")
    mes_produccion = request.GET.get("mes_produccion", "")

    # Traducción manual de meses
    meses_es = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    now = datetime.now()
    mes_actual = f"{meses_es[now.month]} {now.year}"  # -> "Julio 2025"

    # Base queryset
    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado='aprobado_supervisor'
    )
    if id_claro:
        servicios = servicios.filter(id_claro__icontains=id_claro)
    if mes_produccion:
        servicios = servicios.filter(mes_produccion__icontains=mes_produccion)

    # Orden personalizado
    def prioridad(servicio):
        try:
            mes_nombre, año = servicio.mes_produccion.split()
            numero_mes = meses_es.index(mes_nombre.capitalize())
            fecha_servicio = datetime(int(año), numero_mes, 1)
            hoy = datetime.now().replace(day=1)
            if fecha_servicio == hoy:
                return (0, fecha_servicio)
            elif fecha_servicio > hoy:
                return (1, fecha_servicio)
            else:
                return (2, fecha_servicio)
        except:
            return (3, datetime.min)

    servicios = sorted(servicios, key=prioridad)

    # Producción (antes de paginar)
    produccion_info = []
    for servicio in servicios:
        total_mmoo = servicio.monto_mmoo or Decimal("0.0")
        total_tecnicos = servicio.trabajadores_asignados.count()
        monto_tecnico = total_mmoo / \
            total_tecnicos if total_tecnicos else Decimal("0.0")
        produccion_info.append(
            {'servicio': servicio, 'monto_tecnico': round(monto_tecnico, 0)}
        )

    # Paginación sobre produccion_info
    cantidad = request.GET.get('cantidad', 10)
    if cantidad == 'todos':
        paginador = Paginator(produccion_info, len(produccion_info))
    else:
        paginador = Paginator(produccion_info, int(cantidad))
    pagina = request.GET.get('page')
    produccion_info_paginada = paginador.get_page(pagina)

    # Total solo mes actual
    total_acumulado = Decimal("0.0")
    for servicio in servicios:
        if servicio.mes_produccion and servicio.mes_produccion.lower() == mes_actual.lower():
            total_mmoo = servicio.monto_mmoo or Decimal("0.0")
            total_tecnicos = servicio.trabajadores_asignados.count()
            total_acumulado += total_mmoo / \
                total_tecnicos if total_tecnicos else Decimal("0.0")

    return render(request, 'operaciones/produccion_tecnico.html', {
        'produccion_info': produccion_info_paginada,
        'id_claro': id_claro,
        'mes_produccion': mes_produccion,
        'total_estimado': round(total_acumulado, 0),
        'mes_actual': mes_actual,
        'paginador': paginador,
        'cantidad': cantidad,
        'pagina': produccion_info_paginada,
    })


logger = logging.getLogger(__name__)


@login_required
@rol_requerido('usuario')
def exportar_produccion_pdf(request):
    try:
        usuario = request.user
        id_new = request.GET.get("id_new", "")
        mes_produccion = request.GET.get("mes_produccion", "")
        filtro_pdf = request.GET.get("filtro_pdf", "mes_actual")

        # Traducción manual de meses
        meses_es = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        now = datetime.now()
        mes_actual = f"{meses_es[now.month]} {now.year}"

        # Texto de filtro
        if filtro_pdf == "mes_actual":
            filtro_seleccionado = f"Solo mes actual: {mes_actual}"
        elif filtro_pdf == "filtro_actual":
            filtro_seleccionado = f"Con filtros aplicados: {mes_produccion}" if mes_produccion else "Con filtros aplicados"
        else:
            filtro_seleccionado = "Toda la producción"

        # Query base
        servicios = ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado='aprobado_supervisor'
        )

        # Filtro según selección
        if filtro_pdf == "filtro_actual":
            if id_new:
                servicios = servicios.filter(id_new__icontains=id_new)
            if mes_produccion:
                servicios = servicios.filter(
                    mes_produccion__icontains=mes_produccion)
        elif filtro_pdf == "mes_actual":
            servicios = servicios.filter(mes_produccion__iexact=mes_actual)

        # Si no hay datos, lanzamos excepción
        if not servicios.exists():
            raise ValueError("No hay datos para exportar.")

        # Datos PDF
        produccion_data = []
        total_produccion = Decimal("0.0")
        for servicio in servicios:
            total_mmoo = servicio.monto_mmoo or Decimal("0.0")
            total_tecnicos = servicio.trabajadores_asignados.count()
            monto_tecnico = total_mmoo / \
                total_tecnicos if total_tecnicos else Decimal("0.0")

            produccion_data.append([
                f"DU{servicio.du}",
                servicio.id_new or "-",
                Paragraph(servicio.detalle_tarea or "-", ParagraphStyle(
                    'detalle_style', fontSize=9, leading=11, alignment=0)),
                f"{monto_tecnico:,.0f}".replace(",", ".")
            ])

            total_produccion += monto_tecnico

        # Generación PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                                topMargin=50, bottomMargin=50)
        elements = []
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="CenterTitle",
                   alignment=1, fontSize=16, spaceAfter=20))

        # Títulos
        elements.append(Paragraph(
            f"Producción del Técnico: {usuario.get_full_name()}", styles["CenterTitle"]))
        elements.append(Paragraph(
            f"<b>Total Producción:</b> ${total_produccion:,.0f} CLP".replace(",", "."), styles["Normal"]))
        elements.append(Paragraph(
            f"<i>El total corresponde a la selección:</i> {filtro_seleccionado}.", styles["Normal"]))
        elements.append(Paragraph(
            f"<b>Fecha de generación:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
        elements.append(Spacer(1, 12))

        # Tabla
        data = [["DU", "ID NEW", "Detalle",
                 "Producción (CLP)"]] + produccion_data
        table = Table(data, colWidths=[70, 100, 300, 80])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e7490")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.whitesmoke, colors.lightgrey]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.gray),
        ]))
        elements.append(table)

        # Firma
        elements.append(Spacer(1, 40))
        elements.append(
            Paragraph("<b>Firma del Técnico:</b>", styles["Normal"]))
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(
            f"__________________________<br/>{usuario.get_full_name()}", styles["Normal"]))

        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="produccion.pdf"'
        return response

    except Exception as e:
        logger.error(f"Error exportando PDF: {e}")
        return HttpResponse(f"Error generando PDF: {e}", status=500)


@login_required
def mis_rendiciones(request):
    user = request.user

    # --- Crear nueva rendición ---
    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)
        if form.is_valid():
            mov = form.save(commit=False)
            mov.usuario = user
            mov.fecha = now()
            mov.status = 'pendiente_supervisor'
            mov.comprobante = form.cleaned_data.get(
                "comprobante")  # Usamos el comprobante validado
            mov.save()
            messages.success(request, "Rendición registrada correctamente.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm()

    # --- Filtros y Paginación ---
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)

    movimientos = CartolaMovimiento.objects.filter(
        usuario=user
    ).order_by('-fecha')

    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Cálculo de saldos ---
    # Disponible: abonos aprobados - gastos aprobados por finanzas
    saldo_disponible = (
        (movimientos.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
         .aggregate(total=Sum('abonos'))['total'] or 0)
        -
        (movimientos.exclude(tipo__categoria="abono")
         .filter(status="aprobado_finanzas")
         .aggregate(total=Sum('cargos'))['total'] or 0)
    )

    # Pendiente: abonos aún no aprobados por el usuario
    saldo_pendiente = (
        movimientos.filter(tipo__categoria="abono")
        .exclude(status="aprobado_abono_usuario")
        .aggregate(total=Sum('abonos'))['total'] or 0
    )

    # Rendido: todo lo que no es abono y no está aprobado por finanzas
    saldo_rendido = (
        movimientos.exclude(tipo__categoria="abono")
        .exclude(status="aprobado_finanzas")
        .aggregate(total=Sum('cargos'))['total'] or 0
    )

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': request.GET.get('cantidad', '10'),
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
    rendicion = get_object_or_404(
        CartolaMovimiento, pk=pk, usuario=request.user
    )

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "No puedes editar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        form = MovimientoUsuarioForm(
            request.POST, request.FILES, instance=rendicion)

        if form.is_valid():
            # --- Detectar cambios ---
            campos_editados = []
            for field in form.changed_data:
                # ignoramos campos automáticos como 'status'
                if field not in ['status', 'actualizado']:
                    campos_editados.append(field)

            if campos_editados:
                # Si cambió algo y estaba rechazado, restablecer estado
                if rendicion.status in ['rechazado_abono_usuario', 'rechazado_supervisor', 'rechazado_pm', 'rechazado_finanzas']:
                    rendicion.status = 'pendiente_supervisor'  # estado reiniciado

            form.save()
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


@login_required
def vista_rendiciones(request):
    user = request.user

    if user.is_superuser:
        movimientos = CartolaMovimiento.objects.all()
    elif getattr(user, 'es_supervisor', False):
        # Supervisor: solo pendientes y rechazados, excluyendo abonos
        movimientos = CartolaMovimiento.objects.filter(
            Q(status__startswith='pendiente') | Q(
                status__startswith='rechazado')
        ).exclude(tipo__categoria='abono')
    elif getattr(user, 'es_pm', False):
        # PM: ve todas
        movimientos = CartolaMovimiento.objects.all()
    else:
        movimientos = CartolaMovimiento.objects.none()

    # Orden personalizado: pendientes -> rechazados -> aprobados
    movimientos = movimientos.annotate(
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado', then=Value(3)),
            default=Value(4),
            output_field=IntegerField()
        )
    ).order_by('orden_status', '-fecha')

    # Totales
    total = movimientos.aggregate(total=Sum('cargos'))['total'] or 0
    pendientes = movimientos.filter(status__startswith='pendiente').aggregate(
        total=Sum('cargos'))['total'] or 0
    rechazados = movimientos.filter(status__startswith='rechazado').aggregate(
        total=Sum('cargos'))['total'] or 0

    # Paginación
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)
    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
    })


@login_required
def aprobar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    user = request.user

    if getattr(user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
        mov.status = 'aprobado_supervisor'
        mov.aprobado_por_supervisor = user
    elif getattr(user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
        mov.status = 'aprobado_pm'
        mov.aprobado_por_pm = user
    elif getattr(user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
        mov.status = 'aprobado_finanzas'
        mov.aprobado_por_finanzas = user  # ← aquí guardamos al usuario de finanzas

    # Limpiamos motivo de rechazo si fue aprobado
    mov.motivo_rechazo = ''
    mov.save()
    messages.success(request, "Movimiento aprobado correctamente.")
    return redirect('operaciones:vista_rendiciones')


@login_required
def rechazar_rendicion(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo')
        if motivo:
            movimiento.motivo_rechazo = motivo
            # Detectar quién rechaza y actualizar el estado
            if request.user.es_supervisor and movimiento.status == 'pendiente_supervisor':
                movimiento.status = 'rechazado_supervisor'
                movimiento.aprobado_por_supervisor = request.user
            elif request.user.es_pm and movimiento.status == 'aprobado_supervisor':
                movimiento.status = 'rechazado_pm'
                movimiento.aprobado_por_pm = request.user
            elif request.user.es_facturacion and movimiento.status == 'aprobado_pm':
                movimiento.status = 'rechazado_finanzas'
                # ← aquí guardamos al usuario de finanzas
                movimiento.aprobado_por_finanzas = request.user
            movimiento.save()
            messages.success(request, "Movimiento rechazado correctamente.")
        else:
            messages.error(request, "Debe ingresar el motivo del rechazo.")
    return redirect('operaciones:vista_rendiciones')


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

    # Columnas
    columns = ["Nombre", "Fecha", "Proyecto", "Monto", "Estado"]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Filas
    for row_num, mov in enumerate(movimientos, start=1):
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)
        ws.write(row_num, 2, str(mov.proyecto))
        ws.write(row_num, 3, float(mov.cargos or 0))
        ws.write(row_num, 4, mov.get_status_display())

    wb.save(response)
    return response


@login_required
def exportar_mis_rendiciones(request):
    user = request.user
    movimientos = CartolaMovimiento.objects.filter(
        usuario=user).order_by('-fecha')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="mis_rendiciones.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Mis Rendiciones')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # Columnas
    columns = ["Nombre", "Fecha", "Proyecto", "Monto", "Estado"]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Filas
    for row_num, mov in enumerate(movimientos, start=1):
        # Fecha naive
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)
        ws.write(row_num, 2, str(mov.proyecto))
        ws.write(row_num, 3, float(mov.cargos or 0))
        ws.write(row_num, 4, mov.get_status_display())

    wb.save(response)
    return response

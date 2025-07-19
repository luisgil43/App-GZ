# operaciones/views.py

import xlwt
from django.http import HttpResponse
import csv
from usuarios.models import CustomUser
from django.urls import reverse
from usuarios.utils import crear_notificacion  # asegúrate de tener esta función
from datetime import datetime
import locale
from django.http import JsonResponse
from operaciones.models import SitioMovil  # Ajusta según tu modelo real
from django.shortcuts import get_object_or_404
from .models import ServicioCotizado
from .forms import ServicioCotizadoForm
import pandas as pd
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


# operaciones/views.py
@login_required
@rol_requerido('pm', 'admin', 'facturacion', 'supervisor')
def listar_sitios(request):
    id_claro = request.GET.get("id_claro", "")
    sitios = SitioMovil.objects.all()

    if id_claro:
        sitios = sitios.filter(id_claro__icontains=id_claro)

    return render(request, 'operaciones/listar_sitios.html', {
        'sitios': sitios,
        'id_claro': id_claro
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
    servicios = ServicioCotizado.objects.all().order_by('-fecha_creacion')

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

    return render(request, 'operaciones/listar_servicios_pm.html', {
        'servicios': servicios,
        'filtros': {
            'du': du,
            'id_claro': id_claro,
            'mes_produccion': mes_produccion,
            'id_new': id_new,
            'estado': estado,
        }
    })


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def crear_servicio_cotizado(request):
    if request.method == 'POST':
        form = ServicioCotizadoForm(request.POST)
        if form.is_valid():
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

    if servicio.estado not in ['cotizado', 'aprobado'] and not (request.user.is_superuser or request.user.es_facturacion):
        messages.error(
            request, "No puedes editar esta cotización porque ya fue asignada.")
        return redirect('operaciones:listar_servicios_pm')

    if servicio.creado_por != request.user and not request.user.is_superuser:
        messages.error(
            request, "No tienes permisos para editar esta cotización.")
        return redirect('operaciones:listar_servicios_pm')

    if request.method == 'POST':
        form = ServicioCotizadoForm(request.POST, instance=servicio)
        if form.is_valid():
            form.save()
            messages.success(request, "Cotización actualizada correctamente.")
            return redirect('operaciones:listar_servicios_pm')
    else:
        form = ServicioCotizadoForm(instance=servicio)

    return render(request, 'operaciones/editar_servicio_cotizado.html', {'form': form})


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def eliminar_servicio_cotizado(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    # Solo permitir eliminar si el estado es editable o el usuario es admin o facturación
    if servicio.estado not in ['cotizado', 'aprobado'] and not (request.user.is_superuser or request.user.es_facturacion):
        messages.error(
            request, "No puedes eliminar esta cotización porque ya fue asignada.")
        return redirect('operaciones:listar_servicios_pm')

    # Solo permitir que el PM elimine su propia cotización
    if servicio.creado_por != request.user and not request.user.is_superuser:
        messages.error(
            request, "No tienes permisos para eliminar esta cotización.")
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
            }
            df.rename(columns=encabezados_validos, inplace=True)

            columnas_requeridas = [
                'id_claro', 'mes_produccion', 'detalle_tarea', 'monto_cotizado']
            for col in columnas_requeridas:
                if col not in df.columns:
                    messages.error(
                        request, f'Falta la columna requerida: {col}')
                    return redirect('operaciones:listar_servicios_pm')

            for _, row in df.iterrows():
                id_claro = str(row['id_claro'])

                # Autocompletar REGION
                if 'region' in row and not pd.isna(row['region']):
                    region = row['region']
                else:
                    region = id_claro.split(
                        '_')[0] if '_' in id_claro else '13'

                # Autocompletar ID NEW
                if 'id_new' in row and not pd.isna(row['id_new']):
                    id_new = row['id_new']
                else:
                    id_new = f"CL-{region}-CN-{id_claro.replace('_', '')}"

                # Convertir MES PRODUCCION a "julio 2025"
                valor = row['mes_produccion']
                if isinstance(valor, (datetime, pd.Timestamp)):
                    mes_produccion = valor.strftime('%B %Y').capitalize()
                else:
                    try:
                        fecha_parseada = pd.to_datetime(
                            str(valor), dayfirst=True, errors='coerce')
                        if pd.isna(fecha_parseada):
                            mes_produccion = str(valor).capitalize()
                        else:
                            mes_produccion = fecha_parseada.strftime(
                                '%B %Y').capitalize()
                    except:
                        mes_produccion = str(valor).capitalize()

                ServicioCotizado.objects.create(
                    id_claro=id_claro,
                    region=region,
                    mes_produccion=mes_produccion,
                    id_new=id_new,
                    detalle_tarea=row['detalle_tarea'],
                    monto_cotizado=row['monto_cotizado'],
                    estado='cotizado',
                    creado_por=request.user  # ✅ Esta línea permite que se muestren en el listado
                )

            messages.success(request, 'Cotizaciones importadas correctamente.')
            return redirect('operaciones:listar_servicios_pm')

        except Exception as e:
            messages.error(request, f'Error al importar: {e}')
            return redirect('operaciones:listar_servicios_pm')

    return render(request, 'operaciones/importar_cotizaciones.html')


@login_required
@rol_requerido('supervisor', 'admin', 'facturacion')
def listar_servicios_supervisor(request):
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
    )

    # Filtros
    du = request.GET.get('du', '')
    id_claro = request.GET.get('id_claro', '')
    id_new = request.GET.get('id_new', '')
    mes_produccion = request.GET.get('mes_produccion', '')
    estado = request.GET.get('estado', '')

    if du:
        # Elimina el prefijo DU y ceros a la izquierda innecesarios
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

    servicios = servicios.order_by('-du')

    return render(request, 'operaciones/listar_servicios_supervisor.html', {
        'servicios': servicios,
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
@rol_requerido('supervisor', 'admin')
def asignar_trabajadores(request, pk):
    cotizacion = get_object_or_404(ServicioCotizado, pk=pk)

    if request.method == 'POST':
        form = AsignarTrabajadoresForm(request.POST)
        if form.is_valid():
            trabajadores = form.cleaned_data['trabajadores']
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
@rol_requerido('supervisor')
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

    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario
    ).exclude(estado='cotizado').order_by('-du')

    print("Servicios encontrados:", servicios.count())

    return render(request, 'operaciones/mis_servicios_tecnico.html', {
        'servicios': servicios
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
@rol_requerido('supervisor')
def aprobar_asignacion(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    if servicio.estado == 'asignado':
        servicio.estado = 'en_progreso'
    elif servicio.estado == 'finalizado_trabajador':
        servicio.estado = 'aprobado_supervisor'
        servicio.supervisor_aprobo = request.user
    else:
        messages.warning(
            request, "Este servicio no está en un estado aprobable.")
        return redirect('operaciones:listar_servicios_supervisor')

    servicio.save()
    messages.success(request, "Aprobación realizada correctamente.")
    return redirect('operaciones:listar_servicios_supervisor')


@login_required
@rol_requerido('supervisor')
def rechazar_asignacion(request, pk):
    if request.method == 'POST':
        motivo = request.POST.get('motivo', '').strip()
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

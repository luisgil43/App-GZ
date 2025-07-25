from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse
from openpyxl.utils import get_column_letter
import openpyxl
import traceback
from usuarios.decoradores import rol_requerido
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from datetime import datetime
from decimal import Decimal
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib import messages
import re
import pdfplumber
from operaciones.models import ServicioCotizado
from facturacion.models import OrdenCompraFacturacion
from facturacion.forms import OrdenCompraFacturacionForm


from django.core.paginator import Paginator


@login_required
@rol_requerido('facturacion', 'admin')
def listar_ordenes_compra(request):
    # Filtros
    du = request.GET.get('du', '')
    id_claro = request.GET.get('id_claro', '')
    id_new = request.GET.get('id_new', '')
    mes_produccion = request.GET.get('mes_produccion', '')
    estado = request.GET.get('estado', '')

    # Estados válidos (de cotizado a finalizado)
    estados_validos = [
        'cotizado',
        'aprobado_pendiente',
        'asignado',
        'en_progreso',
        'finalizado_trabajador',
        'rechazado_supervisor',
        'aprobado_supervisor',
        'informe_subido',
        'finalizado'
    ]

    # Traer TODOS los servicios en esos estados (con o sin órdenes)
    servicios = ServicioCotizado.objects.select_related(
        'pm_aprueba', 'tecnico_aceptado', 'tecnico_finalizo', 'supervisor_aprobo',
        'supervisor_rechazo', 'supervisor_asigna', 'usuario_informe'
    ).prefetch_related(
        'ordenes_compra', 'trabajadores_asignados'
    ).filter(
        estado__in=estados_validos
    ).order_by('-fecha_creacion')

    # Filtros dinámicos
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
    cantidad = 999999 if cantidad == "todos" else int(cantidad)
    paginator = Paginator(servicios, cantidad)
    page_number = request.GET.get("page")
    pagina = paginator.get_page(page_number)

    return render(request, 'facturacion/listar_ordenes_compra.html', {
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
@rol_requerido('facturacion', 'admin')
def importar_orden_compra(request):
    if request.method == 'POST' and request.FILES.get('archivo_pdf'):
        archivo = request.FILES['archivo_pdf']
        nombre_archivo = archivo.name

        ruta_temporal = default_storage.save(
            f"temp_oc/{nombre_archivo}", ContentFile(archivo.read()))
        ruta_absoluta = default_storage.path(ruta_temporal)

        datos_extraidos = []
        numero_oc = 'NO_ENCONTRADO'

        with pdfplumber.open(ruta_absoluta) as pdf:
            lineas_completas = []
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if not texto:
                    continue

                if pagina.page_number == 1:
                    lineas = texto.split('\n')
                    for idx, linea in enumerate(lineas):
                        if 'ORDEN DE COMPRA' in linea.upper():
                            if idx + 1 < len(lineas):
                                posible_oc = re.search(
                                    r'\d{10}', lineas[idx + 1])
                                if posible_oc:
                                    numero_oc = posible_oc.group()
                                    break

                lineas_completas += texto.split('\n')

        i = 0
        while i < len(lineas_completas):
            linea = lineas_completas[i]
            if re.match(r'^\d+\s+\d+\s+SER', linea):
                partes = re.split(r'\s{2,}', linea.strip())
                if len(partes) < 7:
                    partes = linea.split()

                if len(partes) >= 8:
                    pos = partes[0]
                    cantidad = partes[1]
                    unidad = partes[2]
                    material = partes[3]
                    descripcion = ' '.join(partes[4:-3])
                    fecha_entrega = partes[-3]
                    precio_unitario = partes[-2].replace(',', '.')
                    monto = partes[-1].replace(',', '.')

                    id_new = None
                    if i + 1 < len(lineas_completas):
                        match_id = re.search(
                            r'(CL-\d{2}-[A-Z]{2}-\d{5}-\d{2})',
                            lineas_completas[i + 1]
                        )
                        if match_id:
                            id_new = match_id.group(1)

                    datos_extraidos.append({
                        'orden_compra': numero_oc,
                        'pos': pos,
                        'cantidad': cantidad,
                        'unidad_medida': unidad,
                        'material_servicio': material,
                        'descripcion_sitio': descripcion,
                        'fecha_entrega': fecha_entrega,
                        'precio_unitario': precio_unitario,
                        'monto': monto,
                        'id_new': id_new,
                    })
                    i += 1
            i += 1

        default_storage.delete(ruta_temporal)

        request.session['ordenes_previsualizadas'] = datos_extraidos

        # Verificación: detectar ID NEW sin servicio registrado
        ids_no_encontrados = set()
        for fila in datos_extraidos:
            id_new = fila.get('id_new')
            if not id_new:
                ids_no_encontrados.add("SIN_ID")
                continue

            existe = ServicioCotizado.objects.filter(id_new=id_new).exists()
            if not existe:
                ids_no_encontrados.add(id_new)

        return render(request, 'facturacion/preview_oc.html', {
            'datos': datos_extraidos,
            'nombre_archivo': nombre_archivo,
            'ids_no_encontrados': ids_no_encontrados,
        })

    # 🔁 Este return es fundamental para evitar el ValueError en peticiones GET
    return render(request, 'facturacion/importar_orden_compra.html')


@login_required
@rol_requerido('facturacion', 'admin')
def guardar_ordenes_compra(request):
    if request.method == 'POST':
        datos_previsualizados = request.session.get('ordenes_previsualizadas')
        if not datos_previsualizados:
            messages.error(request, "No hay datos para guardar.")
            return redirect('facturacion:importar_orden_compra')

        ordenes_guardadas = 0
        ordenes_sin_oc_libre = []
        ordenes_sin_servicio = []

        for item in datos_previsualizados:
            id_new = item.get('id_new')
            if not id_new:
                continue

            # Buscar todos los servicios con ese ID_NEW (sin considerar el mes)
            servicios = ServicioCotizado.objects.filter(
                id_new=id_new).order_by('du')

            if not servicios.exists():
                ordenes_sin_servicio.append(f"ID NEW: {id_new}")
                continue

            # Buscar el primer servicio sin OC ya registrada (usando relación inversa)
            servicio_sin_oc = None
            for s in servicios:
                if not s.ordenes_compra.exists():
                    servicio_sin_oc = s
                    break

            if not servicio_sin_oc:
                ordenes_sin_oc_libre.append(
                    f"ID NEW: {id_new} - POS: {item.get('pos')}")
                continue

            # Rellenar y guardar nueva OC
            try:
                cantidad = Decimal(
                    str(item.get('cantidad') or '0').replace(',', '.'))
                precio_unitario = Decimal(
                    str(item.get('precio_unitario') or '0').replace(',', '.'))
                monto = Decimal(
                    str(item.get('monto') or '0').replace(',', '.'))

                fecha_entrega = None
                fecha_texto = item.get('fecha_entrega')
                if fecha_texto:
                    try:
                        fecha_entrega = datetime.strptime(
                            fecha_texto, '%d.%m.%Y').date()
                    except ValueError:
                        pass

                # Crear nueva OC asociada
                OrdenCompraFacturacion.objects.create(
                    du=servicio_sin_oc,
                    orden_compra=item.get('orden_compra'),
                    pos=item.get('pos'),
                    cantidad=cantidad,
                    unidad_medida=item.get('unidad_medida'),
                    material_servicio=item.get('material_servicio'),
                    descripcion_sitio=item.get('descripcion_sitio'),
                    fecha_entrega=fecha_entrega,
                    precio_unitario=precio_unitario,
                    monto=monto,
                )

                ordenes_guardadas += 1

            except Exception as e:
                print(f"❌ Error al guardar datos de OC: {e}")
                continue

        # Limpiar la sesión
        request.session.pop('ordenes_previsualizadas', None)

        if ordenes_guardadas > 0:
            messages.success(
                request,
                f"{ordenes_guardadas} líneas de la orden de compra fueron guardadas correctamente."
            )

        if ordenes_sin_oc_libre:
            messages.warning(
                request,
                "Se omitieron las siguientes líneas porque ya no hay servicios disponibles sin OC para asociar:<br>" +
                "<br>".join(ordenes_sin_oc_libre)
            )

        if ordenes_sin_servicio:
            messages.error(
                request,
                "Las siguientes líneas no se pudieron asociar porque no existe un servicio creado para esos ID NEW:<br>" +
                "<br>".join(set(ordenes_sin_servicio)) +
                "<br><br>Comunícate con el PM para que cree el servicio y vuelve a importar la OC."
            )

        return redirect('facturacion:importar_orden_compra')

    return redirect('facturacion:listar_ordenes_compra')


@login_required
@rol_requerido('facturacion', 'admin')
def editar_orden_compra(request, pk):
    orden = get_object_or_404(OrdenCompraFacturacion, pk=pk)
    if request.method == 'POST':
        form = OrdenCompraFacturacionForm(request.POST, instance=orden)
        if form.is_valid():
            form.save()
            return redirect('facturacion:listar_oc_facturacion')
    else:
        form = OrdenCompraFacturacionForm(instance=orden)
    return render(request, 'facturacion/editar_orden_compra.html', {'form': form})


@login_required
@rol_requerido('facturacion', 'admin')
def eliminar_orden_compra(request, pk):
    orden = get_object_or_404(OrdenCompraFacturacion, pk=pk)

    if request.method == 'POST':
        orden.delete()
        messages.success(request, "Orden de compra eliminada correctamente.")
        return redirect('facturacion:listar_oc_facturacion')

    return render(request, 'facturacion/eliminar_orden_compra.html', {'orden': orden})


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_ordenes_compra_excel(request):
    # Filtros (mismos que en listar)
    du = request.GET.get('du', '')
    id_claro = request.GET.get('id_claro', '')
    id_new = request.GET.get('id_new', '')
    mes_produccion = request.GET.get('mes_produccion', '')
    estado = request.GET.get('estado', '')

    estados_validos = [
        'cotizado',
        'aprobado_pendiente',
        'asignado',
        'en_progreso',
        'finalizado_trabajador',
        'rechazado_supervisor',
        'aprobado_supervisor',
        'informe_subido',
        'finalizado'
    ]

    servicios = ServicioCotizado.objects.select_related(
        'pm_aprueba', 'tecnico_aceptado', 'tecnico_finalizo', 'supervisor_aprobo',
        'supervisor_rechazo', 'supervisor_asigna', 'usuario_informe'
    ).prefetch_related(
        'ordenes_compra', 'trabajadores_asignados'
    ).filter(
        estado__in=estados_validos
    ).order_by('-fecha_creacion')

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

    # Crear libro Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Órdenes de Compra"

    # Encabezados
    columnas = [
        "DU", "ID CLARO", "ID NEW", "DETALLE TAREA", "ASIGNADOS",
        "M. COTIZADO (UF)", "M. MMOO (CLP)", "FECHA FIN", "STATUS",
        "OC", "POS", "CANT", "UM", "MATERIAL", "DESCRIPCIÓN SITIO",
        "FECHA ENTREGA", "P. UNITARIO", "MONTO"
    ]
    ws.append(columnas)

    # Estilo encabezados
    header_fill = PatternFill(start_color="D9D9D9",
                              end_color="D9D9D9", fill_type="solid")
    for col_num, col_name in enumerate(columnas, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Datos
    for servicio in servicios:
        asignados = ", ".join([u.get_full_name()
                              for u in servicio.trabajadores_asignados.all()]) or ''
        if servicio.ordenes_compra.exists():
            for oc in servicio.ordenes_compra.all():
                ws.append([
                    f"DU{servicio.du or ''}",
                    servicio.id_claro or '',
                    servicio.id_new or '',
                    servicio.detalle_tarea or '',
                    asignados,
                    servicio.monto_cotizado or 0,
                    servicio.monto_mmoo or 0,
                    servicio.fecha_aprobacion_supervisor.strftime(
                        "%d-%m-%Y") if servicio.fecha_aprobacion_supervisor else '',
                    servicio.get_estado_display(),
                    oc.orden_compra or '',
                    oc.pos or '',
                    oc.cantidad or '',
                    oc.unidad_medida or '',
                    oc.material_servicio or '',
                    oc.descripcion_sitio or '',
                    oc.fecha_entrega.strftime(
                        "%d-%m-%Y") if oc.fecha_entrega else '',
                    oc.precio_unitario or 0,
                    oc.monto or 0,
                ])
        else:
            # Si no tiene órdenes, llenar con datos del servicio y vacíos para los campos de OC
            ws.append([
                f"DU{servicio.du or ''}",
                servicio.id_claro or '',
                servicio.id_new or '',
                servicio.detalle_tarea or '',
                asignados,
                servicio.monto_cotizado or 0,
                servicio.monto_mmoo or 0,
                servicio.fecha_aprobacion_supervisor.strftime(
                    "%d-%m-%Y") if servicio.fecha_aprobacion_supervisor else '',
                servicio.get_estado_display(),
                '', '', '', '', '', '', '', '', ''
            ])

    # Ajustar ancho de columnas automáticamente
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                if cell.value and len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[col_letter].width = max_length + 2

    # Respuesta HTTP
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="ordenes_compra.xlsx"'
    wb.save(response)
    return response

import traceback
from usuarios.decoradores import rol_requerido
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from .models import OrdenCompraFacturacion, ServicioCotizado
from django.shortcuts import redirect
from datetime import datetime
from decimal import Decimal
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib import messages
from django.shortcuts import render, redirect
import re
import pdfplumber
from django.shortcuts import render
from operaciones.models import ServicioCotizado
from facturacion.models import OrdenCompraFacturacion
from facturacion.forms import OrdenCompraFacturacionForm


@login_required
@rol_requerido('facturacion', 'admin')
def listar_ordenes_compra(request):
    servicios = ServicioCotizado.objects.prefetch_related(
        'ordenes_compra', 'trabajadores_asignados').all().order_by('-fecha_creacion')
    return render(request, 'facturacion/listar_ordenes_compra.html', {'servicios': servicios})


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

        return render(request, 'facturacion/preview_oc.html', {
            'datos': datos_extraidos,
            'nombre_archivo': nombre_archivo,
        })

    # ✅ Importante: manejar GET o caso donde no se subió archivo
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
        ordenes_duplicadas = []

        for idx, item in enumerate(datos_previsualizados, start=1):
            id_new = item.get('id_new')
            if not id_new:
                continue

            try:
                servicio = ServicioCotizado.objects.get(id_new=id_new)
            except ServicioCotizado.DoesNotExist:
                continue

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

                # Validar campos obligatorios antes de crear
                campos_obligatorios = [
                    item.get('orden_compra'),
                    item.get('pos'),
                    item.get('unidad_medida'),
                    item.get('material_servicio'),
                    item.get('descripcion_sitio'),
                ]
                if not all(campos_obligatorios):
                    print(f"❌ Datos incompletos en la fila {idx}: {item}")
                    continue

                # Validación de duplicado por OC + POS + ID_NEW
                ya_existe = OrdenCompraFacturacion.objects.filter(
                    orden_compra=item.get('orden_compra'),
                    pos=item.get('pos'),
                    du__id_new=id_new
                ).exists()

                if ya_existe:
                    ordenes_duplicadas.append(
                        f"OC: {item.get('orden_compra')} - POS: {item.get('pos')} - ID: {id_new}"
                    )
                    continue

                OrdenCompraFacturacion.objects.create(
                    du=servicio,
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
                print(f"❌ Error al guardar una orden en la fila {idx}: {item}")
                traceback.print_exc()
                continue

        # Limpia sesión
        if 'ordenes_previsualizadas' in request.session:
            del request.session['ordenes_previsualizadas']

        # Mensajes
        if ordenes_guardadas > 0:
            messages.success(
                request, f"{ordenes_guardadas} órdenes de compra fueron guardadas correctamente.")
        if ordenes_duplicadas:
            mensaje = "Se omitieron las siguientes órdenes por ser duplicadas:<br>" + \
                "<br>".join(ordenes_duplicadas)
            messages.error(request, mensaje)

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

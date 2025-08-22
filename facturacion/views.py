import xlwt
from django.utils.timezone import is_aware
from django.db.models import Q
from operaciones.forms import MovimientoUsuarioForm
from django.db.models import Sum, Q
from django.contrib.auth import get_user_model
from facturacion.models import CartolaMovimiento
from django.shortcuts import render
from django.db.models import Sum, F, Value
from .forms import CartolaMovimientoCompletoForm
from .forms import ProyectoForm
from .models import Proyecto
from django.template.loader import render_to_string
from .forms import TipoGastoForm
from .models import TipoGasto
from .forms import CartolaAbonoForm
from .forms import CartolaGastoForm
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from dateutil import parser
from decimal import Decimal, InvalidOperation
from .forms import ImportarFacturasForm
from .forms import FacturaOCForm
from .models import OrdenCompraFacturacion, FacturaOC
from django.http import JsonResponse
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
from facturacion.models import FacturaOC

from django.core.paginator import Paginator


from django.db.models import Subquery
from facturacion.models import FacturaOC


User = get_user_model()


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

    # IDs de órdenes ya facturadas
    ordenes_facturadas = FacturaOC.objects.values('orden_compra_id')

    # Traer servicios, EXCLUYENDO los que ya tienen facturas
    servicios = ServicioCotizado.objects.select_related(
        'pm_aprueba', 'tecnico_aceptado', 'tecnico_finalizo', 'supervisor_aprobo',
        'supervisor_rechazo', 'supervisor_asigna', 'usuario_informe'
    ).prefetch_related(
        'ordenes_compra', 'trabajadores_asignados'
    ).filter(
        estado__in=estados_validos
    ).exclude(
        ordenes_compra__in=Subquery(ordenes_facturadas)
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

        import re
        import pdfplumber

        with pdfplumber.open(ruta_absoluta) as pdf:
            # Procesar página por página para que cada una tenga su OC correcta
            for pagina in pdf.pages:
                texto = pagina.extract_text() or ""
                lineas = [l for l in texto.split("\n") if l.strip()]

                # --- 1) Detectar la OC de ESTA página ---
                numero_oc = None
                # Buscar "ORDEN DE COMPRA" y luego el número en las 1-4 líneas siguientes
                for idx, linea in enumerate(lineas[:40]):
                    if 'ORDEN DE COMPRA' in linea.upper():
                        for k in range(1, 5):
                            if idx + k < len(lineas):
                                m = re.search(r'\b(\d{10})\b', lineas[idx + k])
                                if m:
                                    numero_oc = m.group(1)
                                    break
                        if numero_oc:
                            break
                # Fallback: si no se encontró con la etiqueta, busca el primer 10 dígitos en la cabecera
                if not numero_oc:
                    for cab in lineas[:60]:
                        m = re.search(r'\b(\d{10})\b', cab)
                        if m:
                            numero_oc = m.group(1)
                            break
                # Último fallback: marca si no se logró encontrar
                if not numero_oc:
                    numero_oc = 'NO_ENCONTRADO'

                # --- 2) Extraer filas de detalle de ESTA página ---
                i = 0
                while i < len(lineas):
                    linea = lineas[i]

                    # Coincide con: "POS  CANT  UM  MATERIAL  DESCRIP...  FECHA  P_UNIT  MONTO"
                    # Ej: "10 1 SER 3013768 Mantención Correctiva... 10.07.2025  16,72  16,72"
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

                            # El ID NEW suele venir en la línea siguiente
                            id_new = None
                            if i + 1 < len(lineas):
                                m2 = re.search(
                                    r'(CL-\d{2}-[A-Z]{2}-\d{5}-\d{2})',
                                    lineas[i + 1]
                                )
                                if m2:
                                    id_new = m2.group(1)

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
                            i += 1  # saltar la línea del ID NEW
                    i += 1

        # Limpieza del temporal
        default_storage.delete(ruta_temporal)

        # Guardar en sesión para el preview
        request.session['ordenes_previsualizadas'] = datos_extraidos

        # Verificación: detectar ID NEW sin servicio registrado
        ids_no_encontrados = set()
        for fila in datos_extraidos:
            id_new = fila.get('id_new')
            if not id_new:
                ids_no_encontrados.add("SIN_ID")
                continue
            if not ServicioCotizado.objects.filter(id_new=id_new).exists():
                ids_no_encontrados.add(id_new)

        return render(request, 'facturacion/preview_oc.html', {
            'datos': datos_extraidos,
            'nombre_archivo': nombre_archivo,
            'ids_no_encontrados': ids_no_encontrados,
        })

    # GET
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
    """
    Edita una orden de compra.
    Si no existe, la crea para el Servicio (DU) dado por pk.
    """
    # Buscar si es pk de OrdenCompra o de Servicio
    try:
        orden = OrdenCompraFacturacion.objects.get(pk=pk)
    except OrdenCompraFacturacion.DoesNotExist:
        servicio = get_object_or_404(ServicioCotizado, pk=pk)
        orden = OrdenCompraFacturacion.objects.create(du=servicio)

    if request.method == 'POST':
        form = OrdenCompraFacturacionForm(request.POST, instance=orden)
        if form.is_valid():
            form.save()
            messages.success(
                request, "Orden de compra guardada correctamente.")
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


@login_required
@rol_requerido('facturacion', 'admin')
def listar_facturas(request):
    # Traer solo facturas existentes, no órdenes vacías
    facturas = FacturaOC.objects.select_related("orden_compra__du")

    # Filtros dinámicos
    du = request.GET.get("du", "")
    id_claro = request.GET.get("id_claro", "")
    id_new = request.GET.get("id_new", "")
    mes_produccion = request.GET.get("mes_produccion", "")
    estado = request.GET.get("estado", "")

    if du:
        facturas = facturas.filter(orden_compra__du__du__icontains=du)
    if id_claro:
        facturas = facturas.filter(
            orden_compra__du__id_claro__icontains=id_claro)
    if id_new:
        facturas = facturas.filter(orden_compra__du__id_new__icontains=id_new)
    if mes_produccion:
        facturas = facturas.filter(
            orden_compra__du__mes_produccion__icontains=mes_produccion)
    if estado:
        facturas = facturas.filter(orden_compra__du__estado=estado)

    # Paginación
    paginator = Paginator(facturas, 10)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, "facturacion/listar_facturas.html", {
        "pagina": pagina,
        "filtros": {
            "du": du,
            "id_claro": id_claro,
            "id_new": id_new,
            "mes_produccion": mes_produccion,
            "estado": estado,
        },
        "estado_choices": ServicioCotizado.ESTADOS,
        "cantidad": request.GET.get("cantidad", "10")
    })


@login_required
@rol_requerido('facturacion', 'admin')
def enviar_a_facturacion(request):
    if request.method == "POST":
        ids = request.POST.getlist('seleccionados')
        if not ids:
            messages.error(request, "Debes seleccionar al menos una orden.")
            return redirect('facturacion:listar_oc_facturacion')

        enviados, omitidos = [], []

        for oc_id in ids:
            oc = OrdenCompraFacturacion.objects.filter(id=oc_id).first()
            if not oc:
                continue

            # Validar que tenga los campos requeridos
            if not all([oc.orden_compra, oc.pos, oc.cantidad, oc.unidad_medida,
                        oc.material_servicio, oc.descripcion_sitio,
                        oc.fecha_entrega, oc.precio_unitario, oc.monto]):
                omitidos.append(f"DU {oc.du.du} - POS {oc.pos}")
                continue

            # Evitar duplicados
            factura_existente = FacturaOC.objects.filter(
                orden_compra=oc).first()
            if factura_existente:
                omitidos.append(
                    f"DU {oc.du.du} - POS {oc.pos} (ya en facturación)")
                continue

            # Crear registro de facturación
            FacturaOC.objects.create(
                orden_compra=oc,
                mes_produccion=oc.du.mes_produccion
            )
            enviados.append(oc_id)

        # Mensajes flash
        if enviados:
            messages.success(
                request, f"{len(enviados)} órdenes fueron movidas a facturación correctamente.")
        if omitidos:
            messages.warning(
                request, "Las siguientes órdenes no fueron movidas:<br>" + "<br>".join(omitidos))

        return redirect('facturacion:listar_facturas')

    return redirect('facturacion:listar_oc_facturacion')


def limpiar_fecha(valor):
    """
    Intenta convertir múltiples formatos de fecha a YYYY-MM-DD.
    Acepta: 01-08-2025, 2025-08-01, '8 de Julio del 2025', etc.
    """
    if not valor:
        return None
    try:
        if isinstance(valor, datetime):
            return valor.date()
        fecha = parser.parse(str(valor), dayfirst=True, fuzzy=True)
        return fecha.date()
    except Exception:
        return None


@login_required
@rol_requerido('facturacion', 'admin')
def importar_facturas(request):
    datos = []
    if request.method == "POST":
        form = ImportarFacturasForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = request.FILES['archivo']
            wb = openpyxl.load_workbook(archivo)
            ws = wb.active

            for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                id_claro, oc, hes, valor_en_clp, conformidad, num_factura, fecha_facturacion = row[
                    :7]

                # Limpiar y normalizar la fecha
                fecha_limpia = limpiar_fecha(fecha_facturacion)

                datos.append({
                    "fila": i,
                    "id_claro": str(id_claro).strip() if id_claro else None,
                    "oc": str(oc).strip() if oc else None,
                    "hes": hes,
                    "valor_en_clp": valor_en_clp,
                    "conformidad": conformidad,
                    "num_factura": num_factura,
                    "fecha_facturacion": fecha_limpia.strftime("%Y-%m-%d") if fecha_limpia else None,
                })

            request.session["facturas_previsualizadas"] = datos
            messages.info(
                request, "Previsualización cargada. Revisa los datos antes de guardar."
            )
            return render(request, "facturacion/importar_facturas.html", {"form": form, "datos": datos})
    else:
        form = ImportarFacturasForm()
    return render(request, "facturacion/importar_facturas.html", {"form": form})


def limpiar_monto(valor):
    """
    Convierte un valor con formato chileno (con $ y puntos) a Decimal.
    Ej: "1.041.063" -> 1041063
    """
    from decimal import Decimal, InvalidOperation
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    try:
        limpio = str(valor).strip()
        limpio = re.sub(r"[^\d,.-]", "", limpio)  # Quitar símbolos
        limpio = limpio.replace(".", "")          # Eliminar puntos (miles)
        limpio = limpio.replace(",", ".")         # Reemplazar coma por punto
        return Decimal(limpio)
    except (InvalidOperation, ValueError):
        return None


def limpiar_fecha(valor):
    """
    Intenta convertir múltiples formatos de fecha a YYYY-MM-DD.
    Acepta: 01-08-2025, 2025-08-01, '8 de Julio del 2025', etc.
    """
    if not valor:
        return None
    try:
        # Si ya es datetime, convertimos directo
        if isinstance(valor, datetime):
            return valor.date()
        # Usamos dateutil.parser para interpretar múltiples formatos
        fecha = parser.parse(str(valor), dayfirst=True, fuzzy=True)
        return fecha.date()
    except Exception:
        return None


@login_required
@rol_requerido('facturacion', 'admin')
def guardar_facturas(request):
    datos = request.session.get("facturas_previsualizadas")
    if not datos:
        messages.error(request, "No hay datos para guardar.")
        return redirect("facturacion:importar_facturas")

    actualizados, omitidos = 0, []
    facturas_actualizadas_en_sesion = set()

    for fila in datos:
        id_claro = fila.get("id_claro")
        oc = fila.get("oc")

        # Validar que existan id_claro y oc
        if not id_claro or not oc:
            faltantes = []
            if not id_claro:
                faltantes.append("Sin ID CLARO")
            if not oc:
                faltantes.append("Sin OC")
            omitidos.append(
                f"Fila {fila.get('fila')}: {', '.join(faltantes)}.")
            continue

        # Limpiar y convertir el valor a Decimal
        valor = limpiar_monto(fila.get("valor_en_clp"))
        if valor is None:
            omitidos.append(f"Fila {fila.get('fila')}: Valor en CLP inválido.")
            continue

        # Convertir fecha
        fecha = None
        if fila.get("fecha_facturacion"):
            try:
                if isinstance(fila["fecha_facturacion"], str):
                    fecha = datetime.strptime(
                        fila["fecha_facturacion"], "%Y-%m-%d").date()
                elif isinstance(fila["fecha_facturacion"], datetime):
                    fecha = fila["fecha_facturacion"].date()
            except ValueError:
                omitidos.append(f"Fila {fila.get('fila')}: Fecha inválida.")
                continue

        # Validar obligatorios
        if not all([fila.get("hes"), valor, fila.get("conformidad")]):
            omitidos.append(
                f"Fila {fila.get('fila')}: Faltan datos obligatorios.")
            continue

        # Buscar todas las facturas que coincidan con ID_CLARO + OC
        facturas = FacturaOC.objects.filter(
            orden_compra__orden_compra=oc,
            orden_compra__du__id_claro=id_claro
        ).order_by('id')  # más antiguas primero

        if not facturas.exists():
            omitidos.append(
                f"Fila {fila.get('fila')}: No existe Factura para ID_CLARO {id_claro} y OC {oc}."
            )
            continue

        # Buscar la primera factura sin conformidad que no haya sido usada en esta sesión
        factura = None
        for f in facturas:
            if not f.conformidad and f.id not in facturas_actualizadas_en_sesion:
                factura = f
                break

        if not factura:
            # Todas tienen conformidad → no se puede actualizar
            omitidos.append(
                f"Fila {fila.get('fila')}: Todas las facturas para ID_CLARO {id_claro} y OC {oc} ya tienen conformidad."
            )
            continue

        # Actualizar la factura seleccionada
        factura.hes = fila.get("hes")
        factura.valor_en_clp = valor
        factura.conformidad = fila.get("conformidad")
        factura.num_factura = fila.get("num_factura")
        factura.fecha_facturacion = fecha
        factura.save()

        # Marcar como usada en esta sesión
        facturas_actualizadas_en_sesion.add(factura.id)
        actualizados += 1

    # Limpiar sesión
    request.session.pop("facturas_previsualizadas", None)

    # Mensajes
    if actualizados:
        messages.success(
            request, f"{actualizados} facturas actualizadas correctamente.")
    if omitidos:
        messages.warning(request, "Omitidas:<br>" + "<br>".join(omitidos))
    return redirect("facturacion:listar_facturas")


@login_required
@rol_requerido('facturacion', 'admin')
def editar_factura(request, pk):
    factura = get_object_or_404(FacturaOC, pk=pk)
    if request.method == "POST":
        form = FacturaOCForm(request.POST, instance=factura)
        if form.is_valid():
            form.save()
            messages.success(request, "Factura actualizada correctamente.")
            return redirect('facturacion:listar_facturas')
    else:
        form = FacturaOCForm(instance=factura)
    return render(request, "facturacion/editar_factura.html", {"form": form})


@login_required
@rol_requerido('admin')
def eliminar_factura(request, pk):
    factura = get_object_or_404(FacturaOC, pk=pk)
    if request.method == "POST":
        factura.delete()
        messages.success(request, "Factura eliminada correctamente.")
        return redirect('facturacion:listar_facturas')
    return render(request, "facturacion/eliminar_factura.html", {"factura": factura})


@csrf_exempt
def actualizar_factura_ajax(request, pk):
    if request.method == "POST":
        factura = get_object_or_404(FacturaOC, pk=pk)
        campo = request.POST.get("campo")
        valor = request.POST.get("valor")

        # Conversión según tipo de campo
        if campo in ["valor_en_clp"]:
            try:
                valor = float(valor.replace(",", "").replace("$", "").strip())
            except:
                return JsonResponse({"success": False, "error": "Valor inválido"})
        if campo in ["factorizado"]:
            valor = valor.lower() in ["1", "true", "sí", "si"]

        # Guardar valor
        setattr(factura, campo, valor if valor != "" else None)
        factura.save()

        # Recalcular el estado dinámicamente
        nuevo_status = factura.get_status_factura()

        return JsonResponse({
            "success": True,
            "valor": valor,
            "nuevo_status": nuevo_status  # <-- Devolvemos el nuevo estado
        })
    return JsonResponse({"success": False, "error": "Método no permitido"})


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_facturacion_excel(request):
    import openpyxl
    from openpyxl.styles import Alignment, Font
    from django.http import HttpResponse

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lista de Facturación"

    columnas = [
        "DU", "ID CLARO", "ID NEW", "DETALLE TAREA", "ASIGNADOS",
        "M. COTIZADO (UF)", "M. MMOO (CLP)", "FECHA FIN", "STATUS SERVICIO",
        "OC", "POS", "CANT", "UM", "MATERIAL", "DESCRIPCIÓN SITIO", "FECHA ENTREGA",
        "P. UNITARIO", "MONTO", "HES", "VALOR EN CLP", "CONFORMIDAD",
        "N° FACTURA", "FECHA FACTURACIÓN", "MES DE PRODUCCIÓN",
        "FACTORIZADO", "FECHA FACTORING", "STATUS FACTURA"
    ]
    for col_num, column_title in enumerate(columnas, 1):
        cell = ws.cell(row=1, column=col_num, value=column_title)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    facturas = (
        FacturaOC.objects
        .select_related(
            'orden_compra', 'orden_compra__du',
            'orden_compra__du__pm_aprueba',
            'orden_compra__du__tecnico_aceptado',
            'orden_compra__du__tecnico_finalizo',
            'orden_compra__du__supervisor_aprobo',
            'orden_compra__du__supervisor_rechazo',
            'orden_compra__du__supervisor_asigna',
            'orden_compra__du__usuario_informe',
        )
        .prefetch_related('orden_compra__du__trabajadores_asignados')
    )

    for row_num, factura in enumerate(facturas, start=2):
        oc = factura.orden_compra
        du = oc.du if oc else None

        # Servicio
        ws.cell(row=row_num, column=1, value=du.du if du else "")
        ws.cell(row=row_num, column=2, value=du.id_claro if du else "")
        ws.cell(row=row_num, column=3, value=du.id_new if du else "")
        ws.cell(row=row_num, column=4, value=du.detalle_tarea if du else "")
        ws.cell(row=row_num, column=5, value=", ".join(
            [u.get_full_name() for u in du.trabajadores_asignados.all()]) if du else "")
        ws.cell(row=row_num, column=6, value=float(
            du.monto_cotizado) if du and du.monto_cotizado else 0)
        ws.cell(row=row_num, column=7, value=float(
            du.monto_mmoo) if du and du.monto_mmoo else 0)
        ws.cell(row=row_num, column=8, value=du.fecha_aprobacion_supervisor.strftime(
            "%d-%m-%Y") if du and du.fecha_aprobacion_supervisor else "")
        ws.cell(row=row_num, column=9,
                value=du.get_estado_display() if du else "")

        # Orden de compra
        ws.cell(row=row_num, column=10, value=oc.orden_compra if oc else "")
        ws.cell(row=row_num, column=11, value=oc.pos if oc else "")
        ws.cell(row=row_num, column=12, value=float(
            oc.cantidad) if oc and oc.cantidad else 0)
        ws.cell(row=row_num, column=13, value=oc.unidad_medida if oc else "")
        ws.cell(row=row_num, column=14,
                value=oc.material_servicio if oc else "")
        ws.cell(row=row_num, column=15,
                value=oc.descripcion_sitio if oc else "")
        ws.cell(row=row_num, column=16, value=oc.fecha_entrega.strftime(
            "%d-%m-%Y") if oc and oc.fecha_entrega else "")
        ws.cell(row=row_num, column=17, value=float(
            oc.precio_unitario) if oc and oc.precio_unitario else 0)
        ws.cell(row=row_num, column=18, value=float(
            oc.monto) if oc and oc.monto else 0)

        # Factura
        ws.cell(row=row_num, column=19, value=factura.hes or "")
        ws.cell(row=row_num, column=20, value=float(
            factura.valor_en_clp) if factura.valor_en_clp else 0)
        ws.cell(row=row_num, column=21, value=factura.conformidad or "")
        ws.cell(row=row_num, column=22, value=factura.num_factura or "")
        ws.cell(row=row_num, column=23, value=factura.fecha_facturacion.strftime(
            "%d-%m-%Y") if factura.fecha_facturacion else "")
        ws.cell(row=row_num, column=24, value=factura.mes_produccion or "")
        ws.cell(row=row_num, column=25,
                value="Sí" if factura.factorizado else "No")
        ws.cell(row=row_num, column=26, value=factura.fecha_factoring.strftime(
            "%d-%m-%Y") if factura.fecha_factoring else "")
        ws.cell(row=row_num, column=27, value=factura.get_status_factura())

    # Ajustar ancho
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[column].width = max_length + 2

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response['Content-Disposition'] = 'attachment; filename=Lista_Facturacion.xlsx'
    wb.save(response)
    return response


@login_required
@rol_requerido('facturacion', 'admin')
def listar_cartola(request):
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)

    # Capturar filtros
    du = request.GET.get('du', '').strip()
    fecha = request.GET.get('fecha', '').strip()
    proyecto = request.GET.get('proyecto', '').strip()
    categoria = request.GET.get('categoria', '').strip()
    tipo = request.GET.get('tipo', '').strip()
    rut_factura = request.GET.get('rut_factura', '').strip()
    estado = request.GET.get('estado', '').strip()

    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    # Filtrar por usuario (busca en rut, nombre y apellido)
    if du:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

    # Filtrar por fecha con validación segura (dd-mm-yyyy → yyyy-mm-dd)
    if fecha:
        try:
            fecha_valida = datetime.strptime(fecha, "%d-%m-%Y").date()
            movimientos = movimientos.filter(fecha__date=fecha_valida)
        except ValueError:
            messages.warning(
                request, "Formato de fecha inválido. Use DD-MM-YYYY.")

    if proyecto:
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if rut_factura:
        movimientos = movimientos.filter(rut_factura__icontains=rut_factura)
    if estado:
        movimientos = movimientos.filter(status=estado)

    # Paginación
    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    estado_choices = CartolaMovimiento.ESTADOS
    filtros = {
        'du': du,
        'fecha': fecha,
        'proyecto': proyecto,
        'categoria': categoria,
        'tipo': tipo,
        'rut_factura': rut_factura,
        'estado': estado,

    }

    return render(request, 'facturacion/listar_cartola.html', {
        'pagina': pagina,
        'cantidad': request.GET.get('cantidad', '10'),
        'estado_choices': estado_choices,
        'filtros': filtros
    })


@login_required
@rol_requerido('facturacion', 'admin')
def registrar_abono(request):
    if request.method == 'POST':
        form = CartolaAbonoForm(request.POST, request.FILES)
        if form.is_valid():
            movimiento = form.save(commit=False)
            # Si es abono, setear automáticamente valores
            from .models import TipoGasto
            tipo_abono = TipoGasto.objects.filter(categoria='abono').first()
            movimiento.tipo = tipo_abono
            movimiento.cargos = 0
            movimiento.save()
            messages.success(request, "Movimiento registrado correctamente.")
            # <-- Redirección después de guardar
            return redirect('facturacion:listar_cartola')
        else:
            messages.error(
                request, "Por favor corrige los errores antes de continuar.")
    else:
        form = CartolaAbonoForm()
    return render(request, 'facturacion/registrar_abono.html', {'form': form})


@login_required
@rol_requerido('facturacion', 'admin')
@login_required
def crear_tipo(request):
    if request.method == 'POST':
        form = TipoGastoForm(request.POST)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(request, "Tipo de gasto creado correctamente.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm()
    tipos = TipoGasto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos})


@login_required
@rol_requerido('admin')
def editar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    if request.method == 'POST':
        form = TipoGastoForm(request.POST, instance=tipo)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(
                request, "Tipo de gasto actualizado correctamente.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm(instance=tipo)
    tipos = TipoGasto.objects.all().order_by('-id')
    # Usamos el mismo template que crear
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos, 'editando': True})


@login_required
@rol_requerido('admin')
def eliminar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    tipo.delete()
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        tipos = TipoGasto.objects.all().order_by('-id')
        html = render_to_string(
            'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
        return JsonResponse({'success': True, 'html': html})
    messages.success(request, "Tipo de gasto eliminado correctamente.")
    return redirect('facturacion:crear_tipo')


# Listar y crear
@login_required
@rol_requerido('facturacion', 'admin')
def crear_proyecto(request):
    if request.method == 'POST':
        form = ProyectoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Proyecto creado correctamente.")
            return redirect('facturacion:crear_proyecto')
    else:
        form = ProyectoForm()
    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {'form': form, 'proyectos': proyectos})

# Editar


@login_required
@rol_requerido('admin')
def editar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        form = ProyectoForm(request.POST, instance=proyecto)
        if form.is_valid():
            form.save()
            messages.success(request, "Proyecto actualizado correctamente.")
            return redirect('facturacion:crear_proyecto')
    else:
        form = ProyectoForm(instance=proyecto)
    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {'form': form, 'proyectos': proyectos})

# Eliminar


@login_required
@rol_requerido('admin')
def eliminar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        proyecto.delete()
        messages.success(request, "Proyecto eliminado correctamente.")
        return redirect('facturacion:crear_proyecto')
    return redirect('facturacion:crear_proyecto')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
def aprobar_movimiento(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if mov.tipo and mov.tipo.categoria != "abono":
        # Asignar aprobador según el rol
        if request.user.es_supervisor and mov.status == 'pendiente_supervisor':
            mov.status = 'aprobado_supervisor'
            mov.aprobado_por_supervisor = request.user
        elif request.user.es_pm and mov.status == 'aprobado_supervisor':
            mov.status = 'aprobado_pm'
            mov.aprobado_por_pm = request.user
        elif request.user.es_facturacion and mov.status == 'aprobado_pm':
            mov.status = 'aprobado_finanzas'
            # <<< Aquí asignamos el usuario de finanzas
            mov.aprobado_por_finanzas = request.user

        mov.motivo_rechazo = ''  # Limpiar cualquier rechazo previo
        mov.save()
        messages.success(request, "Gasto aprobado correctamente.")
    return redirect('facturacion:listar_cartola')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
def rechazar_movimiento(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo', '').strip()
        if mov.tipo and mov.tipo.categoria != "abono":
            if request.user.es_supervisor and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
            elif request.user.es_pm and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
            elif request.user.es_facturacion and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                # <<< Aquí asignamos el usuario de finanzas
                mov.aprobado_por_finanzas = request.user

            mov.motivo_rechazo = motivo
            mov.save()
            messages.success(request, "Gasto rechazado correctamente.")
    return redirect('facturacion:listar_cartola')


@login_required
@rol_requerido('facturacion', 'admin')
def editar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)

    # Determinar qué formulario usar según la categoría
    if movimiento.tipo and movimiento.tipo.categoria == "abono":
        FormClass = CartolaAbonoForm
        estado_restaurado = 'pendiente_abono_usuario'
    else:
        FormClass = MovimientoUsuarioForm
        estado_restaurado = 'pendiente_supervisor'

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES, instance=movimiento)
        if form.is_valid():
            campos_editados = form.changed_data
            if campos_editados:
                # Restablecer el estado solo si se editó algo
                movimiento.status = estado_restaurado
                # Limpiar el motivo de rechazo si lo tenía
                movimiento.motivo_rechazo = ""
            form.save()
            messages.success(request, "Movimiento actualizado correctamente.")
            return redirect('facturacion:listar_cartola')
    else:
        form = FormClass(instance=movimiento)

    return render(request, 'facturacion/editar_movimiento.html', {'form': form, 'movimiento': movimiento})


@login_required
@rol_requerido('admin')
def eliminar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        movimiento.delete()
        messages.success(request, "Movimiento eliminado correctamente.")
        return redirect('facturacion:listar_cartola')
    return render(request, 'facturacion/eliminar_movimiento.html', {'movimiento': movimiento})


@login_required
@rol_requerido('facturacion', 'admin')
def listar_saldos_usuarios(request):
    cantidad = request.GET.get('cantidad', '5')

    # Agrupar por usuario y calcular rendido y disponible
    saldos = (CartolaMovimiento.objects
              .values('usuario__id', 'usuario__first_name', 'usuario__last_name', 'usuario__email')
              .annotate(
                  monto_rendido=Sum('cargos'),
                  monto_asignado=Sum('abonos'),
              )
              .order_by('usuario__first_name'))

    # Calcular monto disponible
    for s in saldos:
        s['monto_disponible'] = (
            s['monto_asignado'] or 0) - (s['monto_rendido'] or 0)

    # Paginación como facturación
    if cantidad == 'todos':
        paginator = Paginator(saldos, saldos.count() or 1)  # Todo en 1 página
    else:
        paginator = Paginator(saldos, int(cantidad))

    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, 'facturacion/listar_saldos_usuarios.html', {
        'saldos': pagina,
        'pagina': pagina,
        'cantidad': cantidad,
    })


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_cartola_finanzas(request):
    # Puedes aplicar los mismos filtros que usas en la vista original
    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="cartola_finanzas.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Cartola Finanzas')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # Columnas requeridas
    columns = [
        "Usuario", "Fecha", "Proyecto", "Categoría", "Tipo", "RUT Factura",
        "Tipo de Documento", "Número de Documento", "Observaciones",
        "N° Transferencia", "Cargos", "Abonos", "Status"
    ]
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
        ws.write(row_num, 3, mov.tipo.categoria if mov.tipo else "")
        ws.write(row_num, 4, str(mov.tipo))
        ws.write(row_num, 5, mov.rut_factura or "")
        ws.write(row_num, 6, mov.tipo_doc or "")
        ws.write(row_num, 7, mov.numero_doc or "")
        ws.write(row_num, 8, mov.observaciones or "")
        ws.write(row_num, 9, mov.numero_transferencia or "")
        ws.write(row_num, 10, float(mov.cargos or 0))
        ws.write(row_num, 11, float(mov.abonos or 0))
        ws.write(row_num, 12, mov.get_status_display())

    wb.save(response)
    return response


@login_required
@rol_requerido('admin', 'facturacion')  # Solo Admin y Finanzas
def exportar_saldos_disponibles(request):
    # Obtener los mismos datos que usa la vista principal
    from django.db.models import Sum, F, Value
    from facturacion.models import CartolaMovimiento

    saldos = (CartolaMovimiento.objects
              .values('usuario__first_name', 'usuario__last_name')
              .annotate(
                  monto_rendido=Sum('cargos', default=0),
                  monto_disponible=Sum(F('abonos') - F('cargos'), default=0)
              ).order_by('usuario__first_name'))

    # Crear respuesta como Excel
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="saldos_disponibles.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Saldos Disponibles')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    currency_style = xlwt.easyxf(num_format_str='#,##0')

    # Cabeceras
    columns = ["Usuario", "Monto Rendido", "Monto Disponible"]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Filas
    for row_num, s in enumerate(saldos, start=1):
        usuario = f"{s['usuario__first_name']} {s['usuario__last_name']}"
        ws.write(row_num, 0, usuario)
        ws.write(row_num, 1, float(s['monto_rendido'] or 0), currency_style)
        ws.write(row_num, 2, float(s['monto_disponible'] or 0), currency_style)

    wb.save(response)
    return response

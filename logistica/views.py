from logistica.models import Material
from logistica.forms import FiltroIngresoForm
from logistica.models import IngresoMaterial
from .forms import FiltroIngresoForm
from django.shortcuts import render
from django.shortcuts import render, get_object_or_404, redirect
from .forms import MaterialForm
from .models import Material
from .forms import MaterialForm, ImportarExcelForm
from .forms import FiltroIngresoForm  # lo crearemos abajo
import pandas as pd
from django.http import HttpResponse
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils.timezone import now
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import IngresoMaterialForm
from .models import IngresoMaterial
from usuarios.decoradores import rol_requerido
import openpyxl
from django.forms import inlineformset_factory
import unicodedata
from .models import DetalleIngresoMaterial
from django.forms import modelformset_factory
from django.db import transaction
# Modelo que relaciona IngresoMaterial con Material y cantidad
from .models import DetalleIngresoMaterial
from .forms import MaterialIngresoForm
from .models import Bodega
from .forms import BodegaForm


MaterialIngresoFormSet = modelformset_factory(
    DetalleIngresoMaterial,
    form=MaterialIngresoForm,
    extra=1,
    can_delete=True
)


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def registrar_ingreso_material(request):
    if request.method == 'POST':
        form = IngresoMaterialForm(request.POST, request.FILES)
        formset = MaterialIngresoFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            numero_documento = form.cleaned_data.get('numero_documento')
            tipo_documento = form.cleaned_data.get('tipo_documento')

            # Validación de número de documento duplicado
            if IngresoMaterial.objects.filter(numero_documento=numero_documento, tipo_documento=tipo_documento).exists():
                messages.error(
                    request, f'Ya existe un ingreso con el número de documento "{numero_documento}" para ese tipo.')
            else:
                # Validación de materiales duplicados
                materiales_usados = set()
                materiales_repetidos = False

                for material_form in formset:
                    if material_form.cleaned_data and not material_form.cleaned_data.get('DELETE', False):
                        material = material_form.cleaned_data['material']
                        if material in materiales_usados:
                            materiales_repetidos = True
                            break
                        materiales_usados.add(material)

                if materiales_repetidos:
                    messages.error(
                        request, 'No puedes registrar el mismo material más de una vez.')
                else:
                    try:
                        with transaction.atomic():
                            ingreso = form.save(commit=False)
                            ingreso.registrado_por = request.user
                            ingreso.save()

                            for material_form in formset:
                                if material_form.cleaned_data and not material_form.cleaned_data.get('DELETE', False):
                                    detalle = material_form.save(commit=False)
                                    detalle.ingreso = ingreso
                                    detalle.save()

                            messages.success(
                                request, 'Ingreso registrado correctamente.')
                            return redirect('logistica:listar_ingresos')
                    except Exception as e:
                        messages.error(request, f'Error al guardar: {str(e)}')
        else:
            messages.error(request, 'Por favor corrige los errores.')
    else:
        form = IngresoMaterialForm()
        formset = MaterialIngresoFormSet(
            queryset=DetalleIngresoMaterial.objects.none())

    return render(request, 'logistica/registrar_ingreso_material.html', {
        'form': form,
        'formset': formset,
    })


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def listar_ingresos_material(request):
    mes = request.GET.get('mes')
    anio = request.GET.get('anio')

    try:
        anio = int(anio)
    except (TypeError, ValueError):
        anio = now().year

    ingresos = IngresoMaterial.objects.annotate(
        mes=ExtractMonth('fecha_ingreso'),
        anio=ExtractYear('fecha_ingreso')
    ).filter(anio=anio)

    if mes and mes != 'None':
        ingresos = ingresos.filter(mes=int(mes))

    # Exportar a Excel
    if 'exportar' in request.GET:
        filas = []
        for ingreso in ingresos:
            detalles = ingreso.detalles.all()
            for detalle in detalles:
                filas.append({
                    'Fecha': ingreso.fecha_ingreso.strftime('%d/%m/%Y'),
                    'Material': detalle.material.nombre,
                    'Cantidad': detalle.cantidad,
                    'Tipo Doc': ingreso.get_tipo_documento_display(),
                    'N° Documento': ingreso.numero_documento,
                    'Registrado por': ingreso.registrado_por.get_full_name() if ingreso.registrado_por else '-',
                })
        df = pd.DataFrame(filas)
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="ingresos_materiales.xlsx"'
        df.to_excel(response, index=False)
        return response

    form_filtro = FiltroIngresoForm(initial={'mes': mes, 'anio': anio})
    return render(request, 'logistica/listar_ingresos.html', {
        'ingresos': ingresos,
        'form_filtro': form_filtro,
        'mes_seleccionado': mes,
        'año_seleccionado': anio,
    })


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def crear_material(request):
    materiales = Material.objects.all().order_by('nombre')
    form_material = MaterialForm()
    form_excel = ImportarExcelForm()

    if request.method == 'POST':
        # Crear manual
        if 'crear_manual' in request.POST:
            form_material = MaterialForm(request.POST)
            if form_material.is_valid():
                # Verificamos si el código ya existe manualmente (extra por seguridad)
                codigo = form_material.cleaned_data['codigo_interno']
                if Material.objects.filter(codigo_interno=codigo).exists():
                    messages.error(
                        request, "Ya existe un material con ese código interno.")
                else:
                    form_material.save()
                    messages.success(request, "Material creado exitosamente.")
                    return redirect('logistica:crear_material')
            else:
                # Validar si el código ya existe para dar un mensaje más claro
                codigo = request.POST.get('codigo_interno')
                if Material.objects.filter(codigo_interno=codigo).exists():
                    messages.error(
                        request, "Ya existe un material con ese código interno.")
                else:
                    messages.error(
                        request, "Por favor revisa los campos del formulario.")

        # Importar desde Excel
        elif 'importar_excel' in request.POST and request.FILES.get('archivo_excel'):
            form_excel = ImportarExcelForm(request.POST, request.FILES)
            if form_excel.is_valid():
                df = pd.read_excel(request.FILES['archivo_excel'])

                # Validar columnas
                columnas_req = {'nombre', 'codigo_interno', 'unidad_medida',
                                'descripcion', 'stock_actual', 'stock_minimo'}
                columnas_archivo = set(df.columns.str.lower())

                if not columnas_req.issubset(columnas_archivo):
                    missing = columnas_req - columnas_archivo
                    messages.error(
                        request, f"Faltan columnas en el Excel: {', '.join(missing)}")
                else:
                    # Normalizar nombres a minúsculas
                    df.columns = df.columns.str.lower().str.strip()
                    for _, row in df.iterrows():
                        Material.objects.get_or_create(
                            nombre=row['nombre'],
                            defaults={
                                'codigo_interno': row['codigo_interno'],
                                'unidad_medida': row['unidad_medida'],
                                'descripcion': row.get('descripcion', ''),
                                'stock_actual': int(row.get('stock_actual', 0)),
                                'stock_minimo': int(row.get('stock_minimo', 0)),
                                'activo': True
                            }
                        )
                    messages.success(
                        request, "Materiales importados desde Excel.")
                    return redirect('logistica:crear_material')

    return render(request, 'logistica/crear_material.html', {
        'form_material': form_material,
        'form_excel': form_excel,
        'materiales': materiales
    })


@login_required
@rol_requerido('admin')
def editar_material(request, pk):
    material = get_object_or_404(Material, pk=pk)
    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material)
        if form.is_valid():
            form.save()
            messages.success(request, "Material actualizado correctamente.")
            return redirect('logistica:crear_material')
    else:
        form = MaterialForm(instance=material)
    return render(request, 'logistica/editar_material.html', {'form': form, 'material': material})


@login_required
@rol_requerido('admin')
def eliminar_material(request, pk):
    material = get_object_or_404(Material, pk=pk)
    if request.method == 'POST':
        material.delete()
        messages.success(request, "Material eliminado correctamente.")
        return redirect('logistica:crear_material')
    return render(request, 'logistica/eliminar_material.html', {'material': material})


@login_required
@rol_requerido('admin', 'logistica', 'pm')
def importar_materiales(request):
    def normalizar(texto):
        texto = str(texto).strip().lower()
        return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

    if request.method == 'POST':
        form = ImportarExcelForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = request.FILES['archivo_excel']
            try:
                wb = openpyxl.load_workbook(archivo)
                sheet = wb.active

                headers_originales = [str(cell.value).strip()
                                      for cell in sheet[1]]
                headers_normalizados = [normalizar(
                    cell) for cell in headers_originales]

                columnas_requeridas = {
                    'nombre', 'codigo interno', 'codigo externo', 'bodega',
                    'stock actual', 'stock minimo', 'unidad medida', 'descripcion'
                }

                if not columnas_requeridas.issubset(set(headers_normalizados)):
                    faltantes = columnas_requeridas - set(headers_normalizados)
                    messages.error(
                        request, f"Faltan columnas: {', '.join(faltantes)}")
                    return redirect('logistica:importar_materiales')

                header_map = dict(
                    zip(headers_normalizados, headers_originales))
                creados = 0
                no_encontradas = []

                for row in sheet.iter_rows(min_row=2, values_only=True):
                    if not any(row):
                        continue

                    data = dict(zip(headers_normalizados, row))

                    nombre = str(data.get('nombre', '')).strip()
                    codigo = str(data.get('codigo interno', '')).strip()
                    codigo_externo = str(
                        data.get('codigo externo', '')).strip()
                    nombre_bodega = str(data.get('bodega', '')).strip()
                    unidad_medida = str(data.get('unidad medida', '')).strip()
                    descripcion = str(data.get('descripcion', '')).strip()
                    stock_actual = data.get('stock actual') or 0
                    stock_minimo = data.get('stock minimo') or 0

                    if not nombre or not codigo or not nombre_bodega:
                        continue

                    if Material.objects.filter(nombre__iexact=nombre).exists() or \
                       Material.objects.filter(codigo_interno__iexact=codigo).exists():
                        continue

                    # Buscar o crear la bodega
                    bodega = Bodega.objects.filter(
                        nombre__iexact=nombre_bodega).first()
                    if not bodega:
                        no_encontradas.append(nombre_bodega)
                        continue

                    Material.objects.create(
                        nombre=nombre,
                        codigo_interno=codigo,
                        codigo_externo=codigo_externo,
                        unidad_medida=unidad_medida,
                        descripcion=descripcion,
                        stock_actual=stock_actual,
                        stock_minimo=stock_minimo,
                        bodega=bodega
                    )
                    creados += 1

                mensaje_extra = ""
                if no_encontradas:
                    mensaje_extra = f"<br>Bodegas no encontradas: {', '.join(set(no_encontradas))}"

                messages.success(
                    request, f"{creados} materiales importados correctamente.{mensaje_extra}")
                return redirect('logistica:crear_material')

            except Exception as e:
                messages.error(
                    request, f"Error al procesar el archivo: {str(e)}")
    else:
        form = ImportarExcelForm()

    return render(request, 'logistica/importar_materiales.html', {'form_excel': form})


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def exportar_materiales(request):
    materiales = Material.objects.select_related('bodega').values(
        'nombre',
        'codigo_interno',
        'codigo_externo',
        'bodega__nombre',
        'stock_actual',
        'stock_minimo',
        'unidad_medida',
        'descripcion'
    )

    df = pd.DataFrame(materiales)

    # Renombrar columnas para mostrar en el Excel
    df.rename(columns={
        'nombre': 'Nombre',
        'codigo_interno': 'Código Interno',
        'codigo_externo': 'Código Externo',
        'bodega__nombre': 'Bodega',
        'stock_actual': 'Stock Actual',
        'stock_minimo': 'Stock Mínimo',
        'unidad_medida': 'Unidad Medida',
        'descripcion': 'Descripción'
    }, inplace=True)

    # Ordenar columnas según el formato estándar
    columnas_ordenadas = [
        'Nombre',
        'Código Interno',
        'Código Externo',
        'Bodega',
        'Stock Actual',
        'Stock Mínimo',
        'Unidad Medida',
        'Descripción'
    ]
    df = df[columnas_ordenadas]

    # Generar archivo Excel
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=stock_materiales.xlsx'
    df.to_excel(response, index=False)
    return response


@login_required
@rol_requerido('admin')
def editar_ingreso_material(request, pk):
    ingreso = get_object_or_404(IngresoMaterial, pk=pk)

    DetalleFormSet = inlineformset_factory(
        IngresoMaterial,
        DetalleIngresoMaterial,
        form=MaterialIngresoForm,
        extra=0,
        can_delete=True
    )

    archivo_anterior = ingreso.archivo_documento.name if ingreso.archivo_documento else None

    if request.method == 'POST':
        form = IngresoMaterialForm(
            request.POST, request.FILES, instance=ingreso)
        formset = DetalleFormSet(
            request.POST, request.FILES, instance=ingreso, prefix='detalles')

        if form.is_valid() and formset.is_valid():
            numero_documento = form.cleaned_data.get('numero_documento')
            tipo_documento = form.cleaned_data.get('tipo_documento')

            # Verifica si hay otro ingreso con mismo número y tipo
            existe_duplicado = IngresoMaterial.objects.exclude(pk=ingreso.pk).filter(
                numero_documento=numero_documento,
                tipo_documento=tipo_documento
            ).exists()

            if existe_duplicado:
                messages.error(
                    request,
                    f'Ya existe otro ingreso con el número de documento "{numero_documento}" para ese tipo de documento.'
                )
            else:
                # Validación de materiales duplicados
                materiales_usados = set()
                materiales_repetidos = False

                for material_form in formset:
                    if material_form.cleaned_data and not material_form.cleaned_data.get('DELETE', False):
                        material = material_form.cleaned_data['material']
                        if material in materiales_usados:
                            materiales_repetidos = True
                            break
                        materiales_usados.add(material)

                if materiales_repetidos:
                    messages.error(
                        request, 'No puedes registrar el mismo material más de una vez.')
                else:
                    ingreso_actualizado = form.save()

                    # Reemplazar archivo si cambió
                    nuevo_archivo = request.FILES.get('archivo_documento')
                    if nuevo_archivo and archivo_anterior and archivo_anterior != ingreso_actualizado.archivo_documento.name:
                        from django.core.files.storage import default_storage
                        if default_storage.exists(archivo_anterior):
                            default_storage.delete(archivo_anterior)

                    formset.save()
                    messages.success(
                        request, "Ingreso actualizado correctamente.")
                    return redirect('logistica:listar_ingresos')
        else:
            messages.error(request, "Corrige los errores antes de continuar.")
    else:
        form = IngresoMaterialForm(instance=ingreso)
        formset = DetalleFormSet(instance=ingreso, prefix='detalles')

    formset_empty = DetalleFormSet(prefix='detalles').empty_form

    return render(request, 'logistica/editar_ingreso.html', {
        'form': form,
        'formset': formset,
        'formset_empty': formset_empty,
        'ingreso': ingreso,
    })


@login_required
@rol_requerido('admin')
def eliminar_ingreso_material(request, pk):
    ingreso = get_object_or_404(IngresoMaterial, pk=pk)
    ingreso.delete()
    messages.success(request, "Ingreso eliminado correctamente.")
    return redirect('logistica:listar_ingresos')


@login_required
@rol_requerido('logistica', 'admin')
def crear_bodega(request):
    bodegas = Bodega.objects.all().order_by('nombre')

    if request.method == 'POST':
        form = BodegaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bodega creada correctamente.')
            return redirect('logistica:crear_bodega')
    else:
        form = BodegaForm()

    return render(request, 'logistica/crear_bodega.html', {
        'form': form,
        'bodegas': bodegas
    })


@login_required
@rol_requerido('logistica', 'admin')
def editar_bodega(request, pk):
    bodega = get_object_or_404(Bodega, pk=pk)
    if request.method == 'POST':
        form = BodegaForm(request.POST, instance=bodega)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bodega actualizada correctamente.')
            return redirect('logistica:crear_bodega')
    else:
        form = BodegaForm(instance=bodega)

    return render(request, 'logistica/crear_bodega.html', {
        'form': form,
        'bodegas': Bodega.objects.all().order_by('nombre'),
        'editar_bodega': bodega
    })


@login_required
@rol_requerido('logistica', 'admin')
def eliminar_bodega(request, pk):
    bodega = get_object_or_404(Bodega, pk=pk)
    bodega.delete()
    messages.success(request, 'Bodega eliminada correctamente.')
    return redirect('logistica:crear_bodega')

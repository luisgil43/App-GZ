from django.http import JsonResponse
import locale
from django.utils.timezone import localtime
from utils.pdf_generator import generar_pdf_guia_despacho
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from logistica.models import ArchivoCAF, FolioDisponible
from django.utils.text import slugify
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models import Q
import os
from logistica.services.emitir_dte import generar_y_firmar_dte
from django.utils import timezone
from .models import CertificadoDigital
from .forms import ImportarCertificadoForm
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from .models import ArchivoCAF
from .forms import SalidaMaterialForm, DetalleSalidaFormSet
from .models import SalidaMaterial
from .forms import ImportarCAFForm
from .models import ArchivoCAF, FolioDisponible
import xml.etree.ElementTree as ET
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
                form_material.save()
                messages.success(request, "Material creado exitosamente.")
                return redirect('logistica:crear_material')
            # Si no es válido, los errores ya están en el formulario

        # Importar desde Excel
        elif 'importar_excel' in request.POST and request.FILES.get('archivo_excel'):
            form_excel = ImportarExcelForm(request.POST, request.FILES)
            if form_excel.is_valid():
                df = pd.read_excel(request.FILES['archivo_excel'])

                columnas_req = {'nombre', 'codigo_interno', 'codigo_externo', 'unidad_medida',
                                'descripcion', 'stock_actual', 'stock_minimo', 'valor unitario', 'bodega'}
                columnas_archivo = set(df.columns.str.lower().str.strip())

                if not columnas_req.issubset(columnas_archivo):
                    missing = columnas_req - columnas_archivo
                    messages.error(
                        request, f"Faltan columnas en el Excel: {', '.join(missing)}")
                else:
                    df.columns = df.columns.str.lower().str.strip()
                    errores = []
                    creados = 0
                    for _, row in df.iterrows():
                        nombre = str(row.get('nombre', '')).strip()
                        codigo_interno = str(
                            row.get('codigo_interno', '')).strip()
                        codigo_externo = str(
                            row.get('codigo_externo', '')).strip()
                        unidad = str(row.get('unidad_medida', '')).strip()
                        descripcion = str(row.get('descripcion', '')).strip()
                        stock_actual = int(row.get('stock_actual', 0) or 0)
                        stock_minimo = int(row.get('stock_minimo', 0) or 0)
                        valor_unitario = float(
                            row.get('valor unitario', 0) or 0)
                        bodega_nombre = str(row.get('bodega', '')).strip()

                        if not nombre or not codigo_interno or not bodega_nombre:
                            continue

                        bodega, _ = Bodega.objects.get_or_create(
                            nombre=bodega_nombre)

                        existe = Material.objects.filter(
                            bodega=bodega
                        ).filter(
                            models.Q(codigo_interno__iexact=codigo_interno) |
                            models.Q(codigo_externo__iexact=codigo_externo)
                        ).exists()

                        if existe:
                            errores.append(
                                f"Duplicado: {nombre} (CI: {codigo_interno}, CE: {codigo_externo}) en bodega '{bodega_nombre}'"
                            )
                            continue

                        Material.objects.create(
                            nombre=nombre,
                            codigo_interno=codigo_interno,
                            codigo_externo=codigo_externo,
                            unidad_medida=unidad,
                            descripcion=descripcion,
                            stock_actual=stock_actual,
                            stock_minimo=stock_minimo,
                            valor_unitario=valor_unitario,
                            bodega=bodega
                        )
                        creados += 1

                    if errores:
                        messages.warning(request, f"Se omitieron {len(errores)} materiales por duplicidad:<br>" +
                                         "<br>".join(errores))
                    messages.success(
                        request, f"{creados} materiales importados correctamente.")
                    return redirect('logistica:crear_material')

    return render(request, 'logistica/crear_material.html', {
        'form_material': form_material,
        'form_excel': form_excel,
        'materiales': materiales
    })


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def editar_material(request, pk):
    material = get_object_or_404(Material, pk=pk)

    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material)
        if form.is_valid():
            actualizado = form.save(commit=False)

            # Validar duplicados en la misma bodega (excluyendo este mismo material)
            existe_interno = Material.objects.filter(
                codigo_interno__iexact=actualizado.codigo_interno,
                bodega=actualizado.bodega
            ).exclude(pk=material.pk).exists()

            existe_externo = Material.objects.filter(
                codigo_externo__iexact=actualizado.codigo_externo,
                bodega=actualizado.bodega
            ).exclude(pk=material.pk).exists() if actualizado.codigo_externo else False

            if existe_interno or existe_externo:
                mensaje_error = "Ya existe un material con "
                if existe_interno and existe_externo:
                    mensaje_error += f"código interno '{actualizado.codigo_interno}' y externo '{actualizado.codigo_externo}' en la bodega '{actualizado.bodega}'."
                elif existe_interno:
                    mensaje_error += f"código interno '{actualizado.codigo_interno}' en la bodega '{actualizado.bodega}'."
                else:
                    mensaje_error += f"código externo '{actualizado.codigo_externo}' en la bodega '{actualizado.bodega}'."

                messages.error(request, mensaje_error)
            else:
                actualizado.save()
                messages.success(
                    request, "Material actualizado correctamente.")
                return redirect('logistica:crear_material')
        else:
            messages.error(
                request, "Por favor corrige los errores del formulario.")
    else:
        form = MaterialForm(instance=material)

    return render(request, 'logistica/editar_material.html', {
        'form': form,
        'material': material,
    })


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
        return ''.join(c for c in unicodedata.normalize('NFD', texto)
                       if unicodedata.category(c) != 'Mn')

    if request.method == 'POST':
        form = ImportarExcelForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = request.FILES['archivo_excel']
            try:
                wb = openpyxl.load_workbook(archivo)
                sheet = wb.active

                # 1️⃣ Leer cabeceras
                headers_originales = [str(c.value).strip() for c in sheet[1]]
                headers_normalizados = [normalizar(
                    h) for h in headers_originales]

                columnas_requeridas = {
                    'nombre', 'codigo interno', 'codigo externo', 'bodega',
                    'stock actual', 'stock minimo', 'unidad medida',
                    'valor unitario', 'descripcion'
                }
                if not columnas_requeridas.issubset(set(headers_normalizados)):
                    faltan = columnas_requeridas - set(headers_normalizados)
                    messages.error(request,
                                   f"Faltan columnas: {', '.join(faltan)}")
                    return redirect('logistica:importar_materiales')

                # 2️⃣ Mapear cabecera → nombre real
                header_map = dict(
                    zip(headers_normalizados, headers_originales))

                creados = 0
                bodegas_creadas = set()
                duplicados = []      # <─ aquí iremos guardando los duplicados

                # 3️⃣ Iterar filas
                for fila in sheet.iter_rows(min_row=2, values_only=True):
                    if not any(fila):
                        continue

                    data = dict(zip(headers_normalizados, fila))

                    nombre = str(data.get('nombre', '')).strip()
                    codigo = str(data.get('codigo interno', '')).strip()
                    codigo_ext = str(data.get('codigo externo', '')).strip()
                    bodega_nombre = str(data.get('bodega', '')).strip()
                    unidad_medida = str(data.get('unidad medida', '')).strip()
                    descripcion = str(data.get('descripcion', '')).strip()
                    stock_actual = data.get('stock actual') or 0
                    stock_minimo = data.get('stock minimo') or 0
                    valor_unitario = data.get('valor unitario') or 0

                    # --- Validaciones básicas ---
                    if not nombre or not codigo or not bodega_nombre:
                        continue
                    try:
                        valor_unitario = float(valor_unitario)
                        if valor_unitario < 0:
                            raise ValueError
                    except ValueError:
                        messages.error(
                            request,
                            f"Valor unitario inválido o negativo en material '{nombre}'."
                        )
                        return redirect('logistica:importar_materiales')

                    # --- Bodega ---
                    bodega, creada = Bodega.objects.get_or_create(
                        nombre__iexact=bodega_nombre,
                        defaults={'nombre': bodega_nombre}
                    )
                    if creada:
                        bodegas_creadas.add(bodega.nombre)

                    # --- Duplicados sólo dentro de la misma bodega ---
                    dup_qs = Material.objects.filter(bodega=bodega).filter(
                        Q(codigo_interno__iexact=codigo) |
                        Q(codigo_externo__iexact=codigo_ext) if codigo_ext else
                        Q(codigo_interno__iexact=codigo)
                    )

                    if dup_qs.exists():
                        # Guardamos referencia del duplicado para el mensaje
                        duplicados.append(
                            f"[Bodega: {bodega.nombre}] "
                            f"cód. interno '{codigo}'"
                            + (f", cód. externo '{codigo_ext}'" if codigo_ext else "")
                        )
                        continue  # salta a la siguiente fila

                    # --- Crear material ---
                    Material.objects.create(
                        nombre=nombre,
                        codigo_interno=codigo,
                        codigo_externo=codigo_ext,
                        unidad_medida=unidad_medida,
                        descripcion=descripcion,
                        stock_actual=stock_actual,
                        stock_minimo=stock_minimo,
                        valor_unitario=valor_unitario,
                        bodega=bodega
                    )
                    creados += 1

                # 4️⃣ Mensajes al usuario
                if creados:
                    extra = ""
                    if bodegas_creadas:
                        extra = f"<br>Se crearon bodegas: {', '.join(sorted(bodegas_creadas))}"
                    messages.success(
                        request,
                        f"{creados} materiales importados correctamente.{extra}"
                    )

                if duplicados:
                    # Usamos warning para que no bloquee la operación
                    mensajes_dup = "<br>".join(duplicados)
                    messages.warning(
                        request,
                        f"Se omitieron {len(duplicados)} filas por duplicados:<br>{mensajes_dup}"
                    )

                return redirect('logistica:crear_material')

            except Exception as e:
                messages.error(request, f"Error al procesar el archivo: {e}")
                return redirect('logistica:importar_materiales')
    else:
        form = ImportarExcelForm()

    bodegas = Bodega.objects.all()  # <--- Agrega esto
    return render(request, 'logistica/importar_materiales.html',
                  {'form_excel': form, 'bodegas': bodegas})


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
        'valor_unitario',
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
        'valor_unitario': 'Valor Unitario',  # Aquí también cambié el nombre a legible
        'descripcion': 'Descripción'  # ← Faltaba coma antes de esta línea
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
        'Valor Unitario',
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


@login_required
@rol_requerido('logistica', 'admin')
def importar_caf(request):
    if request.method == 'POST':
        form = ImportarCAFForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data['archivo_caf']
            try:
                # Analizar XML del archivo CAF
                tree = ET.parse(archivo)
                root = tree.getroot()

                # Extraer tipo de DTE
                tipo_dte_text = root.findtext('.//DA/TD')
                if tipo_dte_text is None:
                    messages.error(
                        request, "No se encontró el tipo de DTE (TD) en el archivo CAF.")
                    return redirect('logistica:listar_caf')
                tipo_dte = int(tipo_dte_text)

                # Extraer rango de folios
                desde_text = root.findtext('.//DA/RNG/D')
                hasta_text = root.findtext('.//DA/RNG/H')
                if desde_text is None or hasta_text is None:
                    messages.error(
                        request, "No se encontró el rango de folios (D-H) en el archivo CAF.")
                    return redirect('logistica:listar_caf')

                rango_inicio = int(desde_text)
                rango_fin = int(hasta_text)

                # Validar que no haya solapamiento de CAF activos para el mismo tipo
                conflicto = ArchivoCAF.objects.filter(
                    tipo_dte=tipo_dte,
                    estado='activo'
                ).filter(
                    Q(rango_inicio__lte=rango_fin, rango_fin__gte=rango_inicio)
                ).exists()

                if conflicto:
                    messages.error(
                        request,
                        f"Ya existe un archivo CAF activo para el tipo DTE {tipo_dte} con un rango que se cruza con {rango_inicio} - {rango_fin}."
                    )
                    return redirect('logistica:listar_caf')

                # 👉 Importante: reposicionar archivo si fue leído
                archivo.seek(0)

                # Guardar archivo directamente en Cloudinary
                archivo_caf = ArchivoCAF.objects.create(
                    tipo_dte=tipo_dte,
                    nombre_archivo=archivo.name,
                    archivo=archivo,  # ✅ esto sí usa Cloudinary por ser FileField
                    rango_inicio=rango_inicio,
                    rango_fin=rango_fin,
                    estado='activo',
                    usuario=request.user
                )

                # Crear los folios individuales asociados al CAF
                for folio in range(rango_inicio, rango_fin + 1):
                    FolioDisponible.objects.create(
                        caf=archivo_caf, folio=folio)

                messages.success(
                    request, f"CAF importado correctamente para DTE tipo {tipo_dte}.")
                return redirect('logistica:listar_caf')

            except ET.ParseError:
                messages.error(request, "El archivo no es un XML válido.")
            except Exception as e:
                messages.error(request, f"Error al importar CAF: {str(e)}")
    else:
        form = ImportarCAFForm()

    return render(request, 'logistica/importar_caf.html', {'form': form})


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def listar_salidas_material(request):
    mes = request.GET.get('mes')
    anio = request.GET.get('anio')

    try:
        anio = int(anio)
    except (TypeError, ValueError):
        anio = now().year

    salidas = SalidaMaterial.objects.annotate(
        mes=ExtractMonth('fecha_salida'),
        anio=ExtractYear('fecha_salida')
    ).filter(anio=anio)

    if mes and mes != 'None':
        salidas = salidas.filter(mes=int(mes))

    # Enriquecer cada salida con folio y estado de firma
    for salida in salidas:
        salida.folio_usado = None
        salida.firmada = False

        try:
            folio = FolioDisponible.objects.get(
                folio=int(salida.numero_documento), usado=True
            )
            salida.folio_usado = folio.folio
        except FolioDisponible.DoesNotExist:
            pass

        # Verificamos si el XML fue firmado en Cloudinary
        ruta_esperada = f"xml_firmados/{salida.numero_documento}.xml"
        if salida.archivo_pdf and 'cloudinary.com' in salida.archivo_pdf.url:
            salida.firmada = True

    form_filtro = FiltroIngresoForm(initial={'mes': mes, 'anio': anio})
    return render(request, 'logistica/listar_salidas.html', {
        'salidas': salidas,
        'form_filtro': form_filtro,
        'mes_seleccionado': mes,
        'año_seleccionado': anio,
    })

# logistica/views.py


@login_required
@rol_requerido('logistica', 'admin', 'pm')
def registrar_salida(request):
    if request.method == 'POST':
        form = SalidaMaterialForm(request.POST, request.FILES)
        formset = DetalleSalidaFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            salida = form.save(commit=False)

            # Obtener folio desde la tabla FolioDisponible según tipo_documento
            if salida.tipo_documento == 'guia':
                tipo_dte = 52
            elif salida.tipo_documento == 'factura':
                tipo_dte = 33
            else:
                tipo_dte = None

            if tipo_dte:
                folio = FolioDisponible.objects.filter(
                    usado=False,
                    caf__tipo_dte=tipo_dte,
                    caf__estado='activo'
                ).order_by('folio').first()

                if not folio:
                    messages.error(
                        request, "No hay folios disponibles para este tipo de documento.")
                    return redirect('logistica:registrar_salida')

                # Asignar folio a la salida
                salida.numero_documento = str(folio.folio)
                folio.usado = True
                folio.save()

            salida.emitido_por = request.user
            salida.save()

            detalles = formset.save(commit=False)
            for detalle in detalles:
                detalle.salida = salida
                detalle.save()

            messages.success(request, "Salida registrada correctamente.")
            return redirect('logistica:listar_salidas')
        else:
            messages.error(request, "Corrige los errores del formulario.")
    else:
        form = SalidaMaterialForm()
        formset = DetalleSalidaFormSet()

    # 🗓️ Formatear fecha de emisión en español
    try:
        # Usa 'es_ES.utf8' si es necesario
        locale.setlocale(locale.LC_TIME, 'es_CL.utf8')
    except locale.Error:
        # fallback si es_CL no existe
        locale.setlocale(locale.LC_TIME, 'es_ES.utf8')

    fecha_emision = localtime().strftime('%d de %B del %Y')

    return render(request, 'logistica/registrar_salida.html', {
        'form': form,
        'formset': formset,
        'fecha_emision': fecha_emision,
    })


def listar_caf(request):
    archivos = ArchivoCAF.objects.annotate(
        total_folios=Count('foliodisponible'),
        disponibles=Count('foliodisponible', filter=Q(
            foliodisponible__usado=False))
    )

    facturas_disponibles = archivos.filter(tipo_dte=33).aggregate(
        total=Count('foliodisponible', filter=Q(foliodisponible__usado=False))
    )['total'] or 0

    guias_disponibles = archivos.filter(tipo_dte=52).aggregate(
        total=Count('foliodisponible', filter=Q(foliodisponible__usado=False))
    )['total'] or 0

    notas_disponibles = archivos.filter(tipo_dte=61).aggregate(
        total=Count('foliodisponible', filter=Q(foliodisponible__usado=False))
    )['total'] or 0

    return render(request, 'logistica/listar_caf.html', {
        'archivos': archivos,
        'facturas_disponibles': facturas_disponibles,
        'guias_disponibles': guias_disponibles,
        'notas_disponibles': notas_disponibles
    })


@login_required
@rol_requerido('logistica', 'admin')
def eliminar_caf(request, pk):
    caf = get_object_or_404(ArchivoCAF, pk=pk)

    if request.method == 'POST':
        caf.delete()
        messages.success(
            request, "El archivo CAF fue eliminado correctamente.")
        return redirect('logistica:listar_caf')

    messages.error(request, "Método no permitido.")
    return redirect('logistica:listar_caf')


@login_required
@rol_requerido('logistica', 'admin')
def importar_certificado(request):
    if request.method == 'POST':
        form = ImportarCertificadoForm(request.POST, request.FILES)
        if form.is_valid():
            certificado = form.save(commit=False)
            certificado.usuario = request.user
            certificado.fecha_inicio = timezone.now()
            certificado.activo = True
            certificado.save()

            messages.success(
                request, "Certificado digital cargado correctamente.")
            return redirect('logistica:importar_certificado')
        else:
            messages.error(
                request, "Por favor revisa los campos del formulario.")
    else:
        form = ImportarCertificadoForm()

    certificados = CertificadoDigital.objects.all().order_by('-fecha_inicio')

    return render(request, 'logistica/importar_certificado.html', {
        'form': form,
        'certificados': certificados
    })


@login_required
@rol_requerido('logistica', 'admin')
def eliminar_certificado(request, pk):
    certificado = get_object_or_404(CertificadoDigital, pk=pk)
    if request.method == 'POST':
        certificado.delete()
        messages.success(request, "Certificado eliminado correctamente.")
    return redirect('logistica:importar_certificado')


@login_required
def eliminar_salida(request, pk):
    salida = get_object_or_404(SalidaMaterial, pk=pk)
    salida.delete()
    messages.success(request, "Guía de despacho eliminada correctamente.")
    return redirect('logistica:listar_salidas_material')


@login_required
@rol_requerido('logistica', 'admin')
def firmar_salida(request, pk):
    salida = get_object_or_404(SalidaMaterial, pk=pk)

    try:
        # Buscar CAF activo para DTE tipo 52 (Guía)
        caf = ArchivoCAF.objects.filter(tipo_dte=52, estado='activo').first()
        if not caf:
            raise Exception(
                "No se encontró un CAF activo para guías de despacho.")

        caf_path = caf.archivo.name

        # Obtener RUT del certificado (no del CAF)
        cert = CertificadoDigital.objects.filter(activo=True).first()
        if not cert:
            raise Exception("No se encontró un certificado digital activo.")

        rut_emisor = cert.rut_emisor
        pfx_path = cert.archivo.name
        pfx_pass = cert.clave_certificado

        # Rutas de salida
        nombre_archivo_base = f"DTE_Guia_{salida.numero_documento}"
        carpeta = f"xml_firmados/{now().year}/{now().month:02d}/"
        output_path = os.path.join(carpeta, f"{nombre_archivo_base}.xml")

        # Generar y firmar XML
        archivo_xml_path = generar_y_firmar_dte(
            salida, caf_path, pfx_path, pfx_pass, output_path)
        salida.archivo_xml.name = archivo_xml_path

        # Generar PDF
        pdf_bytes = generar_pdf_guia_despacho(salida)
        nombre_pdf = f"guias_despacho_pdf/{now().year}/{now().month:02d}/guia_{salida.numero_documento}.pdf"
        pdf_path = default_storage.save(nombre_pdf, ContentFile(pdf_bytes))
        salida.archivo_pdf.name = pdf_path

        salida.firmada = True
        salida.save()

        messages.success(request, "Guía firmada correctamente.")
    except Exception as e:
        messages.error(request, f"Ocurrió un error al firmar la guía: {e}")

    return redirect("logistica:listar_salidas")


# logistica/views.py


def obtener_datos_material(request):
    material_id = request.GET.get('material_id')
    try:
        material = Material.objects.get(id=material_id)
        return JsonResponse({
            'descripcion': material.nombre,
            'valor_unitario': float(material.valor_unitario),
        })
    except Material.DoesNotExist:
        return JsonResponse({'error': 'Material no encontrado'}, status=404)

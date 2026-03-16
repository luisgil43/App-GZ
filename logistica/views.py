from __future__ import annotations

import locale
import unicodedata
import xml.etree.ElementTree as ET

import openpyxl
import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.db.models import Count, Q
from django.db.models.functions import ExtractMonth, ExtractYear
from django.forms import inlineformset_factory, modelformset_factory
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import localtime, now

from logistica.services.emitir_dte import generar_y_firmar_dte
from usuarios.decoradores import rol_requerido
from utils.pdf_generator import generar_pdf_guia_despacho

from .forms import (BodegaForm, DetalleSalidaFormSet, FiltroIngresoForm,
                    ImportarCAFForm, ImportarCertificadoForm,
                    ImportarExcelForm, IngresoMaterialForm, MaterialForm,
                    MaterialIngresoForm, SalidaMaterialForm)
from .models import (ArchivoCAF, Bodega, CertificadoDigital,
                     DetalleIngresoMaterial, FolioDisponible, IngresoMaterial,
                     Material, SalidaMaterial)

MaterialIngresoFormSet = modelformset_factory(
    DetalleIngresoMaterial,
    form=MaterialIngresoForm,
    extra=1,
    can_delete=True,
)


# ==========================================================
# INGRESO DE MATERIALES
# ==========================================================
@login_required
@rol_requerido("logistica", "admin", "pm")
def registrar_ingreso_material(request):
    if request.method == "POST":
        form = IngresoMaterialForm(request.POST, request.FILES)
        formset = MaterialIngresoFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            numero_documento = form.cleaned_data.get("numero_documento")
            tipo_documento = form.cleaned_data.get("tipo_documento")

            if IngresoMaterial.objects.filter(numero_documento=numero_documento, tipo_documento=tipo_documento).exists():
                messages.error(request, f'Ya existe un ingreso con el número "{numero_documento}" para ese tipo.')
            else:
                materiales_usados = set()
                materiales_repetidos = False

                for material_form in formset:
                    if material_form.cleaned_data and not material_form.cleaned_data.get("DELETE", False):
                        material = material_form.cleaned_data["material"]
                        if material in materiales_usados:
                            materiales_repetidos = True
                            break
                        materiales_usados.add(material)

                if materiales_repetidos:
                    messages.error(request, "No puedes registrar el mismo material más de una vez.")
                else:
                    try:
                        with transaction.atomic():
                            ingreso = form.save(commit=False)
                            ingreso.registrado_por = request.user
                            ingreso.save()

                            for material_form in formset:
                                if material_form.cleaned_data and not material_form.cleaned_data.get("DELETE", False):
                                    detalle = material_form.save(commit=False)
                                    detalle.ingreso = ingreso
                                    detalle.save()

                            messages.success(request, "Ingreso registrado correctamente.")
                            return redirect("logistica:listar_ingresos")
                    except Exception as e:
                        messages.error(request, f"Error al guardar: {str(e)}")
        else:
            messages.error(request, "Por favor corrige los errores.")
    else:
        form = IngresoMaterialForm()
        formset = MaterialIngresoFormSet(queryset=DetalleIngresoMaterial.objects.none())

    return render(request, "logistica/registrar_ingreso_material.html", {"form": form, "formset": formset})


@login_required
@rol_requerido("logistica", "admin", "pm")
def listar_ingresos_material(request):
    mes = request.GET.get("mes")
    anio = request.GET.get("anio")

    try:
        anio = int(anio)
    except (TypeError, ValueError):
        anio = now().year

    ingresos = (
        IngresoMaterial.objects.select_related("bodega", "registrado_por")
        .prefetch_related("detalles", "detalles__material")
        .annotate(mes=ExtractMonth("fecha_ingreso"), anio=ExtractYear("fecha_ingreso"))
        .filter(anio=anio)
        .order_by("-fecha_ingreso", "-id")
    )

    if mes and mes != "None":
        ingresos = ingresos.filter(mes=int(mes))

    if "exportar" in request.GET:
        filas = []
        for ingreso in ingresos:
            for detalle in ingreso.detalles.all():
                filas.append(
                    {
                        "Fecha": ingreso.fecha_ingreso.strftime("%d/%m/%Y"),
                        "Material": detalle.material.nombre,
                        "Cantidad": detalle.cantidad,
                        "Tipo Doc": ingreso.get_tipo_documento_display(),
                        "N° Documento": ingreso.numero_documento,
                        "Registrado por": ingreso.registrado_por.get_full_name() if ingreso.registrado_por else "-",
                    }
                )
        df = pd.DataFrame(filas)
        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = 'attachment; filename="ingresos_materiales.xlsx"'
        df.to_excel(response, index=False)
        return response

    form_filtro = FiltroIngresoForm(initial={"mes": mes, "anio": anio})
    return render(
        request,
        "logistica/listar_ingresos.html",
        {"ingresos": ingresos, "form_filtro": form_filtro, "mes_seleccionado": mes, "año_seleccionado": anio},
    )


# ==========================================================
# MATERIALES
# ==========================================================
@login_required
@rol_requerido("logistica", "admin", "pm")
def crear_material(request):
    materiales = Material.objects.select_related("bodega").all().order_by("nombre")
    form_material = MaterialForm()
    form_excel = ImportarExcelForm()

    if request.method == "POST":
        if "crear_manual" in request.POST:
            form_material = MaterialForm(request.POST)
            if form_material.is_valid():
                form_material.save()
                messages.success(request, "Material creado exitosamente.")
                return redirect("logistica:crear_material")

        elif "importar_excel" in request.POST and request.FILES.get("archivo_excel"):
            form_excel = ImportarExcelForm(request.POST, request.FILES)
            if form_excel.is_valid():
                df = pd.read_excel(request.FILES["archivo_excel"])
                df.columns = df.columns.str.lower().str.strip()

                columnas_req = {
                    "nombre",
                    "codigo_interno",
                    "codigo_externo",
                    "unidad_medida",
                    "descripcion",
                    "stock_actual",
                    "stock_minimo",
                    "valor unitario",
                    "bodega",
                }
                if not columnas_req.issubset(set(df.columns)):
                    missing = columnas_req - set(df.columns)
                    messages.error(request, f"Faltan columnas en el Excel: {', '.join(missing)}")
                else:
                    errores = []
                    creados = 0
                    for _, row in df.iterrows():
                        nombre = str(row.get("nombre", "")).strip()
                        codigo_interno = str(row.get("codigo_interno", "")).strip()
                        codigo_externo = str(row.get("codigo_externo", "")).strip()
                        unidad = str(row.get("unidad_medida", "")).strip()
                        descripcion = str(row.get("descripcion", "")).strip()
                        stock_actual = int(row.get("stock_actual", 0) or 0)
                        stock_minimo = int(row.get("stock_minimo", 0) or 0)
                        valor_unitario = float(row.get("valor unitario", 0) or 0)
                        bodega_nombre = str(row.get("bodega", "")).strip()

                        if not nombre or not codigo_interno or not bodega_nombre:
                            continue

                        bodega, _ = Bodega.objects.get_or_create(nombre__iexact=bodega_nombre, defaults={"nombre": bodega_nombre})

                        existe = Material.objects.filter(bodega=bodega).filter(
                            Q(codigo_interno__iexact=codigo_interno)
                            | (Q(codigo_externo__iexact=codigo_externo) if codigo_externo else Q())
                        ).exists()

                        if existe:
                            errores.append(
                                f"Duplicado: {nombre} (CI: {codigo_interno}, CE: {codigo_externo}) en bodega '{bodega_nombre}'"
                            )
                            continue

                        Material.objects.create(
                            nombre=nombre,
                            codigo_interno=codigo_interno,
                            codigo_externo=codigo_externo or None,
                            unidad_medida=unidad,
                            descripcion=descripcion,
                            stock_actual=stock_actual,
                            stock_minimo=stock_minimo,
                            valor_unitario=valor_unitario,
                            bodega=bodega,
                        )
                        creados += 1

                    if errores:
                        messages.warning(request, f"Se omitieron {len(errores)} filas por duplicidad:<br>" + "<br>".join(errores))
                    messages.success(request, f"{creados} materiales importados correctamente.")
                    return redirect("logistica:crear_material")

    return render(
        request,
        "logistica/crear_material.html",
        {"form_material": form_material, "form_excel": form_excel, "materiales": materiales},
    )


@login_required
@rol_requerido("logistica", "admin", "pm")
def editar_material(request, pk):
    material = get_object_or_404(Material, pk=pk)

    if request.method == "POST":
        form = MaterialForm(request.POST, instance=material)
        if form.is_valid():
            actualizado = form.save(commit=False)

            existe_interno = Material.objects.filter(
                codigo_interno__iexact=actualizado.codigo_interno,
                bodega=actualizado.bodega,
            ).exclude(pk=material.pk).exists()

            existe_externo = False
            if actualizado.codigo_externo:
                existe_externo = Material.objects.filter(
                    codigo_externo__iexact=actualizado.codigo_externo,
                    bodega=actualizado.bodega,
                ).exclude(pk=material.pk).exists()

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
                messages.success(request, "Material actualizado correctamente.")
                return redirect("logistica:crear_material")
        else:
            messages.error(request, "Por favor corrige los errores del formulario.")
    else:
        form = MaterialForm(instance=material)

    return render(request, "logistica/editar_material.html", {"form": form, "material": material})


@login_required
@rol_requerido("admin")
def eliminar_material(request, pk):
    material = get_object_or_404(Material, pk=pk)
    if request.method == "POST":
        material.delete()
        messages.success(request, "Material eliminado correctamente.")
        return redirect("logistica:crear_material")
    return render(request, "logistica/eliminar_material.html", {"material": material})


# ==========================================================
# EDITAR / ELIMINAR INGRESOS
# ==========================================================
@login_required
@rol_requerido("admin")
def editar_ingreso_material(request, pk):
    ingreso = get_object_or_404(IngresoMaterial, pk=pk)

    DetalleFormSet = inlineformset_factory(
        IngresoMaterial,
        DetalleIngresoMaterial,
        form=MaterialIngresoForm,
        extra=0,
        can_delete=True,
    )

    if request.method == "POST":
        form = IngresoMaterialForm(request.POST, request.FILES, instance=ingreso)
        formset = DetalleFormSet(request.POST, request.FILES, instance=ingreso, prefix="detalles")

        if form.is_valid() and formset.is_valid():
            numero_documento = form.cleaned_data.get("numero_documento")
            tipo_documento = form.cleaned_data.get("tipo_documento")

            existe_duplicado = IngresoMaterial.objects.exclude(pk=ingreso.pk).filter(
                numero_documento=numero_documento,
                tipo_documento=tipo_documento,
            ).exists()

            if existe_duplicado:
                messages.error(request, f'Ya existe otro ingreso con el número "{numero_documento}" para ese tipo.')
            else:
                materiales_usados = set()
                for f in formset:
                    if f.cleaned_data and not f.cleaned_data.get("DELETE", False):
                        m = f.cleaned_data["material"]
                        if m in materiales_usados:
                            messages.error(request, "No puedes repetir el mismo material en el mismo ingreso.")
                            return redirect("logistica:editar_ingreso", pk=pk)
                        materiales_usados.add(m)

                form.save()
                formset.save()
                messages.success(request, "Ingreso actualizado correctamente.")
                return redirect("logistica:listar_ingresos")
        else:
            messages.error(request, "Corrige los errores antes de continuar.")
    else:
        form = IngresoMaterialForm(instance=ingreso)
        formset = DetalleFormSet(instance=ingreso, prefix="detalles")

    formset_empty = DetalleFormSet(prefix="detalles").empty_form

    return render(
        request,
        "logistica/editar_ingreso.html",
        {"form": form, "formset": formset, "formset_empty": formset_empty, "ingreso": ingreso},
    )


@login_required
@rol_requerido("admin")
def eliminar_ingreso_material(request, pk):
    ingreso = get_object_or_404(IngresoMaterial, pk=pk)
    if request.method == "POST":
        if ingreso.archivo_documento and ingreso.archivo_documento.name:
            ingreso.archivo_documento.delete(save=False)
        ingreso.delete()
        messages.success(request, "Ingreso eliminado correctamente.")
        return redirect("logistica:listar_ingresos")
    messages.error(request, "La eliminación debe hacerse mediante POST.")
    return redirect("logistica:listar_ingresos")


# ==========================================================
# BODEGAS
# ==========================================================
@login_required
@rol_requerido("logistica", "admin")
def crear_bodega(request):
    bodegas = Bodega.objects.all().order_by("nombre")

    if request.method == "POST":
        form = BodegaForm(request.POST)
        if form.is_valid():
            b = form.save(commit=False)
            b.creada_por = request.user
            b.save()
            messages.success(request, "Bodega creada correctamente.")
            return redirect("logistica:crear_bodega")
    else:
        form = BodegaForm()

    return render(request, "logistica/crear_bodega.html", {"form": form, "bodegas": bodegas})


@login_required
@rol_requerido("logistica", "admin")
def editar_bodega(request, pk):
    bodega = get_object_or_404(Bodega, pk=pk)

    if request.method == "POST":
        form = BodegaForm(request.POST, instance=bodega)
        if form.is_valid():
            form.save()
            messages.success(request, "Bodega actualizada correctamente.")
            return redirect("logistica:crear_bodega")
    else:
        form = BodegaForm(instance=bodega)

    return render(
        request,
        "logistica/crear_bodega.html",
        {"form": form, "bodegas": Bodega.objects.all().order_by("nombre"), "editar_bodega": bodega},
    )

@login_required
@rol_requerido("logistica", "admin")
def eliminar_bodega(request, pk):
    # ✅ compat: si aún queda algún link viejo, lo mandamos al flujo nuevo
    if request.method != "POST":
        messages.error(request, "La eliminación debe hacerse mediante POST.")
        return redirect("logistica:bodegas_manage")

    # Si no es admin general, no puede eliminar (regla nueva)
    if not getattr(request.user, "es_admin_general", False):
        messages.error(request, "Solo el Admin General puede eliminar bodegas.")
        return redirect("logistica:bodegas_manage")

    # redirigimos al endpoint oficial (nuevo)
    return redirect("logistica:bodega_delete", bodega_id=pk)


# ==========================================================
# CAF
# ==========================================================
@login_required
@rol_requerido("logistica", "admin")
def importar_caf(request):
    if request.method == "POST":
        form = ImportarCAFForm(request.POST, request.FILES)
        if form.is_valid():
            archivo = form.cleaned_data["archivo_caf"]
            try:
                tree = ET.parse(archivo)
                root = tree.getroot()

                tipo_dte_text = root.findtext(".//DA/TD")
                if tipo_dte_text is None:
                    messages.error(request, "No se encontró el tipo de DTE (TD) en el CAF.")
                    return redirect("logistica:listar_caf")
                tipo_dte = int(tipo_dte_text)

                desde_text = root.findtext(".//DA/RNG/D")
                hasta_text = root.findtext(".//DA/RNG/H")
                if desde_text is None or hasta_text is None:
                    messages.error(request, "No se encontró el rango (D-H) en el CAF.")
                    return redirect("logistica:listar_caf")

                rango_inicio = int(desde_text)
                rango_fin = int(hasta_text)

                conflicto = ArchivoCAF.objects.filter(tipo_dte=tipo_dte, estado="activo").filter(
                    Q(rango_inicio__lte=rango_fin, rango_fin__gte=rango_inicio)
                ).exists()
                if conflicto:
                    messages.error(
                        request,
                        f"Ya existe un CAF activo para TD {tipo_dte} con rango que se cruza con {rango_inicio}-{rango_fin}.",
                    )
                    return redirect("logistica:listar_caf")

                archivo.seek(0)

                archivo_caf = ArchivoCAF.objects.create(
                    tipo_dte=tipo_dte,
                    nombre_archivo=getattr(archivo, "name", "caf.xml"),
                    archivo=archivo,  # ✅ Wasabi
                    rango_inicio=rango_inicio,
                    rango_fin=rango_fin,
                    estado="activo",
                    usuario=request.user,
                )

                for folio in range(rango_inicio, rango_fin + 1):
                    FolioDisponible.objects.create(caf=archivo_caf, folio=folio)

                messages.success(request, f"CAF importado correctamente para DTE tipo {tipo_dte}.")
                return redirect("logistica:listar_caf")

            except ET.ParseError:
                messages.error(request, "El archivo no es un XML válido.")
            except Exception as e:
                messages.error(request, f"Error al importar CAF: {str(e)}")
    else:
        form = ImportarCAFForm()

    return render(request, "logistica/importar_caf.html", {"form": form})


def listar_caf(request):
    archivos = ArchivoCAF.objects.annotate(
        total_folios=Count("foliodisponible"),
        disponibles=Count("foliodisponible", filter=Q(foliodisponible__usado=False)),
    )

    facturas_disponibles = archivos.filter(tipo_dte=33).aggregate(
        total=Count("foliodisponible", filter=Q(foliodisponible__usado=False))
    )["total"] or 0

    guias_disponibles = archivos.filter(tipo_dte=52).aggregate(
        total=Count("foliodisponible", filter=Q(foliodisponible__usado=False))
    )["total"] or 0

    notas_disponibles = archivos.filter(tipo_dte=61).aggregate(
        total=Count("foliodisponible", filter=Q(foliodisponible__usado=False))
    )["total"] or 0

    return render(
        request,
        "logistica/listar_caf.html",
        {
            "archivos": archivos,
            "facturas_disponibles": facturas_disponibles,
            "guias_disponibles": guias_disponibles,
            "notas_disponibles": notas_disponibles,
        },
    )


@login_required
@rol_requerido("logistica", "admin")
def eliminar_caf(request, pk):
    caf = get_object_or_404(ArchivoCAF, pk=pk)
    if request.method == "POST":
        if caf.archivo and caf.archivo.name:
            caf.archivo.delete(save=False)
        caf.delete()
        messages.success(request, "El archivo CAF fue eliminado correctamente.")
        return redirect("logistica:listar_caf")
    messages.error(request, "Método no permitido.")
    return redirect("logistica:listar_caf")


# ==========================================================
# CERTIFICADOS
# ==========================================================
@login_required
@rol_requerido("logistica", "admin")
def importar_certificado(request):
    if request.method == "POST":
        form = ImportarCertificadoForm(request.POST, request.FILES)
        if form.is_valid():
            certificado = form.save(commit=False)
            certificado.usuario = request.user
            certificado.fecha_inicio = timezone.now().date()
            certificado.activo = True
            certificado.save()

            messages.success(request, "Certificado digital cargado correctamente.")
            return redirect("logistica:importar_certificado")
        else:
            messages.error(request, "Por favor revisa los campos del formulario.")
    else:
        form = ImportarCertificadoForm()

    certificados = CertificadoDigital.objects.all().order_by("-fecha_inicio")
    return render(request, "logistica/importar_certificado.html", {"form": form, "certificados": certificados})


@login_required
@rol_requerido("logistica", "admin")
def eliminar_certificado(request, pk):
    certificado = get_object_or_404(CertificadoDigital, pk=pk)
    if request.method == "POST":
        if certificado.archivo and certificado.archivo.name:
            certificado.archivo.delete(save=False)
        certificado.delete()
        messages.success(request, "Certificado eliminado correctamente.")
    return redirect("logistica:importar_certificado")


# ==========================================================
# SALIDAS
# ==========================================================
@login_required
@rol_requerido("logistica", "admin", "pm")
def listar_salidas_material(request):
    mes = request.GET.get("mes")
    anio = request.GET.get("anio")

    try:
        anio = int(anio)
    except (TypeError, ValueError):
        anio = now().year

    salidas = SalidaMaterial.objects.annotate(
        mes=ExtractMonth("fecha_salida"),
        anio=ExtractYear("fecha_salida"),
    ).filter(anio=anio).order_by("-fecha_salida", "-id")

    if mes and mes != "None":
        salidas = salidas.filter(mes=int(mes))

    for salida in salidas:
        salida.folio_usado = None
        salida.firmada = bool((salida.archivo_pdf and salida.archivo_pdf.name) or (salida.archivo_xml and salida.archivo_xml.name))

        try:
            folio = FolioDisponible.objects.get(folio=int(salida.numero_documento), usado=True)
            salida.folio_usado = folio.folio
        except FolioDisponible.DoesNotExist:
            pass

    form_filtro = FiltroIngresoForm(initial={"mes": mes, "anio": anio})
    return render(
        request,
        "logistica/listar_salidas.html",
        {"salidas": salidas, "form_filtro": form_filtro, "mes_seleccionado": mes, "año_seleccionado": anio},
    )


@login_required
@rol_requerido("logistica", "admin", "pm")
def registrar_salida(request):
    if request.method == "POST":
        form = SalidaMaterialForm(request.POST, request.FILES)
        formset = DetalleSalidaFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            salida = form.save(commit=False)

            if salida.tipo_documento == "guia":
                tipo_dte = 52
            elif salida.tipo_documento == "factura":
                tipo_dte = 33
            else:
                tipo_dte = None

            if tipo_dte:
                folio = FolioDisponible.objects.filter(
                    usado=False,
                    caf__tipo_dte=tipo_dte,
                    caf__estado="activo",
                ).order_by("folio").first()

                if not folio:
                    messages.error(request, "No hay folios disponibles para este tipo de documento.")
                    return redirect("logistica:registrar_salida")

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
            return redirect("logistica:listar_salidas")
        else:
            messages.error(request, "Corrige los errores del formulario.")
    else:
        form = SalidaMaterialForm()
        formset = DetalleSalidaFormSet()

    try:
        locale.setlocale(locale.LC_TIME, "es_CL.utf8")
    except locale.Error:
        try:
            locale.setlocale(locale.LC_TIME, "es_ES.utf8")
        except locale.Error:
            pass

    fecha_emision = localtime().strftime("%d de %B del %Y")
    return render(request, "logistica/registrar_salida.html", {"form": form, "formset": formset, "fecha_emision": fecha_emision})


@login_required
def eliminar_salida(request, pk):
    salida = get_object_or_404(SalidaMaterial, pk=pk)
    if request.method == "POST":
        if salida.archivo_pdf and salida.archivo_pdf.name:
            salida.archivo_pdf.delete(save=False)
        if salida.archivo_xml and salida.archivo_xml.name:
            salida.archivo_xml.delete(save=False)
        salida.delete()
        messages.success(request, "Documento eliminado correctamente.")
        return redirect("logistica:listar_salidas")
    messages.error(request, "La eliminación debe hacerse mediante POST.")
    return redirect("logistica:listar_salidas")


@login_required
@rol_requerido("logistica", "admin")
def firmar_salida(request, pk):
    salida = get_object_or_404(SalidaMaterial, pk=pk)

    try:
        caf = ArchivoCAF.objects.filter(tipo_dte=52, estado="activo").first()
        if not caf:
            raise Exception("No se encontró un CAF activo para guías de despacho.")
        caf_path = caf.archivo.name

        cert = CertificadoDigital.objects.filter(activo=True).first()
        if not cert:
            raise Exception("No se encontró un certificado digital activo.")
        pfx_path = cert.archivo.name
        pfx_pass = cert.clave_certificado

        nombre_archivo_base = f"DTE_Guia_{salida.numero_documento}"
        carpeta = f"xml_firmados/{now().year}/{now().month:02d}/"
        output_path = f"{carpeta}{nombre_archivo_base}.xml"

        # genera xml y devuelve PATH en el storage
        archivo_xml_path = generar_y_firmar_dte(salida, caf_path, pfx_path, pfx_pass, output_path)
        salida.archivo_xml.name = archivo_xml_path

        # PDF en memoria -> guardamos directo en el FileField (que es Wasabi)
        pdf_bytes = generar_pdf_guia_despacho(salida)
        salida.archivo_pdf.save(
            f"guia_{salida.numero_documento}.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )

        salida.save()
        messages.success(request, "Guía firmada correctamente.")
    except Exception as e:
        messages.error(request, f"Ocurrió un error al firmar la guía: {e}")

    return redirect("logistica:listar_salidas")


# ==========================================================
# AJAX
# ==========================================================
def obtener_datos_material(request):
    material_id = request.GET.get("material_id")
    try:
        material = Material.objects.get(id=material_id)
        return JsonResponse({"descripcion": material.nombre, "valor_unitario": float(material.valor_unitario or 0)})
    except Material.DoesNotExist:
        return JsonResponse({"error": "Material no encontrado"}, status=404)
    

def importar_materiales(request):
    """
    Importa materiales desde un archivo Excel (.xlsx).

    Reglas:
    - Crea la Bodega si no existe.
    - Evita duplicados dentro de la misma bodega:
        - codigo_interno (case-insensitive)
        - codigo_externo (case-insensitive) si viene informado
    - Si una fila es duplicada, se omite y se reporta como warning.
    """

    import unicodedata

    import openpyxl
    from django.contrib import messages
    from django.contrib.auth.decorators import login_required
    from django.db.models import Q
    from django.shortcuts import redirect, render

    from usuarios.decoradores import rol_requerido

    from .forms import ImportarExcelForm
    from .models import Bodega, Material

    def normalizar(texto):
        """
        Normaliza texto para comparar headers:
        - lower
        - strip
        - sin tildes
        """
        texto = str(texto or "").strip().lower()
        return "".join(
            c for c in unicodedata.normalize("NFD", texto)
            if unicodedata.category(c) != "Mn"
        )

    @login_required
    @rol_requerido("logistica", "admin", "pm")
    def _view(request):
        if request.method == "POST":
            form = ImportarExcelForm(request.POST, request.FILES)
            if form.is_valid():
                archivo = request.FILES["archivo_excel"]

                try:
                    wb = openpyxl.load_workbook(archivo)
                    sheet = wb.active

                    # 1) Cabeceras
                    headers_originales = [str(c.value).strip() if c.value is not None else "" for c in sheet[1]]
                    headers_normalizados = [normalizar(h) for h in headers_originales]

                    # Columnas requeridas (según tu estándar)
                    columnas_requeridas = {
                        "nombre",
                        "codigo interno",
                        "codigo externo",
                        "bodega",
                        "stock actual",
                        "stock minimo",
                        "unidad medida",
                        "valor unitario",
                        "descripcion",
                    }

                    if not columnas_requeridas.issubset(set(headers_normalizados)):
                        faltan = columnas_requeridas - set(headers_normalizados)
                        messages.error(request, f"Faltan columnas: {', '.join(sorted(faltan))}")
                        return redirect("logistica:importar_materiales")

                    creados = 0
                    bodegas_creadas = set()
                    duplicados = []

                    # 2) Iterar filas
                    for fila in sheet.iter_rows(min_row=2, values_only=True):
                        if not any(fila):
                            continue

                        data = dict(zip(headers_normalizados, fila))

                        nombre = str(data.get("nombre", "") or "").strip()
                        codigo_interno = str(data.get("codigo interno", "") or "").strip()
                        codigo_externo = str(data.get("codigo externo", "") or "").strip()
                        bodega_nombre = str(data.get("bodega", "") or "").strip()

                        unidad_medida = str(data.get("unidad medida", "") or "").strip()
                        descripcion = str(data.get("descripcion", "") or "").strip()

                        stock_actual = data.get("stock actual") or 0
                        stock_minimo = data.get("stock minimo") or 0
                        valor_unitario = data.get("valor unitario") or 0

                        # Validaciones mínimas
                        if not nombre or not codigo_interno or not bodega_nombre:
                            # si falta algo clave, saltamos fila
                            continue

                        # Convertir numéricos de forma segura
                        try:
                            stock_actual = int(stock_actual or 0)
                        except Exception:
                            stock_actual = 0

                        try:
                            stock_minimo = int(stock_minimo or 0)
                        except Exception:
                            stock_minimo = 0

                        try:
                            valor_unitario = float(valor_unitario or 0)
                            if valor_unitario < 0:
                                raise ValueError
                        except Exception:
                            messages.error(
                                request,
                                f"Valor unitario inválido o negativo en material '{nombre}' (código {codigo_interno})."
                            )
                            return redirect("logistica:importar_materiales")

                        # 3) Bodega
                        bodega, creada = Bodega.objects.get_or_create(
                            nombre__iexact=bodega_nombre,
                            defaults={"nombre": bodega_nombre},
                        )
                        if creada:
                            bodegas_creadas.add(bodega.nombre)

                        # 4) Duplicados dentro de la misma bodega
                        # - Siempre por codigo_interno
                        # - Y por codigo_externo si viene informado
                        dup_filter = Q(codigo_interno__iexact=codigo_interno)
                        if codigo_externo:
                            dup_filter = dup_filter | Q(codigo_externo__iexact=codigo_externo)

                        if Material.objects.filter(bodega=bodega).filter(dup_filter).exists():
                            duplicados.append(
                                f"[Bodega: {bodega.nombre}] CI '{codigo_interno}'"
                                + (f", CE '{codigo_externo}'" if codigo_externo else "")
                            )
                            continue

                        # 5) Crear Material
                        Material.objects.create(
                            nombre=nombre,
                            codigo_interno=codigo_interno,
                            codigo_externo=codigo_externo or None,
                            unidad_medida=unidad_medida,
                            descripcion=descripcion,
                            stock_actual=max(stock_actual, 0),
                            stock_minimo=max(stock_minimo, 0),
                            valor_unitario=valor_unitario,
                            bodega=bodega,
                        )
                        creados += 1

                    # 6) Mensajes
                    if creados:
                        extra = ""
                        if bodegas_creadas:
                            extra = f"<br>Se crearon bodegas: {', '.join(sorted(bodegas_creadas))}"
                        messages.success(request, f"{creados} materiales importados correctamente.{extra}")
                    else:
                        messages.info(request, "No se importaron materiales (revisa el archivo).")

                    if duplicados:
                        messages.warning(
                            request,
                            f"Se omitieron {len(duplicados)} filas por duplicados:<br>" + "<br>".join(duplicados)
                        )

                    return redirect("logistica:crear_material")

                except Exception as e:
                    messages.error(request, f"Error al procesar el archivo: {e}")
                    return redirect("logistica:importar_materiales")

        else:
            form = ImportarExcelForm()

        bodegas = Bodega.objects.all().order_by("nombre")
        return render(request, "logistica/importar_materiales.html", {
            "form_excel": form,
            "bodegas": bodegas,
        })

    return _view(request)

def exportar_materiales(request):
    """
    Exporta los materiales a Excel (stock_materiales.xlsx).
    Columnas:
    - Nombre
    - Código Interno
    - Código Externo
    - Bodega
    - Stock Actual
    - Stock Mínimo
    - Unidad Medida
    - Valor Unitario
    - Descripción
    """
    import pandas as pd
    from django.contrib.auth.decorators import login_required
    from django.http import HttpResponse

    from usuarios.decoradores import rol_requerido

    from .models import Material

    @login_required
    @rol_requerido("logistica", "admin", "pm")
    def _view(request):
        materiales = (
            Material.objects
            .select_related("bodega")
            .values(
                "nombre",
                "codigo_interno",
                "codigo_externo",
                "bodega__nombre",
                "stock_actual",
                "stock_minimo",
                "unidad_medida",
                "valor_unitario",
                "descripcion",
            )
        )

        df = pd.DataFrame(list(materiales))

        # Si no hay registros, igual devolvemos un Excel vacío con headers
        if df.empty:
            df = pd.DataFrame(columns=[
                "Nombre",
                "Código Interno",
                "Código Externo",
                "Bodega",
                "Stock Actual",
                "Stock Mínimo",
                "Unidad Medida",
                "Valor Unitario",
                "Descripción",
            ])
        else:
            df.rename(columns={
                "nombre": "Nombre",
                "codigo_interno": "Código Interno",
                "codigo_externo": "Código Externo",
                "bodega__nombre": "Bodega",
                "stock_actual": "Stock Actual",
                "stock_minimo": "Stock Mínimo",
                "unidad_medida": "Unidad Medida",
                "valor_unitario": "Valor Unitario",
                "descripcion": "Descripción",
            }, inplace=True)

            columnas_ordenadas = [
                "Nombre",
                "Código Interno",
                "Código Externo",
                "Bodega",
                "Stock Actual",
                "Stock Mínimo",
                "Unidad Medida",
                "Valor Unitario",
                "Descripción",
            ]
            df = df[columnas_ordenadas]

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="stock_materiales.xlsx"'
        df.to_excel(response, index=False)
        return response

    return _view(request)
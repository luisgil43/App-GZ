import base64
import calendar
import logging
import os
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import fitz  # PyMuPDF
import requests
from dal import autocomplete
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import (FileResponse, Http404, HttpResponse,
                         HttpResponseBadRequest, JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django_select2.views import AutoResponseView
from PIL import Image

from liquidaciones.forms import CargaMasivaLiquidacionesForm
from liquidaciones.models import Liquidacion
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser, FirmaRepresentanteLegal
# 👈 Asegúrate de que esté bien importado
from usuarios.utils import crear_notificacion

from . import views
from .forms import CargaMasivaLiquidacionesForm, LiquidacionForm
from .models import Liquidacion, ruta_archivo_firmado, ruta_archivo_sin_firmar
from .utils_pdf import extraer_paginas_liquidaciones, rut_clave

User = get_user_model()
logger = logging.getLogger(__name__)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def admin_lista_liquidaciones(request):
    try:
        # Base queryset
        qs = Liquidacion.objects.select_related('tecnico').order_by('-año', '-mes')

        # === Filtros por GET ===
        nombre  = request.GET.get('nombre', '').strip()
        mes     = request.GET.get('mes', '').strip()
        anio    = request.GET.get('anio', '').strip()       # parámetro sin ñ
        firmado = request.GET.get('firmado', '').strip()    # 'si', 'no' o ''

        if nombre:
            qs = qs.filter(
                Q(tecnico__first_name__icontains=nombre) |
                Q(tecnico__last_name__icontains=nombre) |
                Q(tecnico__username__icontains=nombre) |
                Q(tecnico__identidad__icontains=nombre)
            )

        if mes:
            qs = qs.filter(mes=mes)

        if anio:
            qs = qs.filter(año=anio)

        if firmado == 'si':
            qs = qs.filter(firmada=True)
        elif firmado == 'no':
            qs = qs.filter(firmada=False)

        # === Paginación ===
        cantidad = request.GET.get('cantidad', '20')
        try:
            cantidad_int = int(cantidad)
        except ValueError:
            cantidad_int = 20

        paginator = Paginator(qs, cantidad_int)
        page_number = request.GET.get('page') or 1
        pagina = paginator.get_page(page_number)

        # Opciones para selects (todas las liquidaciones)
        base_qs = Liquidacion.objects.select_related('tecnico')
        nombres = sorted(set(l.tecnico.get_full_name() for l in base_qs))
        meses   = sorted(set(l.mes for l in base_qs))
        años    = sorted(set(l.año for l in base_qs))

        filtros = {
            "nombre":  nombre,
            "mes":     mes,
            "anio":    anio,
            "firmado": firmado,
        }

        return render(request, 'liquidaciones/admin_lista.html', {
            "pagina":   pagina,
            "cantidad": str(cantidad_int),
            "filtros":  filtros,
            "nombres":  nombres,
            "meses":    meses,
            "años":     años,
        })

    except Exception as e:
        logger.error(f"[admin_lista_liquidaciones] Error: {e}")
        return HttpResponse(f"Error 500: {e}", status=500)


@login_required
def listar_liquidaciones(request):
    usuario = request.user

    # Base queryset: solo las del técnico
    qs = Liquidacion.objects.filter(tecnico=usuario).order_by('-año', '-mes')

    # === Filtros por GET ===
    mes    = request.GET.get('mes', '').strip()
    anio   = request.GET.get('anio', '').strip()      # sin ñ en el parámetro
    estado = request.GET.get('estado', '').strip()    # 'firmadas', 'pendientes' o ''

    if mes:
        qs = qs.filter(mes=mes)

    if anio:
        qs = qs.filter(año=anio)

    if estado == 'firmadas':
        qs = qs.filter(firmada=True)
    elif estado == 'pendientes':
        qs = qs.filter(firmada=False)

    # === Paginación ===
    cantidad = request.GET.get('cantidad', '20')
    try:
        cantidad_int = int(cantidad)
    except ValueError:
        cantidad_int = 20

    paginator   = Paginator(qs, cantidad_int)
    page_number = request.GET.get('page') or 1
    pagina      = paginator.get_page(page_number)

    # Opciones para selects (todas las liquidaciones del usuario)
    base_qs = Liquidacion.objects.filter(tecnico=usuario)
    meses   = sorted(set(l.mes for l in base_qs))
    años    = sorted(set(l.año for l in base_qs))

    filtros = {
        "mes":    mes,
        "anio":   anio,
        "estado": estado,
    }

    return render(request, 'liquidaciones/listar.html', {
        "pagina":   pagina,
        "cantidad": str(cantidad_int),
        "filtros":  filtros,
        "meses":    meses,
        "años":     años,
    })


@login_required
def ver_pdf_liquidacion(request, pk):
    usuario = request.user

    try:
        liquidacion = Liquidacion.objects.get(pk=pk, tecnico=usuario)
    except Liquidacion.DoesNotExist:
        messages.error(
            request, "La liquidación solicitada no existe o no te pertenece.")
        return redirect('liquidaciones:listar')

    archivo = liquidacion.archivo_pdf_liquidacion

    if archivo and archivo.name:
        try:
            return FileResponse(archivo.open('rb'), content_type='application/pdf')
        except Exception as e:
            logger.error(f"[ver_pdf_liquidacion] Error al abrir PDF: {e}")
            messages.error(request, "No se pudo abrir el archivo PDF.")
            return redirect('liquidaciones:listar')
    else:
        messages.error(request, "El archivo ya no está disponible.")
        return redirect('liquidaciones:listar')


def _build_signature_rect(page, label_candidates, side='left'):
    """
    Busca el texto de la línea de firma (ej: 'FIRMA DEL EMPLEADOR')
    y construye un rectángulo justo encima de esa línea.

    side: 'left' o 'right' solo se usa para un fallback proporcional.
    """
    rect = None

    # Intentar cada variante de texto (por si cambia may/minúsculas)
    for text in label_candidates:
        hits = page.search_for(text)
        if hits:
            rect = hits[0]
            break

    page_rect = page.rect
    page_w = page_rect.width
    page_h = page_rect.height

    sig_height = 40          # alto de la firma
    margin_top = 10          # separación entre texto y firma
    extra_x = 20             # espacio lateral extra respecto al texto

    if rect:
        # Coordenadas basadas en la posición real del texto
        bottom = rect.y0 - margin_top          # un poco sobre la línea de texto
        top = bottom - sig_height              # más arriba todavía
        left = max(rect.x0 - extra_x, page_rect.x0)
        right = min(rect.x1 + extra_x, page_rect.x1)

        # Aseguramos que top < bottom
        if top < bottom:
            return fitz.Rect(left, top, right, bottom)

    # ---- Fallback si no se encontró el texto ----
    # Usamos posiciones proporcionales al tamaño de la página
    y_bottom = page_h * 0.87
    y_top = y_bottom - sig_height
    if side == 'left':
        x_left = page_w * 0.10
        x_right = page_w * 0.40
    else:  # 'right'
        x_left = page_w * 0.60
        x_right = page_w * 0.90

    return fitz.Rect(x_left, y_top, x_right, y_bottom)


@login_required
def firmar_liquidacion(request, pk):
    usuario = request.user
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=usuario)

    if not usuario.firma_digital or not usuario.firma_digital.name:
        messages.warning(request, "Debes registrar tu firma digital primero.")
        return redirect('liquidaciones:registrar_firma')

    try:
        usuario.firma_digital.open()
    except Exception as e:
        logger.warning(f"[firmar_liquidacion] Firma digital no accesible: {e}")
        messages.warning(request, "Tu firma ya no está disponible. Vuelve a registrarla.")
        return redirect('liquidaciones:registrar_firma')

    if request.method == 'POST':
        try:
            if not liquidacion.archivo_pdf_liquidacion or not liquidacion.archivo_pdf_liquidacion.name:
                return HttpResponseBadRequest("No se encontró el PDF.")

            # 1. Leer PDF original
            with liquidacion.archivo_pdf_liquidacion.open('rb') as f:
                original_pdf = BytesIO(f.read())

            # 2. Firma del usuario
            with usuario.firma_digital.open('rb') as f:
                firma_usuario_io = BytesIO(f.read())

            # 3. Firma del representante legal (desde modelo en Cloudinary)
            firma_representante = FirmaRepresentanteLegal.objects.order_by('-fecha_subida').first()
            if not firma_representante or not firma_representante.archivo:
                return HttpResponseBadRequest("No se encontró la firma del representante legal.")

            url_representante = firma_representante.archivo.url
            response = requests.get(url_representante)
            if response.status_code != 200:
                raise Exception("No se pudo descargar la firma del representante legal.")
            firma_representante_io = BytesIO(response.content)

            # 4. Convertir firmas a PNG
            img_usuario = Image.open(firma_usuario_io).convert("RGBA")
            img_representante = Image.open(firma_representante_io).convert("RGBA")

            output_usuario = BytesIO()
            img_usuario.save(output_usuario, format='PNG')
            output_usuario.seek(0)

            output_representante = BytesIO()
            img_representante.save(output_representante, format='PNG')
            output_representante.seek(0)

            # 5. Insertar firmas en PDF (última página)
            doc = fitz.open(stream=original_pdf, filetype='pdf')
            page = doc[-1]

            # Rectángulos de firmas ANCLADOS AL TEXTO
            rect_empleador = _build_signature_rect(
                page,
                ["FIRMA DEL EMPLEADOR", "Firma del empleador"],
                side='left'
            )
            rect_trabajador = _build_signature_rect(
                page,
                ["FIRMA DEL TRABAJADOR", "Firma del trabajador"],
                side='right'
            )

            # Firma del representante legal
            page.insert_image(rect_empleador, stream=output_representante)

            # Firma del usuario
            page.insert_image(rect_trabajador, stream=output_usuario)

            # 6. Guardar PDF firmado
            pdf_firmado_io = BytesIO()
            doc.save(pdf_firmado_io)
            doc.close()
            pdf_firmado_io.seek(0)

            nombre_base = Path(liquidacion.archivo_pdf_liquidacion.name).name
            content = ContentFile(pdf_firmado_io.read())
            liquidacion.pdf_firmado.save(nombre_base, content, save=False)

            liquidacion.firmada = True
            liquidacion.fecha_firma = now()
            liquidacion.save()

            messages.success(request, "Liquidación firmada correctamente.")
            return redirect('liquidaciones:listar')

        except Exception as e:
            logger.error(f"[firmar_liquidacion] Error: {e}")
            return HttpResponseBadRequest(f"Error al firmar: {e}")

    return render(request, 'liquidaciones/firmar.html', {
        'liquidacion': liquidacion,
        'tecnico': usuario,
        'solo_lectura': True,
        'firmar_documento': True
    })

@login_required
def registrar_firma(request):
    usuario = request.user
    try:
        redireccion = request.GET.get(
            'next') or reverse('liquidaciones:listar')
    except NoReverseMatch:
        redireccion = '/'  # Fallback seguro si algo falla

    # Eliminar firma
    if request.method == 'POST' and 'eliminar_firma' in request.POST:
        if usuario.firma_digital:
            usuario.firma_digital.delete(save=True)
            messages.success(request, "Firma digital eliminada correctamente.")
        else:
            messages.info(request, "No había firma registrada para eliminar.")
        return redirect(request.path)

    # Guardar firma
    if request.method == 'POST' and 'firma_digital' in request.POST:
        data_url = request.POST.get('firma_digital')

        if not data_url:
            messages.error(request, "No se recibió ninguna firma.")
            return redirect(request.path)

        try:
            if not data_url.startswith('data:image/png;base64,'):
                raise ValueError("El formato de la firma no es PNG válido.")

            # Decodificar imagen base64
            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)

            nombre_archivo = f"firmas/usuario_{usuario.id}_firma.png"

            # Eliminar anterior si existe
            if usuario.firma_digital and usuario.firma_digital.storage.exists(usuario.firma_digital.name):
                usuario.firma_digital.delete(save=False)

            usuario.firma_digital.save(nombre_archivo, content, save=True)

            messages.success(
                request, "Tu firma digital ha sido guardada correctamente.")
            return redirect(redireccion)

        except Exception as e:
            messages.error(
                request,
                f"Error al guardar la firma. Verifica que sea una imagen PNG válida. Detalles: {e}"
            )
            return redirect(request.path)

    # Determinar base html según si es admin
    base_template = 'dashboard_admin/base.html' if usuario.is_staff else 'dashboard/base.html'

    return render(request, 'liquidaciones/registrar_firma.html', {
        'tecnico': usuario,
        'base_template': base_template
    })


@login_required
def liquidaciones_pdf(request):
    usuario = request.user
    liquidaciones = Liquidacion.objects.filter(tecnico=usuario)

    html_string = render_to_string('liquidaciones/liquidaciones_pdf.html', {
        'liquidaciones': liquidaciones,
        # si tu template usa {{ tecnico }}, esto se mantiene
        'tecnico': usuario,
    })

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="liquidaciones.pdf"'

    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response


@login_required
def descargar_pdf(request, pk):
    usuario = request.user
    print("en descargar")
    try:
        liquidacion = Liquidacion.objects.get(pk=pk, tecnico=usuario)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe o no te pertenece.")
        return redirect('liquidaciones:listar')

    archivo = liquidacion.pdf_firmado
    # archivo = liquidacion.archivo_pdf_liquidacion
    print(archivo)
    if archivo and archivo.name:

        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"  # formato 01, 02, ..., 12
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}.pdf"
            # return FileResponse(archivo.open('rb'), content_type='application/pdf')
            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf',
                as_attachment=False,
                filename=nombre_archivo
            )
        except Exception as e:
            print("ERROR: ", e)
            logger.error(
                f"[descargar_pdf] Error al abrir archivo firmado: {e}")
            messages.error(request, "No se pudo abrir el archivo firmado.")
            return redirect('liquidaciones:listar')
    else:
        messages.error(request, "El archivo firmado ya no está disponible.")
        return redirect('liquidaciones:listar')


@login_required
def confirmar_firma(request, pk):
    user = request.user

    if user.is_staff:
        messages.error(
            request, "Solo los técnicos pueden confirmar una firma.")
        return redirect('liquidaciones:listar')

    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=user)

    if not liquidacion.firmada:
        liquidacion.firmada = True
        liquidacion.fecha_firma = timezone.now()
        liquidacion.save()
        logger.info(
            f"[confirmar_firma] Liquidación {pk} firmada por el técnico {user.pk}")
        messages.success(request, "Firma confirmada correctamente.")
    else:
        logger.info(
            f"[confirmar_firma] Liquidación {pk} ya estaba firmada por el técnico {user.pk}")
        messages.info(request, "La liquidación ya estaba firmada.")

    return redirect('liquidaciones:listar')


@staff_member_required
@csrf_protect
def confirmar_reemplazo(request):
    if request.method == 'POST':
        if '_reemplazar' in request.POST:
            data = request.session.get('duplicado_data')
            archivo_binario = request.session.get('archivo_temporal_bytes')
            archivo_nombre = request.session.get('archivo_temporal_nombre')

            if data and archivo_binario and archivo_nombre:
                tecnico_id = data.get('tecnico')
                mes = data.get('mes')
                año = data.get('año')

                anterior = Liquidacion.objects.filter(
                    tecnico_id=tecnico_id, mes=mes, año=año
                ).first()

                if anterior:
                    # Intentar eliminar archivos sin usar storage.exists()
                    try:
                        if anterior.pdf_firmado:
                            anterior.pdf_firmado.delete(save=False)
                    except Exception as e:
                        logger.warning(
                            f"[confirmar_reemplazo] No se pudo eliminar pdf_firmado: {e}")

                    try:
                        if anterior.archivo_pdf_liquidacion:
                            anterior.archivo_pdf_liquidacion.delete(save=False)
                    except Exception as e:
                        logger.warning(
                            f"[confirmar_reemplazo] No se pudo eliminar archivo_pdf_liquidacion: {e}")

                    anterior.delete()

                # Guardar nuevo archivo en Cloudinary
                nombre_final = f"liquidaciones_sin_firmar/{uuid.uuid4()}_{archivo_nombre}"
                ruta_guardada = default_storage.save(
                    nombre_final, ContentFile(archivo_binario))

                nueva = Liquidacion(
                    tecnico_id=tecnico_id,
                    mes=mes,
                    año=año,
                    firmada=False
                )
                nueva.archivo_pdf_liquidacion.name = ruta_guardada
                nueva.save()

                messages.success(
                    request, "✅ Liquidación reemplazada correctamente.")

                # Limpiar sesión
                request.session.pop('duplicado_data', None)
                request.session.pop('archivo_temporal_bytes', None)
                request.session.pop('archivo_temporal_nombre', None)

                return redirect('admin:liquidaciones_liquidacion_changelist')

        elif '_cancelar' in request.POST:
            request.session.pop('duplicado_data', None)
            request.session.pop('archivo_temporal_bytes', None)
            request.session.pop('archivo_temporal_nombre', None)

            messages.info(
                request, "❌ Se canceló el reemplazo de la liquidación.")
            return redirect('admin:liquidaciones_liquidacion_changelist')

    # GET
    data = request.session.get('duplicado_data', {})
    archivo_nombre = request.session.get('archivo_temporal_nombre', '')

    tecnico_nombre = ''
    if data.get('tecnico'):
        obj = Liquidacion.objects.filter(tecnico_id=data['tecnico']).first()
        tecnico_nombre = obj.tecnico if obj else ''

    return render(request, 'liquidaciones/confirmar_reemplazo.html', {
        'tecnico_id': data.get('tecnico'),
        'tecnico': tecnico_nombre,
        'mes': data.get('mes'),
        'año': data.get('año'),
        'pdf_name': archivo_nombre,
    })


MESES_ESPANOL = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def carga_masiva_liquidaciones(request):
    """
    Flujo en dos pasos:

    1) POST con archivos -> genera PREVIEW (no guarda nada) y guarda datos en sesión.
       - Detecta mes/año desde el PDF por cada página.
       - Marca duplicados (ya existe liquidación para técnico+mes+año).

    2) POST con confirmar=1 -> crea/actualiza las Liquidaciones usando lo de la sesión.
       - modo = "solo_nuevas" => no reemplaza duplicados.
       - modo = "reemplazar"  => reemplaza también duplicados.
    """
    User = get_user_model()

    form = CargaMasivaLiquidacionesForm()
    resumen = None
    asociadas = []
    no_asociadas = []

    # --------- Paso 2: Confirmar carga real ---------
    if request.method == 'POST' and request.POST.get("confirmar") == "1":
        preview = request.session.get("carga_liq_preview")

        if not preview:
            messages.error(request, "No hay una carga previa para confirmar.")
            return redirect('liquidaciones:carga_masiva')

        total_archivos = preview["total_archivos"]
        total_paginas = preview["total_paginas"]
        duplicados = preview.get("duplicados", 0)
        modo = request.POST.get("modo", "reemplazar")  # "solo_nuevas" o "reemplazar"

        asociadas_preview = preview["asociadas"]
        no_asociadas_preview = preview["no_asociadas"]

        # partimos de las no asociadas de la preview
        no_asociadas = list(no_asociadas_preview)

        for item in asociadas_preview:
            usuario_id = item["usuario_id"]
            mes = item["mes"]
            año = item["año"]
            ya_existe = item.get("ya_existe", False)

            try:
                usuario = User.objects.get(pk=usuario_id)
            except User.DoesNotExist:
                item_error = {
                    "archivo": item["archivo"],
                    "pagina": item["pagina"],
                    "rut": item["rut"],
                    "nombre": item["nombre"],
                    "mes": mes,
                    "año": año,
                    "motivo": "El usuario ya no existe.",
                }
                no_asociadas.append(item_error)
                continue

            # Si ya existía y el modo es "solo_nuevas", no reemplazamos
            if ya_existe and modo == "solo_nuevas":
                no_asociadas.append({
                    "archivo": item["archivo"],
                    "pagina": item["pagina"],
                    "rut": item["rut"],
                    "nombre": item["nombre"],
                    "mes": mes,
                    "año": año,
                    "motivo": "Ya existía liquidación para este trabajador y mes/año. Se mantuvo la anterior.",
                })
                continue

            pdf_bytes = base64.b64decode(item["pdf_base64"])
            filename = item["filename"]
            content_file = ContentFile(pdf_bytes, name=filename)

            liquidacion, creada = Liquidacion.objects.update_or_create(
                tecnico=usuario,
                mes=mes,
                año=año,
                defaults={
                    "archivo_pdf_liquidacion": content_file,
                    "pdf_firmado": None,
                    "firmada": False,
                    "fecha_firma": None,
                },
            )

            nombre_mes = MESES_ESPANOL.get(int(mes), "Mes inválido")

            crear_notificacion(
                usuario=usuario,
                mensaje=(
                    f"Se ha cargado una nueva liquidación "
                    f"correspondiente a {nombre_mes} de {año}."
                ),
                tipo="info",
                url=reverse('liquidaciones:listar'),
            )

            asociadas.append({
                "archivo": item["archivo"],
                "pagina": item["pagina"],
                "rut": item["rut"],
                "nombre": item["nombre"],
                "usuario": usuario,
                "mes": mes,
                "año": año,
                "ya_existe": ya_existe,
                "liquidacion": liquidacion,
                "creada": creada,
            })

        # limpiar sesión
        try:
            del request.session["carga_liq_preview"]
        except KeyError:
            pass

        resumen = {
            "total_archivos": total_archivos,
            "total_paginas": total_paginas,
            "total_asociadas": len(asociadas),
            "total_no_asociadas": len(no_asociadas),
            "duplicados": duplicados,
            "confirmado": True,
        }

        messages.success(request, "Las liquidaciones se cargaron correctamente.")

        form = CargaMasivaLiquidacionesForm()

        return render(
            request,
            "liquidaciones/carga_masiva_liquidaciones.html",
            {
                "form": form,
                "resumen": resumen,
                "asociadas": asociadas,
                "no_asociadas": no_asociadas,
            },
        )

    # --------- Paso 1: Preview ---------
    if request.method == 'POST' and request.POST.get("confirmar") != "1":
        form = CargaMasivaLiquidacionesForm(request.POST)

        # 🔹 Archivos directamente desde request.FILES
        archivos = request.FILES.getlist('archivos')

        # Validación manual de archivos
        if not archivos:
            form.add_error(None, "Debes subir al menos un archivo PDF.")
        else:
            for archivo in archivos:
                nombre = archivo.name
                content_type = (archivo.content_type or "").lower()
                if not nombre.lower().endswith('.pdf') or content_type not in ("application/pdf", "application/x-pdf"):
                    form.add_error(
                        None,
                        f"El archivo '{nombre}' no es un PDF válido."
                    )
                    break

        if form.is_valid():
            # mapa de usuarios por clave de RUT
            usuarios = User.objects.exclude(identidad__isnull=True).exclude(identidad__exact="")
            usuarios_por_rut = {}
            for u in usuarios:
                clave = rut_clave(u.identidad)
                if clave:
                    usuarios_por_rut.setdefault(clave, u)

            total_archivos = len(archivos)
            total_paginas = 0
            duplicados = 0

            asociadas = []
            no_asociadas = []

            asociadas_session = []
            no_asociadas_session = []

            # cache de liquidaciones existentes para no consultar mil veces
            existentes_cache = {}

            for archivo in archivos:
                resultados = extraer_paginas_liquidaciones(archivo)

                for item in resultados:
                    total_paginas += 1

                    mes = item.get("mes")
                    anio = item.get("anio")

                    # Error al leer o sin RUT
                    if not item["ok"] or not item["rut"]:
                        fila = {
                            "archivo": archivo.name,
                            "pagina": item["pagina"],
                            "rut": item["rut"],
                            "nombre": item["nombre"],
                            "mes": mes,
                            "año": anio,
                            "motivo": item["motivo"] or "Error al leer la página.",
                        }
                        no_asociadas.append(fila)
                        no_asociadas_session.append(fila)
                        continue

                    # Sin mes/año detectado
                    if not mes or not anio:
                        fila = {
                            "archivo": archivo.name,
                            "pagina": item["pagina"],
                            "rut": item["rut"],
                            "nombre": item["nombre"],
                            "mes": mes,
                            "año": anio,
                            "motivo": "No se pudo detectar mes y año en la página.",
                        }
                        no_asociadas.append(fila)
                        no_asociadas_session.append(fila)
                        continue

                    rut = item["rut"]
                    nombre = item["nombre"]
                    clave_pdf = rut_clave(rut)

                    usuario = usuarios_por_rut.get(clave_pdf)

                    if not usuario:
                        fila = {
                            "archivo": archivo.name,
                            "pagina": item["pagina"],
                            "rut": rut,
                            "nombre": nombre,
                            "mes": mes,
                            "año": anio,
                            "motivo": "No existe un usuario con este RUT (identidad).",
                        }
                        no_asociadas.append(fila)
                        no_asociadas_session.append(fila)
                        continue

                    # Ver si ya existe liquidación para este técnico+mes+año
                    clave_existente = (usuario.id, anio, mes)
                    existente = existentes_cache.get(clave_existente)
                    if existente is None:
                        existente = Liquidacion.objects.filter(
                            tecnico=usuario, año=anio, mes=mes
                        ).first()
                        existentes_cache[clave_existente] = existente

                    ya_existe = existente is not None
                    if ya_existe:
                        duplicados += 1

                    # índice dentro del listado de asociadas de la sesión
                    preview_index = len(asociadas_session)

                    # Para mostrar en el preview (todavía sin liquidación)
                    asociadas.append({
                        "archivo": archivo.name,
                        "pagina": item["pagina"],
                        "rut": rut,
                        "nombre": nombre,
                        "usuario": usuario,
                        "mes": mes,
                        "año": anio,
                        "ya_existe": ya_existe,
                        "liquidacion": None,      # se llenará en el paso 2
                        "preview_index": preview_index,  # para ver el PDF
                    })

                    # Para guardar en sesión (datos necesarios para guardar luego)
                    filename = f"{rut.replace('-', '')}-liquidacion-{anio}-{mes}.pdf"
                    pdf_b64 = base64.b64encode(item["pdf_bytes"]).decode("ascii")

                    asociadas_session.append({
                        "archivo": archivo.name,
                        "pagina": item["pagina"],
                        "rut": rut,
                        "nombre": nombre,
                        "usuario_id": usuario.id,
                        "mes": mes,
                        "año": anio,
                        "ya_existe": ya_existe,
                        "filename": filename,
                        "pdf_base64": pdf_b64,
                    })

            resumen = {
                "total_archivos": total_archivos,
                "total_paginas": total_paginas,
                "total_asociadas": len(asociadas),
                "total_no_asociadas": len(no_asociadas),
                "duplicados": duplicados,
                "confirmado": False,
            }

            request.session["carga_liq_preview"] = {
                "total_archivos": total_archivos,
                "total_paginas": total_paginas,
                "duplicados": duplicados,
                "asociadas": asociadas_session,
                "no_asociadas": no_asociadas_session,
            }

            messages.info(
                request,
                "Revisa el resumen. Si estás conforme, presiona 'Cargar' para guardar las liquidaciones."
            )
        else:
            messages.error(request, "Revisa el formulario, hay errores en los datos enviados.")

    contexto = {
        "form": form,
        "resumen": resumen,
        "asociadas": asociadas,
        "no_asociadas": no_asociadas,
    }
    return render(request, "liquidaciones/carga_masiva_liquidaciones.html", contexto)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def preview_pdf_carga_masiva(request, index: int):
    """
    Devuelve el PDF de una página de liquidación que está en el preview
    (almacenado en request.session["carga_liq_preview"]).
    """
    preview = request.session.get("carga_liq_preview")
    if not preview:
        raise Http404("No hay una carga masiva en sesión.")

    try:
        index = int(index)
        item = preview["asociadas"][index]
    except (ValueError, KeyError, IndexError, TypeError):
        raise Http404("PDF de preview no encontrado.")

    pdf_bytes = base64.b64decode(item["pdf_base64"])
    filename = item.get("filename", "liquidacion.pdf")

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response




@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def crear_liquidacion(request):

    if request.method == 'POST':
        form = LiquidacionForm(request.POST, request.FILES)
        if form.is_valid():
            # fields=(tecnico;mes;año;monto;archivo_pdf_liquidacion;pdf_firmado;fecha_firma;firmada)
            liquidacion = form.save()

            crear_notificacion(
                usuario=liquidacion.tecnico,
                mensaje="Tienes una nueva liquidación pendiente por firmar.",
                # Ajusta si tu URL se llama diferente
                url=reverse('liquidaciones:listar'),
                tipo='info'
            )

            messages.success(request, "Liquidación creada con éxito.")
            # return redirect('liquidaciones:lista_liquidaciones')
            return redirect('liquidaciones:admin_lista')
        else:
            print("🔴 Errores en el formulario:", form.errors)
    else:
        form = LiquidacionForm()

    return render(request, 'liquidaciones/crear_liquidacion.html', {'form': form})


@method_decorator(login_required, name='dispatch')
class UsuarioAutocomplete(AutoResponseView):
    def get_queryset(self):
        qs = User.objects.filter(is_active=True)
        if self.q:
            qs = qs.filter(
                Q(identidad__icontains=self.q) |
                Q(first_name__icontains=self.q) |
                Q(last_name__icontains=self.q)
            )
        return qs

    def get_result_label(self, item):
        return f"{item.identidad} - {item.first_name} {item.last_name}"

    def get_result_value(self, item):
        return str(item.pk)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)

    if request.method == "POST":
        liquidacion.delete()
        messages.success(request, "Liquidación eliminada correctamente.")
        return redirect("liquidaciones:admin_lista")

    return render(request, "liquidaciones/eliminar_confirmacion.html", {"liquidacion": liquidacion})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def editar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)
    antigua_ruta_pdf = (
        liquidacion.archivo_pdf_liquidacion.name if liquidacion.archivo_pdf_liquidacion else None
    )

    if request.method == 'POST':
        form = LiquidacionForm(
            request.POST, request.FILES, instance=liquidacion)
        if form.is_valid():
            nueva_ruta_pdf = request.FILES.get('archivo_pdf_liquidacion')

            # 🧠 Si se reemplaza el archivo, eliminamos la firma anterior
            if nueva_ruta_pdf and nueva_ruta_pdf.name != antigua_ruta_pdf:
                if liquidacion.pdf_firmado and default_storage.exists(liquidacion.pdf_firmado.name):
                    default_storage.delete(liquidacion.pdf_firmado.name)
                    messages.info(
                        request, "La firma anterior fue eliminada porque se reemplazó la liquidación.")

                liquidacion.pdf_firmado = None
                liquidacion.fecha_firma = None
                liquidacion.firmada = False

            form.save()
            messages.success(
                request, '✅ Liquidación actualizada correctamente.')
            return redirect('liquidaciones:admin_lista')
        else:
            messages.error(
                request, '❌ Hubo errores al actualizar la liquidación. Revisa los campos.')
    else:
        form = LiquidacionForm(instance=liquidacion)

    return render(request, 'liquidaciones/editar_liquidacion.html', {
        'form': form,
        'liquidacion': liquidacion,
    })


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def ver_pdf_firmado_admin(request, pk):
    try:
        liquidacion = Liquidacion.objects.get(pk=pk)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe.")
        return redirect('liquidaciones:admin_lista')

    archivo = liquidacion.pdf_firmado

    if archivo and archivo.name:
        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}.pdf"

            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf'
            )
        except Exception as e:
            logger.error(
                f"[ver_pdf_firmado_admin] Error al abrir PDF firmado: {e}")
            messages.error(request, "No se pudo abrir el archivo firmado.")
            return redirect('liquidaciones:admin_lista')
    else:
        messages.error(request, "El archivo firmado ya no está disponible.")
        return redirect('liquidaciones:admin_lista')


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def ver_pdf_admin(request, pk):
    try:
        liquidacion = Liquidacion.objects.get(pk=pk)
    except Liquidacion.DoesNotExist:
        messages.error(request, "La liquidación no existe.")
        return redirect('liquidaciones:admin_lista')

    archivo = liquidacion.archivo_pdf_liquidacion

    if archivo and archivo.name:
        try:
            apellido = liquidacion.tecnico.last_name or "tecnico"
            año = liquidacion.año
            mes = f"{liquidacion.mes:02d}"
            nombre_archivo = f"liquidacion_{apellido}_{año}_{mes}_sin_firma.pdf"

            return FileResponse(
                archivo.open('rb'),
                content_type='application/pdf'
            )
        except Exception as e:
            logger.error(f"[ver_pdf_admin] Error al abrir archivo PDF: {e}")
            messages.error(request, "No se pudo abrir el archivo.")
            return redirect('liquidaciones:admin_lista')
    else:
        messages.error(request, "El archivo ya no está disponible.")
        return redirect('liquidaciones:admin_lista')


def verificar_storage(request):
    return JsonResponse({
        "USE_CLOUDINARY": getattr(settings, 'USE_CLOUDINARY', False),
        "STORAGE_BACKEND": getattr(settings, 'DEFAULT_FILE_STORAGE', 'No definido')
    })

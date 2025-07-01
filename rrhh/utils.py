from .models import Feriado
from datetime import timedelta
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import io
import os
import logging
import tempfile
import requests
from datetime import date, timedelta

from django.utils.html import strip_tags
from django.utils.text import slugify
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from cloudinary.uploader import upload, destroy

from PIL import Image as PilImage, UnidentifiedImageError

from PyPDF2 import PdfReader, PdfWriter

from reportlab.lib import colors
from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from rrhh.models import FichaIngreso, Feriado
from .models import SolicitudVacaciones

from PIL import Image as PilImage, UnidentifiedImageError
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle
from django.utils.text import slugify
from reportlab.platypus import SimpleDocTemplate
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from reportlab.lib.units import cm
from django.core.files.base import ContentFile
import io


def contar_dias_habiles(inicio, fin):
    dias_habiles = 0
    delta = fin - inicio
    for i in range(delta.days + 1):
        dia = inicio + timedelta(days=i)
        if dia.weekday() < 5 and not Feriado.objects.filter(fecha=dia).exists():
            dias_habiles += 1
    return dias_habiles


def calcular_estado_documento(fecha_vencimiento):
    """
    Calcula el estado de un documento según su fecha de vencimiento.

    - 'válido': si la fecha de vencimiento es futura y queda más de 15 días.
    - 'por_vencer': si quedan 15 días o menos.
    - 'vencido': si ya expiró.
    """
    hoy = date.today()

    if fecha_vencimiento < hoy:
        return 'vencido'
    elif fecha_vencimiento <= hoy + timedelta(days=15):
        return 'por_vencer'
    else:
        return 'vigente'


def descargar_firma_desde_url(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type:
            print(f"⚠️ La URL no contiene una imagen válida: {content_type}")
            return None
        return io.BytesIO(response.content)
    except Exception as e:
        print(f"⚠️ Error al descargar firma desde {url}: {e}")
        return None


def generar_pdf_solicitud_vacaciones(solicitud):
    print("📝 Generando PDF para solicitud ID:", solicitud.id)

    usuario = solicitud.usuario
    dias_disponibles = usuario.obtener_dias_vacaciones_disponibles()
    tipo_real = 'total' if float(solicitud.dias_solicitados) == float(
        dias_disponibles) else 'parcial'
    identidad = usuario.identidad or str(usuario.pk)
    pm = solicitud.aprobado_por_pm
    rrhh = solicitud.aprobado_por_rrhh

    # Firmas desde Cloudinary
    firma_trabajador = descargar_firma_desde_url(
        usuario.firma_digital.url) if usuario.firma_digital else None
    firma_pm = descargar_firma_desde_url(
        pm.firma_digital.url) if pm and pm.firma_digital else None
    firma_rrhh = descargar_firma_desde_url(
        rrhh.firma_digital.url) if rrhh and rrhh.firma_digital else None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    # Estilos personalizados
    titulo_style = ParagraphStyle(
        name='TituloCentrado',
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        spaceAfter=16,
        textColor=colors.black,
        fontName='Helvetica-Bold',
    )

    subtitulo_style = ParagraphStyle(
        name='SubtituloCentrado',
        alignment=TA_CENTER,
        fontSize=13,
        leading=16,
        spaceAfter=24,
        textColor=colors.black,
        fontName='Helvetica-Bold',
    )

    estilo_fila = ParagraphStyle(
        name='EstiloFila',
        fontName='Helvetica',
        fontSize=10,
        leading=14,
    )

    elements = []
    elements.append(
        Paragraph("FORMULARIO SOLICITUD DE VACACIONES LEGALES", titulo_style))
    elements.append(
        Paragraph("GZ SERVICES AND BUSINESS SPA", subtitulo_style))

    # Datos principales
    datos = [
        Paragraph(f'<b>NOMBRE:</b> {usuario.get_full_name()}', estilo_fila),
        Paragraph(f'<b>RUT:</b> {usuario.identidad}', estilo_fila),
        Paragraph(f'<b>CARGO:</b> Pendiente de ficha', estilo_fila),
        Paragraph(
            f'<b>PERIODO:</b> {solicitud.fecha_inicio.strftime("%d-%m-%Y")} al {solicitud.fecha_fin.strftime("%d-%m-%Y")}', estilo_fila),
        Paragraph(
            f'<b>TIPO:</b> {"Total" if tipo_real == "total" else "Parcial"}', estilo_fila),
        Paragraph(
            f'<b>DÍAS HÁBILES:</b> {solicitud.dias_solicitados}', estilo_fila),
    ]

    tabla_datos = Table([[d] for d in datos], colWidths=[16.5 * cm])
    tabla_datos.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(tabla_datos)
    elements.append(Spacer(1, 20))

    texto = f"""
    <b>El trabajador hará uso {"Total" if tipo_real == "total" else "Parcial"}</b> de sus vacaciones legales,
    desde el día <b>{solicitud.fecha_inicio.strftime('%d-%m-%Y')}</b> hasta el <b>{solicitud.fecha_fin.strftime('%d-%m-%Y')}</b>.<br/><br/>
    Total de días <b>hábiles</b> tomados: <b>{solicitud.dias_solicitados}</b>.<br/><br/>
    Fecha de la Solicitud: <b>{solicitud.fecha_solicitud.strftime('%d-%m-%Y')}</b>
    """
    elements.append(Paragraph(texto, styles['Normal']))
    elements.append(Spacer(1, 24))

    # Firmas
    encabezados = ['Firma del Trabajador',
                   'Jefe Directo', 'V° B° Recursos Humanos']
    firmas = [
        Image(firma_trabajador, width=4 * cm, height=2 *
              cm) if firma_trabajador else Spacer(4 * cm, 2 * cm),
        Image(firma_pm, width=4 * cm, height=2 *
              cm) if firma_pm else Spacer(4 * cm, 2 * cm),
        Image(firma_rrhh, width=4 * cm, height=2 *
              cm) if firma_rrhh else Spacer(4 * cm, 2 * cm),
    ]
    tabla_firmas = Table([encabezados, firmas], colWidths=[5.5 * cm] * 3)
    tabla_firmas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, 1), 4),
    ]))
    elements.append(tabla_firmas)

    # Guardar PDF en buffer
    doc.build(elements)

    nombre_archivo = "Solicitud de vacaciones.pdf"
    pdf_content = ContentFile(buffer.getvalue())

    # Reemplaza si ya existía
    if solicitud.archivo_pdf and solicitud.archivo_pdf.name:
        solicitud.archivo_pdf.delete(save=False)

    solicitud.archivo_pdf.save(nombre_archivo, pdf_content, save=True)
    print("✅ PDF generado y guardado como:", nombre_archivo)


def generar_ficha_ingreso_pdf(ficha):
    print("📝 Generando PDF para ficha ID:", ficha.id)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    titulo_style = ParagraphStyle(name='Titulo', alignment=1, fontSize=16, leading=20,
                                  spaceAfter=10, textColor=colors.black, fontName='Helvetica-Bold')
    subtitulo_style = ParagraphStyle(name='Subtitulo', alignment=1, fontSize=13, leading=16,
                                     spaceAfter=20, textColor=colors.black, fontName='Helvetica-Bold')
    texto_normal = ParagraphStyle(name='TextoNormal', fontName='Helvetica', fontSize=10,
                                  leading=14, spaceAfter=5)

    elements = []

    # Título y subtítulo
    elements.append(
        Paragraph("FICHA DE INGRESO DE PERSONAL NUEVO", titulo_style))
    elements.append(
        Paragraph("GZ SERVICES AND BUSINESS SPA", subtitulo_style))

    datos = [

        Paragraph(
            f"<b>Nombre Completo:</b> {ficha.nombres} {ficha.apellidos}", texto_normal),
        Paragraph(f"<b>RUT/Pasaporte:</b> {ficha.rut}", texto_normal),
        Paragraph(
            f"<b>Fecha de Nacimiento:</b> {ficha.fecha_nacimiento}", texto_normal),
        Paragraph(f"<b>Edad:</b> {ficha.edad}", texto_normal),
        Paragraph(f"<b>Sexo:</b> {ficha.sexo}", texto_normal),
        Paragraph(f"<b>Estado Civil:</b> {ficha.estado_civil}", texto_normal),
        Paragraph(f"<b>Nacionalidad:</b> {ficha.nacionalidad}", texto_normal),
        Paragraph(f"<b>Teléfono:</b> {ficha.telefono}", texto_normal),
        Paragraph(f"<b>Correo Electrónico:</b> {ficha.email}", texto_normal),
        Paragraph(f"<b>Dirección:</b> {ficha.direccion}", texto_normal),
        Paragraph(f"<b>Comuna:</b> {ficha.comuna}", texto_normal),
        # Paragraph(f"<b>Ciudad:</b> {ficha.ciudad}", texto_normal),
        Paragraph(f"<b>Región:</b> {ficha.region}", texto_normal),
        Paragraph(
            f"<b>Nivel de estudios:</b> {ficha.nivel_estudios}", texto_normal),
        Paragraph(
            f"<b>Profesión u Oficio:</b> {ficha.profesion_u_oficio}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>DATOS BANCARIOS</b>", subtitulo_style),
        Paragraph(f"<b>Banco:</b> {ficha.banco}", texto_normal),
        Paragraph(f"<b>Tipo de cuenta:</b> {ficha.tipo_cuenta}", texto_normal),
        Paragraph(
            f"<b>Número de cuenta:</b> {ficha.numero_cuenta}", texto_normal),
        Paragraph(f"<b>Banco 2:</b> {ficha.banco_2}", texto_normal),
        Paragraph(
            f"<b>Tipo de cuenta 2:</b> {ficha.tipo_cuenta_2}", texto_normal),
        Paragraph(
            f"<b>Número de cuenta 2:</b> {ficha.numero_cuenta_2}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>DATOS LABORALES</b>", subtitulo_style),
        Paragraph(
            f"<b>Fecha de Ingreso:</b> {ficha.fecha_inicio}", texto_normal),
        Paragraph(f"<b>Cargo:</b> {ficha.cargo}", texto_normal),
        Paragraph(f"<b>Jefe Directo:</b> {ficha.jefe_directo}", texto_normal),
        # Paragraph(f"<b>Departamento:</b> {ficha.departamento}", texto_normal),
        Paragraph(f"<b>Proyecto:</b> {ficha.proyecto}", texto_normal),
        Paragraph(
            f"<b>Tipo de Contrato:</b> {ficha.tipo_contrato}", texto_normal),
        Paragraph(f"<b>Jornada:</b> {ficha.jornada}", texto_normal),
        Paragraph(
            f"<b>Horario de Trabajo:</b> {ficha.horario_trabajo}", texto_normal),
        Paragraph(f"<b>Sueldo Base:</b> {ficha.sueldo_base}", texto_normal),
        # Paragraph(f"<b>Sueldo Líquido:</b> {ficha.sueldo_liquido}", texto_normal),
        Paragraph(f"<b>Bono:</b> {ficha.bono}", texto_normal),
        Paragraph(f"<b>Colación:</b> {ficha.colacion}", texto_normal),
        Paragraph(f"<b>Movilización:</b> {ficha.movilizacion}", texto_normal),
        Paragraph(
            f"<b>Observaciones:</b> {ficha.observaciones}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>PREVISIÓN</b>", subtitulo_style),
        Paragraph(f"<b>AFP:</b> {ficha.afp}", texto_normal),
        Paragraph(f"<b>Salud:</b> {ficha.salud}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>CONTACTO DE EMERGENCIA</b>", subtitulo_style),
        Paragraph(
            f"<b>Nombre:</b> {ficha.nombre_contacto_emergencia}", texto_normal),
        Paragraph(
            f"<b>Parentesco:</b> {ficha.parentesco_emergencia}", texto_normal),
        Paragraph(
            f"<b>Teléfono:</b> {ficha.telefono_emergencia}", texto_normal),
        Paragraph(
            f"<b>Dirección:</b> {ficha.direccion_emergencia}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>TALLAS</b>", subtitulo_style),
        Paragraph(f"<b>Polera:</b> {ficha.talla_polera}", texto_normal),
        Paragraph(f"<b>Pantalón:</b> {ficha.talla_pantalon}", texto_normal),
        Paragraph(f"<b>Zapato:</b> {ficha.talla_zapato}", texto_normal),
    ]

    elements.extend(datos)

# 🔽 Espacio reservado para firmas (alto ajustable)
    elements.append(Spacer(1, 100))

    doc.build(elements)

    buffer.seek(0)

    # Eliminar archivo anterior si existe
    if ficha.archivo:
        ficha.archivo.delete(save=False)

    # Guardar en la ruta definida por el modelo (upload_to=ruta_ficha_ingreso)
    ficha.archivo.save("ficha_ingreso.pdf",
                       ContentFile(buffer.read()), save=True)
    ficha.save()

    print("✅ Ficha guardada en Cloudinary:", ficha.archivo.url)


def firmar_ficha_ingreso_pdf(ficha):
    # 1. Descargar PDF original desde Cloudinary
    response = requests.get(ficha.archivo.url)
    if response.status_code != 200:
        raise Exception("No se pudo descargar el PDF original")

    original_pdf = io.BytesIO(response.content)

    # 2. Crear capa con firmas
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)

    # Posiciones en la parte baja (asumiendo espacio libre)
    y_firma = 100
    x_rrhh = 80
    x_pm = 250
    x_trabajador = 420

    def insertar_firma(usuario, x, y):
        if usuario and usuario.firma_digital:
            try:
                r = requests.get(usuario.firma_digital.url)
                if r.status_code == 200:
                    firma_img = ImageReader(io.BytesIO(r.content))
                    can.drawImage(firma_img, x, y, width=100,
                                  height=40, mask='auto')
                    can.setFont("Helvetica", 8)
                    can.drawCentredString(
                        x + 50, y - 12, usuario.get_full_name())
            except:
                pass

    insertar_firma(ficha.creado_por, x_rrhh, y_firma)      # RRHH
    insertar_firma(ficha.pm, x_pm, y_firma)                # PM
    insertar_firma(ficha.usuario, x_trabajador, y_firma)   # Trabajador

    # Etiquetas
    can.setFont("Helvetica-Bold", 9)
    can.drawCentredString(x_rrhh + 50, y_firma - 28, "RRHH")
    can.drawCentredString(x_pm + 50, y_firma - 28, "PM")
    can.drawCentredString(x_trabajador + 50, y_firma - 28, "Trabajador")

    can.save()
    packet.seek(0)

    # 3. Fusionar con la última página del PDF original
    original_reader = PdfReader(original_pdf)
    overlay_reader = PdfReader(packet)
    writer = PdfWriter()

    for i, page in enumerate(original_reader.pages):
        if i == len(original_reader.pages) - 1:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    final_output = io.BytesIO()
    writer.write(final_output)
    final_output.seek(0)

    # 4. Guardar en Cloudinary
    if ficha.archivo:
        ficha.archivo.delete(save=False)

    if ficha.usuario and ficha.usuario.identidad:
        identidad = ficha.usuario.identidad.replace('.', '').replace('-', '')
    else:
        identidad = slugify(ficha.rut or f"ficha_{ficha.pk}")

    ruta_final = f"fichas_de_ingreso/{identidad}/Ficha_ingreso.pdf"
    contenido = ContentFile(final_output.read())
    ficha.archivo.save(ruta_final, contenido, save=True)


def calcular_monto_disponible(ficha):
    if not ficha or not ficha.sueldo_base or not ficha.fecha_inicio:
        return 0
    return round(ficha.sueldo_base * 0.5, 2)  # 50% del sueldo base mensual


def generar_pdf_solicitud_adelanto(solicitud):
    print("📝 Generando PDF para adelanto ID:", solicitud.id)

    usuario = solicitud.trabajador
    pm = solicitud.aprobado_por_pm
    rrhh = solicitud.aprobado_por_rrhh

    firma_trabajador = descargar_firma_desde_url(
        usuario.firma_digital.url) if usuario.firma_digital else None
    firma_pm = descargar_firma_desde_url(
        pm.firma_digital.url) if pm and pm.firma_digital else None
    firma_rrhh = descargar_firma_desde_url(
        rrhh.firma_digital.url) if rrhh and rrhh.firma_digital else None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    # Estilos
    titulo_style = ParagraphStyle(name='Titulo', alignment=TA_CENTER, fontSize=18,
                                  leading=22, spaceAfter=16, fontName='Helvetica-Bold')
    subtitulo_style = ParagraphStyle(name='Subtitulo', alignment=TA_CENTER, fontSize=13,
                                     leading=16, spaceAfter=24, fontName='Helvetica-Bold')
    fila_style = ParagraphStyle(
        name='Fila', fontName='Helvetica', fontSize=10, leading=14)
    normal_center = ParagraphStyle(
        name='NormalCenter', fontSize=10, alignment=TA_CENTER)

    elements = [
        Paragraph("FORMULARIO SOLICITUD DE ADELANTO", titulo_style),
        Paragraph("GZ SERVICES AND BUSINESS SPA", subtitulo_style)
    ]

    datos = [
        Paragraph(f'<b>NOMBRE:</b> {usuario.get_full_name()}', fila_style),
        Paragraph(f'<b>RUT:</b> {usuario.identidad}', fila_style),
        Paragraph(
            f'<b>FECHA DE SOLICITUD:</b> {solicitud.fecha_solicitud.strftime("%d-%m-%Y")}', fila_style),
        Paragraph(
            f'<b>MONTO SOLICITADO:</b> ${solicitud.monto_solicitado:,.0f}', fila_style),
        Paragraph(
            f'<b>MONTO APROBADO:</b> ${solicitud.monto_aprobado:,.0f}' if solicitud.monto_aprobado else '', fila_style)
    ]

    tabla = Table([[d] for d in datos], colWidths=[16.5 * cm])
    tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(tabla)
    elements.append(Spacer(1, 24))

    # Firmas
    encabezados = ['Firma del Trabajador',
                   'Jefe Directo', 'V° B° Recursos Humanos']
    firmas = [
        Image(firma_trabajador, width=4 * cm, height=2 *
              cm) if firma_trabajador else Spacer(4 * cm, 2 * cm),
        Image(firma_pm, width=4 * cm, height=2 *
              cm) if firma_pm else Spacer(4 * cm, 2 * cm),
        Image(firma_rrhh, width=4 * cm, height=2 *
              cm) if firma_rrhh else Spacer(4 * cm, 2 * cm),
    ]
    nombres = [
        Paragraph(usuario.get_full_name(), normal_center),
        Paragraph(pm.get_full_name() if pm else "No disponible", normal_center),
        Paragraph(rrhh.get_full_name(), normal_center),
    ]

    tabla_firmas = Table([encabezados, firmas, nombres],
                         colWidths=[5.5 * cm] * 3)
    tabla_firmas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, 1), 4),
        ('TOPPADDING', (0, 2), (-1, 2), 6),
    ]))
    elements.append(tabla_firmas)

    # Guardar
    doc.build(elements)
    nombre_archivo = f"Solicitud de adelanto.pdf"
    content = ContentFile(buffer.getvalue())

    # Eliminar anterior
    if solicitud.planilla_pdf:
        solicitud.planilla_pdf.delete(save=False)

    solicitud.planilla_pdf.save(nombre_archivo, content, save=True)
    print("✅ Planilla de adelanto guardada:", nombre_archivo)


def calcular_dias_habiles(inicio, fin):
    if not inicio or not fin:
        return 0

    feriados = set(Feriado.objects.values_list('fecha', flat=True))
    dias_habiles = 0
    dia_actual = inicio

    while dia_actual <= fin:
        if dia_actual.weekday() < 5 and dia_actual not in feriados:
            dias_habiles += 1
        dia_actual += timedelta(days=1)

    return dias_habiles

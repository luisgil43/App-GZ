from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from io import BytesIO

def generar_pdf_guia_despacho(salida):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=LETTER)
    width, height = LETTER

    # Encabezado
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, "Guía de Despacho Electrónica")

    p.setFont("Helvetica", 12)
    p.drawString(50, height - 90, f"Fecha: {salida.fecha_salida.strftime('%d/%m/%Y')}")
    p.drawString(50, height - 110, f"Número de Documento: {salida.numero_documento}")
    p.drawString(50, height - 130, f"Tipo de Documento: {salida.get_tipo_documento_display()}")
    p.drawString(50, height - 150, f"Centro de Costo / Proyecto: {salida.proyecto.codigo}")
    p.drawString(50, height - 170, f"Entregado a: {salida.entregado_a.get_full_name() if salida.entregado_a else '-'}")
    p.drawString(50, height - 190, f"Emitido por: {salida.emitido_por.get_full_name() if salida.emitido_por else '-'}")
    p.drawString(50, height - 210, f"Obra: {salida.obra or '-'}")
    p.drawString(50, height - 230, f"Chofer: {salida.chofer or '-'}")
    p.drawString(50, height - 250, f"Patente: {salida.patente or '-'}")

    # Observaciones
    if salida.observaciones:
        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, height - 290, "Observaciones:")
        p.setFont("Helvetica", 12)
        p.drawString(50, height - 310, salida.observaciones)

    p.showPage()
    p.save()

    pdf = buffer.getvalue()
    buffer.close()
    return pdf

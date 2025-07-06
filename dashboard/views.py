import uuid
import base64
from django.core.files.base import ContentFile
from rrhh.utils import generar_ficha_ingreso_pdf
from rrhh.models import FichaIngreso
from usuarios.decoradores import rol_requerido
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth import logout
from weasyprint import HTML
from django.db.models import Sum
from django.utils import timezone
from datetime import date
import os
import tempfile
from django.contrib.auth import authenticate, login

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from dashboard.models import ProduccionTecnico
from usuarios.models import CustomUser
from usuarios.models import Notificacion


@login_required
def inicio(request):
    notificaciones = Notificacion.objects.filter(
        usuario=request.user
    ).order_by('leido', '-fecha')[:10]

    return render(request, 'dashboard/inicio.html', {
        'notificaciones': notificaciones
    })


@login_required
def dashboard_view(request):
    usuario = request.user
    producciones = ProduccionTecnico.objects.filter(tecnico=usuario)
    cursos = usuario.cursos.filter(
        activo=True) if hasattr(usuario, 'cursos') else []

    return render(request, 'dashboard/inicio.html', {
        'producciones': producciones,
        'cursos': cursos,
    })


@login_required
def mis_cursos_view(request):
    usuario = request.user
    cursos = usuario.cursos.all() if hasattr(usuario, 'cursos') else []

    return render(request, 'dashboard/mis_cursos.html', {
        'cursos': cursos,
        'tecnico': usuario,
        'today': date.today(),
    })


@login_required
def dashboard_detalle_view(request, produccion_id):
    produccion = get_object_or_404(
        ProduccionTecnico, id=produccion_id, tecnico=request.user
    )
    return render(request, 'dashboard/detalle.html', {'produccion': produccion})


@login_required
def produccion_tecnicos_pdf(request):
    usuario = request.user
    produccion = ProduccionTecnico.objects.filter(
        tecnico=usuario).order_by('fecha_aprobacion')
    total_monto = produccion.aggregate(total=Sum('monto'))['total'] or 0

    html_string = render_to_string('dashboard/produccion_pdf.html', {
        'user': usuario,
        'tecnico': usuario,
        'produccion': produccion,
        'total_monto': total_monto,
        'now': timezone.now()
    })

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        HTML(string=html_string, base_url=request.build_absolute_uri()
             ).write_pdf(tmp_file.name)
        tmp_file.seek(0)
        pdf_content = tmp_file.read()

    os.remove(tmp_file.name)

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="produccion_tecnico.pdf"'
    return response


@login_required
def produccion_tecnicos_view(request):
    producciones = ProduccionTecnico.objects.filter(tecnico=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': producciones,
    })


@login_required
def logout_view(request):
    user = request.user
    logout(request)
    if user.is_superuser:  # o podrías usar `if user.rol == 'admin'` si agregas ese campo
        return redirect('/admin/login/')
    return redirect('usuarios:login')


@login_required
def inicio_tecnico(request):
    return render(request, 'dashboard/inicio.html')


@login_required
def produccion_tecnico(request):
    return render(request, 'dashboard_admin/produccion_tecnico.html')


@login_required
def registrar_firma_usuario(request):
    user = request.user

    if user.firma_digital:
        return render(request, 'liquidaciones/firmar.html', {
            'tecnico': user,
            'solo_lectura': True
        })

    if request.method == 'POST':
        firma_data = request.POST.get('firma_digital')
        if firma_data:
            formato, imgstr = firma_data.split(';base64,')
            nombre_archivo = f"usuario_{user.id}_firma.png"
            data = ContentFile(base64.b64decode(imgstr), name=nombre_archivo)
            user.firma_digital.save(nombre_archivo, data)
            user.save()
            messages.success(request, "Firma registrada correctamente.")
            return redirect('dashboard:registrar_firma_usuario')
        else:
            messages.error(
                request, "No se recibió la firma. Intenta nuevamente.")

    return render(request, 'liquidaciones/firmar.html', {
        'tecnico': user,
        'solo_lectura': False
    })


def index(request):
    return render(request, 'dashboard/index.html')

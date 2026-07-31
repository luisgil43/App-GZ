import base64
import os
import tempfile
import uuid
from datetime import date

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from dashboard.models import ProduccionTecnico
from operaciones.models import ServicioCotizado, SesionFotos, SesionFotoTecnico
from rrhh.models import FichaIngreso
from rrhh.utils import generar_ficha_ingreso_pdf
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser, Notificacion


@login_required(login_url="usuarios:login")
def inicio(request):
    """
    Panel principal del técnico.

    La gráfica muestra únicamente los trabajos correspondientes
    al mes de producción actual del usuario autenticado.

    Los estados se toman desde SesionFotoTecnico para mantener
    consistencia con la pantalla de actividades asignadas.
    """

    # ========================================================
    # NOTIFICACIONES
    # ========================================================

    queryset_notificaciones = Notificacion.objects.filter(
        usuario=request.user,
    ).order_by(
        "leido",
        "-fecha",
    )

    notificaciones = queryset_notificaciones[:10]

    notificaciones_no_leidas = queryset_notificaciones.filter(
        leido=False,
    ).count()

    # ========================================================
    # FECHA Y MES ACTUAL
    # ========================================================

    hoy = timezone.localdate()

    nombres_meses = {
        1: "Enero",
        2: "Febrero",
        3: "Marzo",
        4: "Abril",
        5: "Mayo",
        6: "Junio",
        7: "Julio",
        8: "Agosto",
        9: "Septiembre",
        10: "Octubre",
        11: "Noviembre",
        12: "Diciembre",
    }

    mes_actual_texto = f"{nombres_meses[hoy.month]} {hoy.year}"

    # ========================================================
    # ASIGNACIONES DEL TÉCNICO DEL MES ACTUAL
    # ========================================================

    asignaciones_tecnico = (
        SesionFotoTecnico.objects.filter(
            tecnico=request.user,
            # Limita los conteos al mes que se muestra
            # en el encabezado de la gráfica.
            sesion__servicio__mes_produccion__iexact=(mes_actual_texto),
        )
        .select_related(
            "sesion",
            "sesion__servicio",
        )
        .distinct()
    )

    # ========================================================
    # CONTEOS DEL MES ACTUAL
    # ========================================================

    sitios_en_proceso = asignaciones_tecnico.filter(
        estado="en_proceso",
    ).count()

    sitios_en_revision = asignaciones_tecnico.filter(
        estado="en_revision_supervisor",
    ).count()

    sitios_aprobados = asignaciones_tecnico.filter(
        estado="aprobado_supervisor",
    ).count()

    # ========================================================
    # CONTEXTO
    # ========================================================

    context = {
        "notificaciones": notificaciones,
        "notificaciones_no_leidas": (notificaciones_no_leidas),
        "sitios_en_proceso": sitios_en_proceso,
        "sitios_en_revision": sitios_en_revision,
        "sitios_aprobados": sitios_aprobados,
        "mes_grafica": hoy.replace(day=1),
    }

    return render(
        request,
        "dashboard/inicio.html",
        context,
    )


@login_required(login_url="usuarios:login")
def inicio_tecnico(request):
    """
    La ruta dashboard:inicio_tecnico reutiliza la vista principal para
    no renderizar dashboard/inicio.html sin los datos de la gráfica.
    """

    return inicio(request)


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

import re

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from dashboard.models import ProduccionTecnico
from rrhh.forms import FeriadoForm
from rrhh.models import Feriado
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser
from usuarios.models import CustomUser as User
from usuarios.models import Notificacion, Rol, TrustedDevice

User = get_user_model()


@login_required(login_url='usuarios:login')
def admin_dashboard_view(request):
    # Cargar datos para la plantilla principal del admin dashboard
    return render(request, 'dashboard_admin/base.html')


@login_required(login_url='usuarios:login_unificado')
def logout_view(request):
    logout(request)
    messages.info(request, "Has cerrado sesión correctamente.")
    return redirect('usuarios:login_unificado')


def inicio_admin(request):
    queryset = Notificacion.objects.filter(
        usuario=request.user).order_by('leido', '-fecha')
    notificaciones = queryset[:10]
    no_leidas = queryset.filter(leido=False).count()

    return render(request, 'dashboard_admin/inicio_admin.html', {
        'notificaciones': notificaciones,
        'notificaciones_no_leidas': no_leidas,
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'supervisor')
def produccion_tecnico(request):
    produccion = ProduccionTecnico.objects.filter(tecnico__user=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': produccion
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def grupos_view(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        grupo_id = request.POST.get('grupo_id')

        if 'add_group' in request.POST:
            if nombre:
                grupo, creado = Group.objects.get_or_create(name=nombre)
                if creado:
                    messages.success(
                        request, f'Grupo "{nombre}" creado exitosamente.')
                else:
                    messages.warning(
                        request, f'El grupo "{nombre}" ya existe.')
            else:
                messages.error(
                    request, "Debes ingresar un nombre para el grupo.")
            return redirect('dashboard_admin:grupos')

        elif 'delete_group' in request.POST and grupo_id:
            try:
                grupo = Group.objects.get(id=grupo_id)
                grupo.delete()
                messages.success(
                    request, f'Grupo "{grupo.name}" eliminado correctamente.')
            except Group.DoesNotExist:
                messages.error(request, 'El grupo no existe.')
            return redirect('dashboard_admin:grupos')

    grupos = Group.objects.all().order_by('name')
    return render(request, 'dashboard_admin/grupos.html', {'grupos': grupos})


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)
    grupos = Group.objects.all()
    roles_disponibles = Rol.objects.all()

    if request.method == 'POST':
        # --- Datos básicos ---
        usuario.username = request.POST.get('username', usuario.username)
        usuario.first_name = request.POST.get('first_name', usuario.first_name)
        usuario.last_name = request.POST.get('last_name', usuario.last_name)
        usuario.email = request.POST.get('email', usuario.email)
        usuario.is_active = 'is_active' in request.POST
        usuario.is_staff = 'is_staff' in request.POST
        usuario.is_superuser = 'is_superuser' in request.POST
        usuario.identidad = request.POST.get('identidad', usuario.identidad)

        # 🔔 Campos de notificaciones
        usuario.telegram_chat_id = request.POST.get('telegram_chat_id') or None
        usuario.telegram_activo = 'telegram_activo' in request.POST
        usuario.email_notificaciones_activo = 'email_notificaciones_activo' in request.POST

        # --- Grupos ---
        grupo_ids = request.POST.getlist('groups')
        usuario.groups.set(grupo_ids)

        # --- Roles múltiples ---
        roles_ids = request.POST.getlist('roles')
        usuario.roles.set(roles_ids)

        # --- Contraseña (opcional) ---
        password1 = (request.POST.get('password1') or '').strip()
        password2 = (request.POST.get('password2') or '').strip()
        if password1 and password2:
            if password1 != password2:
                messages.error(request, 'Las contraseñas no coinciden.')
                return render(request, 'dashboard_admin/editar_usuario.html', {
                    'usuario': usuario,
                    'grupos': grupos,
                    'roles': roles_disponibles,
                    'roles_seleccionados': set(map(int, roles_ids)),
                    'grupo_ids_post': set(map(int, grupo_ids)),
                })
            usuario.set_password(password1)

        usuario.save()
        messages.success(request, "Usuario actualizado exitosamente.")
        return redirect('dashboard_admin:listar_usuarios')

    # --- GET: precargar datos actuales ---
    roles_seleccionados = set(usuario.roles.values_list('id', flat=True))
    grupo_ids_post = set(usuario.groups.values_list('id', flat=True))

    return render(request, 'dashboard_admin/editar_usuario.html', {
        'usuario': usuario,
        'grupos': grupos,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
        'grupo_ids_post': grupo_ids_post,
        'two_factor_enforce_date': getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None),
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def crear_usuario_view(request, identidad=None):
    grupos = Group.objects.all()
    usuario = None

    if identidad:
        usuario = get_object_or_404(User, identidad=identidad)

    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        first_name = request.POST['first_name']
        last_name = request.POST['last_name']
        is_active = request.POST.get('is_active') == 'on'
        is_staff = 'is_staff' in request.POST
        is_superuser = 'is_superuser' in request.POST
        grupo_ids = [int(gid) for gid in request.POST.getlist('groups')]
        identidad_post = request.POST.get('identidad')
        roles_ids = request.POST.getlist('roles')

        # 🔔 Campos notificaciones
        telegram_chat_id = request.POST.get('telegram_chat_id') or None
        telegram_activo = 'telegram_activo' in request.POST
        email_notificaciones_activo = 'email_notificaciones_activo' in request.POST

        # Campos jerárquicos
        def get_user_or_none(uid):
            return CustomUser.objects.filter(id=uid).first() if uid else None

        supervisor = get_user_or_none(request.POST.get('supervisor'))
        pm = get_user_or_none(request.POST.get('pm'))
        rrhh_encargado = get_user_or_none(request.POST.get('rrhh_encargado'))
        prevencionista = get_user_or_none(request.POST.get('prevencionista'))
        logistica_encargado = get_user_or_none(
            request.POST.get('logistica_encargado'))
        encargado_flota = get_user_or_none(request.POST.get('encargado_flota'))
        encargado_subcontrato = get_user_or_none(
            request.POST.get('encargado_subcontrato'))
        encargado_facturacion = get_user_or_none(
            request.POST.get('encargado_facturacion'))

        # Validaciones
        if password1 or password2:
            if password1 != password2:
                messages.error(request, 'Las contraseñas no coinciden.')
                return redirect(request.path)

        if identidad_post and not re.match(r'^[A-Za-z0-9\.\-]+$', identidad_post):
            messages.error(
                request, 'La identidad solo puede contener letras, números, puntos o guiones.')
            return redirect(request.path)

        if usuario:
            # Edición desde esta vista
            usuario.username = username
            usuario.email = email
            usuario.first_name = first_name
            usuario.last_name = last_name
            usuario.is_active = is_active
            usuario.is_staff = is_staff
            usuario.is_superuser = is_superuser
            usuario.identidad = identidad_post
            usuario.groups.set(grupo_ids)
            usuario.roles.set(roles_ids)

            # Jerarquías
            usuario.supervisor = supervisor
            usuario.pm = pm
            usuario.rrhh_encargado = rrhh_encargado
            usuario.prevencionista = prevencionista
            usuario.logistica_encargado = logistica_encargado
            usuario.encargado_flota = encargado_flota
            usuario.encargado_subcontrato = encargado_subcontrato
            usuario.encargado_facturacion = encargado_facturacion

            # 🔔 Notificaciones
            usuario.telegram_chat_id = telegram_chat_id
            usuario.telegram_activo = telegram_activo
            usuario.email_notificaciones_activo = email_notificaciones_activo

            if password1:
                usuario.set_password(password1)
            usuario.save()
            messages.success(request, 'Usuario actualizado correctamente.')
        else:
            # Creación
            if User.objects.filter(username=username).exists():
                messages.error(request, 'El nombre de usuario ya existe.')
                return redirect('dashboard_admin:crear_usuario')

            if User.objects.filter(identidad=identidad_post).exists():
                messages.error(
                    request, 'El número de identidad ya está registrado.')
                return redirect('dashboard_admin:crear_usuario')

            usuario = User.objects.create_user(
                username=username,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
                identidad=identidad_post,
                supervisor=supervisor,
                pm=pm,
                rrhh_encargado=rrhh_encargado,
                prevencionista=prevencionista,
                logistica_encargado=logistica_encargado,
                encargado_flota=encargado_flota,
                encargado_subcontrato=encargado_subcontrato,
                encargado_facturacion=encargado_facturacion,
                telegram_chat_id=telegram_chat_id,
                telegram_activo=telegram_activo,
                email_notificaciones_activo=email_notificaciones_activo,
            )
            usuario.groups.set(grupo_ids)
            usuario.roles.set(roles_ids)
            messages.success(request, 'Usuario creado exitosamente.')

        return redirect('dashboard_admin:listar_usuarios')

    # Si es GET
    grupo_ids_post = request.POST.getlist(
        'groups') if request.method == 'POST' else []
    if not grupo_ids_post and usuario:
        grupo_ids_post = [str(g.id) for g in usuario.groups.all()]

    roles_disponibles = Rol.objects.all()
    roles_seleccionados = usuario.roles.values_list(
        'id', flat=True) if usuario else []
    roles_seleccionados = [str(id) for id in roles_seleccionados]

    usuarios_activos = CustomUser.objects.filter(
        is_active=True).order_by('first_name', 'last_name')

    contexto = {
        'grupos': grupos,
        'grupo_ids_post': grupo_ids_post,
        'usuario': usuario,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
        'usuarios': usuarios_activos,  # para los selects jerárquicos
        'two_factor_enforce_date': getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None),
    }
    return render(request, 'dashboard_admin/crear_usuario.html', contexto)


User = get_user_model()


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def listar_usuarios(request):
    # 🔴 Acciones por POST (eliminar / reset 2FA)
    if request.method == "POST":
        user_id = request.POST.get("user_id")

        # Eliminar usuario
        if "delete_user" in request.POST:
            try:
                usuario = User.objects.get(id=user_id)
                username = usuario.username
                usuario.delete()
                messages.success(
                    request, f'Usuario "{username}" eliminado correctamente.'
                )
            except User.DoesNotExist:
                messages.error(request, "Usuario no encontrado.")

        # Resetear 2FA
        elif "reset_2fa" in request.POST:
            try:
                usuario = User.objects.get(id=user_id)

                # Desactivar 2FA y borrar secreto
                usuario.two_factor_enabled = False
                usuario.two_factor_secret = None
                usuario.save(update_fields=["two_factor_enabled", "two_factor_secret"])

                # Borrar dispositivos de confianza
                TrustedDevice.objects.filter(user=usuario).delete()

                messages.success(
                    request,
                    f'Se ha reseteado el 2FA del usuario "{usuario.username}".'
                )
            except User.DoesNotExist:
                messages.error(request, "Usuario no encontrado.")

        return redirect('dashboard_admin:listar_usuarios')

    # 🔍 Filtros GET
    identidad = request.GET.get('identidad', '').strip()
    nombre = request.GET.get('nombre', '').strip()
    email = request.GET.get('email', '').strip()
    rol_filtrado = request.GET.get('rol', '').strip()
    activo = request.GET.get('activo', '').strip()  # '', '1', '0'

    qs = User.objects.all().order_by('first_name', 'last_name', 'username')

    if identidad:
        qs = qs.filter(identidad__icontains=identidad)

    if nombre:
        qs = qs.filter(
            Q(first_name__icontains=nombre) |
            Q(last_name__icontains=nombre) |
            Q(username__icontains=nombre)
        )

    if email:
        qs = qs.filter(email__icontains=email)

    if rol_filtrado:
        qs = qs.filter(roles__nombre=rol_filtrado)

    if activo == '1':
        qs = qs.filter(is_active=True)
    elif activo == '0':
        qs = qs.filter(is_active=False)

    qs = qs.distinct()

    # 🔢 Paginación
    cantidad = request.GET.get('cantidad', '20')
    try:
        cantidad_int = int(cantidad)
    except ValueError:
        cantidad_int = 20

    paginator = Paginator(qs, cantidad_int)
    page_number = request.GET.get('page') or 1
    pagina = paginator.get_page(page_number)

    filtros = {
        "identidad": identidad,
        "nombre": nombre,
        "email": email,
        "rol": rol_filtrado,
        "activo": activo,
    }

    roles_disponibles = Rol.objects.all()

    return render(request, 'dashboard_admin/listar_usuarios.html', {
        'pagina': pagina,
        'roles': roles_disponibles,
        'filtros': filtros,
        'cantidad': str(cantidad_int),
    })

@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        usuario.delete()
        messages.success(
            request, f'Usuario {usuario.username} eliminado correctamente.'
        )
        return redirect('dashboard_admin:listar_usuarios')

    # GET → mostrar confirmación
    return render(request, 'dashboard_admin/eliminar_usuario_confirmacion.html', {'usuario': usuario})


# Vista para usuarios no autorizados
def no_autorizado(request):
    return render(request, 'dashboard_admin/no_autorizado.html')


@login_required
def redireccionar_vacaciones(request):
    user = request.user
    if user.es_supervisor:
        return redirect('rrhh:revisar_supervisor')
    elif user.es_pm:
        return redirect('rrhh:revisar_pm')
    elif user.es_rrhh or user.es_admin_general:  # 👈 Aquí
        return redirect('rrhh:revisar_rrhh')
    else:
        return redirect('dashboard_admin:inicio_admin')


@login_required
@rol_requerido('rrhh')
def listar_feriados(request):
    feriados = Feriado.objects.order_by('fecha')
    form = FeriadoForm()

    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('dashboard_admin:listar_feriados')

    return render(request, 'dashboard_admin/listar_feriados.html', {
        'feriados': feriados,
        'form': form
    })


@login_required
@rol_requerido('rrhh')
def eliminar_feriado(request, pk):
    feriado = get_object_or_404(Feriado, pk=pk)
    feriado.delete()
    messages.success(
        request, f'El feriado "{feriado.nombre}" fue eliminado con éxito.')
    return redirect('dashboard_admin:listar_feriados')


def redirigir_a_login_unificado(request):
    return redirect('usuarios:login')

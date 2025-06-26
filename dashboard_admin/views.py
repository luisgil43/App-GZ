from django.shortcuts import get_object_or_404, render, redirect
from usuarios.models import CustomUser, Rol
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from rrhh.models import Feriado
from rrhh.forms import FeriadoForm
from dashboard.models import ProduccionTecnico
from django.contrib.auth.forms import AuthenticationForm
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from usuarios.models import CustomUser as User
from usuarios.decoradores import rol_requerido
import re


User = get_user_model()


@login_required(login_url='dashboard_admin:login')
def admin_dashboard_view(request):
    # Cargar datos para la plantilla principal del admin dashboard
    return render(request, 'dashboard_admin/base.html')


@login_required(login_url='dashboard_admin:login')
def logout_view(request):
    logout(request)
    return redirect('dashboard_admin:login')


@staff_member_required(login_url='/dashboard_admin/login/')
def inicio_admin(request):
    return render(request, 'dashboard_admin/inicio_admin.html')


@login_required(login_url='dashboard_admin:login')
@rol_requerido('admin', 'pm', 'supervisor')
def produccion_tecnico(request):
    produccion = ProduccionTecnico.objects.filter(tecnico__user=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': produccion
    })


@login_required(login_url='dashboard_admin:login')
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


class UsuarioLoginView(LoginView):
    template_name = 'dashboard_usuario/login.html'
    authentication_form = AuthenticationForm

    def form_valid(self, form):
        user = form.get_user()

        if user.is_staff:
            # Si es staff, redirige directamente al panel admin
            return redirect('dashboard_admin:index')

        login(self.request, user)
        return super().form_valid(form)

    def get_success_url(self):
        # Redirige a `next` si es válido
        redirect_to = self.request.GET.get('next')
        if redirect_to and url_has_allowed_host_and_scheme(redirect_to, self.request.get_host()):
            return redirect_to

        # Fallback
        return reverse_lazy('dashboard_usuario:home')


@login_required(login_url='dashboard_admin:login')
@rol_requerido('admin', 'pm', 'rrhh')
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)
    grupos = Group.objects.all()

    if request.method == 'POST':
        usuario.username = request.POST['username']
        usuario.first_name = request.POST['first_name']
        usuario.last_name = request.POST['last_name']
        usuario.email = request.POST['email']
        usuario.is_active = 'is_active' in request.POST
        usuario.is_staff = 'is_staff' in request.POST
        usuario.is_superuser = 'is_superuser' in request.POST
        usuario.identidad = request.POST['identidad']

        grupo_ids = request.POST.getlist('groups')
        usuario.groups.set(grupo_ids)

        # ✅ Roles múltiples
        roles_ids = request.POST.getlist('roles')
        usuario.roles.set(roles_ids)

        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        if password1 and password1 == password2:
            usuario.set_password(password1)
        elif password1 != password2:
            messages.error(request, 'Las contraseñas no coinciden.')
            return redirect(request.path)

        usuario.save()
        messages.success(request, "Usuario actualizado exitosamente.")
        return redirect('dashboard_admin:listar_usuarios')

    # 👇 Cargamos roles para editar
    roles_disponibles = Rol.objects.all()
    roles_seleccionados = usuario.roles.values_list('id', flat=True)
    roles_seleccionados = [str(rid) for rid in roles_seleccionados]

    grupo_ids_post = [str(g.id) for g in usuario.groups.all()]

    return render(request, 'dashboard_admin/editar_usuario.html', {
        'usuario': usuario,
        'grupos': grupos,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
        'grupo_ids_post': grupo_ids_post,
    })


class AdminLoginView(LoginView):
    template_name = 'dashboard_admin/login.html'
    authentication_form = AuthenticationForm

    def form_valid(self, form):
        user = form.get_user()

        if not user.is_staff:
            raise PermissionDenied(
                "No tienes permiso para acceder al área de administración.")

        login(self.request, user)
        return super().form_valid(form)

    def get_success_url(self):
        redirect_to = self.request.GET.get('next')
        if redirect_to and url_has_allowed_host_and_scheme(redirect_to, self.request.get_host()):
            return redirect_to

        return reverse_lazy('dashboard_admin:inicio_admin')


@login_required(login_url='dashboard_admin:login')
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

        if password1 or password2:
            if password1 != password2:
                messages.error(request, 'Las contraseñas no coinciden.')
                return redirect(request.path)

        if identidad_post and not re.match(r'^[A-Za-z0-9\.\-]+$', identidad_post):
            messages.error(
                request, 'La identidad solo puede contener letras, números, puntos o guiones.')
            return redirect(request.path)

        if usuario:
            usuario.username = username
            usuario.email = email
            usuario.first_name = first_name
            usuario.last_name = last_name
            usuario.is_active = is_active
            usuario.is_staff = is_staff
            usuario.is_superuser = is_superuser
            usuario.identidad = identidad_post
            usuario.groups.set(grupo_ids)
            if password1:
                usuario.set_password(password1)
            usuario.save()
            usuario.roles.set(roles_ids)
            messages.success(request, 'Usuario actualizado correctamente.')
        else:
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
            )
            usuario.groups.set(grupo_ids)
            usuario.roles.set(roles_ids)
            usuario.save()
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

    contexto = {
        'grupos': grupos,
        'grupo_ids_post': grupo_ids_post,
        'usuario': usuario,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
    }
    return render(request, 'dashboard_admin/crear_usuario.html', contexto)

    # @login_required(login_url='dashboard_admin:login')
    # def index(request):
    #    return render(request, 'dashboard_admin/index.html')


@login_required(login_url='dashboard_admin:login')
@rol_requerido('admin', 'pm', 'rrhh')
def listar_usuarios(request):
    if request.method == "POST" and "delete_user" in request.POST:
        user_id = request.POST.get("user_id")
        try:
            usuario = User.objects.get(id=user_id)
            usuario.delete()
            messages.success(
                request, f'Usuario "{usuario.username}" eliminado correctamente.'
            )
        except User.DoesNotExist:
            messages.error(request, "Usuario no encontrado.")
            return redirect('dashboard_admin:listar_usuarios')

    # 🔎 Filtro por rol (GET)
    rol_filtrado = request.GET.get('rol')
    if rol_filtrado:
        usuarios = User.objects.filter(roles__nombre=rol_filtrado).distinct()
    else:
        usuarios = User.objects.all()

    roles_disponibles = Rol.objects.all()

    return render(request, 'dashboard_admin/listar_usuarios.html', {
        'usuarios': usuarios,
        'roles': roles_disponibles,
        'rol_filtrado': rol_filtrado,
    })


@login_required(login_url='dashboard_admin:login')
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

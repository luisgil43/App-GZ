from gz_services.utils.email_utils import enviar_correo_manual
from email.utils import formataddr
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.utils import timezone
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo
import base64
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.cache import cache


def no_autorizado_view(request):
    return render(request, 'usuarios/no_autorizado.html', status=403)


@staff_member_required
def subir_firma_representante(request):
    if request.method == 'POST':
        data_url = request.POST.get('firma_digital')
        if not data_url or not data_url.startswith('data:image/png;base64,'):
            messages.error(request, "Firma inválida o vacía.")
            return redirect(request.path)

        try:
            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)
            nombre_archivo = "firma.png"

            # Eliminar firma anterior (incluyendo el archivo en Cloudinary)
            firma_anterior = FirmaRepresentanteLegal.objects.first()
            if firma_anterior:
                if firma_anterior.archivo:
                    firma_anterior.archivo.delete(
                        save=False)  # Elimina de Cloudinary
                firma_anterior.delete()  # Elimina el registro en DB

            # Crear nueva firma
            firma = FirmaRepresentanteLegal(fecha_subida=timezone.now())
            firma.archivo.save(nombre_archivo, content, save=True)

            messages.success(
                request, "Firma del representante legal subida correctamente.")
            return redirect('liquidaciones:admin_lista')

        except Exception as e:
            messages.error(request, f"Error al guardar firma: {e}")
            return redirect(request.path)

    return render(request, 'usuarios/subir_firma_representante.html')


User = get_user_model()


def recuperar_contraseña(request):
    es_admin_param = request.GET.get('admin') == 'true'

    if request.method == 'POST':
        email = request.POST.get('email')
        usuario = User.objects.filter(email=email).first()

        if usuario:
            es_admin = usuario.is_staff or usuario.is_superuser or es_admin_param
            token = get_random_string(64)
            cache.set(f"token_recuperacion_{usuario.id}", token, timeout=3600)

            reset_url = request.build_absolute_uri(
                reverse('usuarios:resetear_contraseña',
                        args=[usuario.id, token])
            ).replace("127.0.0.1:8000", "app-gz.onrender.com")

            asunto = 'Recuperación de contraseña - Plataforma GZ'
            text_content = f"""
Hola {usuario.get_full_name() or usuario.username},

Has solicitado recuperar tu contraseña.

Haz clic en el siguiente enlace para crear una nueva:

{reset_url}

Si no solicitaste este correo, simplemente ignóralo.
"""

            html_content = render_to_string('usuarios/correo_recuperacion.html', {
                'usuario': usuario,
                'reset_url': reset_url
            })

            try:
                resultado = enviar_correo_manual(
                    destinatario=email,
                    asunto=asunto,
                    cuerpo_texto=text_content,
                    cuerpo_html=html_content
                )

                if resultado:
                    messages.success(
                        request, 'Te hemos enviado un enlace a tu correo registrado para cambiar la clave.')

                    return redirect(f"{reverse('usuarios:confirmacion_envio')}?es_admin={str(es_admin).lower()}")
                else:
                    messages.error(
                        request, 'No se pudo enviar el correo. Intenta más tarde.')

            except Exception as e:
                messages.error(request, f'Error al enviar correo: {str(e)}')
        else:
            messages.error(
                request, 'No se encontró un usuario con ese correo.')

        return redirect('usuarios:recuperar_contraseña')

    return render(request, 'usuarios/recuperar_contraseña.html')


def resetear_contraseña(request, usuario_id, token):
    usuario = User.objects.filter(id=usuario_id).first()
    token_guardado = cache.get(f"token_recuperacion_{usuario_id}")

    if not usuario or token != token_guardado:
        messages.error(
            request, "El enlace de recuperación no es válido o ha expirado.")
        return redirect('usuarios:recuperar_contraseña')

    if request.method == 'POST':
        nueva_contraseña = request.POST.get('password1')
        confirmar_contraseña = request.POST.get('password2')

        if nueva_contraseña != confirmar_contraseña:
            messages.error(request, "Las contraseñas no coinciden.")
        else:
            usuario.password = make_password(nueva_contraseña)
            usuario.save()
            cache.delete(f"token_recuperacion_{usuario_id}")
            messages.success(
                request, "Tu contraseña fue actualizada con éxito.")
            return redirect('usuarios:login')

    return render(request, 'usuarios/resetear_contraseña.html', {'usuario': usuario})


def login_unificado(request):
    form = AuthenticationForm(request, data=request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if user.rol == 'usuario':
                return redirect('dashboard:index')
            else:
                return redirect('usuarios:seleccionar_rol')
        else:
            messages.error(request, "Credenciales inválidas.")

    return render(request, 'usuarios/login.html', {'form': form})


@login_required
def seleccionar_rol(request):
    if request.method == 'POST':
        opcion = request.POST.get('opcion')
        if opcion == 'usuario':
            return redirect('dashboard:index')
        else:
            return redirect('dashboard_admin:index')

    return render(request, 'usuarios/seleccionar_rol.html')

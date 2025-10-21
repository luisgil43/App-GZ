import base64
import logging
from email.utils import formataddr

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives, send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, requires_csrf_token
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from gz_services.utils.email_utils import enviar_correo_manual
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo

from .models import Notificacion


@requires_csrf_token
def csrf_error_view(request, reason=""):
    # No exponemos detalles de 'reason' al usuario final
    messages.error(
        request, "Tu sesión ha expirado o no es válida. Vuelve a iniciar sesión.")
    return redirect('usuarios:login_unificado')


logger = logging.getLogger("usuarios")


def _client_ip(request):
    xff = (request.META.get("HTTP_X_FORWARDED_FOR")
           or "").split(",")[0].strip()
    return xff or request.META.get("REMOTE_ADDR") or ""


def ratelimit(key_prefix: str, limit: int, window_sec: int):
    """
    Decorador sencillo de rate-limit por IP + ruta (cache-based).
    Ej.: @ratelimit("recover", limit=5, window_sec=300) -> 5 intentos/5min.
    """
    def deco(view):
        def _wrapped(request, *args, **kwargs):
            ip = _client_ip(request)
            key = f"rl:{key_prefix}:{ip}"
            hits = cache.get(key, 0)
            if hits >= limit:
                logger.warning(
                    "RATE_LIMIT key=%s ip=%s hits=%s", key, ip, hits)
                # Responder igual que flujo normal para no dar pistas
                messages.error(
                    request, "Demasiadas solicitudes. Intenta más tarde.")
                # Evita loops: redirige a la propia vista o al login según caso
                if key_prefix.startswith("recover"):
                    return redirect('usuarios:recuperar_contraseña')
                return redirect('usuarios:login_unificado')
            cache.set(key, hits + 1, timeout=window_sec)
            return view(request, *args, **kwargs)
        return _wrapped
    return deco


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


@never_cache
@csrf_protect
@sensitive_post_parameters('email')
@require_http_methods(["GET", "POST"])
@ratelimit("recover", limit=5, window_sec=300)
def recuperar_contraseña(request):
    es_admin_param = request.GET.get('admin') == 'true'

    if request.method == 'POST':
        email = (request.POST.get('email') or "").strip()
        usuario = User.objects.filter(email__iexact=email).first()

        # Generamos respuesta SIEMPRE igual (anti-enumeración)
        generic_ok_redirect = redirect('usuarios:confirmacion_envio')

        if not email:
            messages.error(request, "Ingresa un correo válido.")
            return redirect('usuarios:recuperar_contraseña')

        if usuario:
            try:
                es_admin = usuario.is_staff or usuario.is_superuser or es_admin_param
                token = get_random_string(64)
                cache.set(
                    f"token_recuperacion_{usuario.id}", token, timeout=3600)

                # Construye URL pública sin .replace()
                reset_url = request.build_absolute_uri(
                    reverse('usuarios:resetear_contraseña',
                            args=[usuario.id, token])
                )

                asunto = 'Recuperación de contraseña - Plataforma GZ'
                text_content = f"""Hola {usuario.get_full_name() or usuario.username},

Has solicitado recuperar tu contraseña.
Haz clic en el siguiente enlace para crear una nueva:

{reset_url}

Si no solicitaste este correo, simplemente ignóralo.
"""

                html_content = render_to_string('usuarios/correo_recuperacion.html', {
                    'usuario': usuario,
                    'reset_url': reset_url
                })

                resultado = enviar_correo_manual(
                    destinatario=email,
                    asunto=asunto,
                    cuerpo_texto=text_content,
                    cuerpo_html=html_content
                )

                logger.info("PWD_RESET_MAIL_SENT user=%s ip=%s ok=%s",
                            usuario.pk, _client_ip(request), bool(resultado))
            except Exception as e:
                # No exponemos detalles al usuario (anti-inform leakage)
                logger.exception(
                    "PWD_RESET_ERROR email=%s ip=%s err=%s", email, _client_ip(request), str(e))

        # Mensaje genérico siempre
        messages.success(
            request, 'Si el correo existe, te enviamos un enlace para cambiar la clave.')
        return generic_ok_redirect

    return render(request, 'usuarios/recuperar_contraseña.html')


@never_cache
@csrf_protect
@sensitive_post_parameters('password')
@require_http_methods(["GET", "POST"])
@ratelimit("login", limit=10, window_sec=60)
def login_unificado(request):
    form = AuthenticationForm(request, data=request.POST or None)

    # Audit básico de acceso (GET/POST)
    logger.info(
        "LOGIN_UNIFICADO %s path=%s ip=%s ua=%s",
        request.method, request.path, _client_ip(request),
        (request.META.get("HTTP_USER_AGENT", "") or "")[:180]
    )

    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            # Django rota la session key (prevención fixación)
            login(request, user)
            logger.info("LOGIN_OK user=%s ip=%s", getattr(
                user, "pk", "?"), _client_ip(request))

            # Caso 1: solo tiene rol de usuario
            try:
                if user.roles.count() == 1 and user.tiene_rol('usuario'):
                    return redirect(settings.LOGIN_REDIRECT_URL)
            except Exception:
                pass

            # Caso 2: tiene más de un rol
            return redirect('usuarios:seleccionar_rol')

        # Fallo de credenciales (Axes también lo registrará)
        username_try = (request.POST.get('username') or "").strip()
        logger.warning("LOGIN_FAIL username=%s ip=%s",
                       username_try, _client_ip(request))
        messages.error(request, "Credenciales inválidas.")

    return render(request, 'usuarios/login.html', {'form': form})


@login_required
def seleccionar_rol(request):
    usuario = request.user
    roles_usuario = usuario.roles.all()

    if request.method == 'POST':
        opcion = request.POST.get('opcion')
        if opcion == 'usuario':
            return redirect('dashboard:index')
        elif opcion in ['admin', 'rrhh', 'supervisor', 'pm', 'facturacion', 'logistica', 'subcontrato', 'flota', 'bodeguero', 'prevencion']:
            return redirect('dashboard_admin:index')
        else:
            messages.error(request, "Rol no reconocido.")
            return redirect('usuarios:seleccionar_rol')

    return render(request, 'usuarios/seleccionar_rol.html', {'roles': roles_usuario})


@login_required
def marcar_notificacion_como_leida(request, pk):
    notificacion = get_object_or_404(Notificacion, pk=pk, usuario=request.user)
    notificacion.leido = True
    notificacion.save()
    logger.info("NOTIF_READ user=%s notif=%s ip=%s",
                request.user.pk, notificacion.pk, _client_ip(request))

    if notificacion.url:
        return redirect(notificacion.url)

    if request.user.is_superuser or request.user.roles.filter(
        nombre__in=['admin', 'rrhh', 'pm', 'prevencion',
                    'logistica', 'flota', 'subcontrato', 'facturacion']
    ).exists():
        return redirect('dashboard_admin:inicio_admin')

    return redirect('dashboard:inicio_tecnico')


@never_cache
@csrf_protect
@sensitive_post_parameters('nueva', 'confirmar')
@require_http_methods(["GET", "POST"])
@ratelimit("reset", limit=10, window_sec=600)
def resetear_contraseña(request, usuario_id, token):
    usuario = User.objects.filter(id=usuario_id).first()
    token_guardado = cache.get(f"token_recuperacion_{usuario_id}")
    ip = _client_ip(request)

    if not usuario or token != token_guardado:
        logger.warning(
            "PWD_RESET_TOKEN_INVALID user_id=%s ip=%s", usuario_id, ip)
        messages.error(
            request, "El enlace de recuperación no es válido o ha expirado.")
        return redirect('usuarios:recuperar_contraseña')

    if request.method == 'POST':
        nueva = (request.POST.get('nueva') or "")
        confirmar = (request.POST.get('confirmar') or "")

        if nueva != confirmar:
            messages.error(request, "Las contraseñas no coinciden.")
        elif len(nueva) < 8:
            messages.error(
                request, "La nueva contraseña debe tener al menos 8 caracteres.")
        else:
            try:
                usuario.set_password(nueva)
                usuario.save()
                cache.delete(f"token_recuperacion_{usuario_id}")
                logger.info("PWD_RESET_OK user=%s ip=%s", usuario.pk, ip)
                messages.success(
                    request, "Tu contraseña fue actualizada con éxito.")
                return redirect('usuarios:login_unificado')
            except Exception as e:
                logger.exception(
                    "PWD_RESET_SAVE_ERROR user=%s ip=%s err=%s", usuario.pk, ip, str(e))
                messages.error(
                    request, "No se pudo actualizar la contraseña. Intenta más tarde.")

    return render(request, 'usuarios/resetear_contraseña.html', {'usuario': usuario})

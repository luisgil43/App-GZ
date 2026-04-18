import base64
import logging
from datetime import timedelta
from email.utils import formataddr
from functools import wraps

import pyotp
from axes.decorators import axes_dispatch
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
from django.views.decorators.http import require_GET, require_http_methods

from gz_services.utils.email_utils import enviar_correo_manual
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo

from .models import Notificacion, TrustedDevice

...
from django.utils import timezone

TRUSTED_DEVICE_COOKIE_NAME = getattr(
    settings, "TRUSTED_DEVICE_COOKIE_NAME", "gz_trusted_device"
)
TRUSTED_DEVICE_DAYS = getattr(settings, "TRUSTED_DEVICE_DAYS", 30)

def _get_2fa_enforce_date():
    """
    Fecha en la que 2FA pasa a ser obligatorio (si está configurada).
    """
    return getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None)


def _get_2fa_days_left():
    """
    Días que faltan para que 2FA sea obligatorio.
    Si no hay fecha configurada, retorna None.
    """
    enforce_date = _get_2fa_enforce_date()
    if not enforce_date:
        return None

    today = timezone.localdate()
    return (enforce_date - today).days

def _user_requires_2fa(user) -> bool:
    """
    En GZ: el 2FA se exige solo a usuarios administrativos (is_staff=True)
    y que además tengan el 2FA activado.
    """
    # Si el usuario no tiene 2FA activado, no se exige
    if not getattr(user, "two_factor_enabled", False):
        return False

    # Solo personal administrativo/staff
    return bool(user.is_staff)


def _has_valid_trusted_device(request, user) -> bool:
    """
    Revisa si la cookie de dispositivo confiable corresponde a
    un TrustedDevice válido para este usuario.
    """
    token = request.COOKIES.get(TRUSTED_DEVICE_COOKIE_NAME)

    logger.info(
        "2FA_COOKIE_CHECK user=%s cookie_name=%s token_present=%s",
        getattr(user, "pk", None),
        TRUSTED_DEVICE_COOKIE_NAME,
        bool(token),
    )

    if not token:
        return False

    try:
        device = TrustedDevice.objects.get(user=user, token=token)
        logger.info(
            "2FA_DEVICE_FOUND user=%s device_id=%s expires_at=%s now=%s",
            user.pk,
            device.pk,
            device.expires_at,
            timezone.now(),
        )
    except TrustedDevice.DoesNotExist:
        logger.warning(
            "2FA_DEVICE_NOT_FOUND user=%s token=%s",
            user.pk,
            token,
        )
        return False

    if not device.is_valid():
        logger.warning(
            "2FA_DEVICE_EXPIRED user=%s device_id=%s expires_at=%s now=%s",
            user.pk,
            device.pk,
            device.expires_at,
            timezone.now(),
        )
        return False

    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at"])

    logger.info("2FA_DEVICE_VALID user=%s device_id=%s", user.pk, device.pk)
    return True


def _create_trusted_device(request, user) -> TrustedDevice:
    """
    Crea un TrustedDevice para el usuario y retorna la instancia.
    La cookie se setea en la vista two_factor_verify.
    """
    import secrets

    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(days=TRUSTED_DEVICE_DAYS)
    device = TrustedDevice.objects.create(
        user=user,
        token=token,
        expires_at=expires_at,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        ip_address=(request.META.get("REMOTE_ADDR") or None),
    )

    logger.info(
        "2FA_DEVICE_CREATED user=%s device_id=%s token=%s expires_at=%s",
        user.pk,
        device.pk,
        device.token,
        device.expires_at,
    )

    return device


def _verify_totp_code(user, code: str) -> bool:
    """
    Verifica el código TOTP enviado por el usuario.
    """
    if not getattr(user, "two_factor_secret", None):
        return False
    if not code:
        return False

    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False

    totp = pyotp.TOTP(user.two_factor_secret)
    # valid_window=1 acepta un paso hacia atrás/adelante
    return totp.verify(code, valid_window=1)


def _redirect_after_login(request, user):
    """
    Misma lógica que ya tenías en login_unificado, pero en helper reutilizable.
    - Usuarios sin rol o solo rol 'usuario' → dashboard normal.
    - Otros roles → pantalla de seleccionar rol.
    """
    roles_usuario = user.roles.all() if hasattr(user, "roles") else []

    if not roles_usuario or (
        len(roles_usuario) == 1 and roles_usuario[0].nombre == "usuario"
    ):
        # Igual que antes: usar LOGIN_REDIRECT_URL
        from django.shortcuts import redirect as _redirect
        return _redirect(settings.LOGIN_REDIRECT_URL)

    from django.shortcuts import redirect as _redirect
    return _redirect("usuarios:seleccionar_rol")


def two_factor_verify(request):
    """
    Paso intermedio del login cuando el usuario tiene 2FA activo
    y el dispositivo no está marcado como confiable.
    NO usar @login_required aquí, porque todavía no se ha hecho login()
    definitivo: venimos del login_unificado con un user pendiente en sesión.
    """
    pending_user_id = request.session.get("pending_2fa_user_id")
    if not pending_user_id:
        messages.error(
            request,
            "Tu sesión de verificación ha expirado. Por favor, inicia sesión de nuevo.",
        )
        return redirect("usuarios:login_unificado")

    UserModel = get_user_model()
    user = get_object_or_404(UserModel, pk=pending_user_id)

    if request.method == "POST":
        code = request.POST.get("code", "")
        remember_device = request.POST.get("remember_device") == "on"

        if not _verify_totp_code(user, code):
            messages.error(
                request, "El código de verificación no es válido. Inténtalo nuevamente."
            )
            return render(
                request,
                "usuarios/two_factor_verify.html",
                {"user": user},
            )

        backend_path = request.session.pop("pending_2fa_backend", None)
        next_url = request.session.pop("pending_2fa_next", None)
        request.session.pop("pending_2fa_user_id", None)

        if not backend_path:
            backend_path = settings.AUTHENTICATION_BACKENDS[0]

        login(request, user, backend=backend_path)

        # Definir primero el redirect final
        if next_url:
            response = redirect(next_url)
        else:
            response = _redirect_after_login(request, user)

        # Luego setear la cookie sobre ese mismo response
        if remember_device:
            device = _create_trusted_device(request, user)
            max_age = TRUSTED_DEVICE_DAYS * 24 * 60 * 60
            response.set_cookie(
                TRUSTED_DEVICE_COOKIE_NAME,
                device.token,
                max_age=max_age,
                secure=not settings.DEBUG,
                httponly=True,
                samesite="Lax",
                path="/",
            )

        return response

    return render(
        request,
        "usuarios/two_factor_verify.html",
        {"user": user},
    )


def axes_post_only(view_func):
    """
    Aplica Axes (bloqueo por intentos) solo a peticiones POST.
    Las GET pasan directo para poder mostrar el formulario sin bloquear.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method.upper() == "POST":
            return axes_dispatch(view_func)(request, *args, **kwargs)
        return view_func(request, *args, **kwargs)

    return _wrapped

@login_required(login_url="usuarios:login_unificado")
def two_factor_setup(request):
    """
    Pantalla de seguridad:
      - Configurar y activar 2FA.
      - Listar dispositivos de confianza del usuario.
      - Permitir eliminar dispositivos de confianza.
    """
    user = request.user

    # Generar o recuperar el secreto TOTP del usuario
    secret = user.get_or_create_two_factor_secret()

    # Nombre que se muestra en la app de autenticación
    issuer_name = getattr(settings, "TWO_FACTOR_ISSUER_NAME", "GZ Services")

    # Crear URI para apps tipo Google Authenticator
    totp = pyotp.TOTP(secret)
    otp_uri = totp.provisioning_uri(name=user.username, issuer_name=issuer_name)

    # Dispositivos de confianza del usuario
    devices = user.trusted_devices.order_by("-created_at")

    if request.method == "POST":
        action = request.POST.get("action", "enable_2fa")

        if action == "enable_2fa":
            code = request.POST.get("code", "")
            if _verify_totp_code(user, code):
                user.two_factor_enabled = True
                user.save(update_fields=["two_factor_enabled"])
                messages.success(
                    request,
                    "El segundo factor de autenticación se ha activado correctamente en tu cuenta."
                )
                return redirect("usuarios:two_factor_setup")
            else:
                messages.error(
                    request,
                    "El código de verificación no es válido. Revisa tu aplicación de autenticación."
                )

        elif action == "delete_device":
            device_id = request.POST.get("device_id")
            try:
                device = TrustedDevice.objects.get(id=device_id, user=user)
                device.delete()
                messages.success(
                    request,
                    "El dispositivo de confianza ha sido eliminado."
                )
            except TrustedDevice.DoesNotExist:
                messages.error(
                    request,
                    "No se encontró el dispositivo de confianza seleccionado."
                )
            return redirect("usuarios:two_factor_setup")

    context = {
        "secret": secret,
        "otp_uri": otp_uri,
        "two_factor_enabled": getattr(user, "two_factor_enabled", False),
        "devices": devices,
    }
    return render(request, "usuarios/two_factor_setup.html", context)


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
@require_http_methods(["GET", "POST", "HEAD"])
@ratelimit("login", limit=10, window_sec=60)
@axes_post_only
def login_unificado(request):
    """
    Login centralizado GZ:
      1) Valida usuario + password (AuthenticationForm + Axes + ratelimit).
      2) Si el usuario NO requiere 2FA → login normal + redirect según rol.
      3) Si requiere 2FA:
         - Si tiene dispositivo confiable válido → login normal.
         - Si no → guarda user_id y backend en sesión y redirige a two_factor_verify.
    """
    if request.user.is_authenticated:
        # Usuario ya logueado: lo mandamos a donde corresponda
        return _redirect_after_login(request, request.user)

    # ¿se pidió captcha? (si luego quieres engancharlo tras 3 intentos fallidos,
    # aquí puedes añadir la lógica para mostrarlo / validarlo)

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # ----- 2FA obligatorio para staff con 2FA activado -----
            if _user_requires_2fa(user) and not _has_valid_trusted_device(request, user):
                # Guardamos en sesión el usuario pendiente de 2FA
                request.session["pending_2fa_user_id"] = user.pk

                # Guardar también el backend con el que se autenticó
                backend_path = getattr(user, "backend", None)
                if backend_path:
                    request.session["pending_2fa_backend"] = backend_path

                # Respetar ?next=
                next_url = request.GET.get("next") or request.POST.get("next")
                if next_url:
                    request.session["pending_2fa_next"] = next_url

                messages.info(
                    request,
                    "Por seguridad, debes introducir el código de verificación de tu aplicación de autenticación."
                )
                return redirect("usuarios:two_factor_verify")

            # ----- SIN 2FA requerido o dispositivo ya confiable -----
            login(request, user)

            logger.info(
                f"LOGIN_OK user={user.username}, "
                f"is_staff={user.is_staff}, is_superuser={user.is_superuser}, "
                f"roles={[r.nombre for r in user.roles.all()] if hasattr(user, 'roles') else []}"
            )

            return _redirect_after_login(request, user)
        else:
            logger.warning(
                "LOGIN_FAIL username=%s, errores=%s",
                request.POST.get('username'),
                form.errors
            )
            messages.error(request, "Credenciales inválidas.")
    else:
        form = AuthenticationForm(request)

    # Info para mostrar aviso de "2FA pronto obligatorio"
    days_left = _get_2fa_days_left()
    enforce_date = _get_2fa_enforce_date()

    context = {
        "form": form,
        "two_factor_days_left": days_left,
        "two_factor_enforce_date": enforce_date,
    }
    return render(request, 'usuarios/login.html', context)


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


@login_required
def seleccionar_rol(request):
    u = request.user

    # --- Define qué es "admin" en tu sistema ---
    # OJO: en tu modelo, "admin_general" es el rol 'admin'
    # y además hay roles administrativos como pm/supervisor/rrhh/etc.
    ADMIN_ROLE_NAMES = {
        "admin", "pm", "supervisor", "rrhh", "prevencion",
        "logistica", "subcontrato", "facturacion", "flota", "bodeguero"
    }

    # ✅ Perfil usuario: SOLO si tiene rol 'usuario' (o superuser)
    can_user = bool(getattr(u, "es_usuario", False))

    # ✅ Perfil admin: si tiene cualquiera de los roles admin (o superuser)
    can_admin = bool(
        u.is_superuser or u.roles.filter(nombre__in=ADMIN_ROLE_NAMES).exists()
    )

    # ------------------------------------------------------------------
    # ✅ REGLA NUEVA: si NO tiene ambos perfiles, NO preguntamos.
    # ------------------------------------------------------------------
    if not (can_user and can_admin):
        # Guardar modo en sesión para que tu middleware lo respete (si aplica)
        if can_admin and not can_user:
            request.session["ui_mode"] = "admin"
            return redirect("/dashboard_admin/index/")
        if can_user and not can_admin:
            request.session["ui_mode"] = "user"
            return redirect("/dashboard/")
        # Si llega aquí: no tiene ni usuario ni admin
        messages.error(request, "No tienes un rol válido para ingresar.")
        return redirect("usuarios:no_autorizado")

    # ------------------------------------------------------------------
    # ✅ Solo si tiene ambos perfiles, mostramos selector
    # ------------------------------------------------------------------
    if request.method == "POST":
        opcion = (request.POST.get("opcion") or "").strip().lower()

        if opcion == "usuario":
            request.session["ui_mode"] = "user"
            return redirect("/dashboard/")

        if opcion == "admin":
            request.session["ui_mode"] = "admin"
            return redirect("/dashboard_admin/index/")

        messages.error(request, "Opción inválida.")
        return redirect("usuarios:seleccionar_rol")

    # GET -> render selector (tiene ambos)
    return render(request, "usuarios/seleccionar_rol.html", {
        "can_user": can_user,
        "can_admin": can_admin,
    })

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

from django.urls import reverse_lazy
import dj_database_url
from pathlib import Path
import os
from django.utils.module_loading import import_string
from dotenv import load_dotenv
load_dotenv()


# Ruta base del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def is_env_var_set(key):
    return bool(os.environ.get(key) and os.environ.get(key).strip().lower() != "none")


if (
    is_env_var_set("CLOUDINARY_CLOUD_NAME") and
    is_env_var_set("CLOUDINARY_API_KEY") and
    is_env_var_set("CLOUDINARY_API_SECRET")
):
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
        'API_KEY': os.environ.get('CLOUDINARY_API_KEY'),
        'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
    }

# Configuración básica
LOGIN_URL = '/usuarios/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/usuarios/login/'
# Si usas login personalizado para admin
# ADMIN_LOGIN_URL = '/dashboard_admin/login/'  # opcional, para referencia
AUTH_USER_MODEL = 'usuarios.CustomUser'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'clave-insegura')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ['app-gz.onrender.com',
                 'localhost', '127.0.0.1', '172.20.10.2']

# Aplicaciones instaladas
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_select2',
    'cloudinary',
    'cloudinary_storage',
    # Tus apps
    'liquidaciones',
    'dashboard',
    'operaciones',
    'prevencion',
    'rrhh',
    'logistica',
    'subcontrato',
    'facturacion',
    'usuarios',
    'dashboard_admin.apps.DashboardAdminConfig',
    'dal',
    'dal_select2',
    'widget_tweaks',
    'django.contrib.humanize',
]

# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # 'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'gz_services.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'usuarios.context_processors.notificaciones_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'gz_services.wsgi.application'

# Base de datos
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

# Validadores de contraseña
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Configuración regional
LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'America/Santiago'
USE_I18N = True
USE_TZ = True

# Archivos estáticos
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Archivos multimedia (siempre necesarios aunque se use Cloudinary)
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# ===============================
# ✅ Cloudinary (cuando está activo)
# ===============================

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'mail.grupogzs.com'
EMAIL_PORT = 465
EMAIL_USE_TLS = False         # 👈 DEBE estar en False
EMAIL_USE_SSL = True          # 👈 DEBE estar en True
EMAIL_HOST_USER = 'planix@grupogzs.com'
EMAIL_HOST_PASSWORD = '}xZs%l%xGFb3'
DEFAULT_FROM_EMAIL = 'planix@grupogzs.com'


# HTTPS en Render
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ====================================
# Datos de la empresa emisora del DTE
# ====================================

EMPRESA_RUT = "77084679-K"
EMPRESA_NOMBRE = "GZ SERVICES AND BUSINESS SPA"
EMPRESA_GIRO = "Servicio de Ingenieria de Telecomunicaciones y Construcciones"
EMPRESA_DIR = "Cerro el plomo 5931 Of 1011 PS 10"
EMPRESA_COMUNA = "Las Condes"
EMPRESA_CIUDAD = "Santiago"
EMPRESA_ACTIVIDAD_ECONOMICA = "123456"  # Código del SII
EMPRESA_FECHA_RESOLUCION = "2020-01-01"
EMPRESA_NUMERO_RESOLUCION = "80"


CSRF_FAILURE_VIEW = 'usuarios.views.csrf_error_view'

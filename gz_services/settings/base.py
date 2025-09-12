from pathlib import Path
import os

from django.urls import reverse_lazy
from dotenv import load_dotenv
from boto3.s3.transfer import TransferConfig

# ====== Rutas base ======
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Cargar variables de entorno:
# 1) .env (base)  2) .env.local (override en DEV)
load_dotenv(BASE_DIR / ".env", override=False)
load_dotenv(BASE_DIR / ".env.local", override=True)

# Plantillas/recursos (rutas portables)
REPORTE_FOTOS_TEMPLATE_XLSX = BASE_DIR / \
    "static" / "reporte_fotos_template.xlsx"
ACTA_SITES_LOGO_PATH = BASE_DIR / "static" / "images" / "sites_logo.png"
ACTA_FIRMA_EDGARDO_PATH = BASE_DIR / "static" / "images" / "edgardo_zapata.png"


def is_env_var_set(key: str) -> bool:
    v = os.environ.get(key)
    return bool(v and v.strip().lower() != "none")


# ===============================
# ✅ Cloudinary (cuando está activo)
# ===============================
if (
    is_env_var_set("CLOUDINARY_CLOUD_NAME")
    and is_env_var_set("CLOUDINARY_API_KEY")
    and is_env_var_set("CLOUDINARY_API_SECRET")
):
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": os.environ.get("CLOUDINARY_CLOUD_NAME"),
        "API_KEY": os.environ.get("CLOUDINARY_API_KEY"),
        "API_SECRET": os.environ.get("CLOUDINARY_API_SECRET"),
    }

# ===============================
# Configuración básica
# ===============================
LOGIN_URL = reverse_lazy("usuarios:login_unificado")
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/usuarios/login/"
AUTH_USER_MODEL = "usuarios.CustomUser"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "clave-insegura")
DEBUG = os.environ.get("DEBUG", "False").strip().lower() == "true"

ALLOWED_HOSTS = [
    "app-gz.onrender.com",
    "localhost",
    "127.0.0.1",
    "172.20.10.2",
]

# ===============================
# Apps
# ===============================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_select2",
    "cloudinary",
    "cloudinary_storage",
    # Storage S3/Wasabi para los nuevos campos (no afecta Cloudinary)
    "storages",

    # Tus apps
    "liquidaciones",
    "dashboard",
    "operaciones",
    "prevencion",
    "rrhh",
    "logistica",
    "subcontrato",
    "facturacion",
    "usuarios",
    "dashboard_admin.apps.DashboardAdminConfig",
    "dal",
    "dal_select2",
    "widget_tweaks",
    "django.contrib.humanize",
]

# ===============================
# Middleware
# ===============================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # "simple_history.middleware.HistoryRequestMiddleware",  # si lo activas, va aquí
    "django.contrib.messages.middleware.MessageMiddleware",
    # 👇 cierre de sesión por inactividad
    "usuarios.middleware.SessionExpiryMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gz_services.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "usuarios.context_processors.notificaciones_context",
            ],
        },
    },
]

WSGI_APPLICATION = "gz_services.wsgi.application"

# ===============================
# Base de datos
# ===============================
import dj_database_url  # noqa: E402

DATABASES = {
    "default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

# ===============================
# Password validators
# ===============================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ===============================
# Regionalización
# ===============================
LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

# ===============================
# Archivos estáticos y media
# ===============================
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")


DIRECT_UPLOADS_ENABLED = os.environ.get("DIRECT_UPLOADS_ENABLED", "1") == "1"
DIRECT_UPLOADS_MAX_MB = int(os.environ.get("DIRECT_UPLOADS_MAX_MB", "20"))
DIRECT_UPLOADS_SAFE_PREFIX = os.environ.get(
    "DIRECT_UPLOADS_SAFE_PREFIX", "operaciones/evidencias/"
)


# ===============================
# 📦 Wasabi (solo campos dedicados)
# ===============================
# ===============================
# 📦 Wasabi (S3) — aceleración multipart
# ===============================

WASABI_GZ_ACCESS_KEY_ID = os.getenv("WASABI_GZ_ACCESS_KEY_ID")
WASABI_GZ_SECRET_ACCESS_KEY = os.getenv("WASABI_GZ_SECRET_ACCESS_KEY")
WASABI_GZ_BUCKET_NAME = os.getenv("WASABI_GZ_BUCKET_NAME", "gz-services")
WASABI_GZ_REGION_NAME = os.getenv("WASABI_GZ_REGION_NAME", "us-west-1")
WASABI_GZ_ENDPOINT_URL = os.getenv(
    "WASABI_GZ_ENDPOINT_URL", "https://s3.us-west-1.wasabisys.com")

AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "path"
AWS_S3_USE_SSL = True
AWS_S3_VERIFY = True
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_S3_FILE_OVERWRITE = False
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=31536000, public"}

# ⚙️ Parámetros de transferencia (desde env, con defaults)
WASABI_GZ_USE_THREADS = os.getenv("WASABI_GZ_USE_THREADS", "True") == "True"
WASABI_GZ_MAX_CONCURRENCY = int(os.getenv("WASABI_GZ_MAX_CONCURRENCY", "10"))
WASABI_GZ_MULTIPART_THRESHOLD = int(
    os.getenv("WASABI_GZ_MULTIPART_THRESHOLD", str(8 * 1024 * 1024)))
WASABI_GZ_MULTIPART_CHUNKSIZE = int(
    os.getenv("WASABI_GZ_MULTIPART_CHUNKSIZE", str(8 * 1024 * 1024)))

# ✅ Tiene que ser un TransferConfig (NO un dict)
AWS_S3_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=WASABI_GZ_MULTIPART_THRESHOLD,
    multipart_chunksize=WASABI_GZ_MULTIPART_CHUNKSIZE,
    max_concurrency=WASABI_GZ_MAX_CONCURRENCY,
    use_threads=WASABI_GZ_USE_THREADS,
)


# ===============================
# Email (desde variables de entorno)
# ===============================
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mail.grupogzs.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))
EMAIL_USE_TLS = os.environ.get(
    "EMAIL_USE_TLS", "False").strip().lower() == "true"
EMAIL_USE_SSL = os.environ.get(
    "EMAIL_USE_SSL", "True").strip().lower() == "true"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)

# ===============================
# HTTPS detrás de proxy (Render)
# ===============================
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ===============================
# Datos empresa (DTE)
# ===============================
EMPRESA_RUT = "77084679-K"
EMPRESA_NOMBRE = "GZ SERVICES AND BUSINESS SPA"
EMPRESA_GIRO = "Servicio de Ingenieria de Telecomunicaciones y Construcciones"
EMPRESA_DIR = "Cerro el plomo 5931 Of 1011 PS 10"
EMPRESA_COMUNA = "Las Condes"
EMPRESA_CIUDAD = "Santiago"
EMPRESA_ACTIVIDAD_ECONOMICA = "123456"  # Código del SII
EMPRESA_FECHA_RESOLUCION = "2020-01-01"
EMPRESA_NUMERO_RESOLUCION = "80"

CSRF_FAILURE_VIEW = "usuarios.views.csrf_error_view"

# ===============================
# Sesiones
# ===============================
IDLE_TIMEOUT_SECONDS = 15 * 60
SESSION_ABSOLUTE_TIMEOUT = None
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

import mimetypes
import os
from pathlib import Path

from boto3.s3.transfer import TransferConfig
from django.urls import reverse_lazy
from dotenv import load_dotenv

mimetypes.add_type("application/manifest+json", ".webmanifest")

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

# Logo Planix para correos HTML
PLANIX_LOGO_URL = os.getenv(
    "PLANIX_LOGO_URL",
    "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
)



def is_env_var_set(key: str) -> bool:
    v = os.environ.get(key)
    return bool(v and v.strip().lower() != "none")


CSRF_TRUSTED_ORIGINS = ["https://app-gz.onrender.com"]


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
    "axes",
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
    "notificaciones",
    "bot_gz",
    "dashboard_admin.apps.DashboardAdminConfig",
    "dal",
    "dal_select2",
    "widget_tweaks",
    'geo_cam',
    "django.contrib.humanize",
]

# ===============================
# Middleware
# ===============================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",

    "usuarios.middleware.ChileTimezoneMiddleware",   # singular
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "usuarios.middleware.SessionExpiryMiddleware",   # singular
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
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

TELEGRAM_BOT_TOKEN_GZ = os.getenv("TELEGRAM_BOT_TOKEN_GZ", "")


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

AWS_ACCESS_KEY_ID = os.getenv("WASABI_GZ_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("WASABI_GZ_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.getenv("WASABI_GZ_BUCKET_NAME", "gz-services")
AWS_S3_REGION_NAME = os.getenv("WASABI_GZ_REGION_NAME", "us-west-1")
AWS_S3_ENDPOINT_URL = os.getenv(
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


# =========================
# ALERTAS CONTRATOS RRHH
# =========================
CONTRATOS_CRON_TOKEN = os.environ.get("CONTRATOS_CRON_TOKEN", "")

# Coma-separados, por ahora solo para contratos
# Ej: "rrhh@tuempresa.cl,pm@tuempresa.cl,contador@tuempresa.cl"
CONTRATOS_ALERT_EMAILS = os.environ.get("CONTRATOS_ALERT_EMAILS", "")




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


# ===== django-axes =====
AXES_FAILURE_LIMIT = 5                 # intentos fallidos antes de bloquear
AXES_COOLOFF_TIME = 1                  # horas de enfriamiento
# (por defecto) bloquear por usuario + IP
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
# cuenta por IP+usuario (más estricto)
AXES_LOCKOUT_CALLABLE = None
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True

# Detección IP correcta detrás de Render/Proxy
AXES_IPWARE_META_PRECEDENCE_ORDER = (
    "HTTP_X_FORWARDED_FOR",
    "REMOTE_ADDR",
)

# Útil para auditoría
AXES_IPWARE_PROXY_COUNT = 1

# Opcional: configuración de lugar donde se bota la basura
BOT_GZ_URL_BASURA = "https://www.google.com/maps/search/?api=1&query=-33.5954,-70.5778"  # si quieres
BOT_GZ_TEXTO_BASURA = "Punto autorizado para disposición de residuos de GZ Services."

# ===== CSRF / SSL / Cookies seguras =====

SECURE_SSL_REDIRECT = not DEBUG

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ===== Cabeceras de seguridad =====
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name} :: {message}",
            "style": "{",
        },
        "simple": {"format": "[{levelname}] {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        # Eventos de django-axes (intentos fallidos, bloqueos, etc.)
        "axes": {
            "handlers": ["console"],
            "level": "INFO",   # DEBUG si quieres más ruido
            "propagate": False,
        },
        # Autenticación Django (login/logout, permisos)
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.auth": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Tu app de usuarios (para auditar el login unificado)
        "usuarios": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


REPORT_IMG_WIDEN_X = 1.30          # 1.18–1.28 para “como en la foto”
REPORT_IMG_SIDE_PAD_PX = 9         # margen lateral mínimo
REPORT_IMG_TOP_PAD_PX = 0         # 0 = llena el alto exacto


# Clave de navegador (Static Maps): restringir por Sitios web
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "")

# Clave de servidor (Geocoding): restringir por IP del servidor o sin restricción si no tienes IP fija
GOOGLE_MAPS_SERVER_KEY = os.getenv("GOOGLE_MAPS_SERVER_KEY", "")

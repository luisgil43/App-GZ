from .base import *  # Importa todas las configuraciones base
from .base import *
import os

DEBUG = False

ALLOWED_HOSTS = ['app-gz.onrender.com']


DEBUG = False

ALLOWED_HOSTS = ['app-gz.onrender.com']

# Archivos estáticos
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Middleware para WhiteNoise (importante para servir archivos en Render)
MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
    'whitenoise.middleware.WhiteNoiseMiddleware'
)

# Archivos multimedia
# Aunque Cloudinary los maneja, Django sigue exigiendo estos valores definidos
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Seguridad para producción
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True

# Logs informativos para verificar en consola que todo esté correcto
print("🧪 En producción:")
print("🧪 USE_CLOUDINARY:", os.environ.get("CLOUDINARY_CLOUD_NAME") is not None)
try:
    from django.conf import settings
    print("🧪 DEFAULT_FILE_STORAGE:", settings.DEFAULT_FILE_STORAGE)
except Exception as e:
    print("⚠️ No se pudo importar DEFAULT_FILE_STORAGE:", e)


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'ERROR',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

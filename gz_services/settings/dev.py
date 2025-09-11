from .base import *
import os

DEBUG = True

ALLOWED_HOSTS = [
    'localhost', '127.0.0.1', '172.20.10.3', '172.20.10.2',
    '192.168.1.84', '192.168.1.85', '192.168.1.82', '192.168.1.83', '192.168.1.86'
]

# Opcional: mostrar errores detallados
INTERNAL_IPS = ['127.0.0.1']

# Base de datos local (SQLite)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # opcional: separa DB de local/prod
    }
}

# No forzar HTTPS en desarrollo
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# Email en consola si no está definida la var de entorno
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)

# ❌ IMPORTANTE: no definir aquí DEFAULT_FILE_STORAGE ni CLOUDINARY_STORAGE.
# base.py ya activa Cloudinary automáticamente si existen estas variables:
# CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

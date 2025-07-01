from .base import *
import os

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '172.20.10.2', '192.168.1.82']

# Opcional: permite mostrar errores detallados en el navegador
INTERNAL_IPS = ['127.0.0.1']

# Base de datos local (SQLite)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# No usar HTTPS obligatorio ni cookies seguras en desarrollo
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# Email simulado (no se envían realmente)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# === CLOUDINARY ===
USE_CLOUDINARY = True
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

CLOUDINARY_STORAGE = {
    'CLOUD_NAME': 'dm6gqg4fb',
    'API_KEY': '246778338374567',
    'API_SECRET': 'nC_y5gSK6ZkMTPLvhRcKljIRejc',
}

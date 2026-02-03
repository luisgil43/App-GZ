# setiign-dev.py

import os

from .base import *

DEBUG = True

ALLOWED_HOSTS = [
    'localhost', '127.0.0.1', '172.20.10.3', '172.20.10.2',
    '192.168.1.84', '192.168.1.85', '192.168.1.82', '192.168.1.83','192.168.1.87', '192.168.1.86','192.168.1.81'
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

# 2FA
TWO_FACTOR_ENFORCE_DATE = date(2026, 12, 1)

# No forzar HTTPS en desarrollo

TELEGRAM_BOT_TOKEN_GZ = os.getenv("TELEGRAM_BOT_TOKEN_GZ", "")

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_HTTPONLY = False  # porque lees el csrftoken desde document.cookie
CSRF_TRUSTED_ORIGINS = [
    "https://app-gz.onrender.com",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

# Email en consola si no está definida la var de entorno
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)

# ❌ IMPORTANTE: no definir aquí DEFAULT_FILE_STORAGE ni CLOUDINARY_STORAGE.
# base.py ya activa Cloudinary automáticamente si existen estas variables:
# CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

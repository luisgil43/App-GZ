#setting_prod

import os

from .base import *  # noqa

# ===== Producción =====
DEBUG = False

# Ajusta si usas dominio propio además del de Render
ALLOWED_HOSTS = ['app-gz.onrender.com']

# Static files:
# En base.py ya están:
#   STATIC_URL, STATIC_ROOT y STATICFILES_STORAGE (WhiteNoise)
# y también el middleware de WhiteNoise. No repetir nada aquí.

# Archivos multimedia (Django requiere estos valores definidos)
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

TELEGRAM_BOT_TOKEN_GZ = os.getenv("TELEGRAM_BOT_TOKEN_GZ", "")

# Seguridad
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True


# 2FA
TWO_FACTOR_ENFORCE_DATE = date(2025, 1, 5)  # fecha para producción

# HSTS (solo con HTTPS activo)
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cabeceras seguras
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Logging mínimo (opcional)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'ERROR'},
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}

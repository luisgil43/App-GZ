from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import (LogoutView, PasswordResetCompleteView,
                                       PasswordResetConfirmView,
                                       PasswordResetDoneView,
                                       PasswordResetView)
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import include, path
from django.views.generic.base import RedirectView

from dashboard import views as dashboard_views


def health_check(request):
    return HttpResponse("OK", status=200)


urlpatterns = [
    path('admin/', admin.site.urls),
    # ... tus otras urls ...
]

urlpatterns = [
    # Health check
    path('healthz', health_check),


    path('logout/', LogoutView.as_view(next_page='/usuarios/login/'), name='logout'),


    # Panel de administración personalizado
    path('dashboard_admin/', include(('dashboard_admin.urls',
         'dashboard_admin'), namespace='dashboard_admin')),


    # Dashboard técnico
    path('dashboard/', include(('dashboard.urls', 'dashboard'), namespace='dashboard')),

    # Usuarios
    path('usuarios/', include(('usuarios.urls', 'usuarios'), namespace='usuarios')),

    # Recuperación de contraseña
    path('password_reset/', PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', PasswordResetDoneView.as_view(),
         name='password_reset_done'),
    path('reset/<uidb64>/<token>/', PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('reset/done/', PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),

    # Liquidaciones
    path('liquidaciones/', include(('liquidaciones.urls',
         'liquidaciones'), namespace='liquidaciones')),

    # Redirección raíz a dashboard (usuarios normales)
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),

    # Django Select2
    path("select2/", include("django_select2.urls")),
    # Contratos de trabajos
    path('rrhh/', include('rrhh.urls', namespace='rrhh')),
    path('admin/', admin.site.urls),
    path('dashboard_admin/login/',
         RedirectView.as_view(url='/usuarios/login/', permanent=False)),
    path('logistica/', include('logistica.urls', namespace='logistica')),
    path('operaciones/', include('operaciones.urls')),
    path('notificaciones/', include('notificaciones.urls')),
    path('facturacion/', include('facturacion.urls')),
    path("geo-cam/", include("geo_cam.urls")),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/favicon.ico"),
            permanent=True,
        ),
        name="favicon"
    ),
    path(
        "apple-touch-icon.png",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/apple-touch-icon.png"),
            permanent=True,
        ),
        name="apple_touch_icon"
    ),
    path(
        "apple-touch-icon-precomposed.png",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/apple-touch-icon.png"),
            permanent=True,
        ),
        name="apple_touch_icon_precomposed"
    ),
    # (Opcional) variantes que a veces piden dispositivos antiguos
    path(
        "apple-touch-icon-120x120.png",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/apple-touch-icon-120x120.png"),
            permanent=True,
        ),
        name="apple_touch_icon_120"
    ),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=staticfiles_storage.url("icons/favicon.ico"),
            permanent=True,
        )
    ),
    path(
        "apple-touch-icon.png",
        RedirectView.as_view(
            # o tu apple-touch-icon.png
            url=staticfiles_storage.url("icons/apple-touch-icon-180x180.png"),
            permanent=True,
        )
    ),
    path(
        "apple-touch-icon-precomposed.png",
        RedirectView.as_view(
            url=staticfiles_storage.url("icons/apple-touch-icon-180x180.png"),
            permanent=True,
        )
    ),

    path("bot-gz/", include("bot_gz.urls", namespace="bot_gz")),


]

# Archivos estáticos y media (solo en DEBUG)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
if settings.DEBUG and getattr(settings, 'DEFAULT_FILE_STORAGE', '') == 'django.core.files.storage.FileSystemStorage':
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)

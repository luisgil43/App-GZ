# usuarios/urls.py

from django.urls import path
from django.views.generic import TemplateView

from usuarios.views_switch_mode import switch_mode

from . import views
# from .views import UsuarioLoginView, AdminLoginView
from .views import no_autorizado_view

app_name = 'usuarios'

urlpatterns = [

    path('no-autorizado/', no_autorizado_view, name='no_autorizado'),
    path('firma-representante/subir/', views.subir_firma_representante,
         name='subir_firma_representante'),
    path('recuperar/', views.recuperar_contraseña, name='recuperar_contraseña'),
    path('reset/<int:usuario_id>/<str:token>/',
         views.resetear_contraseña, name='resetear_contraseña'),
    path('recuperar/enviado/', TemplateView.as_view(
        template_name='usuarios/confirmacion_envio.html'), name='confirmacion_envio'),
    path('login/', views.login_unificado, name='login_unificado'),
    path('login/', views.login_unificado, name='login'),
    path('seleccionar-rol/', views.seleccionar_rol, name='seleccionar_rol'),
    path('notificacion/<int:pk>/leer/',
         views.marcar_notificacion_como_leida, name='leer_notificacion'),

         # 2FA
    path('two-factor/', views.two_factor_verify, name='two_factor_verify'),
    path('seguridad/', views.two_factor_setup, name='two_factor_setup'),
    path("switch-mode/", switch_mode, name="switch_mode"),


]

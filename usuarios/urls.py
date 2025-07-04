# usuarios/urls.py

from django.urls import path
# from .views import UsuarioLoginView, AdminLoginView
from .views import no_autorizado_view
from . import views
from django.views.generic import TemplateView


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
    path('login/', views.login_unificado, name='login'),
    path('seleccionar-rol/', views.seleccionar_rol, name='seleccionar_rol'),



]

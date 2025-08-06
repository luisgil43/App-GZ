from django.urls import path
from django.shortcuts import redirect
from . import views

app_name = 'dashboard_admin'

urlpatterns = [
    path('', views.admin_dashboard_view, name='home'),
    path('logout/', views.logout_view, name='logout'),

    # Redirige al login unificado
    path('login/', views.redirigir_a_login_unificado, name='login'),

    path('producciones/', views.produccion_tecnico, name='produccion_tecnico'),
    path('grupos/', views.grupos_view, name='grupos'),

    path('usuarios/', views.listar_usuarios, name='listar_usuarios'),
    path('usuarios/crear/', views.crear_usuario_view, name='crear_usuario'),
    path('usuarios/editar/<int:user_id>/',
         views.editar_usuario_view, name='editar_usuario'),
    path('usuarios/eliminar/<int:user_id>/',
         views.eliminar_usuario_view, name='eliminar_usuario'),

    path('dashboard/', views.inicio_admin, name='inicio_admin'),
    path('no-autorizado/', views.no_autorizado, name='no_autorizado'),
    path('vacaciones/', views.redireccionar_vacaciones, name='vacaciones_admin'),

    path('feriados/', views.listar_feriados, name='listar_feriados'),
    path('feriados/eliminar/<int:pk>/',
         views.eliminar_feriado, name='eliminar_feriado'),

    path('index/', views.inicio_admin, name='index'),
]

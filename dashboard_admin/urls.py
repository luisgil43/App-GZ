from django.urls import path
from . import views
from .views import (
    AdminLoginView, logout_view, admin_dashboard_view,
    produccion_tecnico, grupos_view,
    crear_usuario_view, editar_usuario_view, listar_usuarios, eliminar_usuario_view, redireccionar_vacaciones
)

from dashboard_admin import views as admin_views

app_name = 'dashboard_admin'

urlpatterns = [
    path('', admin_dashboard_view, name='home'),
    # path('dashboard/', admin_dashboard_view, name='index'),

    path('logout/', logout_view, name='logout'),
    path('login/', AdminLoginView.as_view(), name='login'),

    path('producciones/', produccion_tecnico, name='produccion_tecnico'),
    path('grupos/', grupos_view, name='grupos'),

    # vistas de usuarios
    path('usuarios/', listar_usuarios, name='listar_usuarios'),
    path('usuarios/crear/', crear_usuario_view, name='crear_usuario'),
    path('usuarios/editar/<int:user_id>/',
         editar_usuario_view, name='editar_usuario'),
    path('usuarios/eliminar/<int:user_id>/',
         eliminar_usuario_view, name='eliminar_usuario'),
    path('dashboard/', views.inicio_admin, name='inicio_admin'),
    path('no-autorizado/', views.no_autorizado, name='no_autorizado'),
    path('vacaciones/', redireccionar_vacaciones, name='vacaciones_admin'),
    path('feriados/', admin_views.listar_feriados, name='listar_feriados'),
    path('feriados/eliminar/<int:pk>/',
         admin_views.eliminar_feriado, name='eliminar_feriado'),
    path('historial/', views.historial_modelos, name='historial_modelos'),

]

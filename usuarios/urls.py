# usuarios/urls.py

from django.urls import path
from .views import UsuarioLoginView, AdminLoginView
from .views import no_autorizado_view
from . import views


app_name = 'usuarios'

urlpatterns = [
    # Login para usuarios normales
    path('login/', UsuarioLoginView.as_view(), name='login'),
    path('admin/login/', AdminLoginView.as_view(),
         name='admin_login'),   # Login para admins
    # path('logout/', logout_view, name='logout'),
    # path('grupos/', grupos_view, name='grupos'),
    # path('usuarios/', usuarios_view, name='usuarios'),
    path('no-autorizado/', no_autorizado_view, name='no_autorizado'),
    path('firma-representante/subir/', views.subir_firma_representante,
         name='subir_firma_representante'),

]

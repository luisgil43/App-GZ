# operaciones/urls.py

from django.urls import path
from . import views

urlpatterns = [
    path('buscar-mi-sitio/', views.buscar_mi_sitio, name='buscar_mi_sitio'),
    path('importar-sitios/', views.importar_sitios_excel, name='importar_sitios'),
    path('listar-sitios/', views.listar_sitios, name='listar_sitios'),
]

# logistica/urls.py

from django.urls import path
from . import views

app_name = 'logistica'  # <- Esto es esencial

urlpatterns = [
    path('ingresos/', views.listar_ingresos_material, name='listar_ingresos'),
    path('ingreso/', views.registrar_ingreso_material, name='registrar_ingreso'),
    path('materiales/crear/', views.crear_material, name='crear_material'),
    path('materiales/<int:pk>/editar/',
         views.editar_material, name='editar_material'),
    path('materiales/<int:pk>/eliminar/',
         views.eliminar_material, name='eliminar_material'),
    path('materiales/importar/', views.importar_materiales,
         name='importar_materiales'),
    path('exportar/', views.exportar_materiales, name='exportar_materiales'),
    path('ingresos/', views.listar_ingresos_material, name='listar_ingresos'),
    path('ingresos/<int:pk>/editar/',
         views.editar_ingreso_material, name='editar_ingreso'),
    path('ingresos/<int:pk>/eliminar/',
         views.eliminar_ingreso_material, name='eliminar_ingreso'),
    path('bodegas/crear/', views.crear_bodega, name='crear_bodega'),
    path('bodegas/<int:pk>/editar/', views.editar_bodega, name='editar_bodega'),
    path('bodegas/<int:pk>/eliminar/',
         views.eliminar_bodega, name='eliminar_bodega'),



]

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
    path('importar-caf/', views.importar_caf, name='importar_caf'),
    path('salidas/', views.listar_salidas_material, name='listar_salidas'),
    path('salidas/registrar/', views.registrar_salida, name='registrar_salida'),
    path('caf/', views.listar_caf, name='listar_caf'),
    path('caf/<int:pk>/eliminar/', views.eliminar_caf, name='eliminar_caf'),
    path('certificados/', views.importar_certificado,
         name='importar_certificado'),
    path('certificados/<int:pk>/eliminar/',
         views.eliminar_certificado, name='eliminar_certificado'),
    path('salidas/<int:pk>/eliminar/',
         views.eliminar_salida, name='eliminar_salida'),
    path('salidas/firmar/<int:pk>/', views.firmar_salida, name='firmar_salida'),
    path('ajax/material/', views.obtener_datos_material,
         name='obtener_datos_material'),



]

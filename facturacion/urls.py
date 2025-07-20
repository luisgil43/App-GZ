from django.urls import path
from . import views


app_name = 'facturacion'

urlpatterns = [
    path('ordenes/', views.listar_ordenes_compra, name='listar_oc_facturacion'),
    path('ordenes/importar/', views.importar_orden_compra,
         name='importar_orden_compra'),
    path('ordenes/guardar/', views.guardar_ordenes_compra,
         name='guardar_ordenes_compra'),
    path('orden-compra/<int:pk>/editar/',
         views.editar_orden_compra, name='editar_orden_compra'),
    path('ordenes/eliminar/<int:pk>/', views.eliminar_orden_compra,
         name='eliminar_orden_compra'),

]

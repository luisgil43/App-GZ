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
    path('ordenes/exportar/', views.exportar_ordenes_compra_excel,
         name='exportar_ordenes_compra'),
    path('facturas/', views.listar_facturas, name='listar_facturas'),
    path('ordenes/enviar-facturacion-ajax/',
         views.enviar_a_facturacion, name='enviar_a_facturacion'),
    path('facturas/importar/', views.importar_facturas, name='importar_facturas'),
    path("facturas/guardar/", views.guardar_facturas, name="guardar_facturas"),
    path('facturas/editar/<int:pk>/', views.editar_factura, name='editar_factura'),
    path('facturas/eliminar/<int:pk>/',
         views.eliminar_factura, name='eliminar_factura'),
    path('facturas/actualizar/<int:pk>/',
         views.actualizar_factura_ajax, name='actualizar_factura_ajax'),
    path("facturas/exportar/", views.exportar_facturacion_excel,
         name="exportar_facturacion_excel"),

]

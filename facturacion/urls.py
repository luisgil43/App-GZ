from django.urls import path
from . import views
from .views_resumen import View_resumen_dato, ViewResumenDatoAPI


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

    path('cartola/', views.listar_cartola, name='listar_cartola'),
    path('cartola/registrar/', views.registrar_abono, name='registrar_abono'),
    path('cartola/crear-tipo/', views.crear_tipo, name='crear_tipo'),
    path('cartola/editar-tipo/<int:pk>/',
         views.editar_tipo, name='editar_tipo'),
    path('cartola/eliminar-tipo/<int:pk>/',
         views.eliminar_tipo, name='eliminar_tipo'),
    path('proyectos/', views.crear_proyecto, name='crear_proyecto'),
    path('proyectos/editar/<int:pk>/',
         views.editar_proyecto, name='editar_proyecto'),
    path('proyectos/eliminar/<int:pk>/',
         views.eliminar_proyecto, name='eliminar_proyecto'),
    path('cartola/aprobar/<int:pk>/',
         views.aprobar_movimiento, name='aprobar_movimiento'),
    path('cartola/rechazar/<int:pk>/',
         views.rechazar_movimiento, name='rechazar_movimiento'),
    path('cartola/editar/<int:pk>/',
         views.editar_movimiento, name='editar_movimiento'),
    path('cartola/eliminar/<int:pk>/',
         views.eliminar_movimiento, name='eliminar_movimiento'),
    path('saldos-usuarios/', views.listar_saldos_usuarios,
         name='listar_saldos_usuarios'),
    path('cartola/exportar/', views.exportar_cartola_finanzas,
         name='exportar_cartola_finanzas'),
    path('saldos/exportar/', views.exportar_saldos_disponibles,
         name='exportar_saldos_disponibles'),

    path("admin/resumen/", View_resumen_dato.as_view(),
         name="facturacion_resumen"),
    path("admin/resumen/api/", ViewResumenDatoAPI.as_view(),
         name="facturacion_resumen_api"),



]

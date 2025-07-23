# operaciones/urls.py

from django.urls import path
from . import views

app_name = 'operaciones'  # <--- ESTA LÍNEA ES OBLIGATORIA PARA USAR NAMESPACES

urlpatterns = [
    path('buscar-mi-sitio/', views.buscar_mi_sitio, name='buscar_mi_sitio'),
    path('importar-sitios/', views.importar_sitios_excel, name='importar_sitios'),
    path('listar-sitios/', views.listar_sitios, name='listar_sitios'),
    path('pm/crear/', views.crear_servicio_cotizado,
         name='crear_servicio_cotizado'),
    path('pm/listar/', views.listar_servicios_pm, name='listar_servicios_pm'),
    path('pm/editar/<int:pk>/', views.editar_servicio_cotizado,
         name='editar_servicio_cotizado'),
    path('pm/eliminar/<int:pk>/', views.eliminar_servicio_cotizado,
         name='eliminar_servicio_cotizado'),
    path('ajax/obtener-datos-sitio/',
         views.obtener_datos_sitio, name='obtener_datos_sitio'),
    path('cotizaciones/<int:pk>/aprobar/',
         views.aprobar_cotizacion, name='aprobar_cotizacion'),
    path('pm/importar/', views.importar_cotizaciones,
         name='importar_cotizaciones'),


    path('supervisor/listar/', views.listar_servicios_supervisor,
         name='listar_servicios_supervisor'),
    path('cotizaciones/<int:pk>/asignar/',
         views.asignar_trabajadores, name='asignar_cotizacion'),
    path('mis-servicios/', views.mis_servicios_tecnico,
         name='mis_servicios_tecnico'),
    path('aceptar-servicio/<int:servicio_id>/',
         views.aceptar_servicio, name='aceptar_servicio'),
    path('finalizar-servicio/<int:servicio_id>/',
         views.finalizar_servicio, name='finalizar_servicio'),
    path('servicios/<int:pk>/rechazar-asignacion/',
         views.rechazar_asignacion, name='rechazar_asignacion'),
    path('servicios/<int:pk>/aprobar-asignacion/',
         views.aprobar_asignacion, name='aprobar_asignacion'),
    path('servicios/supervisor/exportar/', views.exportar_servicios_supervisor,
         name='exportar_servicios_supervisor'),
    path('advertencia-duplicados/', views.advertencia_cotizaciones_omitidas,
         name='advertencia_cotizaciones_omitidas'),
    path('produccion/', views.produccion_tecnico, name='produccion_tecnico'),
    path('produccion/exportar-pdf/', views.exportar_produccion_pdf,
         name='exportar_produccion_pdf'),

]

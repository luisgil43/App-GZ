# operaciones/urls.py

from django.urls import path
from . import views
from . import views_fotos
from . import views_fotos as fotos
from . import views_produccion as produc


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
    path('servicios/<int:pk>/actualizar-motivo/',
         views.actualizar_motivo_rechazo, name='actualizar_motivo_rechazo'),
    path('mis-rendiciones/', views.mis_rendiciones, name='mis_rendiciones'),
    path('aprobar_abono/<int:pk>/', views.aprobar_abono, name='aprobar_abono'),
    path('rechazar_abono/<int:pk>/', views.rechazar_abono, name='rechazar_abono'),
    path('mis-rendiciones/editar/<int:pk>/',
         views.editar_rendicion, name='editar_rendicion'),
    path('mis-rendiciones/eliminar/<int:pk>/',
         views.eliminar_rendicion, name='eliminar_rendicion'),

    path('rendiciones/', views.vista_rendiciones, name='vista_rendiciones'),
    path('rendiciones/aprobar/<int:pk>/',
         views.aprobar_rendicion, name='aprobar_rendicion'),
    path('rendiciones/rechazar/<int:pk>/',
         views.rechazar_rendicion, name='rechazar_rendicion'),
    path('validar-rut-ajax/', views.validar_rut_ajax, name='validar_rut_ajax'),
    path('rendiciones/exportar/', views.exportar_rendiciones_pm,
         name='exportar_rendiciones_pm'),
    path('mis-rendiciones/exportar/', views.exportar_mis_rendiciones,
         name='exportar_mis_rendiciones'),
    path("sitios/<int:pk>/editar/", views.editar_sitio, name="editar_sitio"),
    path("sitios/<int:pk>/eliminar/", views.eliminar_sitio, name="eliminar_sitio"),


    # Supervisor
    path("fotos/servicio/<int:servicio_id>/revisar/",
         fotos.revisar_sesion_fotos, name="fotos_revisar_sesion"),

    # Requisitos de fotos
    path("servicios/<int:servicio_id>/fotos/requisitos/",
         fotos.configurar_requisitos, name="fotos_configurar_requisitos"),
    path("servicios/<int:servicio_id>/fotos/requisitos/importar/",
         fotos.import_requirements_page, name="fotos_import_requirements_page"),
    path("servicios/<int:servicio_id>/fotos/requisitos/descargar/<str:ext>/",
         fotos.download_requirements_template, name="fotos_download_requirements_template"),
    path("servicios/<int:servicio_id>/fotos/requisitos/importar/submit/",
         fotos.importar_requisitos, name="fotos_importar_requisitos"),

    # Atajo desde el servicio para que el técnico entre a su upload
    path("servicios/<int:servicio_id>/fotos/",
         views.ir_a_upload_fotos, name="ir_a_upload_fotos"),

    # Upload del técnico
    path("fotos/asignacion/<int:pk>/upload/",
         fotos.upload_evidencias_fotos, name="fotos_upload"),
    path("fotos/evidencia/<int:ev_id>/borrar/",
         fotos.borrar_evidencia_foto, name="fotos_borrar_evidencia"),
    path(
        "fotos/evidencia/<int:ev_id>/borrar-supervisor/", fotos.borrar_evidencia_supervisor,
        name="fotos_borrar_evidencia_supervisor",),

    path("fotos/servicio/<int:servicio_id>/reporte-parcial/",
         views_fotos.generar_reporte_parcial_proyecto,
         name="generar_reporte_parcial_proyecto",
         ),

    path("operaciones/fotos/servicio/<int:servicio_id>/acta/preview/",
         views_fotos.generar_acta_preview,
         name="generar_acta_preview"),
    path('servicios/<int:pk>/reabrir/',
         views.reabrir_servicio, name='reabrir_servicio'),

    path("operaciones/fotos/upload-ajax/<int:pk>/",
         views_fotos.upload_evidencias_ajax, name="fotos_upload_ajax"),

    path("fotos/asignacion/<int:asig_id>/status/", views_fotos.fotos_status_json,
         name="fotos_status_json"),

    path("fotos/<int:asig_id>/presign/",
         views_fotos.presign_put, name="fotos_presign"),
    path("fotos/<int:asig_id>/finalize/",
         views_fotos.finalize_upload, name="fotos_finalize"),
    path("produccion/listar/", produc.produccion_admin, name="produccion_admin"),

    path("produccion/exportar/", produc.exportar_produccion_admin,
         name="exportar_produccion_admin"),
    path("produccion/pagos/", produc.admin_monthly_payments,
         name="produccion_totales_a_pagar"),
    path("produccion/pagos/exportar/", produc.exportar_totales_produccion,
         name="exportar_totales_produccion"),

    # Admin mensual
    path("produccion/pagos/", produc.admin_monthly_payments,
         name="admin_monthly_payments"),
    path("produccion/pagos/presign/<int:pk>/",
         produc.presign_receipt_monthly, name="presign_receipt_monthly"),
    path("produccion/pagos/confirm/<int:pk>/",
         produc.confirm_receipt_monthly, name="confirm_receipt_monthly"),
    path("produccion/pagos/unpay/<int:pk>/",
         produc.admin_unpay_monthly, name="admin_unpay_monthly"),

    # Usuario mensual
    path("produccion/mis-pagos/", produc.user_monthly_payments,
         name="user_monthly_payments"),
    path("produccion/mis-pagos/approve/<int:pk>/",
         produc.user_approve_monthly, name="user_approve_monthly"),
    path("produccion/mis-pagos/reject/<int:pk>/",
         produc.user_reject_monthly, name="user_reject_monthly"),
    path("produccion/tecnico/", produc.produccion_tecnico,
         name="produccion_tecnico"),
    path('ajustes/crear/', produc.crear_ajuste, name='crear_ajuste'),
    path("ajustes/<int:pk>/", produc.editar_ajuste,
         name="editar_ajuste"),  # GET=datos / POST=guardar
    path("ajustes/<int:pk>/eliminar/",
         produc.eliminar_ajuste, name="eliminar_ajuste"),





]

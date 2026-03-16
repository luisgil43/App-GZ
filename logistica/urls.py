from django.urls import path

from . import views, views_herramientas_admin, views_herramientas_user

app_name = "logistica"

urlpatterns = [
    # ===== Materiales / Ingresos =====
    path("ingresos/", views.listar_ingresos_material, name="listar_ingresos"),
    path("ingreso/", views.registrar_ingreso_material, name="registrar_ingreso"),
    path("ingresos/<int:pk>/editar/", views.editar_ingreso_material, name="editar_ingreso"),
    path("ingresos/<int:pk>/eliminar/", views.eliminar_ingreso_material, name="eliminar_ingreso"),

    path("materiales/crear/", views.crear_material, name="crear_material"),
    path("materiales/<int:pk>/editar/", views.editar_material, name="editar_material"),
    path("materiales/<int:pk>/eliminar/", views.eliminar_material, name="eliminar_material"),
    path("materiales/importar/", views.importar_materiales, name="importar_materiales"),
    path("exportar/", views.exportar_materiales, name="exportar_materiales"),

    # ===== CAF =====
    path("importar-caf/", views.importar_caf, name="importar_caf"),
    path("caf/", views.listar_caf, name="listar_caf"),
    path("caf/<int:pk>/eliminar/", views.eliminar_caf, name="eliminar_caf"),

    # ===== Salidas =====
    path("salidas/", views.listar_salidas_material, name="listar_salidas"),
    path("salidas/registrar/", views.registrar_salida, name="registrar_salida"),
    path("salidas/<int:pk>/eliminar/", views.eliminar_salida, name="eliminar_salida"),
    path("salidas/firmar/<int:pk>/", views.firmar_salida, name="firmar_salida"),

    # ===== Certificados =====
    path("certificados/", views.importar_certificado, name="importar_certificado"),
    path("certificados/<int:pk>/eliminar/", views.eliminar_certificado, name="eliminar_certificado"),

    # ===== AJAX =====
    path("ajax/material/", views.obtener_datos_material, name="obtener_datos_material"),

    # =========================
    # USUARIO (solo rol usuario)
    # =========================
    path("mis-herramientas/", views_herramientas_user.mis_herramientas, name="mis_herramientas"),
    path("mis-herramientas/aceptar/", views_herramientas_user.aceptar_herramientas, name="aceptar_herramientas"),
    path("mis-herramientas/rechazar/<int:asignacion_id>/", views_herramientas_user.rechazar_herramienta, name="rechazar_herramienta"),
    path("mis-herramientas/inventario/<int:asignacion_id>/", views_herramientas_user.subir_inventario, name="subir_inventario"),
    path("mis-herramientas/inventario/<int:asignacion_id>/historial/", views_herramientas_user.historial_inventario, name="historial_inventario"),

    # =========================
    # ADMIN / LOGISTICA
    # =========================
    path("herramientas/", views_herramientas_admin.herramientas_list, name="herramientas_list"),
    path("herramientas/crear/", views_herramientas_admin.herramienta_create, name="herramienta_create"),
    path("herramientas/<int:tool_id>/editar/", views_herramientas_admin.herramienta_edit, name="herramienta_edit"),
    path("herramientas/<int:tool_id>/eliminar/", views_herramientas_admin.herramienta_delete, name="herramienta_delete"),

    path("herramientas/<int:tool_id>/asignar/", views_herramientas_admin.herramienta_assign, name="herramienta_assign"),
    path("herramientas/<int:tool_id>/reiniciar-asignacion/", views_herramientas_admin.herramienta_reset_assignment_status, name="herramienta_reset_assignment_status"),
    path("herramientas/<int:tool_id>/cambiar-estado/", views_herramientas_admin.herramienta_change_status, name="herramienta_change_status"),

    path("herramientas/<int:tool_id>/inventario/solicitar/", views_herramientas_admin.solicitar_inventario, name="solicitar_inventario"),
    path("herramientas/inventario/<int:inv_id>/aprobar/", views_herramientas_admin.aprobar_inventario, name="aprobar_inventario"),
    path("herramientas/inventario/<int:inv_id>/rechazar/", views_herramientas_admin.rechazar_inventario, name="rechazar_inventario"),
    path("herramientas/<int:tool_id>/inventario/historial/", views_herramientas_admin.inventario_historial_admin, name="inventario_historial_admin"),

    # ✅ NUEVO: historial de asignaciones (trazabilidad)
    path("herramientas/<int:tool_id>/asignaciones/historial/", views_herramientas_admin.asignaciones_historial_admin, name="asignaciones_historial_admin"),

    path("bodegas/", views_herramientas_admin.bodegas_manage, name="bodegas_manage"),
    path("bodegas/<int:bodega_id>/eliminar/", views_herramientas_admin.bodega_delete, name="bodega_delete"),
    path("herramientas/exportar/", views_herramientas_admin.exportar_herramientas_excel, name="exportar_herramientas_excel"),
]
# flota/urls.py
from django.urls import path

from . import views, views_cron

app_name = "flota"

urlpatterns = [
    path("", views.vehicle_list, name="vehicle_list"),

    path("vehiculos/crear/", views.vehicle_create, name="vehicle_create"),
    path("vehiculos/<int:pk>/editar/", views.vehicle_edit, name="vehicle_edit"),
    path("vehiculos/<int:pk>/eliminar/", views.vehicle_delete, name="vehicle_delete"),
    path("vehiculos/<int:pk>/status/", views.vehicle_change_status, name="vehicle_change_status"),

    path("status/", views.status_manage, name="status_manage"),
    path("status/<int:pk>/editar/", views.status_manage, name="status_edit"),
    path("status/<int:pk>/toggle/", views.status_toggle_active, name="status_toggle_active"),
    path("status/<int:pk>/eliminar/", views.status_delete, name="status_delete"),

    path("asignaciones/", views.assignment_list, name="assignment_list"),
    path("asignaciones/crear/", views.assignment_create, name="assignment_create"),
    path("asignaciones/<int:pk>/editar/", views.assignment_edit, name="assignment_edit"),
    path("asignaciones/<int:pk>/toggle/", views.assignment_toggle_active, name="assignment_toggle_active"),
    path("asignaciones/<int:pk>/cerrar/", views.assignment_close, name="assignment_close"),
    path("asignaciones/<int:pk>/eliminar/", views.assignment_delete, name="assignment_delete"),

    # ✅ Servicios
    path("servicios/", views.service_list, name="service_list"),
    path("servicios/crear/", views.service_create, name="service_create"),
    path("servicios/<int:pk>/editar/", views.service_edit, name="service_edit"),
    path("servicios/<int:pk>/eliminar/", views.service_delete, name="service_delete"),

    # ✅ Tipos de servicio
    path("tipos-servicio/", views.service_type_manage, name="service_type_manage"),
    path("tipos-servicio/<int:pk>/editar/", views.service_type_manage, name="service_type_edit"),
    path("tipos-servicio/<int:pk>/toggle/", views.service_type_toggle_active, name="service_type_toggle_active"),
    path("tipos-servicio/<int:pk>/eliminar/", views.service_type_delete, name="service_type_delete"),

    # ✅ Notificaciones (tu módulo)
    path("notificaciones/", views.notification_list, name="notification_list"),
    path("notificaciones/<int:vehicle_id>/editar/", views.notification_edit, name="notification_edit"),

    # ✅ CRON Flota (igual RRHH)
    path("cron/mantenciones/", views_cron.cron_flota_mantenciones, name="cron_flota_mantenciones"),
]
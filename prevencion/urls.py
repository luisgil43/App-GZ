from django.urls import path

from . import views, views_cron

app_name = "prevencion"

urlpatterns = [
    path("", views.dashboard_prevencion, name="dashboard"),

    path("documentos/crear/", views.document_create, name="document_create"),
    path("documentos/<int:pk>/editar/", views.document_edit, name="document_edit"),
    path("documentos/<int:pk>/reemplazar/", views.document_replace, name="document_replace"),
    path("documentos/<int:pk>/eliminar/", views.document_delete, name="document_delete"),
    path("documentos/<int:pk>/descargar/", views.document_download, name="document_download"),

    path("tipos/", views.type_manage, name="type_manage"),
    path("tipos/<int:pk>/editar/", views.type_manage, name="type_edit"),
    path("tipos/<int:pk>/toggle/", views.type_toggle, name="type_toggle"),
    path("tipos/<int:pk>/delete/", views.type_delete, name="type_delete"),

    path("notificaciones/", views.notifications_edit, name="notifications_edit"),

   path(
        "historial-general-excel/",
        views.download_general_history_excel,
        name="download_general_history_excel",
    ),

    path(
    "trabajadores/<int:worker_id>/documentos-zip/",
    views.worker_document_zip,
    name="worker_document_zip",
),

 path(
        "empresa/tipos/<int:type_id>/documentos-zip/",
        views.company_document_zip,
        name="company_document_zip",
    ),
    # CRON
    path("cron/documentos/", views_cron.cron_prevencion_documentos, name="cron_prevencion_documentos"),
]
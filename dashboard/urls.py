from django.urls import include, path

from . import views

app_name = "dashboard"


urlpatterns = [
    # Dashboard principal del usuario
    path("", views.inicio, name="inicio"),
    # Ruta alternativa al mismo inicio
    path(
        "inicio/",
        views.inicio_tecnico,
        name="inicio_tecnico",
    ),
    path(
        "mis-cursos/",
        views.mis_cursos_view,
        name="mis_cursos",
    ),
    path(
        "detalle/<int:produccion_id>/",
        views.dashboard_detalle_view,
        name="dashboard_detalle",
    ),
    path(
        "produccion/",
        views.produccion_tecnicos_view,
        name="produccion_tecnicos",
    ),
    path(
        "produccion/pdf/",
        views.produccion_tecnicos_pdf,
        name="produccion_tecnicos_pdf",
    ),
    # Subsección de recursos humanos
    path(
        "rrhh/liquidaciones/",
        include(
            (
                "liquidaciones.urls",
                "liquidaciones",
            ),
            namespace="liquidaciones",
        ),
    ),
    # Logout usando vista personalizada
    path(
        "logout/",
        views.logout_view,
        name="logout",
    ),
    # Dashboard anterior/secundario
    path(
        "dashboard/",
        views.dashboard_view,
        name="home",
    ),
    path(
        "mi-firma/",
        views.registrar_firma_usuario,
        name="registrar_firma_usuario",
    ),
]

from django.urls import path

from . import views_comunicados, views_cron

app_name = "notificaciones"

urlpatterns = [
    path("diario/", views_cron.cron_diario_general, name="cron_diario_general"),
    path(
        "comunicados/telegram/",
        views_comunicados.enviar_comunicado_telegram_view,
        name="enviar_comunicado_telegram",
    ),
]

from django.urls import path

from . import views_cron

app_name = "notificaciones"

urlpatterns = [
    path("diario/", views_cron.cron_diario_general, name="cron_diario_general"),
]
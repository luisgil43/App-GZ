# bot_gz/urls.py

from __future__ import annotations

from django.urls import path

from . import views

app_name = "bot_gz"

urlpatterns = [
    # Health-check para probar rápido desde el navegador o UptimeRobot
    path("telegram/health/", views.telegram_health, name="telegram_health"),

    # Webhook que ya configuraste en setWebhook
    path("telegram/webhook/", views.telegram_webhook, name="telegram_webhook"),

    # Consola de entrenamiento del bot
    path("training/", views.training_dashboard, name="training_dashboard"),
    
    path("training/<int:pk>/", views.training_edit_message, name="training_edit_message"),
]

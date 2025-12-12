# notificaciones/admin.py

from django.contrib import admin

from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "canal",
        "tipo",
        "usuario",
        "actor",
        "du",
        "status",
        "created_at",
        "sent_at",
    )
    list_filter = (
        "canal",
        "tipo",
        "status",
        "created_at",
    )
    search_fields = (
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "actor__username",
        "actor__first_name",
        "actor__last_name",
        "du",
        "mensaje",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
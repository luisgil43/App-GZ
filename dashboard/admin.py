# dashboard/admin.py
from django.contrib import admin
from dashboard.models import ProduccionTecnico
from gz_services.custom_admin import custom_admin_site


@admin.register(ProduccionTecnico, site=custom_admin_site)
class ProduccionTecnicoAdmin(admin.ModelAdmin):
    list_display = ('tecnico', 'id', 'status',
                    'fecha_aprobacion', 'descripcion')

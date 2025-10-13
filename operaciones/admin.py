# operaciones/admin.py

from django.contrib import admin
from .models import SitioMovil


@admin.register(SitioMovil)
class SitioMovilAdmin(admin.ModelAdmin):
    list_display = ('id_sites', 'nombre', 'comuna', 'region')
    search_fields = ('id_sites', 'nombre', 'comuna')

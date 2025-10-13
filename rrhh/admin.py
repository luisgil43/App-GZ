from django.contrib import admin
from .models import ContratoTrabajo
from .models import Feriado
admin.site.register(Feriado)


@admin.register(ContratoTrabajo)
class ContratoTrabajoAdmin(admin.ModelAdmin):
    list_display = ('id', 'tecnico', 'fecha_inicio',
                    'fecha_termino', 'archivo')
    list_filter = ('fecha_inicio', 'fecha_termino')
    search_fields = ('tecnico__first_name',
                     'tecnico__last_name', 'tecnico__username')
    ordering = ('-fecha_inicio',)

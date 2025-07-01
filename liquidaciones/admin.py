# liquidaciones/admin.py
from gz_services.custom_admin import custom_admin_site
from django.contrib import admin
from django.contrib.admin import ModelAdmin

from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.urls import path
from django.shortcuts import render, redirect
from django.utils.html import format_html
from django.core.files.base import ContentFile
from decimal import Decimal

from .models import Liquidacion
from .forms import LiquidacionForm


class LiquidacionAdmin(admin.ModelAdmin):
    form = LiquidacionForm
    list_display = ('tecnico', 'mes', 'año',
                    'firmada', 'fecha_firma', 'ver_pdf_firmado')
    readonly_fields = ('firmada', 'fecha_firma', 'pdf_firmado')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'confirmar-reemplazo/',
                self.admin_site.admin_view(self.confirmar_reemplazo_view),
                name='liquidaciones_liquidacion_confirmar_reemplazo'
            ),
        ]
        return custom + urls

    def ver_pdf_firmado(self, obj):
        if obj.pdf_firmado:
            return format_html(
                '<a href="{}" target="_blank">Descargar PDF Firmado</a>',
                obj.pdf_firmado.url
            )
        return "-"
    ver_pdf_firmado.short_description = "PDF Firmado"

    def add_view(self, request, form_url='', extra_context=None):
        if request.method == "POST" and not request.POST.get("_reemplazar") and not request.POST.get("_cancelar"):
            form = self.get_form(request)(request.POST, request.FILES)
            if form.is_valid():
                t = form.cleaned_data['tecnico']
                m = form.cleaned_data['mes']
                a = form.cleaned_data['año']
                existe = Liquidacion.objects.filter(
                    tecnico=t, mes=m, año=a).first()
                if existe:
                    if request.FILES.get('archivo_pdf_liquidacion'):
                        archivo = request.FILES['archivo_pdf_liquidacion']
                        archivo_binario = archivo.read()
                        request.session['archivo_temporal'] = {
                            'bytes': archivo_binario,
                            'name': archivo.name,
                        }
                    request.session['duplicado_data'] = {
                        'tecnico': t.id, 'mes': m, 'año': a,
                    }
                    return redirect('admin:liquidaciones_liquidacion_confirmar_reemplazo')
        return super().add_view(request, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    def confirmar_reemplazo_view(self, request):
        if request.method == 'POST':
            if '_cancelar' in request.POST:
                request.session.pop('archivo_temporal', None)
                request.session.pop('duplicado_data', None)
                messages.info(
                    request, "❌ Se canceló el reemplazo de la liquidación.")
                return redirect('admin:liquidaciones_liquidacion_changelist')

            if '_reemplazar' in request.POST:
                data = request.session.pop('duplicado_data', None)
                arch = request.session.pop('archivo_temporal', None)
                if data and arch:
                    anterior = Liquidacion.objects.filter(
                        tecnico_id=data['tecnico'], mes=data['mes'], año=data['año']
                    ).first()
                    if anterior:
                        if anterior.firmada and anterior.pdf_firmado and anterior.pdf_firmado.storage.exists(anterior.pdf_firmado.name):
                            anterior.pdf_firmado.delete(save=False)
                        anterior.delete()

                    archivo = ContentFile(arch['bytes'], name=arch['name'])
                    nueva = Liquidacion(
                        tecnico_id=data['tecnico'],
                        mes=data['mes'],
                        año=data['año'],
                        firmada=False
                    )
                    nueva.archivo_pdf_liquidacion.save(
                        arch['name'], archivo, save=True)
                    messages.success(
                        request, "✅ Liquidación reemplazada correctamente.")
                return redirect('admin:liquidaciones_liquidacion_changelist')

        data = request.session.get('duplicado_data', {})
        arch = request.session.get('archivo_temporal', {})
        tecnico_obj = None
        if data.get('tecnico'):
            from tecnicos.models import Tecnico
            tecnico_obj = Tecnico.objects.get(pk=data['tecnico'])

        return render(request, 'liquidaciones/confirmar_reemplazo.html', {
            'tecnico_id': data.get('tecnico'),
            'tecnico': tecnico_obj,
            'mes': data.get('mes'),
            'año': data.get('año'),
            'pdf_name': arch.get('name'),
        })


admin.site.register(Liquidacion, LiquidacionAdmin)

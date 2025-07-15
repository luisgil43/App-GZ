# operaciones/views.py

import pandas as pd
from django.contrib import messages
from django.shortcuts import render, redirect
from django.shortcuts import render
from .models import SitioMovil


def buscar_mi_sitio(request):
    id_sitio = request.GET.get("id")
    sitio = None
    buscado = False

    if id_sitio:
        buscado = True
        try:
            obj = SitioMovil.objects.get(id_claro=id_sitio)

            sitio = {}
            for field in obj._meta.fields:
                if field.name != 'id':
                    valor = getattr(obj, field.name)
                    # Normalizar coordenadas si fueran string (por seguridad)
                    if field.name.lower() in ['latitud', 'longitud'] and isinstance(valor, str):
                        valor = valor.replace(",", ".")
                    sitio[field.verbose_name] = str(valor)

        except SitioMovil.DoesNotExist:
            sitio = None

    return render(request, 'operaciones/buscar_mi_sitio.html', {
        'sitio': sitio,
        'buscado': buscado
    })


# operaciones/views.py


def listar_sitios(request):
    id_claro = request.GET.get("id_claro", "")
    sitios = SitioMovil.objects.all()

    if id_claro:
        sitios = sitios.filter(id_claro__icontains=id_claro)

    return render(request, 'operaciones/listar_sitios.html', {
        'sitios': sitios,
        'id_claro': id_claro
    })


# operaciones/views.py


def importar_sitios_excel(request):
    if request.method == 'POST' and request.FILES.get('archivo'):
        archivo = request.FILES['archivo']

        try:
            df = pd.read_excel(archivo)

            sitios_creados = 0
            for _, row in df.iterrows():
                # Normalizamos coordenadas reemplazando ',' por '.'
                latitud = float(str(row.get('Latitud')).replace(
                    ',', '.')) if pd.notna(row.get('Latitud')) else None
                longitud = float(str(row.get('Longitud')).replace(
                    ',', '.')) if pd.notna(row.get('Longitud')) else None

                sitio, created = SitioMovil.objects.update_or_create(
                    id_sites=row.get('ID Sites'),
                    defaults={
                        'id_claro': row.get('ID Claro'),
                        'id_sites_new': row.get('ID Sites NEW'),
                        'region': row.get('Región'),
                        'nombre': row.get('Nombre'),
                        'direccion': row.get('Direccion'),
                        'latitud': latitud,
                        'longitud': longitud,
                        'comuna': row.get('Comuna'),
                        'tipo_construccion': row.get('Tipo de contruccion'),
                        'altura': row.get('Altura'),
                        'candado_bt': row.get('Candado BT'),
                        'condiciones_acceso': row.get('Condiciones de acceso'),
                        'claves': row.get('Claves'),
                        'llaves': row.get('Llaves'),
                        'cantidad_llaves': row.get('Cantidad de Llaves'),
                        'observaciones_generales': row.get('Observaciones Generales'),
                        'zonas_conflictivas': row.get('Sitios zonas conflictivas'),
                        'alarmas': row.get('Alarmas'),
                        'guardias': row.get('Guardias'),
                        'nivel': row.get('Nivel'),
                        'descripcion': row.get('Descripción'),
                    }
                )
                if created:
                    sitios_creados += 1

            messages.success(
                request, f'Se importaron correctamente {sitios_creados} sitios.')
            return redirect('listar_sitios')

        except Exception as e:
            messages.error(request, f'Ocurrió un error al importar: {str(e)}')

    return render(request, 'operaciones/importar_sitios.html')

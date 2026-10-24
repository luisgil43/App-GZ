from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from operaciones.models import SitioMovil
from usuarios.decoradores import rol_requerido


@login_required
@rol_requerido("admin", "pm", "supervisor", "facturacion")
@require_GET
def consultar_detalle_sitio_movil(request, sitio_id):
    sitio = get_object_or_404(
        SitioMovil,
        pk=sitio_id,
    )

    campos = [
        ("id_sites", "ID Sites"),
        ("id_claro", "ID Claro"),
        ("id_sites_new", "ID NEW"),
        ("nombre", "Nombre"),
        ("region", "Región"),
        ("comuna", "Comuna"),
        ("direccion", "Dirección"),
        ("latitud", "Latitud"),
        ("longitud", "Longitud"),
        ("tipo_construccion", "Tipo de construcción"),
        ("altura", "Altura"),
        ("tipo_zona", "Tipo de zona"),
        ("candado_bt", "Candado BT"),
        ("condiciones_acceso", "Condiciones de acceso"),
        ("claves", "Claves"),
        ("llaves", "Llaves"),
        ("cantidad_llaves", "Cantidad de llaves"),
        ("observaciones_generales", "Observaciones generales"),
        ("zonas_conflictivas", "Zonas conflictivas"),
        ("alarmas", "Alarmas"),
        ("guardias", "Guardias"),
        ("nivel", "Nivel"),
        ("descripcion", "Descripción"),
    ]

    informacion = [
        {
            "campo": campo,
            "etiqueta": etiqueta,
            "valor": getattr(sitio, campo, None),
        }
        for campo, etiqueta in campos
    ]

    return JsonResponse(
        {
            "ok": True,
            "sitio_id": sitio.pk,
            "titulo": sitio.nombre or sitio.id_claro or sitio.id_sites,
            "campos": informacion,
        }
    )

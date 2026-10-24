from django.contrib.auth.decorators import login_required
from django.db.models import Q

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from operaciones.models import SitioMovil
from usuarios.decoradores import rol_requerido



# GZ_CONTACTOS_VISOR_V2
def _contactos_activos_para_visor(sitio):
    from planificacion.models import ContactoSitio

    campos = [
        ("propietario", "Propietario / contacto"),
        ("tipo_contacto", "Tipo de contacto"),
        ("telefono", "Teléfono"),
        ("correo", "Correo electrónico"),
        ("fecha_informacion", "Fecha de información"),
        ("responsable", "Responsable"),
        ("observaciones", "Observaciones"),
        ("accion", "Acción / indicaciones"),
    ]

    contactos = (
        ContactoSitio.objects
        .filter(sitio_id=sitio.pk, activo=True)
        .order_by("prioridad_contacto", "id")
    )

    resultado = []

    for contacto in contactos:
        informacion = []

        for campo, etiqueta in campos:
            valor = getattr(contacto, campo, None)

            if campo == "fecha_informacion" and valor:
                valor = valor.strftime("%d/%m/%Y")

            informacion.append({
                "campo": campo,
                "etiqueta": etiqueta,
                "valor": valor,
            })

        resultado.append({
            "id": contacto.pk,
            "prioridad": contacto.prioridad_contacto,
            "campos": informacion,
        })

    return resultado


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
            "contactos": _contactos_activos_para_visor(sitio),
            "titulo": sitio.nombre or sitio.id_claro or sitio.id_sites,
            "campos": informacion,
        }
    )


# GZ_VISOR_SITIO_TECNICOS_V1
@login_required
@rol_requerido("usuario")
@require_GET
def consultar_detalle_sitio_movil_tecnico(request, sitio_id):
    """
    Consulta de sitio exclusivamente para técnicos asignados.

    No modifica los permisos de la consulta administrativa.
    """
    from operaciones.models import ServicioCotizado

    sitio = get_object_or_404(SitioMovil, pk=sitio_id)

    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=request.user,
    ).exclude(
        estado__in=[
            "cotizado",
            "aprobado_supervisor",
            "ajuste_adelanto",
            "ajuste_descuento",
        ]
    )

    relacion_directa = servicios.filter(
        sitio_planificado__sitio_id=sitio.pk,
    ).exists()

    autorizado = relacion_directa

    if not autorizado and sitio.id_claro:
        # La asociación histórica solo es válida si ID Claro
        # identifica exactamente un registro de SitioMovil.
        cantidad = SitioMovil.objects.filter(
            id_claro=sitio.id_claro,
        ).count()

        if cantidad == 1:
            autorizado = servicios.filter(
                sitio_planificado__isnull=True,
                id_claro=sitio.id_claro,
            ).exists()

    if not autorizado:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied(
            "No tienes autorización para consultar este sitio."
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

    return JsonResponse({
        "ok": True,
        "sitio_id": sitio.pk,
            "contactos": _contactos_activos_para_visor(sitio),
        "titulo": sitio.nombre or sitio.id_claro or sitio.id_sites,
        "campos": [
            {
                "campo": campo,
                "etiqueta": etiqueta,
                "valor": getattr(sitio, campo, None),
            }
            for campo, etiqueta in campos
        ],
    })

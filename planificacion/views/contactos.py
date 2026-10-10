from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from planificacion.models import ContactoSitio, ImportacionContactosSitios
from usuarios.decoradores import rol_requerido


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def listar_contactos(request):
    buscar = (request.GET.get("q") or "").strip()

    raw_cantidad = request.GET.get("cantidad", "20")

    try:
        per_page = int(raw_cantidad)
    except (TypeError, ValueError):
        per_page = 20

    if per_page not in {10, 20, 50, 100}:
        per_page = 20

    cantidad = str(per_page)

    contactos = (
        ContactoSitio.objects.select_related("sitio")
        .filter(activo=True)
        .order_by(
            "id_origen",
            "prioridad_contacto",
            "id",
        )
    )

    if buscar:
        contactos = contactos.filter(
            Q(id_origen__icontains=buscar)
            | Q(nombre_sitio__icontains=buscar)
            | Q(propietario__icontains=buscar)
            | Q(telefono__icontains=buscar)
            | Q(correo__icontains=buscar)
            | Q(responsable__icontains=buscar)
            | Q(observaciones__icontains=buscar)
            | Q(accion__icontains=buscar)
            | Q(sitio__nombre__icontains=buscar)
            | Q(sitio__id_claro__icontains=buscar)
        )

    paginator = Paginator(
        contactos,
        per_page,
    )

    pagina = paginator.get_page(request.GET.get("page", 1))

    ultima_importacion = (
        ImportacionContactosSitios.objects.filter(estado="aplicada")
        .select_related("creado_por")
        .order_by("-aplicado_en", "-id")
        .first()
    )

    return render(
        request,
        "planificacion/contactos/lista.html",
        {
            "contactos": pagina,
            "pagina": pagina,
            "buscar": buscar,
            "cantidad": cantidad,
            "ultima_importacion": ultima_importacion,
        },
    )

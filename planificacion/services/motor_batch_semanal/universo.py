from planificacion.models import SitioPlanificado
from planificacion.services.motor_batch_semanal.tipos import SitioMotor
from planificacion.services.planificacion_semanal import \
    obtener_candidatos_batch


def _float_seguro(valor):
    if valor in [
        None,
        "",
    ]:
        return None

    try:
        return float(valor)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _convertir_sitio_motor(
    sitio_planificado,
):
    """
    Convierte un SitioPlanificado al formato oficial
    utilizado por el motor semanal.
    """

    sitio = sitio_planificado.sitio

    tipo_zona = (
        sitio.tipo_zona
        or ""
    ).strip()

    tipo_zona_normalizado = (
        tipo_zona.lower()
    )

    urbano = (
        "urb"
        in tipo_zona_normalizado
    )

    rural = (
        "rural"
        in tipo_zona_normalizado
    )

    return SitioMotor(
        sitio_planificado_id=(
            sitio_planificado.id
        ),
        sitio_id=sitio.id,
        id_claro=(
            sitio.id_claro
            or sitio.id_sites
            or ""
        ),
        nombre=(
            sitio.nombre
            or ""
        ),
        comuna=(
            sitio.comuna
            or ""
        ),
        tipo_zona=tipo_zona,
        latitud=_float_seguro(
            sitio.latitud
        ),
        longitud=_float_seguro(
            sitio.longitud
        ),
        condicion_acceso=(
            sitio.condiciones_acceso
            or ""
        ),
        estado_permiso=(
            sitio_planificado.estado_permiso
        ),
        prioridad=(
            sitio_planificado.prioridad
        ),
        urbano=urbano,
        rural=rural,
    )


def construir_universo_batch(
    batch,
    incluir_ids=None,
):
    """
    Construye el universo utilizado por el motor semanal.

    El universo normal parte exactamente de
    obtener_candidatos_batch(batch).

    `incluir_ids` permite reincorporar explícitamente sitios
    que ya pertenecen activamente al batch actual y que, por
    esa misma razón, dejaron de aparecer como candidatos.

    Esto permite respetar decisiones manuales existentes sin
    alterar las reglas oficiales de elegibilidad para sitios
    nuevos.

    No modifica ninguna base de datos.
    """

    incluir_ids = {
        int(sitio_id)
        for sitio_id in (
            incluir_ids
            or []
        )
    }

    candidatos = list(
        obtener_candidatos_batch(
            batch,
        )
        .select_related(
            "sitio",
        )
    )

    ids_presentes = {
        sitio_planificado.id
        for sitio_planificado
        in candidatos
    }

    ids_adicionales = (
        incluir_ids
        - ids_presentes
    )

    adicionales = []

    if ids_adicionales:

        adicionales = list(
            SitioPlanificado.objects.filter(
                pk__in=ids_adicionales,
            )
            .select_related(
                "sitio",
            )
            .order_by(
                "id",
            )
        )

    sitios_planificados = (
        candidatos
        + adicionales
    )

    # Protección contra duplicados por SitioPlanificado.
    vistos = set()

    universo = []

    for sitio_planificado in sitios_planificados:

        if (
            sitio_planificado.id
            in vistos
        ):
            continue

        vistos.add(
            sitio_planificado.id
        )

        universo.append(
            _convertir_sitio_motor(
                sitio_planificado
            )
        )

    return universo

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


def construir_universo_batch(batch):
    """
    Construye el universo de sitios disponibles del mes
    para análisis semanal.

    No modifica ninguna base de datos.
    """

    queryset = obtener_candidatos_batch(batch).select_related(
        "sitio",
    )

    universo = []

    for sitio_planificado in queryset:
        sitio = sitio_planificado.sitio

        tipo_zona = (sitio.tipo_zona or "").strip()

        tipo_zona_normalizado = tipo_zona.lower()

        urbano = "urb" in tipo_zona_normalizado

        rural = "rural" in tipo_zona_normalizado

        universo.append(
            SitioMotor(
                sitio_planificado_id=sitio_planificado.id,
                sitio_id=sitio.id,
                id_claro=(sitio.id_claro or sitio.id_sites or ""),
                nombre=(sitio.nombre or ""),
                comuna=(sitio.comuna or ""),
                tipo_zona=tipo_zona,
                latitud=_float_seguro(sitio.latitud),
                longitud=_float_seguro(sitio.longitud),
                condicion_acceso=(sitio.condiciones_acceso or ""),
                estado_permiso=(sitio_planificado.estado_permiso),
                prioridad=(sitio_planificado.prioridad),
                urbano=urbano,
                rural=rural,
            )
        )

    return universo

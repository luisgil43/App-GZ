from statistics import mean

from planificacion.services.motor_batch_semanal.distancias import \
    distancia_haversine_km

# ============================================================
# RESERVAS
# ============================================================


def seleccionar_reservas(
    *,
    principales,
    universo,
    cantidad_reserva,
):
    """
    Selecciona sitios de respaldo cercanos a los principales.

    IMPORTANTE:

    `universo` puede ser previamente restringido a la zona
    semanal seleccionada.

    Por lo tanto esta función ya no tiene que decidir si un sitio
    pertenece territorialmente a la semana. Esa responsabilidad
    vive en zonas_semanales.py.
    """

    if not principales:
        return []

    if cantidad_reserva <= 0:
        return []

    ids_principales = {sitio.sitio_planificado_id for sitio in principales}

    candidatos = [
        sitio
        for sitio in universo
        if (sitio.sitio_planificado_id not in ids_principales)
    ]

    ranking = []

    for candidato in candidatos:

        distancias = []

        for principal in principales:

            distancia = distancia_haversine_km(
                candidato.latitud,
                candidato.longitud,
                principal.latitud,
                principal.longitud,
            )

            if distancia is not None:
                distancias.append(distancia)

        if not distancias:
            continue

        distancias.sort()

        distancia_minima = distancias[0]

        distancia_media_cercanos = mean(
            distancias[
                : min(
                    3,
                    len(distancias),
                )
            ]
        )

        # Menor valor = mejor reserva.
        #
        # Damos mayor importancia a que tenga al menos un
        # principal realmente cerca, pero también revisamos
        # su afinidad general con la zona.
        score = distancia_minima * 0.65 + distancia_media_cercanos * 0.35

        ranking.append(
            (
                score,
                distancia_minima,
                candidato,
            )
        )

    ranking.sort(
        key=lambda elemento: (
            elemento[0],
            elemento[1],
        )
    )

    return [candidato for _, _, candidato in ranking[:cantidad_reserva]]

def _producto_cruzado(
    origen,
    punto_a,
    punto_b,
):
    return (punto_a[0] - origen[0]) * (punto_b[1] - origen[1]) - (
        punto_a[1] - origen[1]
    ) * (punto_b[0] - origen[0])


def convex_hull(sitios):
    """
    Devuelve puntos del perímetro usando Monotonic Chain.

    Resultado:
    [
        {"lat": ..., "lng": ...},
        ...
    ]
    """

    puntos = sorted(
        {
            (
                sitio.longitud,
                sitio.latitud,
            )
            for sitio in sitios
            if (sitio.latitud is not None and sitio.longitud is not None)
        }
    )

    if len(puntos) <= 1:
        return [
            {
                "lat": punto[1],
                "lng": punto[0],
            }
            for punto in puntos
        ]

    inferior = []

    for punto in puntos:
        while (
            len(inferior) >= 2
            and _producto_cruzado(
                inferior[-2],
                inferior[-1],
                punto,
            )
            <= 0
        ):
            inferior.pop()

        inferior.append(punto)

    superior = []

    for punto in reversed(puntos):
        while (
            len(superior) >= 2
            and _producto_cruzado(
                superior[-2],
                superior[-1],
                punto,
            )
            <= 0
        ):
            superior.pop()

        superior.append(punto)

    hull = inferior[:-1] + superior[:-1]

    return [
        {
            "lat": punto[1],
            "lng": punto[0],
        }
        for punto in hull
    ]

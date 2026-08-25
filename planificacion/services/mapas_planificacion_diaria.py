# planificacion/services/mapas_planificacion_diaria.py

from urllib.parse import urlencode

from planificacion.modelos import SalidaPlanificacionDiaria
from planificacion.services.planificacion_diaria import \
    obtener_estado_operacional_sitio

# ============================================================
# COLORES DE CUADRILLAS
# ============================================================

COLORES_CUADRILLAS = [
    {
        "marcador": "#2563eb",
        "borde": "#1d4ed8",
        "relleno": "#dbeafe",
    },
    {
        "marcador": "#16a34a",
        "borde": "#15803d",
        "relleno": "#dcfce7",
    },
    {
        "marcador": "#9333ea",
        "borde": "#7e22ce",
        "relleno": "#f3e8ff",
    },
    {
        "marcador": "#ea580c",
        "borde": "#c2410c",
        "relleno": "#ffedd5",
    },
    {
        "marcador": "#0891b2",
        "borde": "#0e7490",
        "relleno": "#cffafe",
    },
    {
        "marcador": "#be123c",
        "borde": "#9f1239",
        "relleno": "#ffe4e6",
    },
]


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(
    valor,
):
    if valor in (
        None,
        "",
    ):
        return None

    try:
        return float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _color_indice(
    indice,
):
    return COLORES_CUADRILLAS[indice % len(COLORES_CUADRILLAS)]


def _estado_operacional_mapa(
    sitio_planificado,
):
    estado = obtener_estado_operacional_sitio(
        sitio_planificado,
    )

    codigo = estado.get(
        "estado_planificacion",
        "",
    )

    textos = {
        "sin_servicio": "Sin servicio operativo",
        "listo_asignar": "Pendiente por asignar",
        "asignado": "Asignado",
        "en_ejecucion": "En ejecución",
        "revision": "Pendiente revisión",
        "finalizado": "Finalizado",
        "no_disponible": (estado.get("estado_operaciones_display") or "No disponible"),
    }

    return {
        "codigo": codigo,
        "texto": textos.get(
            codigo,
            estado.get(
                "estado_operaciones_display",
                "Sin estado",
            ),
        ),
        "du": estado.get("du"),
        "servicio_id": estado.get("servicio_id"),
    }


def _construir_punto_sitio(
    *,
    sitio_salida,
    color,
):
    sitio_planificado = sitio_salida.sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    latitud = _float_seguro(
        sitio.latitud,
    )

    longitud = _float_seguro(
        sitio.longitud,
    )

    if latitud is None or longitud is None:
        return None

    estado_operativo = _estado_operacional_mapa(
        sitio_planificado,
    )

    return {
        "sitio_salida_id": sitio_salida.pk,
        "orden": sitio_salida.orden,
        "id_claro": (sitio.id_claro or sitio.id_sites or ""),
        "id_new": (sitio.id_sites_new or ""),
        "nombre": (sitio.nombre or ""),
        "comuna": (sitio.comuna or ""),
        "direccion": (sitio.direccion or ""),
        "tipo_zona": (sitio.tipo_zona or ""),
        "latitud": latitud,
        "longitud": longitud,
        "estado_permiso": (sitio_planificado.get_estado_permiso_display()),
        "estado_operativo": (estado_operativo["texto"]),
        "estado_operativo_codigo": (estado_operativo["codigo"]),
        "du": estado_operativo["du"],
        "color": color,
    }


# ============================================================
# URL EXTERNA GOOGLE MAPS
# ============================================================


def construir_url_ruta_google_maps(
    *,
    salida,
    puntos,
):
    """
    Construye una URL estándar de Google Maps.

    Ruta:

        base
        ->
        sitio 1
        ->
        sitio 2
        ->
        sitio 3
        ->
        base

    No requiere una API backend adicional.
    """

    disponibilidad = salida.disponibilidad_cuadrilla

    base_lat = _float_seguro(
        disponibilidad.base_latitud_efectiva,
    )

    base_lng = _float_seguro(
        disponibilidad.base_longitud_efectiva,
    )

    puntos_validos = [
        punto
        for punto in puntos
        if (punto.get("latitud") is not None and punto.get("longitud") is not None)
    ]

    if not puntos_validos:
        return ""

    # ========================================================
    # CON BASE
    # ========================================================

    if base_lat is not None and base_lng is not None:

        origen = f"{base_lat},{base_lng}"

        destino = f"{base_lat},{base_lng}"

        waypoints = "|".join(
            (f"{punto['latitud']}," f"{punto['longitud']}") for punto in puntos_validos
        )

    # ========================================================
    # SIN BASE
    # ========================================================

    else:

        origen = f"{puntos_validos[0]['latitud']}," f"{puntos_validos[0]['longitud']}"

        destino = (
            f"{puntos_validos[-1]['latitud']}," f"{puntos_validos[-1]['longitud']}"
        )

        intermedios = puntos_validos[1:-1]

        waypoints = "|".join(
            (f"{punto['latitud']}," f"{punto['longitud']}") for punto in intermedios
        )

    parametros = {
        "api": "1",
        "origin": origen,
        "destination": destino,
        "travelmode": "driving",
    }

    if waypoints:
        parametros["waypoints"] = waypoints

    return "https://www.google.com/maps/dir/?" + urlencode(
        parametros,
        safe="|,",
    )


# ============================================================
# MAPA DE UNA SALIDA
# ============================================================


def construir_mapa_salida_diaria(
    salida,
):
    salida = (
        SalidaPlanificacionDiaria.objects.select_related(
            "batch",
            "batch__planificacion",
            "disponibilidad_cuadrilla",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        )
        .prefetch_related(
            ("sitios__" "sitio_batch__" "sitio_planificado__" "sitio"),
        )
        .get(
            pk=salida.pk,
        )
    )

    color = _color_indice(0)

    puntos = []

    for sitio_salida in (
        salida.sitios.select_related(
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            ("sitio_batch__" "sitio_planificado__" "sitio"),
        )
        .exclude(
            estado__in=[
                "retirado",
                "cancelado",
            ],
        )
        .order_by(
            "orden",
            "id",
        )
    ):

        punto = _construir_punto_sitio(
            sitio_salida=sitio_salida,
            color=color,
        )

        if punto is not None:
            puntos.append(punto)

    disponibilidad = salida.disponibilidad_cuadrilla

    base_latitud = _float_seguro(
        disponibilidad.base_latitud_efectiva,
    )

    base_longitud = _float_seguro(
        disponibilidad.base_longitud_efectiva,
    )

    base = None

    if base_latitud is not None and base_longitud is not None:

        base = {
            "nombre": (disponibilidad.base_nombre_efectiva),
            "latitud": base_latitud,
            "longitud": base_longitud,
        }

    return {
        "salida_id": salida.pk,
        "fecha": (salida.fecha.isoformat()),
        "fecha_display": (salida.fecha.strftime("%d/%m/%Y")),
        "cuadrilla_codigo": (salida.cuadrilla_codigo),
        "cuadrilla_nombre": (salida.cuadrilla_nombre),
        "tipo_vehiculo": (disponibilidad.tipo_vehiculo),
        "estado": salida.estado,
        "estado_display": (salida.get_estado_display()),
        "color": color,
        "base": base,
        "sitios": puntos,
        "total_sitios": len(puntos),
        "url_google_maps": (
            construir_url_ruta_google_maps(
                salida=salida,
                puntos=puntos,
            )
        ),
    }


# ============================================================
# MAPA DEL DÍA
# ============================================================


def construir_mapa_dia_planificacion(
    *,
    batch,
    fecha,
):
    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
            fecha=fecha,
        )
        .exclude(
            estado="cancelada",
        )
        .select_related(
            "disponibilidad_cuadrilla",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        )
        .prefetch_related(
            ("sitios__" "sitio_batch__" "sitio_planificado__" "sitio"),
        )
        .order_by(
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa__orden"),
            "orden",
            "id",
        )
    )

    cuadrillas = []

    total_sitios = 0

    for indice, salida in enumerate(
        salidas,
    ):

        color = _color_indice(
            indice,
        )

        puntos = []

        for sitio_salida in (
            salida.sitios.select_related(
                "sitio_batch",
                "sitio_batch__sitio_planificado",
                ("sitio_batch__" "sitio_planificado__" "sitio"),
            )
            .exclude(
                estado__in=[
                    "retirado",
                    "cancelado",
                ],
            )
            .order_by(
                "orden",
                "id",
            )
        ):

            punto = _construir_punto_sitio(
                sitio_salida=sitio_salida,
                color=color,
            )

            if punto is not None:
                puntos.append(
                    punto,
                )

        disponibilidad = salida.disponibilidad_cuadrilla

        base_lat = _float_seguro(
            disponibilidad.base_latitud_efectiva,
        )

        base_lng = _float_seguro(
            disponibilidad.base_longitud_efectiva,
        )

        base = None

        if base_lat is not None and base_lng is not None:

            base = {
                "nombre": (disponibilidad.base_nombre_efectiva),
                "latitud": base_lat,
                "longitud": base_lng,
            }

        total_sitios += len(
            puntos,
        )

        cuadrillas.append(
            {
                "salida_id": salida.pk,
                "codigo": (salida.cuadrilla_codigo),
                "nombre": (salida.cuadrilla_nombre),
                "tipo_vehiculo": (disponibilidad.tipo_vehiculo),
                "color": color,
                "base": base,
                "sitios": puntos,
                "cantidad": len(puntos),
                "url_google_maps": (
                    construir_url_ruta_google_maps(
                        salida=salida,
                        puntos=puntos,
                    )
                ),
            }
        )

    return {
        "batch_id": batch.pk,
        "fecha": fecha.isoformat(),
        "fecha_display": (fecha.strftime("%d/%m/%Y")),
        "total_cuadrillas": len(
            cuadrillas,
        ),
        "total_sitios": total_sitios,
        "cuadrillas": cuadrillas,
    }


# ============================================================
# CONSTRUIR PUNTO DE SITIO PENDIENTE
# ============================================================


def _construir_punto_sitio_pendiente(
    *,
    item_batch,
    color,
):
    """
    Convierte un SitioBatchSemanal pendiente en un punto
    utilizable por el mapa de Planificación Diaria.

    IMPORTANTE
    ==========================================================

    El sitio todavía no pertenece a una salida diaria, por lo
    que aquí no trabajamos con SitioSalidaPlanificacionDiaria.

    El origen real es:

        SitioBatchSemanal
        -> SitioPlanificado
        -> SitioMovil
    """

    sitio_planificado = item_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    latitud = _float_seguro(
        sitio.latitud,
    )

    longitud = _float_seguro(
        sitio.longitud,
    )

    if latitud is None or longitud is None:
        return None

    estado_operativo = _estado_operacional_mapa(
        sitio_planificado,
    )

    return {
        # ====================================================
        # IDENTIFICADORES INTERNOS
        # ====================================================
        "sitio_batch_id": item_batch.pk,
        "sitio_planificado_id": sitio_planificado.pk,
        "sitio_id": sitio.pk,
        # ====================================================
        # IDENTIFICACIÓN
        # ====================================================
        "id_claro": (sitio.id_claro or sitio.id_sites or ""),
        "id_new": (sitio.id_sites_new or ""),
        "nombre": (sitio.nombre or ""),
        "comuna": (sitio.comuna or ""),
        "direccion": (sitio.direccion or ""),
        "tipo_zona": (sitio.tipo_zona or ""),
        # ====================================================
        # COORDENADAS
        # ====================================================
        "latitud": latitud,
        "longitud": longitud,
        # ====================================================
        # PLANIFICACIÓN
        # ====================================================
        "estado_permiso": (sitio_planificado.get_estado_permiso_display()),
        "estado_planificacion": (sitio_planificado.get_estado_display()),
        # ====================================================
        # OPERACIONES
        # ====================================================
        "estado_operativo": (estado_operativo["texto"]),
        "estado_operativo_codigo": (estado_operativo["codigo"]),
        "du": estado_operativo["du"],
        # ====================================================
        # MAPA
        # ====================================================
        "color": color,
    }


# ============================================================
# URL GOOGLE MAPS PARA SITIOS PENDIENTES
# ============================================================


def construir_url_google_maps_pendientes(
    *,
    puntos,
):
    """
    Construye una URL externa de Google Maps únicamente como
    apoyo adicional.

    IMPORTANTE
    ==========================================================

    Esta URL NO reemplaza nuestro mapa interno con la API de
    Google Maps.

    El mapa principal seguirá siendo la vista propia de GZ.

    Cuando existen varios puntos utilizamos:

        punto 1 -> intermedios -> último punto

    No incluimos base de cuadrilla porque estos sitios todavía
    no tienen cuadrilla ni jornada asignada.
    """

    puntos_validos = [
        punto
        for punto in puntos
        if (punto.get("latitud") is not None and punto.get("longitud") is not None)
    ]

    if not puntos_validos:
        return ""

    # ========================================================
    # UN SOLO SITIO
    # ========================================================

    if len(puntos_validos) == 1:

        punto = puntos_validos[0]

        parametros = {
            "api": "1",
            "query": (f"{punto['latitud']}," f"{punto['longitud']}"),
        }

        return "https://www.google.com/maps/search/?" + urlencode(
            parametros,
            safe=",",
        )

    # ========================================================
    # DOS O MÁS SITIOS
    # ========================================================

    primero = puntos_validos[0]

    ultimo = puntos_validos[-1]

    origen = f"{primero['latitud']}," f"{primero['longitud']}"

    destino = f"{ultimo['latitud']}," f"{ultimo['longitud']}"

    intermedios = puntos_validos[1:-1]

    waypoints = "|".join(
        (f"{punto['latitud']}," f"{punto['longitud']}") for punto in intermedios
    )

    parametros = {
        "api": "1",
        "origin": origen,
        "destination": destino,
        "travelmode": "driving",
    }

    if waypoints:

        parametros["waypoints"] = waypoints

    return "https://www.google.com/maps/dir/?" + urlencode(
        parametros,
        safe="|,",
    )


# ============================================================
# MAPA DE SITIOS PENDIENTES
# ============================================================


def construir_mapa_pendientes_planificacion(
    *,
    batch,
    pendientes,
):
    """
    Construye la información necesaria para visualizar en
    nuestro mapa interno los sitios aprobados que todavía no
    pertenecen a una salida diaria.

    REGLA FUNDAMENTAL
    ==========================================================

    Esta función NO decide cuáles son los pendientes.

    Esa decisión ya fue realizada por:

        obtener_sitios_pendientes_planificacion_diaria()

    Por lo tanto recibimos directamente la lista de pendientes
    desde la view y solamente construimos la representación
    cartográfica.

    De esta forma:

        pantalla diaria
        mapa de pendientes
        traslado al mes siguiente

    pueden trabajar sobre el mismo universo operacional.
    """

    color = {
        "marcador": "#d97706",
        "borde": "#b45309",
        "relleno": "#fef3c7",
    }

    puntos = []

    sitios_sin_coordenadas = []

    # ========================================================
    # CONSTRUIR PUNTOS
    # ========================================================

    for item_batch in pendientes:

        punto = _construir_punto_sitio_pendiente(
            item_batch=item_batch,
            color=color,
        )

        if punto is not None:

            puntos.append(
                punto,
            )

            continue

        sitio = item_batch.sitio_planificado.sitio

        sitios_sin_coordenadas.append(
            {
                "sitio_batch_id": item_batch.pk,
                "id_claro": (sitio.id_claro or sitio.id_sites or ""),
                "nombre": (sitio.nombre or ""),
                "comuna": (sitio.comuna or ""),
            }
        )

    # ========================================================
    # CENTRO INICIAL
    # ========================================================
    #
    # El template puede utilizar esto como centro provisional.
    #
    # Después Google Maps hará fitBounds() utilizando todos
    # los marcadores, por lo que el mapa se ajustará
    # automáticamente al conjunto real de pendientes.
    # ========================================================

    centro = None

    if puntos:

        latitud_promedio = sum(punto["latitud"] for punto in puntos) / len(puntos)

        longitud_promedio = sum(punto["longitud"] for punto in puntos) / len(puntos)

        centro = {
            "latitud": latitud_promedio,
            "longitud": longitud_promedio,
        }

    # ========================================================
    # RESPUESTA
    # ========================================================

    return {
        "batch_id": batch.pk,
        "color": color,
        "sitios": puntos,
        "total_sitios": len(
            puntos,
        ),
        "total_pendientes": len(
            pendientes,
        ),
        "total_sin_coordenadas": len(
            sitios_sin_coordenadas,
        ),
        "sitios_sin_coordenadas": (sitios_sin_coordenadas),
        "centro": centro,
        "url_google_maps": (
            construir_url_google_maps_pendientes(
                puntos=puntos,
            )
        ),
    }

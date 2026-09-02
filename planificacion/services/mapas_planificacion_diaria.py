# planificacion/services/mapas_planificacion_diaria.py

from urllib.parse import urlencode

from django.urls import reverse

from planificacion.modelos import (SalidaPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
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


# ============================================================
# MAPA OPERACIONAL SEMANAL
# ============================================================


ESTADOS_PARTICIPACION_NO_VIGENTES_MAPA = {
    "reprogramado",
    "retirado",
    "cancelado",
}


COLOR_DISPONIBLE_MAPA_OPERACIONAL = {
    "marcador": "#d97706",
    "borde": "#b45309",
    "relleno": "#fef3c7",
}


COLOR_PLANIFICADO_MAPA_OPERACIONAL = {
    "marcador": "#475569",
    "borde": "#334155",
    "relleno": "#f1f5f9",
}


COLOR_EJECUCION_MAPA_OPERACIONAL = {
    "marcador": "#ea580c",
    "borde": "#c2410c",
    "relleno": "#ffedd5",
}


COLOR_REVISION_MAPA_OPERACIONAL = {
    "marcador": "#7c3aed",
    "borde": "#6d28d9",
    "relleno": "#ede9fe",
}


COLOR_FINALIZADO_MAPA_OPERACIONAL = {
    "marcador": "#059669",
    "borde": "#047857",
    "relleno": "#d1fae5",
}


def _texto_limpio_mapa(
    valor,
):
    return str(valor or "").strip()


def _valores_unicos_mapa(
    valores,
):
    """
    Normaliza y elimina duplicados conservando el orden.
    """

    resultado = []

    vistos = set()

    for valor in valores:

        texto = _texto_limpio_mapa(
            valor,
        )

        if not texto:
            continue

        clave = texto.casefold()

        if clave in vistos:
            continue

        vistos.add(
            clave,
        )

        resultado.append(
            texto,
        )

    return resultado


def _informacion_contacto_mapa_operacional(
    sitio,
):
    """
    Construye la información de contacto y acceso que será
    mostrada dentro del popup del mapa operacional.

    Únicamente considera ContactoSitio activos.

    Las observaciones y acciones se agregan sin duplicados.
    """

    contactos = list(
        sitio.contactos_planificacion.filter(
            activo=True,
        ).order_by(
            "prioridad_contacto",
            "id",
        )
    )

    telefonos = _valores_unicos_mapa(contacto.telefono for contacto in contactos)

    correos = _valores_unicos_mapa(contacto.correo for contacto in contactos)

    responsables = _valores_unicos_mapa(contacto.responsable for contacto in contactos)

    propietarios = _valores_unicos_mapa(contacto.propietario for contacto in contactos)

    observaciones = _valores_unicos_mapa(
        contacto.observaciones for contacto in contactos
    )

    acciones = _valores_unicos_mapa(contacto.accion for contacto in contactos)

    tipos_contacto = _valores_unicos_mapa(
        contacto.tipo_contacto for contacto in contactos
    )

    return {
        "condiciones_acceso": _texto_limpio_mapa(
            sitio.condiciones_acceso,
        ),
        "telefonos": telefonos,
        "correos": correos,
        "responsables": responsables,
        "propietarios": propietarios,
        "tipos_contacto": tipos_contacto,
        "observaciones": observaciones,
        "acciones": acciones,
    }


def _codigo_cuadrilla_mapa_operacional(
    participacion,
):
    """
    Obtiene el código visual real de la cuadrilla.

    No asumimos que existan solamente B1/B2/B3.
    """

    if participacion is None:
        return ""

    salida = participacion.salida

    return _texto_limpio_mapa(
        salida.cuadrilla_codigo,
    )


def _clasificar_sitio_mapa_operacional(
    *,
    item_batch,
    participacion,
    es_pendiente,
):
    """
    Determina la nomenclatura visual del marcador.

    PRIORIDAD DE ESTADOS
    ==========================================================

    F  Finalizado
    R  Revisión
    E  En ejecución
    Bx Asignado operativamente a cuadrilla
    P  Tiene planificación diaria pero todavía no está
       asignado operativamente
    D  Disponible/aprobado todavía sin salida diaria

    La ejecución REAL proviene de
    obtener_estado_operacional_sitio().
    """

    sitio_planificado = item_batch.sitio_planificado

    estado_operativo = _estado_operacional_mapa(
        sitio_planificado,
    )

    codigo_operativo = (
        estado_operativo.get(
            "codigo",
            "",
        )
        or ""
    )

    # ========================================================
    # FINALIZADO
    # ========================================================

    if codigo_operativo == "finalizado":

        return {
            "codigo": "F",
            "texto": "Finalizado",
            "tipo": "finalizado",
            "color": COLOR_FINALIZADO_MAPA_OPERACIONAL,
            "estado_operativo": estado_operativo,
        }

    # ========================================================
    # REVISIÓN
    # ========================================================

    if codigo_operativo == "revision":

        return {
            "codigo": "R",
            "texto": "En revisión",
            "tipo": "revision",
            "color": COLOR_REVISION_MAPA_OPERACIONAL,
            "estado_operativo": estado_operativo,
        }

    # ========================================================
    # EJECUCIÓN
    # ========================================================

    if codigo_operativo == "en_ejecucion":

        return {
            "codigo": "E",
            "texto": "En ejecución",
            "tipo": "en_ejecucion",
            "color": COLOR_EJECUCION_MAPA_OPERACIONAL,
            "estado_operativo": estado_operativo,
        }

    # ========================================================
    # PARTICIPACIÓN DIARIA VIGENTE
    # ========================================================

    if participacion is not None:

        codigo_cuadrilla = _codigo_cuadrilla_mapa_operacional(
            participacion,
        )

        if codigo_operativo == "asignado":

            return {
                "codigo": (codigo_cuadrilla or "A"),
                "texto": (
                    "Asignado" + (f" · {codigo_cuadrilla}" if codigo_cuadrilla else "")
                ),
                "tipo": "asignado",
                "color": COLOR_PLANIFICADO_MAPA_OPERACIONAL,
                "estado_operativo": estado_operativo,
            }

        return {
            "codigo": "P",
            "texto": "Planificado",
            "tipo": "planificado",
            "color": COLOR_PLANIFICADO_MAPA_OPERACIONAL,
            "estado_operativo": estado_operativo,
        }

    # ========================================================
    # DISPONIBLE PARA PROGRAMAR
    # ========================================================

    if es_pendiente:

        return {
            "codigo": "D",
            "texto": "Disponible para programar",
            "tipo": "disponible",
            "color": COLOR_DISPONIBLE_MAPA_OPERACIONAL,
            "estado_operativo": estado_operativo,
        }

    # ========================================================
    # OTRO ESTADO DEL BATCH
    # ========================================================

    return {
        "codigo": "",
        "texto": item_batch.get_estado_display(),
        "tipo": "otro",
        "color": COLOR_PLANIFICADO_MAPA_OPERACIONAL,
        "estado_operativo": estado_operativo,
    }


def _construir_punto_mapa_operacional(
    *,
    item_batch,
    participacion,
    es_pendiente,
):
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

    clasificacion = _clasificar_sitio_mapa_operacional(
        item_batch=item_batch,
        participacion=participacion,
        es_pendiente=es_pendiente,
    )

    contacto = _informacion_contacto_mapa_operacional(
        sitio,
    )

    fecha = None

    fecha_display = ""

    cuadrilla_codigo = ""

    cuadrilla_nombre = ""

    orden = None

    salida_id = None

    sitio_salida_id = None

    estado_participacion = ""

    estado_participacion_display = ""

    if participacion is not None:

        salida = participacion.salida

        salida_id = salida.pk

        sitio_salida_id = participacion.pk

        fecha = salida.fecha.isoformat()

        fecha_display = salida.fecha.strftime(
            "%d/%m/%Y",
        )

        cuadrilla_codigo = _texto_limpio_mapa(
            salida.cuadrilla_codigo,
        )

        cuadrilla_nombre = _texto_limpio_mapa(
            salida.cuadrilla_nombre,
        )

        orden = participacion.orden

        estado_participacion = participacion.estado

        estado_participacion_display = participacion.get_estado_display()

    return {
        # ====================================================
        # IDENTIFICADORES
        # ====================================================
        "sitio_batch_id": item_batch.pk,
        "sitio_planificado_id": sitio_planificado.pk,
        "sitio_id": sitio.pk,
        "sitio_salida_id": sitio_salida_id,
        "salida_id": salida_id,
        # ====================================================
        # SITIO
        # ====================================================
        "id_claro": (sitio.id_claro or sitio.id_sites or ""),
        "id_sites": (sitio.id_sites or ""),
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
        # BATCH / PERMISOS
        # ====================================================
        "estado_batch": item_batch.estado,
        "estado_batch_display": (item_batch.get_estado_display()),
        "es_reserva": bool(
            item_batch.es_reserva,
        ),
        "estado_permiso": (sitio_planificado.get_estado_permiso_display()),
        "estado_planificacion": (sitio_planificado.get_estado_display()),
        # ====================================================
        # PLANIFICACIÓN DIARIA
        # ====================================================
        "fecha": fecha,
        "fecha_display": fecha_display,
        "cuadrilla_codigo": cuadrilla_codigo,
        "cuadrilla_nombre": cuadrilla_nombre,
        "orden": orden,
        "estado_participacion": estado_participacion,
        "estado_participacion_display": (estado_participacion_display),
        # ====================================================
        # OPERACIONES
        # ====================================================
        "estado_operativo": (clasificacion["estado_operativo"]["texto"]),
        "estado_operativo_codigo": (clasificacion["estado_operativo"]["codigo"]),
        "du": (clasificacion["estado_operativo"]["du"]),
        "servicio_id": (clasificacion["estado_operativo"]["servicio_id"]),
        # ====================================================
        # CONTACTO / ACCESO
        # ====================================================
        "condiciones_acceso": (contacto["condiciones_acceso"]),
        "telefonos": contacto["telefonos"],
        "correos": contacto["correos"],
        "responsables": contacto["responsables"],
        "propietarios": contacto["propietarios"],
        "tipos_contacto": contacto["tipos_contacto"],
        "observaciones_contacto": (contacto["observaciones"]),
        "acciones_contacto": (contacto["acciones"]),
        # ====================================================
        # PRESENTACIÓN
        # ====================================================
        "codigo_mapa": clasificacion["codigo"],
        "estado_mapa": clasificacion["texto"],
        "tipo_mapa": clasificacion["tipo"],
        "color": clasificacion["color"],
        # ====================================================
        # ACCIONES
        # ====================================================
        "puede_programar": bool(es_pendiente),
        "programar_url": (
            reverse(
                "planificacion:" "programar_sitio_manual_planificacion_diaria",
                kwargs={
                    "batch_id": item_batch.batch_id,
                    "sitio_batch_id": item_batch.pk,
                },
            )
            if es_pendiente
            else ""
        ),
        "puede_reprogramar": bool(
            participacion is not None
            and clasificacion["tipo"]
            not in {
                "en_ejecucion",
                "revision",
                "finalizado",
            }
        ),
    }


def construir_mapa_operacional_semanal(
    *,
    batch,
    fecha_rutas,
    pendientes,
):
    """
    Construye el mapa operacional completo de una semana.

    OBJETIVO
    ==========================================================

    Mostrar simultáneamente todos los sitios relevantes del
    batch semanal y superponer únicamente las rutas del día
    seleccionado.

    El mapa NO modifica planificación ni operaciones.

    UNIVERSO
    ==========================================================

    Se excluyen las filas históricas que ya no representan un
    sitio operativo de esta semana:

        excluido
        reemplazado

    Los sitios rechazados permanecen fuera del mapa operativo
    porque no constituyen alternativas ejecutables.

    Los pendientes recibidos deben provenir de:

        obtener_sitios_pendientes_planificacion_diaria()

    De esta forma el marcador D representa exactamente el mismo
    universo que la pantalla diaria.
    """

    # ========================================================
    # IDS PENDIENTES
    # ========================================================

    ids_pendientes = {item.pk for item in pendientes}

    # ========================================================
    # PARTICIPACIONES VIGENTES
    # ========================================================

    participaciones = list(
        SitioSalidaPlanificacionDiaria.objects.filter(
            salida__batch=batch,
        )
        .exclude(
            salida__estado="cancelada",
        )
        .exclude(
            estado__in=(ESTADOS_PARTICIPACION_NO_VIGENTES_MAPA),
        )
        .select_related(
            "salida",
            "salida__disponibilidad_cuadrilla",
            ("salida__disponibilidad_cuadrilla__" "cuadrilla_operativa"),
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
        )
        .order_by(
            "salida__fecha",
            "salida__orden",
            "orden",
            "id",
        )
    )

    # ========================================================
    # ÚLTIMA PARTICIPACIÓN VIGENTE POR SITIO
    # ========================================================
    #
    # En condiciones normales debe existir una única
    # participación vigente.
    #
    # Si existe historia previa, la participación cronológica
    # más reciente es la representación operacional vigente.
    # ========================================================

    participacion_por_sitio = {}

    for participacion in participaciones:

        participacion_por_sitio[participacion.sitio_batch_id] = participacion

    # ========================================================
    # SITIOS DEL BATCH
    # ========================================================

    items_batch = list(
        batch.sitios.exclude(
            estado__in=[
                "rechazado",
                "excluido",
                "reemplazado",
            ],
        )
        .select_related(
            "sitio_planificado",
            "sitio_planificado__sitio",
        )
        .order_by(
            "sitio_planificado__sitio__id_claro",
            "id",
        )
    )

    puntos = []

    sitios_sin_coordenadas = []

    conteos = {
        "D": 0,
        "P": 0,
        "asignados": 0,
        "E": 0,
        "R": 0,
        "F": 0,
        "otros": 0,
    }

    # ========================================================
    # CONSTRUIR PUNTOS
    # ========================================================

    for item_batch in items_batch:

        participacion = participacion_por_sitio.get(
            item_batch.pk,
        )

        punto = _construir_punto_mapa_operacional(
            item_batch=item_batch,
            participacion=participacion,
            es_pendiente=(item_batch.pk in ids_pendientes),
        )

        if punto is None:

            sitio = item_batch.sitio_planificado.sitio

            sitios_sin_coordenadas.append(
                {
                    "sitio_batch_id": item_batch.pk,
                    "id_claro": (sitio.id_claro or sitio.id_sites or ""),
                    "nombre": (sitio.nombre or ""),
                    "comuna": (sitio.comuna or ""),
                }
            )

            continue

        puntos.append(
            punto,
        )

        tipo = punto["tipo_mapa"]

        if tipo == "disponible":
            conteos["D"] += 1

        elif tipo == "planificado":
            conteos["P"] += 1

        elif tipo == "asignado":
            conteos["asignados"] += 1

        elif tipo == "en_ejecucion":
            conteos["E"] += 1

        elif tipo == "revision":
            conteos["R"] += 1

        elif tipo == "finalizado":
            conteos["F"] += 1

        else:
            conteos["otros"] += 1

    # ========================================================
    # RUTAS DEL DÍA SELECCIONADO
    # ========================================================
    #
    # Reutilizamos exactamente el constructor existente.
    # No se crea una segunda lógica de rutas.
    # ========================================================

    rutas_dia = construir_mapa_dia_planificacion(
        batch=batch,
        fecha=fecha_rutas,
    )

    # ========================================================
    # CENTRO
    # ========================================================

    centro = None

    if puntos:

        centro = {
            "latitud": (sum(punto["latitud"] for punto in puntos) / len(puntos)),
            "longitud": (sum(punto["longitud"] for punto in puntos) / len(puntos)),
        }

    # ========================================================
    # RESPUESTA
    # ========================================================

    return {
        "batch_id": batch.pk,
        "fecha_rutas": (fecha_rutas.isoformat()),
        "fecha_rutas_display": (
            fecha_rutas.strftime(
                "%d/%m/%Y",
            )
        ),
        "sitios": puntos,
        "total_sitios": len(
            puntos,
        ),
        "total_sin_coordenadas": len(
            sitios_sin_coordenadas,
        ),
        "sitios_sin_coordenadas": (sitios_sin_coordenadas),
        "conteos": conteos,
        "centro": centro,
        "rutas_dia": rutas_dia,
    }

from collections import defaultdict

from planificacion.models import SitioBatchSemanal
from planificacion.services.motor_batch_semanal.perimetros import convex_hull

# ============================================================
# PALETA DE COLORES
# ============================================================

PALETA_CLUSTERS = [
    {
        "marcador": "#2563eb",
        "borde": "#1d4ed8",
        "relleno": "#3b82f6",
    },
    {
        "marcador": "#16a34a",
        "borde": "#15803d",
        "relleno": "#22c55e",
    },
    {
        "marcador": "#ea580c",
        "borde": "#c2410c",
        "relleno": "#f97316",
    },
    {
        "marcador": "#9333ea",
        "borde": "#7e22ce",
        "relleno": "#a855f7",
    },
    {
        "marcador": "#0891b2",
        "borde": "#0e7490",
        "relleno": "#06b6d4",
    },
    {
        "marcador": "#db2777",
        "borde": "#be185d",
        "relleno": "#ec4899",
    },
]


# ============================================================
# UTILIDADES
# ============================================================


def _float_seguro(valor):
    try:
        return float(valor)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _numero_cluster_desde_codigo(
    codigo,
):
    """
    Extrae el número de códigos como:

    cluster_1
    cluster_2
    cluster_10

    Esto permite ordenar correctamente:

    cluster_1
    cluster_2
    cluster_3
    ...
    cluster_10

    en lugar de:

    cluster_1
    cluster_10
    cluster_2
    """

    try:
        return int(
            str(codigo).rsplit(
                "_",
                1,
            )[-1]
        )

    except (
        TypeError,
        ValueError,
        IndexError,
    ):
        return 999999


class _SitioPerimetro:
    def __init__(
        self,
        latitud,
        longitud,
    ):
        self.latitud = latitud
        self.longitud = longitud


# ============================================================
# CONSTRUIR MAPA DEL BATCH SEMANAL
# ============================================================


def construir_mapa_batch_semanal(
    batch,
):
    """
    Construye la representación cartográfica del batch semanal.

    El mapa trabaja exclusivamente con CLUSTERS geográficos.

    IMPORTANTE:

    - no distingue principal/reserva visualmente;
    - todos los sitios activos del batch se muestran dentro
      de su cluster correspondiente;
    - cada cluster recibe un color;
    - cada cluster puede tener un perímetro;
    - los sitios sin cluster quedan separados para mostrar
      una alerta en la interfaz.
    """

    # ========================================================
    # ITEMS ACTIVOS DEL BATCH
    # ========================================================

    items = (
        SitioBatchSemanal.objects.filter(
            batch=batch,
        )
        .exclude(
            estado__in=[
                "excluido",
                "reemplazado",
            ],
        )
        .select_related(
            "sitio_planificado",
            "sitio_planificado__sitio",
        )
        .order_by(
            "cluster_codigo",
            "sitio_planificado__sitio__id_claro",
        )
    )

    # ========================================================
    # AGRUPACIÓN POR CLUSTER
    # ========================================================

    agrupados = defaultdict(list)

    sin_cluster = []

    for item in items:

        sitio_planificado = item.sitio_planificado

        sitio = sitio_planificado.sitio

        latitud = _float_seguro(sitio.latitud)

        longitud = _float_seguro(sitio.longitud)

        datos = {
            "item_id": item.id,
            "sitio_planificado_id": (item.sitio_planificado_id),
            "id_claro": (sitio.id_claro or sitio.id_sites or ""),
            "nombre": (sitio.nombre or ""),
            "comuna": (sitio.comuna or ""),
            "tipo_zona": (sitio.tipo_zona or ""),
            "direccion": (
                getattr(
                    sitio,
                    "direccion_proyecto",
                    "",
                )
                or sitio.direccion
                or ""
            ),
            "condicion_acceso": (sitio.condiciones_acceso or ""),
            "estado_permiso": (sitio_planificado.get_estado_permiso_display()),
            "latitud": latitud,
            "longitud": longitud,
            "cluster_codigo": (item.cluster_codigo or ""),
        }

        if item.cluster_codigo:

            agrupados[item.cluster_codigo].append(datos)

        else:

            sin_cluster.append(datos)

    # ========================================================
    # ORDEN NUMÉRICO DE CLUSTERS
    # ========================================================

    codigos_ordenados = sorted(
        agrupados.keys(),
        key=_numero_cluster_desde_codigo,
    )

    # ========================================================
    # CONSTRUCCIÓN DE CLUSTERS PARA EL MAPA
    # ========================================================

    clusters = []

    for indice, codigo in enumerate(codigos_ordenados):

        sitios = agrupados[codigo]

        color = PALETA_CLUSTERS[indice % len(PALETA_CLUSTERS)]

        # ====================================================
        # SITIOS CON COORDENADAS VÁLIDAS
        # ====================================================

        sitios_validos = [
            sitio
            for sitio in sitios
            if (sitio["latitud"] is not None and sitio["longitud"] is not None)
        ]

        # ====================================================
        # PERÍMETRO
        # ====================================================

        objetos_perimetro = [
            _SitioPerimetro(
                sitio["latitud"],
                sitio["longitud"],
            )
            for sitio in sitios_validos
        ]

        perimetro = convex_hull(objetos_perimetro)

        # ====================================================
        # COMUNAS
        # ====================================================

        comunas = sorted({sitio["comuna"] for sitio in sitios if sitio["comuna"]})

        # ====================================================
        # TIPO DE ZONA
        # ====================================================

        urbanos = sum(
            1 for sitio in sitios if ("urb" in (sitio["tipo_zona"] or "").lower())
        )

        rurales = sum(
            1 for sitio in sitios if ("rural" in (sitio["tipo_zona"] or "").lower())
        )

        sin_tipo_zona = len(sitios) - urbanos - rurales

        # ====================================================
        # NÚMERO REAL DEL CLUSTER
        # ====================================================

        numero_cluster = _numero_cluster_desde_codigo(codigo)

        if numero_cluster == 999999:
            numero_cluster = indice + 1

        # ====================================================
        # CLUSTER SERIALIZABLE
        # ====================================================

        clusters.append(
            {
                "codigo": codigo,
                "numero": numero_cluster,
                "nombre": (f"Cluster {numero_cluster}"),
                "cantidad": len(sitios),
                "cantidad_con_coordenadas": len(sitios_validos),
                "cantidad_sin_coordenadas": (len(sitios) - len(sitios_validos)),
                "urbanos": urbanos,
                "rurales": rurales,
                "sin_tipo_zona": (sin_tipo_zona),
                "comunas": comunas,
                "color": color,
                "perimetro": perimetro,
                "sitios": sitios,
            }
        )

    # ========================================================
    # RESUMEN GENERAL
    # ========================================================

    total_sitios = sum(cluster["cantidad"] for cluster in clusters)

    total_con_coordenadas = sum(
        cluster["cantidad_con_coordenadas"] for cluster in clusters
    )

    total_sin_coordenadas = sum(
        cluster["cantidad_sin_coordenadas"] for cluster in clusters
    )

    return {
        "batch_id": batch.pk,
        "total_sitios": total_sitios,
        "total_clusters": len(clusters),
        "total_con_coordenadas": (total_con_coordenadas),
        "total_sin_coordenadas": (total_sin_coordenadas),
        "clusters": clusters,
        "sin_cluster": sin_cluster,
    }

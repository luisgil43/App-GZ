from django.db import transaction

from planificacion.models import SitioBatchSemanal
from planificacion.services.motor_batch_semanal.clustering import \
    detectar_clusters

# ============================================================
# ADAPTADOR
# ============================================================


class SitioBatchClusterAdapter:
    """
    Adaptador ligero para ejecutar exactamente el mismo
    motor geográfico sobre sitios que ya pertenecen
    a un batch semanal.
    """

    def __init__(
        self,
        item,
    ):
        self.item = item

        sitio_planificado = item.sitio_planificado

        sitio = sitio_planificado.sitio

        self.sitio_planificado_id = sitio_planificado.id

        self.sitio_id = sitio.id

        self.id_claro = sitio.id_claro or sitio.id_sites or ""

        self.nombre = sitio.nombre or ""

        self.comuna = sitio.comuna or ""

        self.tipo_zona = sitio.tipo_zona or ""

        try:
            self.latitud = float(sitio.latitud)
        except (
            TypeError,
            ValueError,
        ):
            self.latitud = None

        try:
            self.longitud = float(sitio.longitud)
        except (
            TypeError,
            ValueError,
        ):
            self.longitud = None

        tipo_zona = self.tipo_zona.strip().lower()

        self.rural = "rural" in tipo_zona

        self.urbano = "urb" in tipo_zona


# ============================================================
# RECONSTRUCCIÓN
# ============================================================


@transaction.atomic
def reconstruir_clusters_batch(
    *,
    batch,
    cantidad_clusters=None,
):
    """
    Reconstruye los clusters de un batch existente usando
    exactamente el mismo motor geográfico que utiliza la
    generación automática de propuestas.

    IMPORTANTE:

    cantidad_clusters se conserva solamente por compatibilidad
    con llamadas antiguas.

    Ya NO forzamos 4 clusters.

    El motor determina cuántas concentraciones territoriales
    existen realmente.

    No modifica:
    - principales/reservas;
    - permisos;
    - estado del batch;
    - sitios;
    - puntajes.

    Solamente actualiza cluster_codigo.
    """

    items = list(
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
            "id",
        )
    )

    if not items:
        raise ValueError("El batch no contiene sitios activos.")

    adaptadores = [SitioBatchClusterAdapter(item) for item in items]

    con_coordenadas = [
        sitio
        for sitio in adaptadores
        if (sitio.latitud is not None and sitio.longitud is not None)
    ]

    sin_coordenadas = [
        sitio
        for sitio in adaptadores
        if (sitio.latitud is None or sitio.longitud is None)
    ]

    if not con_coordenadas:
        raise ValueError("Ningún sitio del batch posee coordenadas válidas.")

    # ========================================================
    # MOTOR ÚNICO
    # ========================================================

    clusters = detectar_clusters(con_coordenadas)

    # ========================================================
    # LIMPIAMOS SOLO CLUSTER PREVIO
    # ========================================================

    SitioBatchSemanal.objects.filter(
        batch=batch,
    ).update(cluster_codigo="")

    resultado_clusters = []

    # ordenar geográficamente por código numérico generado
    clusters_ordenados = sorted(
        clusters,
        key=lambda cluster: int(cluster.id_cluster.split("_")[-1]),
    )

    for cluster in clusters_ordenados:
        codigo = cluster.id_cluster

        ids_items = []

        comunas = set()

        for sitio_motor in cluster.sitios:
            item = sitio_motor.item

            item.cluster_codigo = codigo

            item.save(
                update_fields=[
                    "cluster_codigo",
                    "actualizado_en",
                ]
            )

            ids_items.append(item.id)

            if sitio_motor.comuna:
                comunas.add(sitio_motor.comuna)

        resultado_clusters.append(
            {
                "codigo": codigo,
                "cantidad": len(cluster.sitios),
                "centro_latitud": (cluster.centro_latitud),
                "centro_longitud": (cluster.centro_longitud),
                "radio_km": (cluster.radio_km),
                "distancia_media_km": (cluster.distancia_media_km),
                "distancia_maxima_km": (cluster.distancia_maxima_km),
                "score_compactacion": (cluster.score_compactacion),
                "urbanos": (cluster.urbanos),
                "rurales": (cluster.rurales),
                "comunas": sorted(comunas),
                "item_ids": ids_items,
            }
        )

    return {
        "total_items": len(items),
        "con_coordenadas": len(con_coordenadas),
        "sin_coordenadas": len(sin_coordenadas),
        "cantidad_clusters": len(resultado_clusters),
        "clusters": (resultado_clusters),
    }

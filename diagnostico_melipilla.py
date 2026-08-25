from types import SimpleNamespace

from planificacion.models import (
    BatchPlanificacionSemanal,
    SitioBatchSemanal,
    SitioPlanificado,
)

from planificacion.services.motor_batch_semanal.distancias import (
    distancia_haversine_km,
)

from planificacion.services.motor_batch_semanal.zonas_semanales import (
    MARGEN_EXPANSION_FRONTERA_KM,
    RADIO_ZONA_URBANA_KM,
    _radio_maximo_para_sitios,
    calcular_metricas_zona,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

BATCH_W3_ID = 20


# ============================================================
# ADAPTADOR
# ============================================================


def adaptar_sitio(sp):
    """
    Convierte SitioPlanificado de BD al formato geográfico
    utilizado internamente por el motor.
    """

    sitio = sp.sitio

    tipo_zona = (sitio.tipo_zona or "").strip().lower()

    urbano = "urb" in tipo_zona
    rural = "rur" in tipo_zona

    return SimpleNamespace(
        sitio_planificado_id=sp.id,
        sitio_id=sp.sitio_id,
        id_claro=sitio.id_claro,
        nombre=sitio.nombre,
        comuna=sitio.comuna,
        tipo_zona=sitio.tipo_zona,
        latitud=sitio.latitud,
        longitud=sitio.longitud,
        condicion_acceso=sitio.condiciones_acceso,
        estado_permiso=sp.estado_permiso,
        prioridad=sp.prioridad,
        urbano=urbano,
        rural=rural,
        original=sp,
    )


# ============================================================
# UTILIDADES
# ============================================================


def distancia(a, b):
    return distancia_haversine_km(
        a.latitud,
        a.longitud,
        b.latitud,
        b.longitud,
    )


def distancia_coords(
    lat_a,
    lng_a,
    lat_b,
    lng_b,
):
    return distancia_haversine_km(
        lat_a,
        lng_a,
        lat_b,
        lng_b,
    )


# ============================================================
# OBTENER W3
# ============================================================

batch_w3 = BatchPlanificacionSemanal.objects.get(
    pk=BATCH_W3_ID
)

participaciones_w3 = list(
    SitioBatchSemanal.objects
    .filter(
        batch=batch_w3
    )
    .select_related(
        "sitio_planificado__sitio"
    )
    .order_by(
        "id"
    )
)

sitios_w3 = [
    adaptar_sitio(
        item.sitio_planificado
    )
    for item in participaciones_w3
]


print("\n" + "=" * 120)

print(
    f"W3 = BATCH {batch_w3.id} | "
    f"OBJETIVO={batch_w3.objetivo_sitios} | "
    f"SITIOS={len(sitios_w3)}"
)

print("=" * 120)


# ============================================================
# MÉTRICAS ACTUALES DE W3
# ============================================================

metricas_w3 = calcular_metricas_zona(
    sitios_w3
)

centro_lat = metricas_w3[
    "centro_latitud"
]

centro_lng = metricas_w3[
    "centro_longitud"
]


print(
    f"Centro W3: "
    f"{centro_lat:.6f}, "
    f"{centro_lng:.6f}"
)

print(
    f"Radio W3: "
    f"{metricas_w3['radio_km']:.2f} km"
)

print(
    f"Distancia media W3: "
    f"{metricas_w3['distancia_media_km']:.2f} km"
)

print(
    f"P75 W3: "
    f"{metricas_w3['distancia_p75_km']:.2f} km"
)

print(
    f"Distancia máxima interna W3: "
    f"{metricas_w3['distancia_maxima_km']:.2f} km"
)


# ============================================================
# MELIPILLA
# ============================================================

melipilla_db = list(
    SitioPlanificado.objects
    .select_related(
        "sitio"
    )
    .filter(
        sitio__comuna__iexact="MELIPILLA",
        activo_en_mes=True,
    )
    .order_by(
        "id"
    )
)

melipilla = [
    adaptar_sitio(sp)
    for sp in melipilla_db
]


print("\n" + "=" * 120)
print("ANÁLISIS DE LOS SITIOS DE MELIPILLA CONTRA W3")
print("=" * 120)


# ============================================================
# ANALIZAR MELIPILLA UNO POR UNO
# ============================================================

for candidato in melipilla:

    distancias_w3 = []

    for sitio_w3 in sitios_w3:

        d = distancia(
            candidato,
            sitio_w3,
        )

        if d is not None:

            distancias_w3.append(
                (
                    d,
                    sitio_w3,
                )
            )

    distancias_w3.sort(
        key=lambda item: item[0]
    )

    distancia_minima = (
        distancias_w3[0][0]
        if distancias_w3
        else None
    )

    distancia_centro = distancia_coords(
        candidato.latitud,
        candidato.longitud,
        centro_lat,
        centro_lng,
    )

    zona_temporal = (
        sitios_w3
        +
        [candidato]
    )

    radio_base = _radio_maximo_para_sitios(
        zona_temporal
    )

    metricas_temporales = calcular_metricas_zona(
        zona_temporal
    )

    radio_resultante = metricas_temporales[
        "radio_km"
    ]

    radio_expansion_maximo = (
        radio_base
        +
        MARGEN_EXPANSION_FRONTERA_KM
    )

    distancia_contacto_maxima = (
        MARGEN_EXPANSION_FRONTERA_KM
        +
        RADIO_ZONA_URBANA_KM * 0.35
    )

    pasa_radio = (
        radio_resultante
        <=
        radio_expansion_maximo
    )

    pasa_contacto = (
        distancia_minima is not None
        and
        distancia_minima
        <=
        distancia_contacto_maxima
    )

    score_frontera = None

    if (
        distancia_minima is not None
        and
        distancia_centro is not None
    ):

        score_frontera = (
            distancia_minima * 0.55
            +
            distancia_centro * 0.25
            +
            radio_resultante * 0.20
        )


    print("\n" + "-" * 120)

    print(
        f"{candidato.id_claro} | "
        f"{candidato.nombre}"
    )

    print(
        f"Comuna: "
        f"{candidato.comuna}"
    )

    print(
        f"Tipo zona: "
        f"{candidato.tipo_zona}"
    )

    print(
        f"Coordenadas: "
        f"{candidato.latitud}, "
        f"{candidato.longitud}"
    )

    print(
        f"Distancia al CENTRO actual de W3: "
        f"{distancia_centro:.2f} km"
    )

    print(
        f"Distancia al SITIO de W3 más cercano: "
        f"{distancia_minima:.2f} km"
    )

    print(
        f"Radio actual de W3: "
        f"{metricas_w3['radio_km']:.2f} km"
    )

    print(
        f"Radio de W3 si agregáramos este sitio: "
        f"{radio_resultante:.2f} km"
    )

    print(
        f"Radio natural permitido: "
        f"{radio_base:.2f} km"
    )

    print(
        f"Radio máximo permitido con expansión: "
        f"{radio_expansion_maximo:.2f} km"
    )

    print(
        f"Contacto máximo permitido con la zona: "
        f"{distancia_contacto_maxima:.2f} km"
    )

    print(
        f"PASA REGLA DE RADIO: "
        f"{'SI' if pasa_radio else 'NO'}"
    )

    print(
        f"PASA REGLA DE CONTACTO: "
        f"{'SI' if pasa_contacto else 'NO'}"
    )

    if score_frontera is not None:

        print(
            f"SCORE DE FRONTERA: "
            f"{score_frontera:.2f}"
        )

    else:

        print(
            "SCORE DE FRONTERA: "
            "NO CALCULABLE"
        )


    print(
        "\n5 SITIOS DE W3 MÁS CERCANOS:"
    )

    for d, vecino in distancias_w3[:5]:

        print(
            f"    {d:7.2f} km | "
            f"{vecino.id_claro} | "
            f"{vecino.nombre} | "
            f"{vecino.comuna}"
        )


# ============================================================
# SITIOS QUE ACTUALMENTE FORMAN EL BORDE DE W3
# ============================================================

print("\n" + "=" * 120)
print("SITIOS DE W3 MÁS LEJANOS DE SU PROPIO CENTRO")
print("=" * 120)

ranking_borde = []

for sitio_w3 in sitios_w3:

    d_centro = distancia_coords(
        sitio_w3.latitud,
        sitio_w3.longitud,
        centro_lat,
        centro_lng,
    )

    ranking_borde.append(
        (
            d_centro,
            sitio_w3,
        )
    )

ranking_borde.sort(
    key=lambda item: item[0],
    reverse=True,
)

for d_centro, sitio_w3 in ranking_borde[:15]:

    print(
        f"{d_centro:7.2f} km | "
        f"{sitio_w3.id_claro} | "
        f"{sitio_w3.nombre} | "
        f"{sitio_w3.comuna}"
    )


# ============================================================
# COMPARACIÓN DIRECTA
# ============================================================

print("\n" + "=" * 120)
print("10 VECINOS DE W3 MÁS CERCANOS A CADA SITIO DE MELIPILLA")
print("=" * 120)

for candidato in melipilla:

    ranking = []

    for sitio_w3 in sitios_w3:

        d = distancia(
            candidato,
            sitio_w3,
        )

        if d is not None:

            ranking.append(
                (
                    d,
                    sitio_w3,
                )
            )

    ranking.sort(
        key=lambda item: item[0]
    )

    print(
        f"\n{candidato.id_claro} | "
        f"{candidato.nombre}"
    )

    for d, sitio_w3 in ranking[:10]:

        print(
            f"    {d:7.2f} km | "
            f"{sitio_w3.id_claro} | "
            f"{sitio_w3.nombre} | "
            f"{sitio_w3.comuna}"
        )

# planificacion/services/asignacion_operativa.py

from django.db import transaction

from operaciones.models import ServicioCotizado
from planificacion.modelos import (SalidaPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
from planificacion.services.asignacion_tecnicos import \
    asignar_tecnicos_servicio

# ============================================================
# ESTADO OPERACIONAL ADMITIDO PARA ASIGNACIÓN MASIVA
# ============================================================

ESTADO_SERVICIO_ASIGNABLE = "aprobado_pendiente"


# ============================================================
# ESTADOS DE PARTICIPACIÓN DIARIA ASIGNABLES
# ============================================================

ESTADOS_SITIO_SALIDA_ASIGNABLES = {
    "planificado",
    "listo_asignar",
}


# ============================================================
# NOMBRE DE USUARIO
# ============================================================


def _nombre_usuario(
    usuario,
):
    nombre = usuario.get_full_name() or usuario.username or str(usuario.pk)

    return nombre.strip()


# ============================================================
# IDENTIFICADOR DEL SITIO
# ============================================================


def _identificador_sitio(
    sitio,
):
    return sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"


# ============================================================
# DU FORMATEADO
# ============================================================


def _du_formateado(
    servicio,
):
    return f"DU{str(servicio.du).zfill(8)}"


# ============================================================
# OBTENER INTEGRANTES ACTIVOS DE CUADRILLA
# ============================================================


def obtener_integrantes_cuadrilla(
    cuadrilla,
):
    """
    Devuelve los integrantes activos configurados
    explícitamente en CuadrillaOperativa.

    La pertenencia a cuadrilla.integrantes es la fuente
    oficial de asignación operacional.

    No se vuelve a filtrar por rol, porque la cuadrilla
    ya define de forma explícita quiénes son sus integrantes.
    """

    if cuadrilla is None:

        return []

    return list(
        cuadrilla.integrantes.filter(
            is_active=True,
        )
        .distinct()
        .order_by(
            "first_name",
            "last_name",
            "username",
            "id",
        )
    )


# ============================================================
# BUSCAR SERVICIO DEL SITIO
# ============================================================


def obtener_servicio_sitio_salida(
    sitio_salida,
):
    """
    Obtiene el ServicioCotizado vigente asociado al sitio.

    La relación utilizada actualmente por Operaciones
    continúa siendo id_claro.
    """

    sitio = sitio_salida.sitio_batch.sitio_planificado.sitio

    id_claro = (sitio.id_claro or "").strip()

    if not id_claro:

        return None

    return (
        ServicioCotizado.objects.filter(
            id_claro=id_claro,
        )
        .order_by(
            "-id",
        )
        .first()
    )


# ============================================================
# EVALUAR UN SITIO
# ============================================================


def evaluar_sitio_para_asignacion(
    sitio_salida,
):
    """
    Determina si una participación diaria puede incluirse
    dentro de una asignación masiva.

    REGLAS:

    1. participación diaria activa;
    2. permiso aprobado o no requiere;
    3. ServicioCotizado existente;
    4. estado exactamente aprobado_pendiente.
    """

    sitio_planificado = sitio_salida.sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    identificador = _identificador_sitio(
        sitio,
    )

    resultado = {
        "sitio_salida": sitio_salida,
        "sitio_salida_id": sitio_salida.pk,
        "sitio": sitio,
        "sitio_id": sitio.pk,
        "id_claro": identificador,
        "nombre": sitio.nombre or "",
        "comuna": sitio.comuna or "",
        "direccion": sitio.direccion or "",
        "servicio": None,
        "servicio_id": None,
        "du": None,
        "du_texto": "",
        "estado_operaciones": None,
        "estado_operaciones_display": "",
        "asignable": False,
        "motivo": "",
    }

    # ========================================================
    # PARTICIPACIÓN DIARIA
    # ========================================================

    if sitio_salida.estado not in ESTADOS_SITIO_SALIDA_ASIGNABLES:

        resultado["motivo"] = (
            "El sitio ya no se encuentra pendiente "
            "de asignación dentro de esta jornada."
        )

        return resultado

    # ========================================================
    # PERMISO
    # ========================================================

    if sitio_planificado.estado_permiso not in {
        "aprobado",
        "no_requiere",
    }:

        resultado["motivo"] = "El sitio no posee permiso aprobado."

        return resultado

    # ========================================================
    # SERVICIO OPERACIONAL
    # ========================================================

    servicio = obtener_servicio_sitio_salida(
        sitio_salida,
    )

    resultado["servicio"] = servicio

    if servicio is None:

        resultado["motivo"] = "No existe ServicioCotizado asociado."

        resultado["estado_operaciones_display"] = "Sin servicio operacional"

        return resultado

    resultado["servicio_id"] = servicio.pk

    resultado["du"] = servicio.du

    resultado["du_texto"] = _du_formateado(
        servicio,
    )

    resultado["estado_operaciones"] = servicio.estado

    try:

        resultado["estado_operaciones_display"] = servicio.get_estado_display()

    except Exception:

        resultado["estado_operaciones_display"] = servicio.estado

    # ========================================================
    # VALIDACIÓN FUNDAMENTAL
    # ========================================================

    if servicio.estado != ESTADO_SERVICIO_ASIGNABLE:

        resultado["motivo"] = (
            "El servicio no está pendiente por asignar. "
            f"Estado actual: "
            f"{resultado['estado_operaciones_display']}."
        )

        return resultado

    resultado["asignable"] = True

    return resultado


# ============================================================
# CARGAR SALIDA
# ============================================================


def _obtener_salida(
    salida_id,
    *,
    bloquear=False,
):
    queryset = SalidaPlanificacionDiaria.objects.select_related(
        "batch",
        "batch__planificacion",
        "disponibilidad_cuadrilla",
        ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
    )

    if bloquear:

        queryset = queryset.select_for_update()

    return queryset.get(
        pk=salida_id,
    )


# ============================================================
# CARGAR SITIOS ACTIVOS DE SALIDA
# ============================================================


def _obtener_sitios_salida(
    salida,
    *,
    bloquear=False,
):
    queryset = (
        SitioSalidaPlanificacionDiaria.objects.filter(
            salida=salida,
        )
        .exclude(
            estado__in=[
                "retirado",
                "cancelado",
                "reprogramado",
            ]
        )
        .select_related(
            "salida",
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            ("sitio_batch__" "sitio_planificado__sitio"),
        )
        .order_by(
            "orden",
            "id",
        )
    )

    if bloquear:

        queryset = queryset.select_for_update()

    return list(
        queryset,
    )


# ============================================================
# PREVIEW DE UNA SALIDA
# ============================================================


def construir_preview_asignacion_salida(
    salida,
):
    """
    Construye toda la información de una cuadrilla/salida
    antes de confirmar la asignación.
    """

    disponibilidad = salida.disponibilidad_cuadrilla

    cuadrilla = disponibilidad.cuadrilla_operativa if disponibilidad else None

    integrantes = (
        obtener_integrantes_cuadrilla(
            cuadrilla,
        )
        if cuadrilla
        else []
    )

    sitios = _obtener_sitios_salida(
        salida,
    )

    evaluaciones = [
        evaluar_sitio_para_asignacion(
            sitio_salida,
        )
        for sitio_salida in sitios
    ]

    asignables = [evaluacion for evaluacion in evaluaciones if evaluacion["asignable"]]

    omitidos = [
        evaluacion for evaluacion in evaluaciones if not evaluacion["asignable"]
    ]

    return {
        "salida": salida,
        "salida_id": salida.pk,
        "fecha": salida.fecha,
        "cuadrilla": cuadrilla,
        "cuadrilla_id": (cuadrilla.pk if cuadrilla else None),
        "cuadrilla_codigo": (
            cuadrilla.codigo if cuadrilla else salida.cuadrilla_codigo
        ),
        "cuadrilla_nombre": (
            cuadrilla.nombre if cuadrilla else salida.cuadrilla_nombre
        ),
        "integrantes": integrantes,
        "integrantes_ids": [usuario.pk for usuario in integrantes],
        "integrantes_nombres": [
            _nombre_usuario(
                usuario,
            )
            for usuario in integrantes
        ],
        "sitios": evaluaciones,
        "asignables": asignables,
        "omitidos": omitidos,
        "cantidad_sitios": len(
            evaluaciones,
        ),
        "cantidad_asignables": len(
            asignables,
        ),
        "cantidad_omitidos": len(
            omitidos,
        ),
        "puede_confirmar": bool(cuadrilla and integrantes and asignables),
    }


# ============================================================
# PREVIEW DE DÍA COMPLETO
# ============================================================


def construir_preview_asignacion_dia(
    *,
    batch,
    fecha,
):
    """
    Construye el preview de todas las salidas existentes
    para una fecha determinada.
    """

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
        .order_by(
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa__orden"),
            "orden",
            "id",
        )
    )

    grupos = [
        construir_preview_asignacion_salida(
            salida,
        )
        for salida in salidas
    ]

    cantidad_asignables = sum(grupo["cantidad_asignables"] for grupo in grupos)

    cantidad_omitidos = sum(grupo["cantidad_omitidos"] for grupo in grupos)

    cuadrillas_sin_integrantes = [
        grupo
        for grupo in grupos
        if (grupo["cantidad_asignables"] > 0 and not grupo["integrantes"])
    ]

    return {
        "batch": batch,
        "fecha": fecha,
        "salidas": grupos,
        "cantidad_salidas": len(
            grupos,
        ),
        "cantidad_asignables": (cantidad_asignables),
        "cantidad_omitidos": (cantidad_omitidos),
        "cuadrillas_sin_integrantes": (cuadrillas_sin_integrantes),
        "puede_confirmar": bool(cantidad_asignables and not cuadrillas_sin_integrantes),
    }


# ============================================================
# MARCAR PARTICIPACIÓN COMO ASIGNADA
# ============================================================


def _marcar_sitio_salida_asignado(
    *,
    sitio_salida,
    usuario,
):
    sitio_salida.estado = "asignado"

    sitio_salida.actualizado_por = usuario

    sitio_salida.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )


# ============================================================
# ACTUALIZAR ESTADO DE SALIDA
# ============================================================


def _actualizar_estado_salida_despues_asignacion(
    *,
    salida,
    usuario,
):
    estados = list(
        salida.sitios.exclude(
            estado__in=[
                "retirado",
                "cancelado",
                "reprogramado",
            ]
        ).values_list(
            "estado",
            flat=True,
        )
    )

    if not estados:

        return

    if all(estado == "asignado" for estado in estados):

        nuevo_estado = "asignada"

    elif any(estado == "asignado" for estado in estados):

        nuevo_estado = "parcial"

    else:

        return

    if salida.estado == nuevo_estado:

        return

    salida.estado = nuevo_estado

    salida.actualizado_por = usuario

    salida.save(
        update_fields=[
            "estado",
            "actualizado_por",
            "actualizado_en",
        ]
    )


# ============================================================
# EJECUTAR ASIGNACIÓN DE UNA SALIDA
# ============================================================


@transaction.atomic
def asignar_salida_completa(
    *,
    salida_id,
    usuario,
    request=None,
):
    """
    Asigna todos los sitios elegibles de una salida a los
    integrantes activos configurados en la cuadrilla.

    Cada ServicioCotizado se vuelve a validar bajo lock
    dentro de asignar_tecnicos_servicio().
    """

    salida = _obtener_salida(
        salida_id,
        bloquear=True,
    )

    disponibilidad = salida.disponibilidad_cuadrilla

    cuadrilla = disponibilidad.cuadrilla_operativa if disponibilidad else None

    # ========================================================
    # CUADRILLA
    # ========================================================

    if cuadrilla is None:

        return {
            "salida": salida,
            "cuadrilla": None,
            "integrantes": [],
            "asignados": [],
            "omitidos": [],
            "cantidad_asignados": 0,
            "cantidad_omitidos": 0,
            "error": ("La salida no posee una cuadrilla " "operativa relacionada."),
        }

    # ========================================================
    # INTEGRANTES
    # ========================================================

    integrantes = obtener_integrantes_cuadrilla(
        cuadrilla,
    )

    if not integrantes:

        return {
            "salida": salida,
            "cuadrilla": cuadrilla,
            "integrantes": [],
            "asignados": [],
            "omitidos": [],
            "cantidad_asignados": 0,
            "cantidad_omitidos": 0,
            "error": (
                f"{cuadrilla.nombre} no posee " "integrantes activos configurados."
            ),
        }

    # ========================================================
    # SITIOS
    # ========================================================

    sitios_salida = _obtener_sitios_salida(
        salida,
        bloquear=True,
    )

    asignados = []

    omitidos = []

    # ========================================================
    # PROCESAR
    # ========================================================

    for sitio_salida in sitios_salida:

        evaluacion = evaluar_sitio_para_asignacion(
            sitio_salida,
        )

        if not evaluacion["asignable"]:

            omitidos.append(
                evaluacion,
            )

            continue

        servicio = evaluacion["servicio"]

        try:

            resultado_servicio = asignar_tecnicos_servicio(
                servicio=servicio,
                tecnicos=integrantes,
                actor=usuario,
                request=request,
                exigir_pendiente=True,
                enviar_notificaciones=True,
            )

        except Exception as exc:

            evaluacion["asignable"] = False

            evaluacion["motivo"] = str(
                exc,
            )

            omitidos.append(
                evaluacion,
            )

            continue

        servicio_actualizado = resultado_servicio["servicio"]

        _marcar_sitio_salida_asignado(
            sitio_salida=sitio_salida,
            usuario=usuario,
        )

        evaluacion["servicio"] = servicio_actualizado

        evaluacion["estado_operaciones"] = servicio_actualizado.estado

        try:

            evaluacion["estado_operaciones_display"] = (
                servicio_actualizado.get_estado_display()
            )

        except Exception:

            evaluacion["estado_operaciones_display"] = servicio_actualizado.estado

        asignados.append(
            evaluacion,
        )

    # ========================================================
    # ACTUALIZAR ESTADO DE SALIDA
    # ========================================================

    _actualizar_estado_salida_despues_asignacion(
        salida=salida,
        usuario=usuario,
    )

    return {
        "salida": salida,
        "cuadrilla": cuadrilla,
        "integrantes": integrantes,
        "asignados": asignados,
        "omitidos": omitidos,
        "cantidad_asignados": len(
            asignados,
        ),
        "cantidad_omitidos": len(
            omitidos,
        ),
        "error": "",
    }


# ============================================================
# EJECUTAR ASIGNACIÓN DE TODO EL DÍA
# ============================================================


@transaction.atomic
def asignar_dia_completo(
    *,
    batch,
    fecha,
    usuario,
    request=None,
):
    """
    Ejecuta todas las salidas del día.

    Cada salida utiliza exclusivamente los integrantes
    activos configurados en su propia CuadrillaOperativa.
    """

    salidas = list(
        SalidaPlanificacionDiaria.objects.select_for_update()
        .filter(
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
        .order_by(
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa__orden"),
            "orden",
            "id",
        )
    )

    resultados = []

    total_asignados = 0

    total_omitidos = 0

    # ========================================================
    # PROCESAR CADA SALIDA
    # ========================================================

    for salida in salidas:

        resultado = asignar_salida_completa(
            salida_id=salida.pk,
            usuario=usuario,
            request=request,
        )

        resultados.append(
            resultado,
        )

        total_asignados += int(
            resultado.get(
                "cantidad_asignados",
                0,
            )
            or 0
        )

        total_omitidos += int(
            resultado.get(
                "cantidad_omitidos",
                0,
            )
            or 0
        )

    return {
        "batch": batch,
        "fecha": fecha,
        "resultados": resultados,
        "cantidad_salidas": len(
            resultados,
        ),
        "cantidad_asignados": (total_asignados),
        "cantidad_omitidos": (total_omitidos),
    }

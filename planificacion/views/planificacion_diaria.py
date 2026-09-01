# planificacion/views/planificacion_diaria.py

from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from planificacion.modelos import (SalidaPlanificacionDiaria,
                                   SitioSalidaPlanificacionDiaria)
from planificacion.models import BatchPlanificacionSemanal, ContactoSitio
from planificacion.services.motor_batch_semanal.cuadrillas import \
    construir_configuracion_cuadrilla
from planificacion.services.motor_batch_semanal.salidas import \
    encontrar_mejor_salida
from planificacion.services.planificacion_diaria import (
    ESTADOS_SALIDA_EDITABLES, ESTADOS_SITIO_SALIDA_EDITABLES,
    _restaurar_sitio_planificado_si_corresponde, _sitio_batch_a_motor,
    guardar_plan_diario_batch, obtener_estado_operacional_sitio,
    obtener_resumen_planificacion_diaria,
    obtener_sitios_pendientes_planificacion_diaria, sincronizar_estado_salida)
from planificacion.services.traslado_mes_siguiente import (
    obtener_planificacion_mes_siguiente, trasladar_sitio_mes_siguiente)
from usuarios.decoradores import rol_requerido

# ============================================================
# ROLES
# ============================================================

ROLES_PLANIFICACION_DIARIA = [
    "admin",
    "pm",
    "supervisor",
]


# ============================================================
# UTILIDADES
# ============================================================


def _url_detalle_diario(
    batch,
):
    return reverse(
        "planificacion:detalle_planificacion_diaria",
        kwargs={
            "batch_id": batch.pk,
        },
    )

# ============================================================
# IDENTIFICAR SITIO
# ============================================================


def _identificador_sitio_planificacion(
    sitio_planificado,
):
    sitio = sitio_planificado.sitio

    return (
        sitio.id_claro
        or sitio.id_sites
        or f"Sitio {sitio.pk}"
    )

# ============================================================
# INFORMACIÓN DE CONTACTO PARA PLANIFICACIÓN DIARIA
# ============================================================


def _limpiar_texto_contacto(
    valor,
):
    """
    Normaliza exclusivamente para visualización.

    No modifica ContactoSitio ni SitioMovil.
    """

    return str(valor or "").strip()


def _deduplicar_textos_contacto(
    valores,
):
    """
    Elimina textos vacíos y duplicados conservando
    el orden original.
    """

    resultado = []

    vistos = set()

    for valor in valores:

        texto = _limpiar_texto_contacto(
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


def _construir_info_contactos_ui(
    sitio,
    contactos,
):
    """
    Consolida la información ACTUAL de ContactoSitio
    para mostrarla en Planificación Diaria.

    IMPORTANTE
    ==========================================================

    - No persiste ningún snapshot.
    - No modifica el motor.
    - No modifica PerfilAccesoSitio.
    - No modifica SitioMovil.
    - Conserva todos los contactos activos del sitio.
    """

    contactos_ui = []

    observaciones = []

    acciones = []

    for contacto in contactos:

        propietario = _limpiar_texto_contacto(
            contacto.propietario,
        )

        telefono = _limpiar_texto_contacto(
            contacto.telefono,
        )

        correo = _limpiar_texto_contacto(
            contacto.correo,
        )

        responsable = _limpiar_texto_contacto(
            contacto.responsable,
        )

        tipo_contacto = _limpiar_texto_contacto(
            contacto.tipo_contacto,
        )

        observacion = _limpiar_texto_contacto(
            contacto.observaciones,
        )

        accion = _limpiar_texto_contacto(
            contacto.accion,
        )

        if observacion:

            observaciones.append(
                observacion,
            )

        if accion:

            acciones.append(
                accion,
            )

        contactos_ui.append(
            {
                "id": contacto.pk,
                "propietario": propietario,
                "telefono": telefono,
                "correo": correo,
                "responsable": responsable,
                "tipo_contacto": tipo_contacto,
                "prioridad_contacto": contacto.prioridad_contacto,
                "fecha_informacion": contacto.fecha_informacion,
            }
        )

    observaciones = _deduplicar_textos_contacto(
        observaciones,
    )

    acciones = _deduplicar_textos_contacto(
        acciones,
    )

    # ========================================================
    # ¿EXISTEN DATOS REALMENTE COMPARTIBLES?
    # ========================================================

    tiene_datos_contacto = any(
        (
            contacto["propietario"]
            or contacto["telefono"]
            or contacto["correo"]
            or contacto["responsable"]
            or contacto["tipo_contacto"]
        )
        for contacto in contactos_ui
    )

    # ========================================================
    # TEXTO PARA COPIAR / WHATSAPP
    # ========================================================

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    lineas = [
        "GZ SERVICES - DATOS DE CONTACTO",
        "",
        f"Sitio: {identificador}",
    ]

    nombre = _limpiar_texto_contacto(
        sitio.nombre,
    )

    if nombre:

        lineas.append(f"Nombre: {nombre}")

    comuna = _limpiar_texto_contacto(
        sitio.comuna,
    )

    if comuna:

        lineas.append(f"Comuna: {comuna}")

    direccion = _limpiar_texto_contacto(
        sitio.direccion,
    )

    if direccion:

        lineas.append(f"Dirección: {direccion}")

    acceso = _limpiar_texto_contacto(
        sitio.condiciones_acceso,
    )

    if acceso:

        lineas.append(f"Acceso: {acceso}")

    # ========================================================
    # CONTACTOS
    # ========================================================

    if tiene_datos_contacto:

        lineas.extend(
            [
                "",
                "CONTACTOS",
            ]
        )

        numero_contacto = 0

        for contacto in contactos_ui:

            if not (
                contacto["propietario"]
                or contacto["telefono"]
                or contacto["correo"]
                or contacto["responsable"]
                or contacto["tipo_contacto"]
            ):
                continue

            numero_contacto += 1

            lineas.extend(
                [
                    "",
                    f"Contacto {numero_contacto}",
                ]
            )

            if contacto["tipo_contacto"]:

                lineas.append(f"Tipo: {contacto['tipo_contacto']}")

            if contacto["propietario"]:

                lineas.append(f"Propietario / contacto: {contacto['propietario']}")

            if contacto["telefono"]:

                lineas.append(f"Teléfono: {contacto['telefono']}")

            if contacto["correo"]:

                lineas.append(f"Correo: {contacto['correo']}")

            if contacto["responsable"]:

                lineas.append(f"Responsable: {contacto['responsable']}")

            if contacto["fecha_informacion"]:

                lineas.append(
                    ("Fecha información: " f"{contacto['fecha_informacion']:%d/%m/%Y}")
                )

    # ========================================================
    # OBSERVACIONES
    # ========================================================

    if observaciones:

        lineas.extend(
            [
                "",
                "⚠️ OBSERVACIONES",
            ]
        )

        for observacion in observaciones:

            lineas.append(f"- {observacion}")

    # ========================================================
    # ACCIONES
    # ========================================================

    if acciones:

        lineas.extend(
            [
                "",
                "⚠️ ACCIONES",
            ]
        )

        for accion in acciones:

            lineas.append(f"- {accion}")

    return {
        "contactos": contactos_ui,
        "observaciones": observaciones,
        "acciones": acciones,
        "tiene_observaciones": bool(
            observaciones,
        ),
        "tiene_acciones": bool(
            acciones,
        ),
        "tiene_alertas": bool(observaciones or acciones),
        "tiene_datos_contacto": bool(
            tiene_datos_contacto,
        ),
        "texto_compartir": "\n".join(
            lineas,
        ),
    }


def _estado_visual_operaciones(
    estado,
):
    """
    Traduce el estado real de Operaciones a una etiqueta
    pensada exclusivamente para la pantalla de planificación.

    No modifica ningún modelo.
    """

    mapa = {
        None: {
            "codigo": "sin_servicio",
            "texto": "Sin servicio operativo",
            "grupo": "pendiente",
        },
        "cotizado": {
            "codigo": "cotizado",
            "texto": "Cotizado",
            "grupo": "pendiente",
        },
        "aprobado_pendiente": {
            "codigo": "listo_asignar",
            "texto": "Pendiente por asignar",
            "grupo": "pendiente",
        },
        "asignado": {
            "codigo": "asignado",
            "texto": "Asignado",
            "grupo": "activo",
        },
        "en_progreso": {
            "codigo": "en_ejecucion",
            "texto": "En ejecución",
            "grupo": "activo",
        },
        "finalizado_trabajador": {
            "codigo": "revision",
            "texto": "Pendiente revisión supervisor",
            "grupo": "revision",
        },
        "en_revision_supervisor": {
            "codigo": "revision",
            "texto": "Pendiente revisión supervisor",
            "grupo": "revision",
        },
        "rechazado_supervisor": {
            "codigo": "rechazado_supervisor",
            "texto": "Rechazado por supervisor",
            "grupo": "activo",
        },
        "aprobado_supervisor": {
            "codigo": "finalizado",
            "texto": "Finalizado",
            "grupo": "finalizado",
        },
        "finalizado": {
            "codigo": "finalizado",
            "texto": "Finalizado",
            "grupo": "finalizado",
        },
    }

    return mapa.get(
        estado,
        {
            "codigo": str(estado or ""),
            "texto": str(estado or "Sin estado"),
            "grupo": "pendiente",
        },
    )


def _obtener_prioridad_diaria(
    item_batch,
):
    """
    Obtiene la PrioridadPlanificacionDiaria asociada al sitio.

    Al ser OneToOne puede no existir.

    No consideramos como prioridad activa las prioridades
    cumplidas o canceladas.
    """

    try:
        prioridad = item_batch.prioridad_diaria

    except ObjectDoesNotExist:
        return None

    if prioridad.estado != "activa":
        return prioridad

    return prioridad


# ============================================================
# VER PENDIENTES EN GOOGLE MAPS
# ============================================================


@require_GET
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def mapa_pendientes_planificacion_diaria(
    request,
    batch_id,
):
    """
    Abre en Google Maps los sitios actualmente aprobados
    que todavía no tienen salida diaria.

    No modifica ningún dato.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
        ),
        pk=batch_id,
    )

    pendientes = list(
        obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )
    )

    coordenadas = []

    for item in pendientes:

        sitio = item.sitio_planificado.sitio

        try:
            latitud = float(sitio.latitud)

            longitud = float(sitio.longitud)

        except (
            TypeError,
            ValueError,
        ):
            continue

        coordenadas.append(f"{latitud},{longitud}")

    if not coordenadas:

        messages.warning(
            request,
            (
                "Los sitios pendientes no poseen "
                "coordenadas válidas para abrir el mapa."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # UN SOLO SITIO
    # ========================================================

    if len(coordenadas) == 1:

        url = "https://www.google.com/maps/search/" "?api=1" f"&query={coordenadas[0]}"

        return redirect(
            url,
        )

    # ========================================================
    # VARIOS SITIOS
    # ========================================================
    #
    # Primero:
    #     origen
    #
    # Último:
    #     destino
    #
    # Intermedios:
    #     waypoints
    #
    # Google Maps decidirá visualmente la navegación.
    # Esto NO altera el motor de planificación.
    # ========================================================

    origen = coordenadas[0]

    destino = coordenadas[-1]

    intermedios = coordenadas[1:-1]

    url = (
        "https://www.google.com/maps/dir/"
        "?api=1"
        f"&origin={origen}"
        f"&destination={destino}"
    )

    if intermedios:

        waypoints = "%7C".join(intermedios)

        url += f"&waypoints={waypoints}"

    return redirect(
        url,
    )


# ============================================================
# CONSTRUIR FILA DE SITIO
# ============================================================


def _construir_fila_sitio(
    sitio_salida,
    *,
    contactos_por_sitio=None,
):
    """
    Construye toda la información necesaria para la interfaz.

    REGLA DE RETIRO
    ==========================================================

    Todo sitio visible dentro de una jornada puede retirarse
    desde Planificación Diaria.

    Quitar de la jornada NO significa cancelar Operaciones.

    INFORMACIÓN DE CONTACTO
    ==========================================================

    La información de ContactoSitio utilizada aquí corresponde
    a la lectura actual realizada al cargar esta pantalla.

    No se persiste ningún snapshot.
    """

    item_batch = sitio_salida.sitio_batch

    sitio_planificado = item_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    salida = sitio_salida.salida

    # ========================================================
    # CONTACTOS DEL SITIO
    # ========================================================

    contactos_por_sitio = contactos_por_sitio or {}

    contactos_sitio = contactos_por_sitio.get(
        sitio.pk,
        [],
    )

    info_contactos = _construir_info_contactos_ui(
        sitio,
        contactos_sitio,
    )

    # ========================================================
    # PRIORIDAD DIARIA
    # ========================================================

    prioridad_diaria = _obtener_prioridad_diaria(
        item_batch,
    )

    prioridad_activa = bool(prioridad_diaria and prioridad_diaria.estado == "activa")

    prioridad_id = prioridad_diaria.pk if prioridad_activa else None

    # ========================================================
    # OPERACIONES
    # ========================================================

    estado_operacional = obtener_estado_operacional_sitio(
        sitio_planificado,
    )

    servicio = estado_operacional["servicio"]

    estado_visual = _estado_visual_operaciones(
        estado_operacional["estado_operaciones"],
    )

    # ========================================================
    # TÉCNICOS
    # ========================================================

    tecnicos = []

    if servicio is not None:

        try:

            tecnicos = [
                (tecnico.get_full_name() or tecnico.username or str(tecnico.pk))
                for tecnico in servicio.trabajadores_asignados.all()
            ]

        except Exception:

            tecnicos = []

    # ========================================================
    # RETIRO DE PLANIFICACIÓN
    # ========================================================

    puede_quitar_planificacion = True

    # ========================================================
    # RESPUESTA
    # ========================================================

    return {
        # ====================================================
        # ENTIDADES
        # ====================================================
        "sitio_salida": sitio_salida,
        "sitio_batch": item_batch,
        "sitio_planificado": sitio_planificado,
        "sitio": sitio,
        "servicio": servicio,
        # ====================================================
        # IDENTIFICACIÓN
        # ====================================================
        "id_claro": (sitio.id_claro or sitio.id_sites or ""),
        "id_new": (sitio.id_sites_new or ""),
        "nombre": (sitio.nombre or ""),
        "comuna": (sitio.comuna or ""),
        "direccion": (sitio.direccion or ""),
        "tipo_zona": (sitio.tipo_zona or ""),
        "condiciones_acceso": (sitio.condiciones_acceso or ""),
        # ====================================================
        # CONTACTOS / ALERTAS
        # ====================================================
        "contactos": info_contactos["contactos"],
        "observaciones_contacto": (info_contactos["observaciones"]),
        "acciones_contacto": (info_contactos["acciones"]),
        "tiene_observaciones_contacto": (info_contactos["tiene_observaciones"]),
        "tiene_acciones_contacto": (info_contactos["tiene_acciones"]),
        "tiene_alertas_contacto": (info_contactos["tiene_alertas"]),
        "tiene_datos_contacto": (info_contactos["tiene_datos_contacto"]),
        "texto_contacto_compartir": (info_contactos["texto_compartir"]),
        # ====================================================
        # PLANIFICACIÓN
        # ====================================================
        "fecha": salida.fecha,
        "orden": sitio_salida.orden,
        "estado_planificacion": (sitio_salida.estado),
        "estado_planificacion_display": (sitio_salida.get_estado_display()),
        "origen_planificacion": (sitio_salida.origen),
        "bloqueado_planificacion": bool(sitio_salida.bloqueado),
        # ====================================================
        # ACCIONES
        # ====================================================
        "puede_quitar_planificacion": (puede_quitar_planificacion),
        # ====================================================
        # PRIORIDAD
        # ====================================================
        "es_prioridad": prioridad_activa,
        "prioridad_id": prioridad_id,
        "prioridad_diaria": prioridad_diaria,
        "prioridad_activa": prioridad_activa,
        "prioridad_nivel": (prioridad_diaria.prioridad if prioridad_diaria else ""),
        "prioridad_nivel_display": (
            prioridad_diaria.get_prioridad_display() if prioridad_diaria else ""
        ),
        "prioridad_estado": (prioridad_diaria.estado if prioridad_diaria else ""),
        "prioridad_estado_display": (
            prioridad_diaria.get_estado_display() if prioridad_diaria else ""
        ),
        "prioridad_es_ancla": bool(prioridad_diaria and prioridad_diaria.es_ancla),
        "prioridad_fecha_objetivo": (
            prioridad_diaria.fecha_objetivo if prioridad_diaria else None
        ),
        "prioridad_fecha_obligatoria": bool(
            prioridad_diaria and prioridad_diaria.fecha_es_obligatoria
        ),
        "prioridad_cuadrilla": (
            prioridad_diaria.cuadrilla_obligatoria if prioridad_diaria else None
        ),
        "prioridad_cuadrilla_id": (
            prioridad_diaria.cuadrilla_obligatoria_id if prioridad_diaria else None
        ),
        "prioridad_cuadrilla_codigo": (
            prioridad_diaria.cuadrilla_obligatoria.codigo
            if (prioridad_diaria and prioridad_diaria.cuadrilla_obligatoria_id)
            else ""
        ),
        "prioridad_cuadrilla_nombre": (
            prioridad_diaria.cuadrilla_obligatoria.nombre
            if (prioridad_diaria and prioridad_diaria.cuadrilla_obligatoria_id)
            else ""
        ),
        "prioridad_motivo": (prioridad_diaria.motivo if prioridad_diaria else ""),
        "prioridad_objetivo_sitios": (
            prioridad_diaria.objetivo_sitios_salida if prioridad_diaria else None
        ),
        "prioridad_distancia_preferida_km": (
            prioridad_diaria.distancia_preferida_km if prioridad_diaria else None
        ),
        "prioridad_distancia_maxima_km": (
            prioridad_diaria.distancia_maxima_km if prioridad_diaria else None
        ),
        "prioridad_minutos_preferidos": (
            prioridad_diaria.minutos_preferidos if prioridad_diaria else None
        ),
        "prioridad_minutos_maximos": (
            prioridad_diaria.minutos_maximos if prioridad_diaria else None
        ),
        # ====================================================
        # PERMISO
        # ====================================================
        "estado_permiso": (sitio_planificado.estado_permiso),
        "estado_permiso_display": (sitio_planificado.get_estado_permiso_display()),
        # ====================================================
        # OPERACIONES
        # ====================================================
        "servicio_id": (estado_operacional["servicio_id"]),
        "du": (estado_operacional["du"]),
        "estado_operaciones": (estado_operacional["estado_operaciones"]),
        "estado_operativo_codigo": (estado_visual["codigo"]),
        "estado_operativo_texto": (estado_visual["texto"]),
        "grupo_operativo": (estado_visual["grupo"]),
        "puede_asignar": (estado_operacional["puede_asignar"]),
        "finalizado": (estado_operacional["finalizado"]),
        "tecnicos": tecnicos,
    }


# ============================================================
# LISTA GENERAL DE PLANIFICACIÓN DIARIA
# ============================================================


@require_GET
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def lista_planificacion_diaria(
    request,
):
    """
    Pantalla general de entrada a Planificación Diaria.

    Desde aquí se muestran las semanas existentes y su
    situación diaria actual.
    """

    batches = list(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        )
        .prefetch_related(
            "salidas_diarias",
            "salidas_diarias__sitios",
            "sitios",
        )
        .exclude(
            estado="cancelado",
        )
        .order_by(
            "-fecha_inicio",
            "-id",
        )
    )

    filas = []

    # ========================================================
    # CONSTRUIR RESUMEN DE CADA SEMANA
    # ========================================================

    for batch in batches:

        resumen = obtener_resumen_planificacion_diaria(
            batch,
        )

        pendientes_planificar = obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )

        total_finalizados = int(
            resumen.get(
                "finalizados",
                0,
            )
            or 0
        )

        total_sitios = int(
            resumen.get(
                "total_sitios",
                0,
            )
            or 0
        )

        total_salidas = int(
            resumen.get(
                "total_salidas",
                0,
            )
            or 0
        )

        # ====================================================
        # ESTADO VISUAL GENERAL
        # ====================================================

        if total_sitios > 0 and total_finalizados == total_sitios:

            estado_diario = "finalizada"

            estado_diario_texto = "Finalizada"

        elif (
            int(
                resumen.get(
                    "salidas_en_ejecucion",
                    0,
                )
                or 0
            )
            > 0
        ):

            estado_diario = "en_ejecucion"

            estado_diario_texto = "En ejecución"

        elif (
            int(
                resumen.get(
                    "salidas_parciales",
                    0,
                )
                or 0
            )
            > 0
        ):

            estado_diario = "parcial"

            estado_diario_texto = "Parcial"

        elif (
            int(
                resumen.get(
                    "salidas_asignadas",
                    0,
                )
                or 0
            )
            > 0
        ):

            estado_diario = "asignada"

            estado_diario_texto = "Asignada"

        elif total_salidas > 0:

            estado_diario = "planificada"

            estado_diario_texto = "Planificada"

        elif pendientes_planificar:

            estado_diario = "lista_planificar"

            estado_diario_texto = "Sitios disponibles"

        else:

            estado_diario = "esperando_permisos"

            estado_diario_texto = "Esperando permisos"

        filas.append(
            {
                "batch": batch,
                "mensual": batch.planificacion,
                "resumen": resumen,
                "pendientes_planificar": (pendientes_planificar),
                "cantidad_pendientes_planificar": (len(pendientes_planificar)),
                "estado_diario": estado_diario,
                "estado_diario_texto": (estado_diario_texto),
            }
        )

    return render(
        request,
        "planificacion/diaria/lista.html",
        {
            "filas": filas,
        },
    )


# ============================================================
# DETALLE DE PLANIFICACIÓN DIARIA
# ============================================================


@require_GET
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def detalle_planificacion_diaria(
    request,
    batch_id,
):
    """
    Pantalla central de operación diaria.

    Muestra:

    - cuadrillas;
    - jornadas diarias;
    - salidas propuestas;
    - sitios;
    - prioridades activas;
    - retiro de prioridad;
    - retiro de sitios de una propuesta automática;
    - permiso;
    - estado real de Operaciones;
    - asignación;
    - ejecución;
    - revisión;
    - finalizados.

    El flujo real continúa perteneciendo a Operaciones.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "planificacion",
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    # ========================================================
    # QUERY OPTIMIZADO DE SITIOS DE LAS SALIDAS
    # ========================================================

    sitios_salida_queryset = (
        SitioSalidaPlanificacionDiaria.objects.exclude(
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
            "sitio_batch__sitio_planificado__sitio",
            "sitio_batch__prioridad_diaria",
            ("sitio_batch__" "prioridad_diaria__" "cuadrilla_obligatoria"),
        )
        .order_by(
            "orden",
            "id",
        )
    )

    # ========================================================
    # SALIDAS
    # ========================================================

    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
        )
        .select_related(
            "disponibilidad_cuadrilla",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa"),
        )
        .prefetch_related(
            Prefetch(
                "sitios",
                queryset=sitios_salida_queryset,
            ),
        )
        .order_by(
            "fecha",
            ("disponibilidad_cuadrilla__" "cuadrilla_operativa__orden"),
            "orden",
            "id",
        )
    )
    # ========================================================
    # CONTACTOS ACTUALES DE LOS SITIOS DEL BATCH
    # ========================================================
    #
    # Esta consulta se ejecuta en CADA carga de la pantalla.
    #
    # No utilizamos caché ni snapshots.
    #
    # Solo consultamos ContactoSitio de los SitioMovil que
    # realmente pertenecen al batch actual.
    # ========================================================

    contactos_actuales = list(
        ContactoSitio.objects.filter(
            sitio__planificaciones__participaciones_batch__batch=batch,
            activo=True,
            sitio__isnull=False,
        )
        .select_related(
            "sitio",
        )
        .order_by(
            "sitio_id",
            "prioridad_contacto",
            "id",
        )
        .distinct()
    )

    contactos_por_sitio = defaultdict(
        list,
    )

    for contacto in contactos_actuales:

        contactos_por_sitio[contacto.sitio_id].append(
            contacto,
        )
    # ========================================================
    # CONSTRUIR SALIDAS PARA LA INTERFAZ
    # ========================================================

    salidas_ui = []

    for salida in salidas:

        filas = []

        for sitio_salida in salida.sitios.all():

            filas.append(
                _construir_fila_sitio(
                    sitio_salida,
                    contactos_por_sitio=contactos_por_sitio,
                )
            )

        cantidad_finalizados = sum(1 for fila in filas if fila["finalizado"])

        cantidad_asignables = sum(1 for fila in filas if fila["puede_asignar"])

        cantidad_sin_servicio = sum(1 for fila in filas if fila["servicio"] is None)

        cantidad_prioridades = sum(1 for fila in filas if fila["prioridad_activa"])

        cantidad_anclas = sum(
            1
            for fila in filas
            if (fila["prioridad_activa"] and fila["prioridad_es_ancla"])
        )

        cantidad_quitables = sum(
            1 for fila in filas if fila["puede_quitar_planificacion"]
        )

        salidas_ui.append(
            {
                "salida": salida,
                "fecha": salida.fecha,
                "cuadrilla_codigo": (salida.cuadrilla_codigo),
                "cuadrilla_nombre": (salida.cuadrilla_nombre),
                "disponibilidad": (salida.disponibilidad_cuadrilla),
                "sitios": filas,
                "cantidad_sitios": len(filas),
                "cantidad_finalizados": (cantidad_finalizados),
                "cantidad_asignables": (cantidad_asignables),
                "cantidad_sin_servicio": (cantidad_sin_servicio),
                "cantidad_prioridades": (cantidad_prioridades),
                "cantidad_anclas": (cantidad_anclas),
                "cantidad_quitables": (cantidad_quitables),
                "tiene_prioridad": (cantidad_prioridades > 0),
                "tiene_ancla": (cantidad_anclas > 0),
                "todos_finalizados": (
                    bool(filas) and cantidad_finalizados == len(filas)
                ),
            }
        )

    # ========================================================
    # AGRUPAR POR FECHA
    # ========================================================

    dias_mapa = defaultdict(list)

    for salida_ui in salidas_ui:

        dias_mapa[salida_ui["fecha"]].append(salida_ui)

    dias = [
        {
            "fecha": fecha,
            "salidas": salidas_dia,
        }
        for fecha, salidas_dia in sorted(
            dias_mapa.items(),
            key=lambda elemento: elemento[0],
        )
    ]

    # ========================================================
    # SITIOS APROBADOS TODAVÍA SIN PROGRAMAR
    # ========================================================

    pendientes_planificar = list(
        obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )
    )

    # ========================================================
    # PREPARAR PRIORIDAD EN LOS PENDIENTES
    # ========================================================
    #
    # detalle.html utiliza:
    #
    #     item.es_prioridad
    #     item.prioridad_id
    #
    # Como estos elementos son SitioBatchSemanal y no pasan
    # por _construir_fila_sitio(), agregamos estos atributos
    # exclusivamente para la interfaz.
    # ========================================================

    for item in pendientes_planificar:

        prioridad_diaria = _obtener_prioridad_diaria(
            item,
        )

        prioridad_activa = bool(
            prioridad_diaria and prioridad_diaria.estado == "activa"
        )

        item.es_prioridad = prioridad_activa

        item.prioridad_id = (
            prioridad_diaria.pk
            if prioridad_activa
            else None
        )

        item.prioridad_diaria_ui = (
            prioridad_diaria
            if prioridad_activa
            else None
        )

        # ====================================================
        # CONTACTOS / ALERTAS DEL PENDIENTE
        # ====================================================

        sitio = item.sitio_planificado.sitio

        info_contactos = _construir_info_contactos_ui(
            sitio,
            contactos_por_sitio.get(
                sitio.pk,
                [],
            ),
        )

        item.observaciones_contacto_ui = (
            info_contactos["observaciones"]
        )

        item.acciones_contacto_ui = (
            info_contactos["acciones"]
        )

        item.tiene_observaciones_contacto_ui = (
            info_contactos["tiene_observaciones"]
        )

        item.tiene_acciones_contacto_ui = (
            info_contactos["tiene_acciones"]
        )

        item.tiene_alertas_contacto_ui = (
            info_contactos["tiene_alertas"]
        )

    # ========================================================
    # RESUMEN
    # ========================================================

    resumen = obtener_resumen_planificacion_diaria(
        batch,
    )

    # ========================================================
    # CONTADORES OPERACIONALES REALES
    # ========================================================

    total_operativo = 0

    pendientes_asignar = 0

    asignados = 0

    en_ejecucion = 0

    revision = 0

    finalizados = 0

    sin_servicio = 0

    prioridades_activas = 0

    sitios_ancla = 0

    sitios_quitables = 0

    for salida_ui in salidas_ui:

        for fila in salida_ui["sitios"]:

            total_operativo += 1

            # ================================================
            # PRIORIDAD
            # ================================================

            if fila["prioridad_activa"]:

                prioridades_activas += 1

                if fila["prioridad_es_ancla"]:

                    sitios_ancla += 1

            # ================================================
            # RETIRO DE PLANIFICACIÓN
            # ================================================

            if fila["puede_quitar_planificacion"]:

                sitios_quitables += 1

            # ================================================
            # OPERACIONES
            # ================================================

            grupo = fila["grupo_operativo"]

            if fila["servicio"] is None:

                sin_servicio += 1

            elif fila["puede_asignar"]:

                pendientes_asignar += 1

            elif fila["finalizado"]:

                finalizados += 1

            elif grupo == "revision":

                revision += 1

            elif fila["estado_operativo_codigo"] == "asignado":

                asignados += 1

            elif grupo == "activo":

                en_ejecucion += 1

    # ========================================================
    # PRIORIDADES PENDIENTES
    # ========================================================

    prioridades_pendientes = sum(
        1
        for item in pendientes_planificar
        if getattr(
            item,
            "es_prioridad",
            False,
        )
    )

    resumen_operaciones = {
        "total": total_operativo,
        "pendientes_asignar": (pendientes_asignar),
        "asignados": asignados,
        "en_ejecucion": (en_ejecucion),
        "revision": revision,
        "finalizados": finalizados,
        "sin_servicio": sin_servicio,
        "prioridades_activas": (prioridades_activas),
        "prioridades_pendientes": (prioridades_pendientes),
        "sitios_ancla": (sitios_ancla),
        "sitios_quitables": (sitios_quitables),
    }

    # ========================================================
    # RESPUESTA
    # ========================================================

    return render(
        request,
        "planificacion/diaria/detalle.html",
        {
            "batch": batch,
            "mensual": batch.planificacion,
            "salidas": salidas_ui,
            "dias": dias,
            "resumen": resumen,
            "resumen_operaciones": (resumen_operaciones),
            "pendientes_planificar": (pendientes_planificar),
        },
    )


# ============================================================
# GENERAR / RECALCULAR PLAN DIARIO
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def generar_planificacion_diaria(
    request,
    batch_id,
):
    """
    Ejecuta el motor diario y guarda las salidas.

    REGLA DE FECHA ACTUAL
    ==========================================================

    Cuando el batch corresponde a la semana operacional
    vigente:

    - nunca se generan nuevas salidas en días anteriores a hoy;
    - el día actual solamente puede utilizarse si el usuario
      lo autorizó explícitamente;
    - si no lo autoriza, el motor comienza desde mañana.

    Las semanas futuras continúan funcionando normalmente.

    Las semanas históricas fuera de la semana actual no cambian
    su comportamiento por esta regla.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal.objects.select_related(
            "configuracion_semana",
        ),
        pk=batch_id,
    )

    # ========================================================
    # DECISIÓN SOBRE EL DÍA ACTUAL
    # ========================================================

    incluir_hoy = (
        request.POST.get(
            "incluir_hoy",
            "",
        )
        == "1"
    )

    # ========================================================
    # SEGURIDAD
    # ========================================================
    #
    # Solo tiene sentido permitir "incluir hoy" cuando hoy
    # realmente pertenece al rango operacional de este batch.
    #
    # El rango base utilizado actualmente es:
    #
    # fecha_inicio + 0..5 días
    #
    # es decir:
    #
    # lunes -> sábado
    # ========================================================

    hoy = timezone.localdate()

    fecha_fin_operacional = batch.fecha_inicio + timedelta(
        days=5,
    )

    batch_es_semana_actual = batch.fecha_inicio <= hoy <= fecha_fin_operacional

    if not batch_es_semana_actual:
        incluir_hoy = False

    # ========================================================
    # GENERAR
    # ========================================================

    try:

        resultado = guardar_plan_diario_batch(
            batch=batch,
            usuario=request.user,
            incluir_hoy=incluir_hoy,
        )

    except Exception as exc:

        messages.error(
            request,
            ("No fue posible generar la " f"planificación diaria: {exc}"),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    salidas_creadas = int(
        resultado.get(
            "salidas_creadas",
            0,
        )
        or 0
    )

    salidas_eliminadas = int(
        resultado.get(
            "salidas_eliminadas",
            0,
        )
        or 0
    )

    sitios_planificados = int(
        resultado.get(
            "sitios_planificados",
            0,
        )
        or 0
    )

    faltantes = int(
        resultado.get(
            "faltantes",
            0,
        )
        or 0
    )

    advertencias = resultado.get(
        "advertencias",
        [],
    )

    propuesta_anterior_conservada = bool(
        resultado.get(
            "propuesta_anterior_conservada",
            False,
        )
    )

    # ========================================================
    # INFORMAR DECISIÓN TEMPORAL
    # ========================================================

    if batch_es_semana_actual:

        if incluir_hoy:

            messages.info(
                request,
                (
                    f"El recálculo consideró el día de hoy "
                    f"{hoy:%d/%m/%Y} y los días operacionales "
                    "posteriores. Los días anteriores fueron "
                    "excluidos."
                ),
            )

        else:

            messages.info(
                request,
                (
                    f"El recálculo no utilizó el día de hoy "
                    f"{hoy:%d/%m/%Y}. Los nuevos sitios fueron "
                    "evaluados únicamente para días operacionales "
                    "posteriores."
                ),
            )

    # ========================================================
    # MENSAJE PRINCIPAL
    # ========================================================

    if salidas_creadas:

        texto = (
            f"Se generaron {salidas_creadas} "
            f"salida(s) con "
            f"{sitios_planificados} sitio(s) "
            "planificados."
        )

        if salidas_eliminadas:

            texto += (
                f" Se reemplazaron "
                f"{salidas_eliminadas} "
                "salida(s) editables anteriores."
            )

        messages.success(
            request,
            texto,
        )

    elif propuesta_anterior_conservada:

        messages.warning(
            request,
            (
                "El motor no encontró una nueva propuesta "
                "válida. La planificación anterior fue "
                "conservada sin cambios."
            ),
        )

    elif sitios_planificados:

        messages.success(
            request,
            (f"Se procesaron " f"{sitios_planificados} sitio(s)."),
        )

    else:

        messages.warning(
            request,
            ("El motor no encontró sitios disponibles " "para generar nuevas salidas."),
        )

    # ========================================================
    # FALTANTES
    # ========================================================

    if faltantes:

        messages.warning(
            request,
            (
                f"Quedaron {faltantes} sitio(s) "
                "sin poder incorporar a una salida "
                "operacional."
            ),
        )

    # ========================================================
    # ADVERTENCIAS
    # ========================================================

    for advertencia in advertencias:

        messages.warning(
            request,
            advertencia,
        )

    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch.pk,
    )


# ============================================================
# SINCRONIZAR ESTADOS DESDE OPERACIONES
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
@transaction.atomic
def sincronizar_planificacion_diaria(
    request,
    batch_id,
):
    """
    Sincroniza los estados persistidos en planificación
    utilizando el estado real de Operaciones.

    No modifica el flujo de Operaciones.
    """

    batch = get_object_or_404(
        BatchPlanificacionSemanal,
        pk=batch_id,
    )

    salidas = list(
        SalidaPlanificacionDiaria.objects.filter(
            batch=batch,
        )
        .exclude(
            estado="cancelada",
        )
        .order_by(
            "fecha",
            "orden",
            "id",
        )
    )

    if not salidas:

        messages.warning(
            request,
            ("Todavía no existen salidas " "diarias para sincronizar."),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    sincronizadas = 0

    for salida in salidas:

        sincronizar_estado_salida(
            salida=salida,
            usuario=request.user,
        )

        sincronizadas += 1

    messages.success(
        request,
        (
            f"Se sincronizaron {sincronizadas} "
            "salida(s) con el estado real "
            "de Operaciones."
        ),
    )

    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch.pk,
    )

# ============================================================
# QUITAR SITIO DE UNA JORNADA
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
@transaction.atomic
def quitar_sitio_planificacion_diaria(
    request,
    sitio_salida_id,
):
    """
    Retira un sitio de una jornada diaria.

    REGLA GENERAL
    ==========================================================

    Todo sitio de Planificación Diaria puede retirarse de
    su jornada.

    Esto aplica independientemente de si:

    - fue generado por el motor;
    - fue programado manualmente;
    - pertenece a una prioridad;
    - la salida está bloqueada;
    - la participación está bloqueada.

    IMPORTANTE
    ==========================================================

    Quitar de Planificación NO cancela Operaciones.

    Si todavía no existe un compromiso operacional:

        el sitio vuelve a quedar disponible para
        planificación.

    Si ya existe una operación comprometida:

        asignado
        en ejecución
        revisión
        finalizado

    solamente desaparece de esta jornada de planificación.
    Operaciones permanece intacto.
    """

    sitio_salida = get_object_or_404(
        SitioSalidaPlanificacionDiaria.objects.select_for_update().select_related(
            "salida",
            "salida__batch",
            "sitio_batch",
            "sitio_batch__sitio_planificado",
            "sitio_batch__sitio_planificado__sitio",
        ),
        pk=sitio_salida_id,
    )

    salida = sitio_salida.salida

    batch = salida.batch

    item_batch = sitio_salida.sitio_batch

    sitio_planificado = item_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    identificador = sitio.id_claro or sitio.id_sites or f"Sitio {sitio.pk}"

    # ========================================================
    # ESTADO OPERACIONAL REAL ANTES DEL RETIRO
    # ========================================================

    estado_operacional = obtener_estado_operacional_sitio(
        sitio_planificado,
    )

    estado_planificacion_operacional = estado_operacional.get(
        "estado_planificacion",
    )

    # ========================================================
    # ¿PUEDE VOLVER AL POOL DE PLANIFICACIÓN?
    # ========================================================
    #
    # Estos estados ya implican un compromiso real en
    # Operaciones.
    #
    # No debemos devolver el sitio a "listo_planificar"
    # porque podría terminar duplicado.
    # ========================================================

    existe_compromiso_operacional = estado_planificacion_operacional in {
        "asignado",
        "en_ejecucion",
        "revision",
        "finalizado",
    }

    # ========================================================
    # RETIRAR PARTICIPACIÓN
    # ========================================================

    sitio_salida.estado = "retirado"

    sitio_salida.motivo_reprogramacion = (
        "Retirado manualmente de la jornada " "desde Planificación Diaria."
    )

    sitio_salida.actualizado_por = request.user

    sitio_salida.save(
        update_fields=[
            "estado",
            "motivo_reprogramacion",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    # ========================================================
    # RESTAURAR AL POOL SI NO ESTÁ COMPROMETIDO
    # ========================================================

    if not existe_compromiso_operacional:

        _restaurar_sitio_planificado_si_corresponde(
            sitio_planificado=sitio_planificado,
            usuario=request.user,
        )

    # ========================================================
    # SITIOS QUE PERMANECEN EN LA SALIDA
    # ========================================================

    sitios_restantes = list(
        salida.sitios.exclude(
            estado__in=[
                "retirado",
                "cancelado",
                "reprogramado",
            ]
        )
        .select_related(
            "sitio_batch",
            "sitio_batch__sitio_planificado",
        )
        .order_by(
            "orden",
            "id",
        )
    )

    # ========================================================
    # REORDENAR
    # ========================================================

    for nuevo_orden, participacion in enumerate(
        sitios_restantes,
        start=1,
    ):

        if participacion.orden != nuevo_orden:

            participacion.orden = nuevo_orden

            participacion.actualizado_por = request.user

            participacion.save(
                update_fields=[
                    "orden",
                    "actualizado_por",
                    "actualizado_en",
                ]
            )

        sitio_planificado_restante = participacion.sitio_batch.sitio_planificado

        if sitio_planificado_restante.orden_dia != nuevo_orden:

            sitio_planificado_restante.orden_dia = nuevo_orden

            sitio_planificado_restante.actualizado_por = request.user

            sitio_planificado_restante.save(
                update_fields=[
                    "orden_dia",
                    "actualizado_por",
                    "actualizado_en",
                ]
            )

    # ========================================================
    # SALIDA VACÍA
    # ========================================================

    if not sitios_restantes:

        fecha_salida = salida.fecha

        salida.delete()

        if existe_compromiso_operacional:

            messages.success(
                request,
                (
                    f"{identificador} fue quitado de la jornada "
                    f"del {fecha_salida:%d/%m/%Y}. "
                    "Su estado real en Operaciones permanece "
                    "sin modificaciones."
                ),
            )

        else:

            messages.success(
                request,
                (
                    f"{identificador} fue quitado de la jornada "
                    f"del {fecha_salida:%d/%m/%Y}. "
                    "La salida quedó vacía y fue eliminada. "
                    "El sitio vuelve a estar disponible para "
                    "un próximo recálculo."
                ),
            )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # RECALCULAR TRABAJO
    # ========================================================

    disponibilidad = salida.disponibilidad_cuadrilla

    try:

        minutos_por_sitio = int(disponibilidad.minutos_trabajo_sitio_efectivos or 180)

    except (
        TypeError,
        ValueError,
    ):

        minutos_por_sitio = 180

    minutos_trabajo = len(sitios_restantes) * minutos_por_sitio

    # ========================================================
    # RECALCULAR RUTA COMPLETA
    # ========================================================
    #
    # Ya que quitamos un sitio, no debemos conservar el
    # viaje de la ruta anterior.
    # ========================================================

    motores = []

    for participacion in sitios_restantes:

        item_restante = participacion.sitio_batch

        motores.append(
            _sitio_batch_a_motor(
                item_restante,
            )
        )

    configuracion = construir_configuracion_cuadrilla(
        disponibilidad,
    )

    calculo_nuevo = encontrar_mejor_salida(
        sitios=motores,
        configuracion_cuadrilla=configuracion,
    )

    if calculo_nuevo:

        salida.minutos_viaje_estimados = int(
            calculo_nuevo.get(
                "minutos_viaje",
                0,
            )
            or 0
        )

        salida.minutos_trabajo_estimados = int(
            calculo_nuevo.get(
                "minutos_trabajo",
                minutos_trabajo,
            )
            or minutos_trabajo
        )

        salida.minutos_total_estimados = int(
            calculo_nuevo.get(
                "minutos_total",
                0,
            )
            or 0
        )

        salida.distancia_directa_km = calculo_nuevo.get(
            "distancia_directa_km",
        )

        salida.distancia_vial_estimada_km = calculo_nuevo.get(
            "distancia_vial_estimada_km",
        )

        salida.jornada_extendida = bool(
            calculo_nuevo.get(
                "jornada_extendida",
                False,
            )
        )

        salida.exceso_jornada_minutos = int(
            calculo_nuevo.get(
                "exceso_jornada_minutos",
                0,
            )
            or 0
        )

    else:

        salida.minutos_trabajo_estimados = minutos_trabajo

        salida.minutos_total_estimados = (
            int(salida.minutos_viaje_estimados or 0) + minutos_trabajo
        )

    salida.actualizado_por = request.user

    salida.save(
        update_fields=[
            "minutos_viaje_estimados",
            "minutos_trabajo_estimados",
            "minutos_total_estimados",
            "distancia_directa_km",
            "distancia_vial_estimada_km",
            "jornada_extendida",
            "exceso_jornada_minutos",
            "actualizado_por",
            "actualizado_en",
        ]
    )

    # ========================================================
    # MENSAJE
    # ========================================================

    if existe_compromiso_operacional:

        messages.success(
            request,
            (
                f"{identificador} fue quitado de la jornada "
                f"del {salida.fecha:%d/%m/%Y}. "
                "Su estado real en Operaciones permanece "
                "sin modificaciones."
            ),
        )

    else:

        messages.success(
            request,
            (
                f"{identificador} fue quitado de la jornada "
                f"del {salida.fecha:%d/%m/%Y}. "
                "Quedó nuevamente disponible para que un "
                "próximo recálculo pueda ubicarlo donde "
                "corresponda."
            ),
        )

    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch.pk,
    )


# ============================================================
# ASIGNAR SITIO DESDE PLANIFICACIÓN
# ============================================================


@require_GET
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
def asignar_sitio_desde_planificacion(
    request,
    sitio_salida_id,
):
    """
    Puente entre Planificación y el flujo real de Operaciones.

    Planificación NO asigna técnicos directamente.

    Valida:

    1. que exista la participación diaria;
    2. que el permiso esté aprobado;
    3. que exista ServicioCotizado;
    4. que ServicioCotizado esté exactamente en
       aprobado_pendiente;
    5. redirige al formulario existente de Operaciones.
    """

    sitio_salida = get_object_or_404(
        SitioSalidaPlanificacionDiaria.objects.select_related(
            "salida",
            "salida__batch",
            "sitio_batch",
            ("sitio_batch__" "sitio_planificado"),
            ("sitio_batch__" "sitio_planificado__" "sitio"),
        ),
        pk=sitio_salida_id,
    )

    salida = sitio_salida.salida

    batch = salida.batch

    sitio_planificado = sitio_salida.sitio_batch.sitio_planificado

    sitio = sitio_planificado.sitio

    identificador = sitio.id_claro or sitio.id_sites or "Sitio"

    # ========================================================
    # VALIDAR ESTADO DE LA PARTICIPACIÓN DIARIA
    # ========================================================

    if sitio_salida.estado not in {
        "planificado",
        "listo_asignar",
    }:

        messages.error(
            request,
            (
                f"{identificador}: "
                "el sitio ya no se encuentra en un "
                "estado disponible para iniciar una "
                "nueva asignación."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # VALIDAR PERMISO
    # ========================================================

    if sitio_planificado.estado_permiso not in {
        "aprobado",
        "no_requiere",
    }:

        messages.error(
            request,
            (
                f"{identificador}: "
                "el sitio no posee permiso aprobado "
                "para ser asignado."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # OBTENER ESTADO OPERACIONAL REAL
    # ========================================================

    estado_operacional = obtener_estado_operacional_sitio(
        sitio_planificado,
    )

    servicio = estado_operacional["servicio"]

    # ========================================================
    # DEBE EXISTIR SERVICIO OPERACIONAL
    # ========================================================

    if servicio is None:

        messages.error(
            request,
            (
                f"{identificador}: "
                "no existe un ServicioCotizado "
                "asociado en Operaciones."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # VALIDACIÓN CRÍTICA
    # ========================================================
    #
    # Solamente se puede abrir la asignación cuando:
    #
    # ServicioCotizado.estado == aprobado_pendiente
    #
    # ========================================================

    if servicio.estado != "aprobado_pendiente":

        try:

            estado_actual = servicio.get_estado_display()

        except Exception:

            estado_actual = servicio.estado

        messages.error(
            request,
            (
                f"DU{str(servicio.du).zfill(8)} "
                f"({identificador}) "
                "no se encuentra pendiente por asignar "
                "en Operaciones. "
                f"Estado actual: {estado_actual}."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # URL DE RETORNO
    # ========================================================

    next_url = _url_detalle_diario(
        batch,
    )

    # ========================================================
    # UTILIZAR ASIGNACIÓN EXISTENTE DE OPERACIONES
    # ========================================================

    url_asignacion = reverse(
        "operaciones:asignar_cotizacion",
        kwargs={
            "pk": servicio.pk,
        },
    )

    return redirect(f"{url_asignacion}" f"?next={next_url}" f"&modo=reasignar")


# ============================================================
# TRASLADAR PENDIENTES AL MES SIGUIENTE
# ============================================================


@require_POST
@rol_requerido(*ROLES_PLANIFICACION_DIARIA)
@transaction.atomic
def trasladar_pendientes_mes_siguiente_planificacion_diaria(
    request,
    batch_id,
):
    """
    Traslada al mes siguiente los sitios aprobados que
    actualmente están pendientes de programación diaria.

    REGLA FUNDAMENTAL
    ==========================================================

    El sitio trasladado NO conserva posición, cluster,
    salida, fecha ni memoria operacional del mes anterior.

    En el mes siguiente entra nuevamente al universo mensual
    como un sitio disponible común.

    SE CONSERVA
    ----------------------------------------------------------

    - SitioMovil maestro;
    - permiso aprobado / no requiere;
    - información territorial del sitio;
    - información de contacto aplicable;
    - prioridad mensual propia del SitioPlanificado si existe.

    NO SE CONSERVA
    ----------------------------------------------------------

    - fecha_planificada;
    - orden_dia;
    - salida diaria;
    - cluster semanal;
    - ubicación anterior dentro del batch;
    - bloqueo del motor;
    - programación manual anterior.

    Además se retira el SitioBatchSemanal del batch actual
    para que el sitio no siga apareciendo como pendiente en
    la semana de origen.
    """

    batch = (
        BatchPlanificacionSemanal.objects.select_for_update()
        .select_related(
            "planificacion",
        )
        .get(
            pk=batch_id,
        )
    )

    planificacion_origen = batch.planificacion

    planificacion_destino = obtener_planificacion_mes_siguiente(
        planificacion_origen,
    )

    # ========================================================
    # DEBE EXISTIR MES SIGUIENTE
    # ========================================================

    if planificacion_destino is None:

        messages.error(
            request,
            (
                "No existe todavía una planificación mensual "
                "creada para el mes siguiente. Créala antes "
                "de trasladar los sitios pendientes."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

        # ========================================================
    # SITIOS SELECCIONADOS POR EL USUARIO
    # ========================================================

    ids_seleccionados_raw = request.POST.getlist(
        "sitios_trasladar",
    )

    ids_seleccionados = []

    for valor in ids_seleccionados_raw:

        try:

            ids_seleccionados.append(
                int(
                    valor,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

    # ========================================================
    # DEBE EXISTIR AL MENOS UNA SELECCIÓN
    # ========================================================

    if not ids_seleccionados:

        messages.warning(
            request,
            ("No seleccionaste ningún sitio " "para trasladar al mes siguiente."),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    # ========================================================
    # PENDIENTES REALES ACTUALES
    # ========================================================
    #
    # Primero obtenemos el universo REAL de pendientes.
    #
    # Después lo limitamos exclusivamente a los IDs enviados
    # por el formulario.
    #
    # De esta forma un usuario jamás puede trasladar mediante
    # POST un sitio que no sea realmente pendiente del batch.
    # ========================================================

    pendientes_reales = list(
        obtener_sitios_pendientes_planificacion_diaria(
            batch,
        )
    )

    ids_pendientes_reales = {item.pk for item in pendientes_reales}

    ids_validos = {
        sitio_batch_id
        for sitio_batch_id in ids_seleccionados
        if sitio_batch_id in ids_pendientes_reales
    }

    # ========================================================
    # CONSTRUIR EXCLUSIVAMENTE LA SELECCIÓN VÁLIDA
    # ========================================================

    pendientes = [item for item in pendientes_reales if item.pk in ids_validos]

    # ========================================================
    # NINGUNO DE LOS SELECCIONADOS ES TRASLADABLE
    # ========================================================

    if not pendientes:

        messages.warning(
            request,
            (
                "Ninguno de los sitios seleccionados "
                "se encuentra actualmente disponible "
                "para ser trasladado."
            ),
        )

        return redirect(
            "planificacion:detalle_planificacion_diaria",
            batch_id=batch.pk,
        )

    trasladados = []

    omitidos = []

    # ========================================================
    # PROCESAR
    # ========================================================

    for item_batch in pendientes:

        item_batch = (
            type(item_batch)
            .objects.select_for_update()
            .select_related(
                "sitio_planificado",
                "sitio_planificado__sitio",
            )
            .get(
                pk=item_batch.pk,
            )
        )

        sitio_planificado = item_batch.sitio_planificado

        identificador = _identificador_sitio_planificacion(
            sitio_planificado,
        )

        # ====================================================
        # SEGURIDAD: NO DEBE TENER PARTICIPACIÓN ACTIVA
        # ====================================================

        tiene_salida_activa = (
            SitioSalidaPlanificacionDiaria.objects.filter(
                sitio_batch=item_batch,
            )
            .exclude(
                estado__in=[
                    "retirado",
                    "cancelado",
                    "reprogramado",
                ]
            )
            .exists()
        )

        if tiene_salida_activa:

            omitidos.append(
                (f"{identificador}: ya posee una " "participación diaria activa.")
            )

            continue

        # ====================================================
        # TRASLADAR SITIO PLANIFICADO
        # ====================================================

        try:

            resultado = trasladar_sitio_mes_siguiente(
                sitio_planificado=sitio_planificado,
                usuario=request.user,
            )

        except ValidationError as exc:

            mensaje = "; ".join(exc.messages)

            omitidos.append(f"{identificador}: {mensaje}")

            continue

        # ====================================================
        # RETIRAR DEL BATCH DE ORIGEN
        # ====================================================
        #
        # Este registro representa pertenencia a W35.
        #
        # Una vez trasladado a septiembre ya no debe
        # participar en ningún nuevo cálculo de agosto.
        # ====================================================

        item_batch.delete()

        trasladados.append(
            {
                "identificador": identificador,
                "resultado": resultado,
            }
        )

    # ========================================================
    # RESULTADO
    # ========================================================

    if trasladados:

        nombre_destino = (
            f"{planificacion_destino.mes:02d}/" f"{planificacion_destino.anio}"
        )

        messages.success(
            request,
            (
                f"Se trasladaron {len(trasladados)} "
                f"sitio(s) al mes {nombre_destino}. "
                "Los sitios fueron retirados del batch actual "
                "y quedarán disponibles para mezclarse con el "
                "universo normal del mes siguiente."
            ),
        )

    if omitidos:

        for motivo in omitidos:

            messages.warning(
                request,
                motivo,
            )

    if not trasladados:

        messages.warning(
            request,
            ("No fue posible trasladar ninguno " "de los sitios pendientes."),
        )

    return redirect(
        "planificacion:detalle_planificacion_diaria",
        batch_id=batch.pk,
    )

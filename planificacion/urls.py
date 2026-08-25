from django.urls import path

from planificacion.views import (actualizar_permiso_inline,
                                 cambiar_estado_cuadrilla, crear_cuadrilla,
                                 crear_planificacion_mensual,
                                 descargar_formato_contactos,
                                 editar_contacto_inline, editar_cuadrilla,
                                 editar_planificacion_mensual,
                                 eliminar_planificacion_mensual,
                                 importar_asignacion_mensual,
                                 importar_contactos, lista_asignacion_mensual,
                                 listar_contactos, listar_cuadrillas,
                                 planificacion_semanal,
                                 planificaciones_mensuales,
                                 regeocodificar_cuadrilla)
from planificacion.views.analisis_batch_semanal import (
    analizar_batch_semanal_view, aplicar_propuesta_batch_view)
from planificacion.views.asignacion_operativa import (
    confirmar_asignacion_dia, confirmar_asignacion_salida,
    preview_asignacion_dia, preview_asignacion_salida)
from planificacion.views.completar_semana_anterior import (
    analizar_completar_semana, completar_semana_anterior,
    confirmar_completar_semanas)
from planificacion.views.mapa_planificacion import mapa_batch_semanal
from planificacion.views.mapa_planificacion_diaria import (
    mapa_dia_planificacion_diaria, mapa_pendientes_planificacion_diaria,
    mapa_salida_planificacion_diaria)
from planificacion.views.mover_semana import (confirmar_mover_semana,
                                              seleccionar_semana_destino)
from planificacion.views.planificacion_diaria import (
    asignar_sitio_desde_planificacion, detalle_planificacion_diaria,
    generar_planificacion_diaria, lista_planificacion_diaria,
    quitar_sitio_planificacion_diaria, sincronizar_planificacion_diaria,
    trasladar_pendientes_mes_siguiente_planificacion_diaria)
from planificacion.views.planificacion_diaria_manual import \
    programar_sitio_manual_planificacion_diaria
from planificacion.views.planificacion_semanal import (
    actualizar_permiso_sitio_batch, agregar_sitios_batch,
    cerrar_propuesta_semanal, confirmar_sitios_batch,
    crear_planificacion_semanal, descargar_excel_batch,
    detalle_planificacion_semanal, eliminar_planificacion_semanal,
    enviar_gestion_permisos_batch, lista_planificacion_semanal,
    mapa_batches_mensuales, quitar_sitio_batch)
from planificacion.views.prioridades_diarias import (
    cancelar_prioridad_diaria, crear_prioridad_diaria, editar_prioridad_diaria,
    quitar_prioridad_planificacion_diaria, reactivar_prioridad_diaria)

app_name = "planificacion"


urlpatterns = [
    # ========================================================
    # CONTACTOS
    # ========================================================
    path(
        "contactos/",
        listar_contactos,
        name="listar_contactos",
    ),
    path(
        "contactos/importar/",
        importar_contactos,
        name="importar_contactos",
    ),
    path(
        "contactos/formato/",
        descargar_formato_contactos,
        name="descargar_formato_contactos",
    ),
    path(
        "contactos/<int:pk>/editar-inline/",
        editar_contacto_inline,
        name="editar_contacto_inline",
    ),
    # ========================================================
    # PLANIFICACIONES MENSUALES
    # ========================================================
    path(
        "mensual/",
        planificaciones_mensuales,
        name="planificaciones_mensuales",
    ),
    path(
        "mensual/crear/",
        crear_planificacion_mensual,
        name="crear_planificacion_mensual",
    ),
    # ========================================================
    # ASIGNACIÓN MENSUAL
    # ========================================================
    path(
        "mensual/<int:pk>/",
        lista_asignacion_mensual,
        name="lista_asignacion_mensual",
    ),
    path(
        "mensual/<int:pk>/importar/",
        importar_asignacion_mensual,
        name="importar_asignacion_mensual",
    ),
    path(
        "mensual/sitio/<int:pk>/permiso/",
        actualizar_permiso_inline,
        name="actualizar_permiso_inline",
    ),
    path(
        "mensual/<int:pk>/editar/",
        editar_planificacion_mensual,
        name="editar_planificacion_mensual",
    ),
    path(
        "mensual/<int:pk>/eliminar/",
        eliminar_planificacion_mensual,
        name="eliminar_planificacion_mensual",
    ),
    # ========================================================
    # PLANIFICACIÓN SEMANAL
    # ========================================================
    path(
        "mensual/<int:mensual_id>/semanal/",
        lista_planificacion_semanal,
        name="lista_planificacion_semanal",
    ),
    path(
        "mensual/<int:mensual_id>/semanal/crear/",
        crear_planificacion_semanal,
        name="crear_planificacion_semanal",
    ),
    path(
        "semanal/<int:batch_id>/",
        detalle_planificacion_semanal,
        name="detalle_planificacion_semanal",
    ),
    # ========================================================
    # SITIOS DEL BATCH SEMANAL
    # ========================================================
    path(
        "semanal/<int:batch_id>/agregar-sitios/",
        agregar_sitios_batch,
        name="agregar_sitios_batch",
    ),
    path(
        "semanal/sitio/<int:item_id>/quitar/",
        quitar_sitio_batch,
        name="quitar_sitio_batch",
    ),
    path(
        "semanal/sitio/<int:item_id>/permiso/",
        actualizar_permiso_sitio_batch,
        name="actualizar_permiso_sitio_batch",
    ),
    # ========================================================
    # FLUJO DEL BATCH SEMANAL
    # ========================================================
    path(
        "semanal/<int:batch_id>/cerrar-propuesta/",
        cerrar_propuesta_semanal,
        name="cerrar_propuesta_semanal",
    ),
    path(
        "semanal/<int:batch_id>/enviar-permisos/",
        enviar_gestion_permisos_batch,
        name="enviar_gestion_permisos_batch",
    ),
    path(
        "semanal/<int:batch_id>/confirmar/",
        confirmar_sitios_batch,
        name="confirmar_sitios_batch",
    ),
    path(
        "semanal/<int:batch_id>/analizar/",
        analizar_batch_semanal_view,
        name="analizar_batch_semanal",
    ),
    path(
        "semanal/<int:batch_id>/propuesta/<int:posicion>/aplicar/",
        aplicar_propuesta_batch_view,
        name="aplicar_propuesta_batch",
    ),
    path(
        "semanal/<int:batch_id>/mapa/",
        mapa_batch_semanal,
        name="mapa_batch_semanal",
    ),
    path(
        "semanal/<int:batch_id>/eliminar/",
        eliminar_planificacion_semanal,
        name="eliminar_planificacion_semanal",
    ),
    path(
        "semanal/<int:batch_id>/excel/",
        descargar_excel_batch,
        name="descargar_excel_batch",
    ),
    path(
        "mensual/<int:mensual_id>/mapa-batches/",
        mapa_batches_mensuales,
        name="mapa_batches_mensuales",
    ),
    # ========================================================
    # PLANIFICACIÓN DIARIA
    # ========================================================
    path(
        "diaria/",
        lista_planificacion_diaria,
        name="planificacion_diaria",
    ),
    path(
        "semanal/<int:batch_id>/diaria/",
        detalle_planificacion_diaria,
        name="detalle_planificacion_diaria",
    ),
    # ========================================================
    # MOTOR DIARIO
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/generar/",
        generar_planificacion_diaria,
        name="generar_planificacion_diaria",
    ),
    # ========================================================
    # SINCRONIZACIÓN CON OPERACIONES
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/sincronizar/",
        sincronizar_planificacion_diaria,
        name="sincronizar_planificacion_diaria",
    ),
    # ========================================================
    # ASIGNACIÓN INDIVIDUAL EXISTENTE
    # ========================================================
    path(
        "diaria/sitio/<int:sitio_salida_id>/asignar/",
        asignar_sitio_desde_planificacion,
        name="asignar_sitio_desde_planificacion",
    ),
    # ========================================================
    # ASIGNACIÓN MASIVA DE UNA CUADRILLA / SALIDA
    # ========================================================
    path(
        "diaria/salida/<int:salida_id>/asignar/",
        preview_asignacion_salida,
        name="preview_asignacion_salida",
    ),
    path(
        "diaria/salida/<int:salida_id>/asignar/confirmar/",
        confirmar_asignacion_salida,
        name="confirmar_asignacion_salida",
    ),
    # ========================================================
    # ASIGNACIÓN MASIVA DE TODO EL DÍA
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/<str:fecha>/asignar/",
        preview_asignacion_dia,
        name="preview_asignacion_dia",
    ),
    path(
        "semanal/<int:batch_id>/diaria/<str:fecha>/asignar/confirmar/",
        confirmar_asignacion_dia,
        name="confirmar_asignacion_dia",
    ),
    # ========================================================
    # PROGRAMACIÓN MANUAL
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/sitio/<int:sitio_batch_id>/programar/",
        programar_sitio_manual_planificacion_diaria,
        name="programar_sitio_manual_planificacion_diaria",
    ),
    # ========================================================
    # QUITAR SITIO DE UNA JORNADA
    # ========================================================
    path(
        "diaria/sitio/<int:sitio_salida_id>/quitar/",
        quitar_sitio_planificacion_diaria,
        name="quitar_sitio_planificacion_diaria",
    ),
    # ========================================================
    # MOVER SITIO A OTRA SEMANA
    # ========================================================
    path(
        "diaria/sitio-batch/<int:sitio_batch_id>/mover-semana/",
        seleccionar_semana_destino,
        name="seleccionar_semana_destino",
    ),
    path(
        "diaria/sitio-batch/<int:sitio_batch_id>/mover-semana/confirmar/",
        confirmar_mover_semana,
        name="confirmar_mover_semana",
    ),
    # ========================================================
    # TRASLADAR PENDIENTES AL MES SIGUIENTE
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/pendientes/trasladar/",
        trasladar_pendientes_mes_siguiente_planificacion_diaria,
        name="trasladar_pendientes_mes_siguiente_planificacion_diaria",
    ),
    # ========================================================
    # MAPAS PLANIFICACIÓN DIARIA
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/mapa/<str:fecha>/",
        mapa_dia_planificacion_diaria,
        name="mapa_dia_planificacion_diaria",
    ),
    path(
        "semanal/<int:batch_id>/diaria/pendientes/mapa/",
        mapa_pendientes_planificacion_diaria,
        name="mapa_pendientes_planificacion_diaria",
    ),
    path(
        "diaria/salida/<int:salida_id>/mapa/",
        mapa_salida_planificacion_diaria,
        name="mapa_salida_planificacion_diaria",
    ),
    # ========================================================
    # PRIORIDADES DE PLANIFICACIÓN DIARIA
    # ========================================================
    path(
        "semanal/<int:batch_id>/diaria/prioridad/crear/",
        crear_prioridad_diaria,
        name="crear_prioridad_diaria",
    ),
    path(
        "diaria/prioridad/<int:prioridad_id>/editar/",
        editar_prioridad_diaria,
        name="editar_prioridad_diaria",
    ),
    path(
        "diaria/prioridad/<int:prioridad_id>/cancelar/",
        cancelar_prioridad_diaria,
        name="cancelar_prioridad_diaria",
    ),
    path(
        "diaria/prioridad/<int:prioridad_id>/reactivar/",
        reactivar_prioridad_diaria,
        name="reactivar_prioridad_diaria",
    ),
    path(
        "diaria/prioridad/<int:prioridad_id>/quitar/",
        quitar_prioridad_planificacion_diaria,
        name="quitar_prioridad_planificacion_diaria",
    ),
    # ========================================================
    # CUADRILLAS
    # ========================================================
    path(
        "cuadrillas/",
        listar_cuadrillas,
        name="listar_cuadrillas",
    ),
    path(
        "cuadrillas/crear/",
        crear_cuadrilla,
        name="crear_cuadrilla",
    ),
    path(
        "cuadrillas/<int:pk>/editar/",
        editar_cuadrilla,
        name="editar_cuadrilla",
    ),
    path(
        "cuadrillas/<int:pk>/estado/",
        cambiar_estado_cuadrilla,
        name="cambiar_estado_cuadrilla",
    ),
    path(
        "cuadrillas/<int:pk>/regeocodificar/",
        regeocodificar_cuadrilla,
        name="regeocodificar_cuadrilla",
    ),
    # ========================================================
    # COMPLETAR SEMANA ANTERIOR
    # ========================================================
    path(
        "mensual/<int:mensual_id>/completar-semana/",
        completar_semana_anterior,
        name="completar_semana_anterior",
    ),
    path(
        "mensual/<int:mensual_id>/completar-semana/analizar/",
        analizar_completar_semana,
        name="analizar_completar_semana",
    ),
    path(
        "mensual/<int:mensual_id>/completar-semana/confirmar/",
        confirmar_completar_semanas,
        name="confirmar_completar_semanas",
    ),
    # ========================================================
    # ESTADO MASIVO
    # ========================================================
    path(
        "semanal/<int:batch_id>/estado-masivo/",
        planificacion_semanal.actualizar_estado_masivo_sitios_batch,
        name="actualizar_estado_masivo_sitios_batch",
    ),
]

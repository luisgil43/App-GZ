from .asignacion_mensual import (importar_asignacion_mensual,
                                 lista_asignacion_mensual)
from .completar_semana_anterior import (analizar_completar_semana,
                                        completar_semana_anterior,
                                        confirmar_completar_semana)
from .contactos import listar_contactos
from .cuadrillas import (cambiar_estado_cuadrilla, crear_cuadrilla,
                         editar_cuadrilla, listar_cuadrillas,
                         regeocodificar_cuadrilla)
from .edicion_contactos import editar_contacto_inline
from .formato_contactos import descargar_formato_contactos
from .importacion_contactos import importar_contactos
from .permisos_planificacion import actualizar_permiso_inline
from .planificacion_diaria import (asignar_sitio_desde_planificacion,
                                   detalle_planificacion_diaria,
                                   generar_planificacion_diaria,
                                   quitar_sitio_planificacion_diaria,
                                   sincronizar_planificacion_diaria)
from .planificacion_mensual import (crear_planificacion_mensual,
                                    editar_planificacion_mensual,
                                    eliminar_planificacion_mensual,
                                    planificaciones_mensuales)

__all__ = [
    # ========================================================
    # CONTACTOS
    # ========================================================
    "listar_contactos",
    "importar_contactos",
    "descargar_formato_contactos",
    "editar_contacto_inline",
    # ========================================================
    # PLANIFICACIÓN MENSUAL
    # ========================================================
    "planificaciones_mensuales",
    "crear_planificacion_mensual",
    "editar_planificacion_mensual",
    "eliminar_planificacion_mensual",
    # ========================================================
    # ASIGNACIÓN MENSUAL
    # ========================================================
    "lista_asignacion_mensual",
    "importar_asignacion_mensual",
    "actualizar_permiso_inline",
    # ========================================================
    # COMPLETAR SEMANA OPERACIONAL ANTERIOR
    # ========================================================
    "completar_semana_anterior",
    "analizar_completar_semana",
    "confirmar_completar_semana",
    # ========================================================
    # CUADRILLAS
    # ========================================================
    "listar_cuadrillas",
    "crear_cuadrilla",
    "editar_cuadrilla",
    "cambiar_estado_cuadrilla",
    "regeocodificar_cuadrilla",
    # ========================================================
    # PLANIFICACIÓN DIARIA
    # ========================================================
    "detalle_planificacion_diaria",
    "generar_planificacion_diaria",
    "sincronizar_planificacion_diaria",
    "asignar_sitio_desde_planificacion",
    "quitar_sitio_planificacion_diaria",
]

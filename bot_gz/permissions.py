# bot_gz/permissions.py

from __future__ import annotations

# Intents permitidos para usuarios técnicos / usuario normal
TECNICO_ALLOWED_INTENTS = {
    "mis_liquidaciones",
    "mi_contrato_vigente",
    "mi_produccion_hasta_hoy",
    "mis_proyectos_pendientes",
    "mis_proyectos_rechazados",
    "mis_rendiciones_pendientes",
    "ayuda_rendicion_gastos",
    "direccion_basura",
    "mi_asignacion",
    "info_sitio_id_claro",
    "cronograma_produccion_corte",
}


# Por ahora no agregamos permisos extra hasta tener handlers administrativos seguros.
ADMIN_EXTRA_INTENTS = set()
RRHH_EXTRA_INTENTS = set()
FINANZAS_EXTRA_INTENTS = set()
PREVENCION_EXTRA_INTENTS = set()


ADMIN_ALLOWED_INTENTS = TECNICO_ALLOWED_INTENTS | ADMIN_EXTRA_INTENTS
RRHH_ALLOWED_INTENTS = TECNICO_ALLOWED_INTENTS | RRHH_EXTRA_INTENTS
FINANZAS_ALLOWED_INTENTS = TECNICO_ALLOWED_INTENTS | FINANZAS_EXTRA_INTENTS
PREVENCION_ALLOWED_INTENTS = TECNICO_ALLOWED_INTENTS | PREVENCION_EXTRA_INTENTS


def _has_attr_true(user, attr: str) -> bool:
    return bool(getattr(user, attr, False))


def get_bot_allowed_intent_slugs(user) -> set[str]:
    """
    Devuelve los intents que este usuario puede usar dentro del bot.

    Regla principal:
    - El técnico / usuario normal solo puede acceder a información propia.
    - Admin / RRHH / Finanzas / Prevención pueden tener más herramientas,
      pero las consultas reales siempre deben filtrar permisos en Django.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return set()

    if getattr(user, "is_superuser", False) or _has_attr_true(user, "es_admin_general"):
        return set(ADMIN_ALLOWED_INTENTS)

    if _has_attr_true(user, "es_rrhh"):
        return set(RRHH_ALLOWED_INTENTS)

    if _has_attr_true(user, "es_facturacion"):
        return set(FINANZAS_ALLOWED_INTENTS)

    if _has_attr_true(user, "es_prevencion"):
        return set(PREVENCION_ALLOWED_INTENTS)

    # Por defecto: técnico / usuario normal
    return set(TECNICO_ALLOWED_INTENTS)


def user_can_use_bot_intent(user, intent_slug: str) -> bool:
    if not intent_slug:
        return False
    return intent_slug in get_bot_allowed_intent_slugs(user)


def build_ai_capabilities_text(user) -> str:
    """
    Texto corto para pasarle a la IA.
    La IA solo verá capacidades permitidas, no todo el sistema.
    """
    allowed = sorted(get_bot_allowed_intent_slugs(user))

    descriptions = {
        "mis_liquidaciones": "Consultar liquidaciones propias del usuario.",
        "mi_contrato_vigente": "Consultar contrato/anexos propios del usuario.",
        "mi_produccion_hasta_hoy": "Consultar producción propia.",
        "mis_proyectos_pendientes": (
            "Consultar proyectos o servicios propios pendientes en general, no necesariamente "
            "la asignación específica del día."
        ),
        "mis_proyectos_rechazados": "Consultar proyectos propios rechazados.",
        "mis_rendiciones_pendientes": "Consultar rendiciones propias.",
        "ayuda_rendicion_gastos": "Ayuda sobre rendiciones de gastos.",
        "direccion_basura": "Consultar lugar autorizado para disposición de residuos.",
        "mi_asignacion": (
            "Consultar asignación propia del usuario, pega del día, sitio al que debe ir, "
            "trabajo asignado, tarea de hoy o destino de trabajo."
        ),
        "info_sitio_id_claro": "Consultar información de un sitio por ID.",
        "cronograma_produccion_corte": "Consultar cronograma de pago/corte configurado.",
    }

    lines = []
    for slug in allowed:
        desc = descriptions.get(slug, "Intent disponible para este usuario.")
        lines.append(f"- {slug}: {desc}")

    return "\n".join(lines)

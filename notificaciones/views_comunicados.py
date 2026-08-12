from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from bot_gz.services_comunicados import (enviar_comunicado_telegram_usuarios,
                                         obtener_destinatarios_comunicados)
from notificaciones.models import NotificationLog
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS HISTORIAL
# ============================================================


def _parse_notification_extra(log: NotificationLog) -> dict:
    """
    Convierte NotificationLog.extra en dict de forma segura.

    Actualmente extra puede venir como:
    - JSON string
    - dict
    - vacío
    """

    raw = getattr(log, "extra", None)

    if not raw:
        return {}

    if isinstance(raw, dict):
        return raw

    try:
        parsed = json.loads(raw)

        if isinstance(parsed, dict):
            return parsed

    except Exception:
        pass

    return {}


def _fecha_notification_log(log: NotificationLog):
    """
    Obtiene la fecha del NotificationLog sin depender
    de un único nombre de campo.

    Esto nos permite soportar el modelo actual aunque
    el campo se llame fecha, creado_en, created_at, etc.
    """

    posibles = [
        "creado_en",
        "created_at",
        "fecha",
        "fecha_creacion",
        "created",
    ]

    for campo in posibles:

        valor = getattr(
            log,
            campo,
            None,
        )

        if valor is not None:
            return valor

    return None


def _nombre_actor(log: NotificationLog, extra: dict) -> str:
    """
    Devuelve nombre del usuario que envió el comunicado.
    """

    actor = getattr(
        log,
        "actor",
        None,
    )

    if actor:

        nombre = (actor.get_full_name() or actor.username or "").strip()

        if nombre:
            return nombre

    return extra.get("actor_nombre") or "Sistema"


def _construir_historial_comunicados() -> list[dict]:
    """
    Construye el historial agrupando NotificationLog.

    NUEVOS COMUNICADOS:
    Todos los destinatarios comparten comunicado_id,
    por lo que aparecen como UNA sola fila.

    COMUNICADOS ANTIGUOS:
    Los logs que todavía no tenían comunicado_id
    igualmente aparecerán en el historial,
    cada log como un comunicado independiente.

    Ejemplo:

    comunicado_id ABC
        usuario 1 -> sent
        usuario 2 -> sent
        usuario 3 -> error

    Historial:
        Nómina Agosto
        3 destinatarios
        2 enviados
        1 fallido
    """

    logs = list(
        NotificationLog.objects.filter(
            tipo="comunicado_general",
        )
        .select_related(
            "usuario",
            "actor",
        )
        .order_by(
            "-id",
        )
    )

    grupos: dict[str, dict] = {}

    status_sent = getattr(
        NotificationLog,
        "STATUS_SENT",
        "sent",
    )

    for log in logs:

        extra = _parse_notification_extra(log)

        comunicado_id = extra.get("comunicado_id") or f"legacy-{log.id}"

        # ====================================================
        # CREAR GRUPO DEL COMUNICADO
        # ====================================================

        if comunicado_id not in grupos:

            titulo = (
                extra.get("titulo")
                or getattr(
                    log,
                    "titulo",
                    "",
                )
                or "Sin título"
            )

            mensaje_original = (
                extra.get("mensaje_original")
                or getattr(
                    log,
                    "mensaje",
                    "",
                )
                or ""
            )

            actor = getattr(
                log,
                "actor",
                None,
            )

            grupos[comunicado_id] = {
                "comunicado_id": comunicado_id,
                "creado_en": _fecha_notification_log(log),
                "titulo": titulo,
                "mensaje": mensaje_original,
                "actor": actor,
                "actor_nombre": _nombre_actor(
                    log,
                    extra,
                ),
                "total_destinatarios": 0,
                "enviados": 0,
                "fallidos": 0,
                "destinatarios": [],
            }

        grupo = grupos[comunicado_id]

        # ====================================================
        # DESTINATARIO
        # ====================================================

        grupo["total_destinatarios"] += 1

        usuario = getattr(
            log,
            "usuario",
            None,
        )

        if usuario:

            nombre_usuario = (
                usuario.get_full_name() or usuario.username or f"Usuario {usuario.id}"
            )

            grupo["destinatarios"].append(
                {
                    "id": usuario.id,
                    "nombre": nombre_usuario,
                    "status": getattr(
                        log,
                        "status",
                        "",
                    ),
                }
            )

        # ====================================================
        # RESULTADO
        # ====================================================

        if (
            getattr(
                log,
                "status",
                "",
            )
            == status_sent
        ):

            grupo["enviados"] += 1

        else:

            grupo["fallidos"] += 1

    # Los logs vienen ordenados por -id.
    # Python conserva el orden de inserción del dict,
    # así que los comunicados más recientes quedan primero.

    return list(grupos.values())


# ============================================================
# VISTA COMUNICADOS
# ============================================================


@login_required
@rol_requerido(
    "admin",
    "supervisor",
    "pm",
    "rrhh",
    "facturacion",
)
def enviar_comunicado_telegram_view(request):
    """
    Pantalla administrativa para comunicados Telegram.

    Funciones:
    - Lista usuarios activos con Telegram.
    - Separa técnicos y administrativos.
    - Todos seleccionados inicialmente.
    - Permite destildar destinatarios.
    - Envía solamente a los seleccionados.
    - Valida nuevamente los usuarios antes de enviar.
    - Muestra historial agrupado de comunicados.
    - Pagina el historial.
    """

    # ========================================================
    # DESTINATARIOS DISPONIBLES
    # ========================================================

    grupos = obtener_destinatarios_comunicados()

    tecnicos = grupos["tecnicos"]

    administrativos = grupos["administrativos"]

    total_destinatarios = grupos["total"]

    titulo = ""
    mensaje_txt = ""

    # ========================================================
    # GET:
    # TODOS SELECCIONADOS POR DEFECTO
    # ========================================================

    seleccionados_ids = {usuario.id for usuario in (administrativos + tecnicos)}

    # ========================================================
    # POST
    # ========================================================

    if request.method == "POST":

        titulo = (request.POST.get("titulo") or "").strip()

        mensaje_txt = (request.POST.get("mensaje") or "").strip()

        seleccionados_raw = request.POST.getlist("destinatarios")

        seleccionados_ids = set()

        # ====================================================
        # LIMPIAR IDS
        # ====================================================

        for uid in seleccionados_raw:

            try:

                uid_int = int(uid)

            except (
                TypeError,
                ValueError,
            ):
                continue

            seleccionados_ids.add(uid_int)

        # ====================================================
        # VALIDACIONES
        # ====================================================

        if not titulo:

            messages.error(
                request,
                "Debes indicar un título para el comunicado.",
            )

        elif not mensaje_txt:

            messages.error(
                request,
                "Debes escribir el contenido del comunicado.",
            )

        elif not seleccionados_ids:

            messages.error(
                request,
                "Debes seleccionar al menos un destinatario.",
            )

        else:

            # =================================================
            # SEGURIDAD:
            # SOLO USUARIOS ACTIVOS CON TELEGRAM REAL
            # =================================================

            usuarios_seleccionados = list(
                CustomUser.objects.filter(
                    id__in=seleccionados_ids,
                    is_active=True,
                    telegram_activo=True,
                    telegram_chat_id__isnull=False,
                )
                .exclude(telegram_chat_id="")
                .prefetch_related("roles")
                .distinct()
            )

            if not usuarios_seleccionados:

                messages.error(
                    request,
                    ("No hay destinatarios válidos " "para realizar el envío."),
                )

            else:

                # =============================================
                # ENVIAR COMUNICADO
                # =============================================

                try:

                    resultado = enviar_comunicado_telegram_usuarios(
                        titulo=titulo,
                        mensaje=mensaje_txt,
                        actor=request.user,
                        usuarios=usuarios_seleccionados,
                    )

                except Exception:

                    logger.exception("Error enviando comunicado general por Telegram")

                    messages.error(
                        request,
                        ("Ocurrió un error al intentar " "enviar el comunicado."),
                    )

                else:

                    enviados = int(
                        resultado.get(
                            "enviados",
                            0,
                        )
                    )

                    fallidos = int(
                        resultado.get(
                            "errores",
                            0,
                        )
                    )

                    comunicado_id = resultado.get("comunicado_id")

                    # =========================================
                    # RESULTADO COMPLETO
                    # =========================================

                    if enviados > 0:

                        if fallidos > 0:

                            messages.warning(
                                request,
                                (
                                    f"Comunicado enviado a "
                                    f"{enviados} usuario(s). "
                                    f"{fallidos} envío(s) "
                                    f"no pudieron completarse."
                                ),
                            )

                        else:

                            messages.success(
                                request,
                                (
                                    "Comunicado enviado "
                                    f"correctamente a "
                                    f"{enviados} usuario(s)."
                                ),
                            )

                        logger.info(
                            (
                                "Comunicado Telegram enviado "
                                "comunicado_id=%s "
                                "actor_id=%s "
                                "enviados=%s "
                                "fallidos=%s"
                            ),
                            comunicado_id,
                            request.user.id,
                            enviados,
                            fallidos,
                        )

                        # =====================================
                        # REDIRECT
                        #
                        # Al volver por GET, el historial
                        # inmediatamente leerá los logs recién
                        # creados.
                        # =====================================

                        return redirect("notificaciones:enviar_comunicado_telegram")

                    # =========================================
                    # NINGÚN ENVÍO EXITOSO
                    # =========================================

                    messages.error(
                        request,
                        (
                            "No fue posible enviar "
                            "el comunicado a ningún destinatario."
                        ),
                    )

    # ========================================================
    # HISTORIAL
    # ========================================================

    historial_comunicados = _construir_historial_comunicados()

    # ========================================================
    # PAGINACIÓN HISTORIAL
    # ========================================================
    #
    # 10 comunicados por página.
    #
    # El HTML ya utiliza:
    #
    # ?page_comunicados=2
    #
    # ========================================================

    paginator_comunicados = Paginator(
        historial_comunicados,
        10,
    )

    numero_pagina = request.GET.get("page_comunicados") or 1

    pagina_comunicados = paginator_comunicados.get_page(numero_pagina)

    # ========================================================
    # CONTEXTO
    # ========================================================

    context = {
        "titulo": titulo,
        "mensaje": mensaje_txt,
        "tecnicos": tecnicos,
        "administrativos": administrativos,
        "total_tecnicos": len(tecnicos),
        "total_administrativos": len(administrativos),
        "total_destinatarios": (total_destinatarios),
        "seleccionados_ids": (seleccionados_ids),
        # HISTORIAL
        "pagina_comunicados": (pagina_comunicados),
        "total_comunicados": len(historial_comunicados),
    }

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "notificaciones/enviar_comunicado.html",
        context,
    )

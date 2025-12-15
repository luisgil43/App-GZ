# bot_gz/services.py

import logging
import re
import unicodedata
from datetime import timedelta
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento
from liquidaciones.models import Liquidacion
from operaciones.models import ServicioCotizado, SitioMovil
from rrhh.models import ContratoTrabajo, CronogramaPago
from usuarios.models import CustomUser

from .models import BotIntent, BotMessageLog, BotSession, BotTrainingExample
from .services_tecnico import \
    responder_direccion_basura as _responder_direccion_basura
from .services_tecnico import \
    responder_produccion_hasta_hoy as _responder_produccion_hasta_hoy

logger = logging.getLogger(__name__)


def _get_bot_token() -> Optional[str]:
    """
    Lee el token del bot desde settings.

    Usamos el mismo criterio que en notificaciones:
    TELEGRAM_BOT_TOKEN_GZ o TELEGRAM_BOT_TOKEN.
    """
    token = getattr(settings, "TELEGRAM_BOT_TOKEN_GZ", None) or getattr(
        settings, "TELEGRAM_BOT_TOKEN", None
    )
    if not token:
        logger.warning(
            "Telegram bot: no se encontró TELEGRAM_BOT_TOKEN_GZ ni TELEGRAM_BOT_TOKEN en settings."
        )
    return token


# ===================== Helpers de normalización =====================

_STOPWORDS = {
    "yo",
    "mi",
    "mis",
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "por",
    "para",
    "que",
    "en",
    "a",
    "al",
    "este",
    "mes",
    "quiero",
    "necesito",
    "dime",
    "decime",
    "muéstrame",
    "muestrame",
    "pásame",
    "pasame",
    "ayudame",
    "ayúdame",
    "porfa",
    "porfavor",
    "por",
    "favor",
    "hola",
    "buenos",
    "dias",
    "días",
    "tardes",
    "noches",
    "y",
}


def _unaccent(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = _unaccent(t)
    # dejar solo letras/números/espacios
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _tokenize(text: str):
    norm = _normalize(text)
    tokens = norm.split()
    return [t for t in tokens if t not in _STOPWORDS]


def _es_saludo(texto: str) -> bool:
    """
    Detecta saludos simples tipo 'hola', 'buenas', 'buenos días', etc.
    Sin usar STOPWORDS, para no perder la señal.
    """
    norm = _normalize(texto)
    if not norm:
        return False

    tokens = norm.split()
    if not tokens:
        return False

    primeras = {"hola", "buenas", "buenos"}
    if tokens[0] in primeras:
        return True

    frases = {
        "hola",
        "hola bot",
        "buenas",
        "buenas tardes",
        "buenas noches",
        "buenos dias",
        "buen dia",
    }
    return norm in frases


def _menciona_otra_persona(texto_original: str, usuario: CustomUser) -> bool:
    """
    Intenta detectar si el mensaje habla de otra persona distinta al usuario.
    Ej: 'contrato de Edgardo', 'liquidación de Juan', etc.
    Solo se usa para mostrar mensajes de privacidad.
    """
    # Posibles nombres propios en el texto (primera letra mayúscula)
    posibles = {
        m.group(0).strip()
        for m in re.finditer(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b", texto_original)
    }
    if not posibles:
        return False

    # Normalizamos nombres del usuario
    nombres_usuario = set()
    for campo in [usuario.first_name, usuario.last_name, getattr(usuario, "full_name", "")]:
        if campo:
            for trozo in _normalize(str(campo)).split():
                if trozo:
                    nombres_usuario.add(trozo)

    # Comparamos nombres detectados vs nombres del usuario
    for nombre in posibles:
        n_norm = _normalize(nombre)
        if n_norm and n_norm not in nombres_usuario:
            return True

    return False


# ===================== Parsing de mes/año =====================

_MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def _parse_mes_anio_desde_texto(texto: str) -> Optional[Tuple[int, int]]:
    """
    Intenta extraer mes/año desde texto libre:
    - nombres de mes (enero, febrero, ...)
    - número de mes (1..12, 01..12)
    - año (2024, 2025, ...) si viene; si no, usa año actual.
    """
    tokens = _tokenize(texto)
    if not tokens:
        return None

    # año explícito
    anio = None
    for t in tokens:
        m = re.match(r"20\d{2}$", t)
        if m:
            anio = int(m.group())
            break
    if not anio:
        anio = timezone.localdate().year

    # mes por nombre
    for t in tokens:
        if t in _MESES:
            return _MESES[t], anio

    # mes numérico simple
    for t in tokens:
        if t.isdigit():
            val = int(t)
            if 1 <= val <= 12:
                return val, anio

    # intentos tipo 07-2025 o 07/2025
    m2 = re.search(r"(0?[1-9]|1[0-2])[-/](20\d{2})", _normalize(texto))
    if m2:
        mes = int(m2.group(1))
        anio = int(m2.group(2))
        return mes, anio

    return None


# ===================== Sesiones y usuarios =====================

def get_or_create_session(
    chat_id: str, from_user: Optional[dict] = None
) -> Tuple[BotSession, Optional[CustomUser]]:
    """
    Obtiene o crea la sesión del bot para este chat.
    Intenta vincularla con un CustomUser que tenga ese telegram_chat_id.
    """
    chat_id_str = str(chat_id)

    usuario = CustomUser.objects.filter(
        telegram_chat_id=chat_id_str,
        telegram_activo=True,
    ).first()

    defaults = {
        "usuario": usuario,
        "contexto": "tecnico",
        "activa": True,
        "ultima_interaccion": timezone.now(),
    }

    sesion, created = BotSession.objects.get_or_create(
        chat_id=chat_id_str,
        defaults=defaults,
    )

    # Si luego encontramos usuario y la sesión no lo tenía, actualizamos
    if not created and usuario and sesion.usuario_id != usuario.id:
        sesion.usuario = usuario
        sesion.save(update_fields=["usuario"])

    return sesion, sesion.usuario


# ===================== Detección de intent =====================

def detect_intent_from_text(
    texto: str, scope: Optional[str] = None
) -> Tuple[Optional[BotIntent], float]:
    """
    Detección de intent basada en:
    1) overlap de tokens con ejemplos de entrenamiento
    2) refuerzo por palabras clave (liquidación, contrato, rendiciones, etc.)

    Así, frases como "liquidacion", "ver mi liquidacion", "necesito mi contrato",
    etc. se entienden aunque sean muy cortas.
    """
    user_tokens = set(_tokenize(texto))
    if not user_tokens:
        return None, 0.0

    # --- 1) Matching con ejemplos de entrenamiento ---
    examples_qs = (
        BotTrainingExample.objects.filter(activo=True).select_related("intent")
    )

    if scope:
        examples_qs = examples_qs.filter(intent__scope__in=[scope, "global"])

    best_score = 0.0
    best_intent: Optional[BotIntent] = None

    for ex in examples_qs:
        ex_tokens = set(_tokenize(ex.texto))
        if not ex_tokens:
            continue
        inter = user_tokens & ex_tokens
        if not inter:
            continue

        score = len(inter) / len(ex_tokens)
        if score > best_score:
            best_score = score
            best_intent = ex.intent

    # --- 2) Reglas rápidas por palabras clave ---
    keyword_intent: Optional[BotIntent] = None
    keyword_score: float = 0.0

    def add_keyword_candidate(slug: str, score: float):
        nonlocal keyword_intent, keyword_score
        if score <= keyword_score:
            return
        try:
            intent = BotIntent.objects.get(slug=slug, activo=True)
        except BotIntent.DoesNotExist:
            return
        keyword_intent = intent
        keyword_score = score

    # Liquidaciones
    if {"liquidacion", "liquidaciones"} & user_tokens:
        add_keyword_candidate("mis_liquidaciones", 0.9)

    # Contrato
    if "contrato" in user_tokens or "contratos" in user_tokens:
        add_keyword_candidate("mi_contrato_vigente", 0.9)

    # Producción
    if "produccion" in user_tokens:
        add_keyword_candidate("mi_produccion_hasta_hoy", 0.8)

    # Proyectos pendientes / asignados
    if "proyectos" in user_tokens or "servicios" in user_tokens:
        if (
            "pendientes" in user_tokens
            or "pendiente" in user_tokens
            or "asignados" in user_tokens
        ):
            add_keyword_candidate("mis_proyectos_pendientes", 0.8)

    # Proyectos rechazados
    if (
        "rechazados" in user_tokens
        or "rechazado" in user_tokens
        or "rechazadas" in user_tokens
        or "rechazada" in user_tokens
    ):
        add_keyword_candidate("mis_proyectos_rechazados", 0.8)

    # Rendiciones / gastos
    if (
        "rendicion" in user_tokens
        or "rendiciones" in user_tokens
        or "gasto" in user_tokens
        or "gastos" in user_tokens
    ):
        add_keyword_candidate("mis_rendiciones_pendientes", 0.8)
        add_keyword_candidate("ayuda_rendicion_gastos", 0.8)

    # Dirección de la basura / residuos
    if (
        "basura" in user_tokens
        or "residuos" in user_tokens
        or "desechos" in user_tokens
    ):
        add_keyword_candidate("direccion_basura", 0.9)

    # Corte de producción / cuándo pagan
    if (
        "pago" in user_tokens
        or "pagan" in user_tokens
        or "pagar" in user_tokens
        or "corte" in user_tokens
        or "cronograma" in user_tokens
    ):
        add_keyword_candidate("cronograma_produccion_corte", 0.7)

    # Info sitio por ID Claro
    if "sitio" in user_tokens or "site" in user_tokens:
        add_keyword_candidate("info_sitio_id_claro", 0.7)

    # --- Elegir el mejor resultado entre ejemplos y reglas ---
    final_intent = best_intent
    final_score = best_score

    if keyword_intent and keyword_score > final_score:
        final_intent = keyword_intent
        final_score = keyword_score

    # Umbral mínimo para aceptar un intent
    if final_score < 0.3:
        return None, float(final_score)

    return final_intent, float(final_score)


# ===================== Envío de mensajes a Telegram + log =====================

def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    sesion: Optional[BotSession] = None,
    usuario: Optional[CustomUser] = None,
    intent: Optional[BotIntent] = None,
    meta: Optional[dict] = None,
    marcar_para_entrenamiento: bool = False,
) -> BotMessageLog:
    """
    Envía un mensaje a Telegram y registra el BotMessageLog (salida).
    """
    token = _get_bot_token()
    chat_id_str = str(chat_id)

    meta = meta or {}

    log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id_str,
        direccion="out",
        texto=text,
        intent_detectado=intent,
        status="ok",
        marcar_para_entrenamiento=marcar_para_entrenamiento,
        meta=meta,
    )

    if not token:
        log.status = "error"
        log.meta = {**meta, "error": "No hay token de Telegram configurado"}
        log.save(update_fields=["status", "meta"])
        logger.error("No se puede enviar mensaje Telegram: falta token.")
        return log

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id_str,
        "text": text,
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        try:
            data = resp.json()
        except Exception:
            data = None

        if resp.status_code == 200 and isinstance(data, dict) and data.get("ok"):
            # ok
            logger.info("Mensaje bot OUT ok chat_id=%s", chat_id_str)
        else:
            desc = ""
            if isinstance(data, dict):
                desc = data.get("description") or ""
            if not desc:
                desc = resp.text[:500]

            log.status = "error"
            meta_error = dict(meta)
            meta_error["telegram_error"] = {
                "status_code": resp.status_code,
                "description": desc,
            }
            log.meta = meta_error
            log.save(update_fields=["status", "meta"])
            logger.error("Error enviando mensaje bot OUT a Telegram: %s", desc)

    except Exception as e:
        log.status = "error"
        meta_error = dict(meta)
        meta_error["exception"] = str(e)
        log.meta = meta_error
        log.save(update_fields=["status", "meta"])
        logger.exception("Excepción enviando mensaje bot OUT a Telegram")

    return log


# ===================== Handlers de intents (respuestas) =====================

def _respuesta_sin_usuario(chat_id: str) -> str:
    return (
        "👋 Hola. Todavía no tengo vinculado este chat de Telegram con tu usuario de GZ Services.\n\n"
        "Pídele al administrador que configure tu `telegram_chat_id` en tu ficha de usuario "
        "para que pueda mostrarte tu información personal (liquidaciones, contratos, proyectos, etc.)."
    )


def _handler_cronograma_produccion(usuario: CustomUser) -> str:
    obj = CronogramaPago.objects.first()
    if not obj:
        return (
            "Por ahora no tengo un cronograma de pagos/corte de producción configurado "
            "en el sistema. Consulta con administración o finanzas."
        )

    hoy = timezone.localdate()
    mes = hoy.month

    mapping = {
        1: ("enero_texto", "enero_fecha", "Enero"),
        2: ("febrero_texto", "febrero_fecha", "Febrero"),
        3: ("marzo_texto", "marzo_fecha", "Marzo"),
        4: ("abril_texto", "abril_fecha", "Abril"),
        5: ("mayo_texto", "mayo_fecha", "Mayo"),
        6: ("junio_texto", "junio_fecha", "Junio"),
        7: ("julio_texto", "julio_fecha", "Julio"),
        8: ("agosto_texto", "agosto_fecha", "Agosto"),
        9: ("septiembre_texto", "septiembre_fecha", "Septiembre"),
        10: ("octubre_texto", "octubre_fecha", "Octubre"),
        11: ("noviembre_texto", "noviembre_fecha", "Noviembre"),
        12: ("diciembre_texto", "diciembre_fecha", "Diciembre"),
    }

    texto_field, fecha_field, nombre_mes = mapping[mes]
    texto_mes = getattr(obj, texto_field)
    fecha_mes = getattr(obj, fecha_field)

    msg = "📆 *Corte de producción*\n\n"
    if fecha_mes:
        msg += f"Para *{nombre_mes}* el corte está definido el *{fecha_mes.strftime('%d-%m-%Y')}*.\n"
    else:
        msg += f"Para *{nombre_mes}* no tengo una fecha de corte configurada.\n"

    if texto_mes:
        msg += f"\n📝 Nota: {texto_mes}"

    return msg


def _handler_mis_liquidaciones(usuario: CustomUser, texto_usuario: str) -> str:
    """
    Maneja consultas de liquidaciones:
    - Si menciona a otra persona -> mensaje de privacidad.
    - Si indica mes/año -> devuelve enlace directo.
    - Si no indica mes/año -> lista las liquidaciones disponibles y pide que elija.
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tus propias* liquidaciones de sueldo.\n"
            "Si un compañero necesita la suya, debe pedirla directamente a RRHH o entrar con su usuario."
        )

    qs = Liquidacion.objects.filter(tecnico=usuario).order_by("-año", "-mes")

    if not qs.exists():
        return "Por ahora no tengo liquidaciones de sueldo cargadas a tu nombre en el sistema."

    # Intentar extraer mes/año desde el texto
    parsed = _parse_mes_anio_desde_texto(texto_usuario)
    if parsed:
        mes, anio = parsed
        objetivo = qs.filter(mes=mes, año=anio).first()
        if not objetivo:
            return (
                f"No encontré una liquidación para {mes:02d}/{anio}.\n"
                "Revisa si ya fue cargada en el sistema o intenta con otro mes/año."
            )
    else:
        # Sin mes/año -> mostramos listado y pedimos precisión
        lineas = []
        lineas.append("🧾 *Liquidaciones registradas a tu nombre*")
        lineas.append("")
        for liq in qs[:12]:
            estado = "firmada ✅" if liq.firmada else "pendiente de firma ✍️"
            lineas.append(f"• {liq.mes:02d}/{liq.año} – {estado}")
        if qs.count() > 12:
            lineas.append("")
            lineas.append("Mostrando solo las 12 más recientes.")

        lineas.append("")
        lineas.append(
            "Dime de qué mes/año necesitas el PDF.\n"
            "Por ejemplo: `liquidación de 11/2025` o `liquidación de noviembre 2025`."
        )
        return "\n".join(lineas)

    # Preferimos el PDF firmado si existe
    url = None
    if objetivo.pdf_firmado:
        url = objetivo.pdf_firmado.url
    elif objetivo.archivo_pdf_liquidacion:
        url = objetivo.archivo_pdf_liquidacion.url

    if not url:
        return (
            f"Tengo registrada tu liquidación de {objetivo.mes:02d}/{objetivo.año}, "
            "pero aún no tiene un archivo PDF asociado."
        )

    return (
        f"🧾 Tu liquidación de sueldo {objetivo.mes:02d}/{objetivo.año}:\n\n"
        f"{url}\n\n"
        "Puedes abrir ese enlace para descargarla."
    )


def _handler_mi_contrato(usuario: CustomUser, texto_usuario: str) -> str:
    """
    Solo muestra el contrato del propio usuario.
    Si el mensaje parece referirse a otro (nombre distinto) -> mensaje de privacidad.
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Por seguridad solo puedo mostrarte *tu propio contrato de trabajo*.\n"
            "No tengo permiso para mostrar contratos de otros compañeros."
        )

    contrato = (
        ContratoTrabajo.objects.filter(tecnico=usuario)
        .order_by("-fecha_inicio")
        .first()
    )
    if not contrato:
        return "No tengo registrado ningún contrato de trabajo asociado a tu usuario."

    url = contrato.archivo.url if contrato.archivo else None
    estado = contrato.status_label

    msg = "📄 *Tu contrato de trabajo*\n\n"
    msg += f"• Estado: *{estado}*\n"
    msg += f"• Fecha de inicio: {contrato.fecha_inicio.strftime('%d-%m-%Y')}\n"
    if contrato.fecha_termino:
        msg += f"• Fecha de término: {contrato.fecha_termino.strftime('%d-%m-%Y')}\n"

    if url:
        msg += f"\n🔗 Archivo del contrato:\n{url}"
    else:
        msg += "\n(No tengo un archivo PDF/subido para este contrato)."

    return msg


def _handler_mi_produccion(usuario: CustomUser, texto_usuario: str) -> str:
    """
    Maneja consultas de producción:
    - Si menciona a otra persona -> mensaje de privacidad.
    - Si incluye 'hoy' / 'hasta hoy' / 'a la fecha' -> usa responder_produccion_hasta_hoy.
    - Si es muy genérico -> guía al usuario.
    """
    if _menciona_otra_persona(texto_usuario, usuario):
        return (
            "Solo puedo mostrarte *tu propia producción*.\n"
            "No tengo permiso para entregar información de producción de otros compañeros."
        )

    tokens = set(_tokenize(texto_usuario))

    # Producción hasta hoy
    if "hoy" in tokens or "ahora" in tokens or "fecha" in tokens:
        return _responder_produccion_hasta_hoy(usuario)

    # Si menciona 'mes' o un mes específico pero todavía no tenemos desglose por mes
    if "mes" in tokens or any(t in _MESES for t in tokens):
        return (
            "Por ahora solo puedo calcular tu *producción estimada acumulada hasta hoy*.\n"
            "En una siguiente versión te podré mostrar también por mes específico.\n\n"
            "Si quieres verla, dime por ejemplo: `mi producción hasta hoy`."
        )

    # Mensaje genérico para 'producción', 'produccion', etc.
    return (
        "¿Sobre qué periodo quieres saber tu producción?\n\n"
        "Por ahora puedo mostrarte tu *producción estimada acumulada hasta hoy*.\n"
        "Pídeme, por ejemplo: `mi producción hasta hoy`."
    )


def _handler_info_sitio_id_claro(texto_usuario: str) -> str:
    # Buscar algo tipo MA5694, CL1234, etc.
    match = re.search(r"\b[A-Za-z]{1,3}\d{3,6}\b", texto_usuario)
    if not match:
        return (
            "Para ayudarte con la información del sitio necesito que me indiques el *ID Claro*, "
            "por ejemplo: `MA5694`."
        )

    id_claro = match.group(0).upper()

    sitio = (
        SitioMovil.objects.filter(id_claro__iexact=id_claro).first()
        or SitioMovil.objects.filter(id_sites__iexact=id_claro).first()
        or SitioMovil.objects.filter(id_sites_new__iexact=id_claro).first()
    )

    if not sitio:
        return f"No encontré un sitio con ID Claro `{id_claro}` en el sistema."

    msg = f"📡 *Sitio {id_claro}*\n\n"
    if sitio.nombre:
        msg += f"• Nombre: {sitio.nombre}\n"
    if sitio.direccion:
        msg += f"• Dirección: {sitio.direccion}\n"
    if sitio.comuna:
        msg += f"• Comuna: {sitio.comuna}\n"
    if sitio.region:
        msg += f"• Región: {sitio.region}\n"

    # Seguridad / acceso
    detalles = []
    if sitio.candado_bt:
        detalles.append(f"Candado BT: {sitio.candado_bt}")
    if sitio.condiciones_acceso:
        detalles.append(f"Acceso: {sitio.condiciones_acceso}")
    if sitio.claves:
        detalles.append(f"Claves: {sitio.claves}")
    if sitio.llaves:
        detalles.append(f"Llaves: {sitio.llaves}")
    if sitio.cantidad_llaves:
        detalles.append(f"Cantidad de llaves: {sitio.cantidad_llaves}")

    if detalles:
        msg += "\n🔐 *Acceso / Seguridad:*\n"
        for d in detalles:
            msg += f"• {d}\n"

    if sitio.latitud is not None and sitio.longitud is not None:
        lat = str(sitio.latitud).replace(",", ".")
        lng = str(sitio.longitud).replace(",", ".")
        google_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        msg += f"\n📍 Google Maps:\n{google_link}"

    return msg


def _handler_mis_proyectos_pendientes(usuario: CustomUser) -> str:
    estados = ["asignado", "en_progreso", "en_revision_supervisor"]

    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado__in=estados,
    ).order_by("fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos/servicios pendientes en este momento. ✅"

    msg = f"Tienes *{total}* proyectos pendientes.\n\n"
    msg += "Te muestro los últimos asignados:\n"

    for s in qs[:10]:
        du = s.du or "—"
        id_claro = s.id_claro or "Sin ID Claro"
        detalle = (s.detalle_tarea or "").strip()
        if len(detalle) > 80:
            detalle = detalle[:77] + "…"
        msg += f"• DU {du} / {id_claro}: {detalle}\n"

    return msg


def _handler_mis_proyectos_rechazados(usuario: CustomUser) -> str:
    qs = ServicioCotizado.objects.filter(
        trabajadores_asignados=usuario,
        estado="rechazado_supervisor",
    ).order_by("-fecha_aprobacion_supervisor", "-fecha_creacion")

    total = qs.count()
    if total == 0:
        return "No tienes proyectos rechazados actualmente. ✅"

    msg = f"Tienes *{total}* proyectos rechazados por supervisor.\n\n"
    for s in qs[:10]:
        du = s.du or "—"
        id_claro = s.id_claro or "Sin ID Claro"
        motivo = (s.motivo_rechazo or "").strip()
        if len(motivo) > 80:
            motivo = motivo[:77] + "…"
        msg += f"• DU {du} / {id_claro} – Motivo: {motivo or 'Sin detalle'}\n"

    return msg


def _handler_mis_rendiciones_pendientes(
    usuario: CustomUser, texto_usuario: str
) -> str:
    """
    Resumen de rendiciones de gastos.
    - Si el mensaje habla de "hacer / crear / declarar" -> explica que el flujo
      de creación por bot aún no está activo.
    - Si el mensaje es muy genérico ("gasto", "rendiciones"), pregunta qué tipo quiere.
    - Soporta filtros por pendientes / aprobadas / rechazadas.
    - Soporta filtro por día con "hoy" o "ayer".
    """
    tokens = set(_tokenize(texto_usuario))

    # Caso: el usuario quiere CREAR una rendición nueva
    if {"hacer", "crear", "nueva", "nuevo", "declarar"} & tokens:
        return (
            "Por ahora todavía *no puedo crear rendiciones nuevas* desde el bot 🤖.\n\n"
            "Para declarar un gasto debes hacerlo en la sección de *Mis Rendiciones* "
            "de la app web.\n"
            "A futuro iremos habilitando este flujo por aquí para que sea más rápido."
        )

    # Caso 1: mensaje ultra-genérico -> hacemos preguntas
    generic = {"gasto", "gastos", "rendicion", "rendiciones"}
    if tokens and tokens <= generic:
        return (
            "Para ayudarte mejor con tus rendiciones dime qué necesitas exactamente:\n\n"
            "• Si quieres ver las *pendientes*: escribe `rendiciones pendientes`\n"
            "• Si quieres las *aprobadas* y por quién: `rendiciones aprobadas`\n"
            "• Si quieres las *rechazadas*: `rendiciones rechazadas`\n"
            "• Si son solo de *hoy*: agrega `de hoy`, por ejemplo `rendiciones pendientes de hoy`.\n"
            "• Si quieres ver todas tus rendiciones de un día específico: `rendiciones de hoy` o `rendiciones de ayer`."
        )

    # Filtro por estado
    estados = []
    titulo = ""
    extra_label = ""

    if "rechazadas" in tokens or "rechazado" in tokens or "rechazada" in tokens:
        estados = ["rechazado_supervisor", "rechazado_pm", "rechazado_finanzas"]
        titulo = "Rendiciones rechazadas"
        extra_label = "rechazadas"
    elif "aprobadas" in tokens or "aprobado" in tokens or "aprobada" in tokens:
        estados = ["aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"]
        titulo = "Rendiciones aprobadas"
        extra_label = "aprobadas"
    else:
        estados = [
            "pendiente_abono_usuario",
            "pendiente_supervisor",
            "pendiente_pm",
            "pendiente_finanzas",
        ]
        titulo = "Rendiciones pendientes en el flujo de aprobación"
        extra_label = "pendientes"

    # Filtro por día (hoy / ayer)
    hoy = timezone.localdate()
    fecha = None
    if "hoy" in tokens:
        fecha = hoy
    elif "ayer" in tokens:
        fecha = hoy - timedelta(days=1)

    filtros = {"usuario": usuario, "status__in": estados}
    if fecha is not None:
        filtros["fecha"] = fecha

    qs = CartolaMovimiento.objects.filter(**filtros)

    total = qs.count()
    if total == 0:
        if fecha:
            return (
                f"No tienes rendiciones {extra_label} para el día "
                f"{fecha.strftime('%d-%m-%Y')}."
            )
        return f"No tienes rendiciones {extra_label} en este momento. ✅"

    suma_cargos = qs.aggregate(total=Sum("cargos"))["total"] or 0

    msg = f"🧾 *{titulo}*\n"
    if fecha:
        msg += f"Del día {fecha.strftime('%d-%m-%Y')}.\n"

    msg += (
        f"\nTienes *{total}* rendiciones en este grupo.\n"
        f"Monto total (cargos): ${suma_cargos:,.0f}\n\n"
        "Ejemplos recientes:\n"
    )

    estados_dict = dict(getattr(CartolaMovimiento, "ESTADOS", []))

    for m in qs.order_by("-fecha")[:5]:
        proyecto = m.proyecto.nombre if m.proyecto else "Sin proyecto"
        tipo = m.tipo.nombre if m.tipo else "Sin tipo"
        estado_legible = estados_dict.get(m.status, m.status)
        msg += (
            f"• {m.fecha.strftime('%d-%m-%Y')} – {proyecto} – {tipo} – "
            f"${m.cargos:,.0f} – {estado_legible}\n"
        )

    msg += (
        "\nSi quieres otro filtro, puedes decirme por ejemplo:\n"
        "• `rendiciones aprobadas`\n"
        "• `rendiciones rechazadas`\n"
        "• `rendiciones pendientes de hoy`"
    )

    return msg


def _handler_direccion_basura(usuario: CustomUser) -> str:
    """
    Llama al helper de services_tecnico que lee BOT_GZ_URL_BASURA / BOT_GZ_TEXTO_BASURA.
    """
    return _responder_direccion_basura()


# ===================== Router principal de intents =====================

def run_intent(
    intent: Optional[BotIntent],
    texto_usuario: str,
    sesion: BotSession,
    usuario: Optional[CustomUser],
    inbound_log: BotMessageLog,
) -> str:
    """
    Dado un intent detectado (o None), decide qué responder.
    """
    chat_id = sesion.chat_id

    if usuario is None:
        # No hay usuario vinculado -> mensaje estándar
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
        return _respuesta_sin_usuario(chat_id)

    # Si no se detectó intent, probamos algunos casos especiales antes del fallback
    if not intent:
        tokens = set(_tokenize(texto_usuario))

        # 1) Saludo simple
        if _es_saludo(texto_usuario):
            inbound_log.status = "ok"
            inbound_log.marcar_para_entrenamiento = False
            inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
            nombre = usuario.first_name or usuario.get_full_name() or ""
            nombre = nombre.strip()
            saludo_nombre = f"{nombre}, " if nombre else ""
            return (
                f"👋 Hola {saludo_nombre}soy el bot de GZ Services.\n\n"
                "Puedo ayudarte con cosas como:\n"
                "• ver tu liquidación de sueldo\n"
                "• consultar tu contrato de trabajo\n"
                "• ver tus proyectos pendientes\n"
                "• revisar tus rendiciones de gastos\n\n"
                "Escríbeme con frases cortas, por ejemplo: `liquidación de 11/2025`."
            )

        # 2) Seguimiento de conversación sobre rendiciones:
        #    Ej: primero pregunta por pendientes, luego escribe solo "y rechazadas?"
        if (
            sesion.ultimo_intent
            and sesion.ultimo_intent.slug
            in ["mis_rendiciones_pendientes", "ayuda_rendicion_gastos"]
        ):
            if {"rechazadas", "rechazada", "aprobadas", "aprobada", "pendientes", "hoy", "ayer"} & tokens:
                inbound_log.status = "ok"
                inbound_log.marcar_para_entrenamiento = False
                inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])
                return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

        # Si nada de lo anterior aplica -> fallback estándar
        inbound_log.status = "fallback"
        inbound_log.marcar_para_entrenamiento = True
        inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

        return (
            "Por ahora todavía estoy aprendiendo y no pude entender bien tu mensaje 🤖.\n\n"
            "Puedes pedirme, por ejemplo:\n"
            "• ver mi liquidación de sueldo\n"
            "• consultar mi contrato de trabajo\n"
            "• ver mis proyectos pendientes\n"
            "• ver mis rendiciones de gastos pendientes por aprobación\n\n"
            "Intenta usar frases cortas y directas, y yo te voy guiando."
        )

    # Marcamos el intent y confianza como OK
    inbound_log.status = "ok"
    inbound_log.marcar_para_entrenamiento = intent.requiere_revision_humana
    inbound_log.save(update_fields=["status", "marcar_para_entrenamiento"])

    slug = intent.slug

    # === Router por slug ===
    if slug == "cronograma_produccion_corte":
        return _handler_cronograma_produccion(usuario)

    if slug == "mis_liquidaciones":
        return _handler_mis_liquidaciones(usuario, texto_usuario)

    if slug == "mi_contrato_vigente":
        return _handler_mi_contrato(usuario, texto_usuario)

    if slug == "mi_produccion_hasta_hoy":
        return _handler_mi_produccion(usuario, texto_usuario)

    if slug == "info_sitio_id_claro":
        return _handler_info_sitio_id_claro(texto_usuario)

    if slug == "mis_proyectos_pendientes":
        return _handler_mis_proyectos_pendientes(usuario)

    if slug == "mis_proyectos_rechazados":
        return _handler_mis_proyectos_rechazados(usuario)

    # Tanto para "ayuda_rendicion_gastos" como para "mis_rendiciones_pendientes"
    # usamos el mismo handler que entiende pendientes/aprobadas/rechazadas/hoy/ayer.
    if slug == "ayuda_rendicion_gastos":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "mis_rendiciones_pendientes":
        return _handler_mis_rendiciones_pendientes(usuario, texto_usuario)

    if slug == "direccion_basura":
        return _handler_direccion_basura(usuario)

    # Otros intents que todavía no implementamos bien:
    return (
        f"Recibí tu consulta y la reconocí como '{intent.nombre}', "
        "pero esta funcionalidad aún se está terminando de implementar en el bot.\n\n"
        "Mientras tanto, puedes revisar esa información directamente en la app web."
    )


# ===================== Entry point: manejar update de Telegram =====================

def handle_telegram_update(update: dict) -> None:
    """
    Punto de entrada para el webhook de Telegram.
    - Lee el mensaje
    - Crea/actualiza sesión
    - Detecta intent
    - Genera respuesta
    - Envía respuesta y registra logs
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        logger.info("Update de Telegram sin 'message': %s", update)
        return

    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    text = message.get("text") or ""

    if not text.strip():
        # Por ahora ignoramos mensajes sin texto (stickers, fotos, etc.)
        logger.info("Mensaje sin texto recibido (chat_id=%s)", chat.get("id"))
        return

    chat_id = str(chat.get("id"))

    sesion, usuario = get_or_create_session(chat_id, from_user)
    sesion.ultima_interaccion = timezone.now()
    sesion.save(update_fields=["ultima_interaccion"])

    # Log IN
    inbound_log = BotMessageLog.objects.create(
        sesion=sesion,
        usuario=usuario,
        chat_id=chat_id,
        direccion="in",
        texto=text,
        status="ok",
        meta={"update_id": update.get("update_id")},
    )

    # Detect intent
    intent, confianza = detect_intent_from_text(text, scope=sesion.contexto)
    if intent:
        sesion.ultimo_intent = intent
        sesion.save(update_fields=["ultimo_intent"])

    inbound_log.intent_detectado = intent
    inbound_log.confianza = confianza
    inbound_log.save(update_fields=["intent_detectado", "confianza"])

    # Ejecutar intent
    reply_text = run_intent(intent, text, sesion, usuario, inbound_log)

    # Enviar OUT
    marcar_train_out = (intent is None) or intent.requiere_revision_humana
    send_telegram_message(
        chat_id,
        reply_text,
        sesion=sesion,
        usuario=usuario,
        intent=intent,
        meta={"from_update_id": update.get("update_id")},
        marcar_para_entrenamiento=marcar_train_out,
    )
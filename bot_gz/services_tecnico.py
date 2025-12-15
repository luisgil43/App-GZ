# bot_gz/services_tecnico.py

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from facturacion.models import CartolaMovimiento, Proyecto, TipoGasto
from liquidaciones.models import Liquidacion
from operaciones.models import MonthlyPayment, ServicioCotizado, SitioMovil
from rrhh.models import ContratoTrabajo, CronogramaPago
from usuarios.models import CustomUser


def _formato_monto(valor: Decimal | int | float | None) -> str:
    if valor is None:
        valor = Decimal("0")
    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor))
    # Formato CLP: 1.234.567
    txt = f"{valor:,.0f}"
    return txt.replace(",", ".")  # 1,234 -> 1.234


def responder_rendiciones_pendientes(usuario: CustomUser) -> str:
    """
    Resumen de rendiciones (CartolaMovimiento) pendientes de aprobación
    del punto de vista del técnico.
    Solo ve sus propias rendiciones.
    """
    pendientes = CartolaMovimiento.objects.filter(
        usuario=usuario,
        status__in=[
            "pendiente_abono_usuario",
            "pendiente_supervisor",
            "pendiente_pm",
            "pendiente_finanzas",
        ],
    ).order_by("-fecha")

    if not pendientes.exists():
        return (
            "Por ahora no tienes rendiciones de gastos pendientes de aprobación ✅.\n"
            "Si declaras un nuevo gasto, te voy avisando cómo avanza en cada etapa."
        )

    total_cargos = pendientes.aggregate(s=Sum("cargos"))["s"] or Decimal("0")
    total_abonos = pendientes.aggregate(s=Sum("abonos"))["s"] or Decimal("0")
    total_neto = total_cargos - total_abonos

    lineas = []
    lineas.append("📄 *Rendiciones pendientes de aprobación*")
    lineas.append("")
    lineas.append(f"Cantidad de movimientos: {pendientes.count()}")
    lineas.append(f"Monto cargos: ${_formato_monto(total_cargos)}")
    if total_abonos:
        lineas.append(f"Monto abonos: ${_formato_monto(total_abonos)}")
    lineas.append(f"Monto neto: ${_formato_monto(total_neto)}")
    lineas.append("")

    # Un pequeño detalle resumido por estado
    agrupados = (
        pendientes.values("status")
        .annotate(cant=Sum("cargos"))
        .order_by("status")
    )

    estado_labels = dict(CartolaMovimiento.ESTADOS)
    for item in agrupados:
        status = item["status"]
        monto = item["cant"] or Decimal("0")
        label = estado_labels.get(status, status)
        lineas.append(f"- {label}: ${_formato_monto(monto)}")

    lineas.append("")
    lineas.append("Si quieres, puedes preguntarme por un gasto en particular indicando el proyecto o el monto aproximado.")

    return "\n".join(lineas)


def responder_mis_liquidaciones(usuario: CustomUser) -> str:
    """
    Devuelve info de la última liquidación disponible para el técnico.
    Más adelante podemos filtrar por mes/año usando NLP.
    """
    liqs = Liquidacion.objects.filter(tecnico=usuario).order_by("-año", "-mes")

    if not liqs.exists():
        return (
            "No encontré liquidaciones registradas a tu nombre todavía.\n"
            "Si crees que debería existir alguna, habla con RRHH para confirmarlo."
        )

    ultima = liqs.first()
    estado = "firmada ✅" if ultima.firmada else "pendiente de firma ✍️"

    lineas = [
        "🧾 *Tu liquidación más reciente*",
        "",
        f"- Mes/Año: {ultima.mes}/{ultima.año}",
        f"- Estado: {estado}",
    ]

    if ultima.archivo_pdf_liquidacion:
        lineas.append(f"- PDF sin firmar: {ultima.archivo_pdf_liquidacion.url}")

    if ultima.pdf_firmado:
        lineas.append(f"- PDF firmado: {ultima.pdf_firmado.url}")

    lineas.append("")
    lineas.append(
        "Si necesitas otra liquidación específica, dime por ejemplo:\n"
        "`liquidación de 07/2025`"
    )

    return "\n".join(lineas)


def responder_mis_contratos(usuario: CustomUser) -> str:
    """
    Lista contratos del técnico con su estado (vigente, por vencer, vencido, indefinido).
    """
    contratos = ContratoTrabajo.objects.filter(
        tecnico=usuario
    ).order_by("-fecha_inicio")

    if not contratos.exists():
        return (
            "No encontré contratos asociados a tu usuario en el sistema.\n"
            "Si ya tienes contrato firmado, avisa a RRHH para que lo suban."
        )

    lineas: list[str] = []
    lineas.append("📑 *Tus contratos de trabajo*")
    lineas.append("")

    for c in contratos:
        estado = c.status_label
        inicio = c.fecha_inicio.strftime("%d-%m-%Y")
        if c.fecha_termino:
            fin = c.fecha_termino.strftime("%d-%m-%Y")
        else:
            fin = "Indefinido"

        lineas.append(f"- {estado}: desde {inicio} hasta {fin}")
        if c.archivo:
            lineas.append(f"  📎 Archivo: {c.archivo.url}")

    lineas.append("")
    lineas.append(
        "Puedes pedirme por ejemplo: `mi contrato vigente` o `contratos vencidos` "
        "para filtrar mejor más adelante."
    )

    return "\n".join(lineas)


def responder_produccion_hasta_hoy(usuario: CustomUser) -> str:
    """
    Calcula la producción del técnico considerando servicios donde está asignado.
    Usa monto_mmoo si existe, si no monto_cotizado.
    """
    hoy = timezone.localdate()

    estados_incluir = [
        "asignado",
        "en_progreso",
        "en_revision_supervisor",
        "aprobado_supervisor",
    ]

    servicios = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado__in=estados_incluir,
        )
        .distinct()
        .order_by("mes_produccion", "du")
    )

    if not servicios.exists():
        return (
            "Por ahora no tienes producción asociada en servicios activos o aprobados.\n"
            "Cuando te asignen proyectos, te podré mostrar un resumen acá."
        )

    total = Decimal("0")
    aprobados = servicios.filter(estado="aprobado_supervisor")
    asignados_no_aprobados = servicios.exclude(estado="aprobado_supervisor")

    def monto_servicio(s: ServicioCotizado) -> Decimal:
        if s.monto_mmoo is not None:
            return s.monto_mmoo
        return s.monto_cotizado

    for s in servicios:
        total += monto_servicio(s)

    total_aprobados = sum((monto_servicio(s) for s in aprobados), Decimal("0"))
    total_no_aprobados = total - total_aprobados

    lineas: list[str] = []
    lineas.append("📊 *Producción estimada a la fecha*")
    lineas.append(f"Fecha de hoy: {hoy.strftime('%d-%m-%Y')}")
    lineas.append("")
    lineas.append(f"- Total servicios considerados: {servicios.count()}")
    lineas.append(f"- Aprobados por supervisor: {aprobados.count()}")
    lineas.append(f"- No aprobados aún: {asignados_no_aprobados.count()}")
    lineas.append("")
    lineas.append(f"💰 Total estimado: ${_formato_monto(total)}")
    lineas.append(f"   - Aprobado: ${_formato_monto(total_aprobados)}")
    lineas.append(f"   - Pendiente de aprobación: ${_formato_monto(total_no_aprobados)}")
    lineas.append("")
    lineas.append(
        "Esto es un estimado según las cotizaciones y montos de mano de obra.\n"
        "Más adelante podemos cruzarlo con los pagos mensuales consolidados."
    )

    return "\n".join(lineas)


def responder_proyectos_rechazados(usuario: CustomUser) -> str:
    """
    Lista proyectos donde el técnico tiene participación y están rechazados.
    """
    rechazados = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado="rechazado_supervisor",
        )
        .distinct()
        .order_by("-fecha_creacion")[:15]
    )

    if not rechazados.exists():
        return "No tienes proyectos rechazados por supervisor en el sistema ✅."

    lineas: list[str] = []
    lineas.append("❌ *Proyectos rechazados por supervisor*")
    lineas.append(f"Mostrando los últimos {rechazados.count()} registros.")
    lineas.append("")

    for s in rechazados:
        du = s.du or "Sin DU"
        id_claro = s.id_claro or "Sin ID Claro"
        motivo = (s.motivo_rechazo or "").strip() or "Sin motivo registrado"
        lineas.append(f"- DU {du} / ID Claro {id_claro}")
        lineas.append(f"  Motivo: {motivo[:180]}")
        lineas.append("")

    return "\n".join(lineas)


def responder_corte_produccion() -> str:
    """
    Usa CronogramaPago para responder "¿cuándo pagan?" o "¿cuál es el corte?".
    """
    cron = CronogramaPago.objects.first()
    if not cron:
        return (
            "Aún no hay un cronograma de pagos configurado en el sistema.\n"
            "Pide a Finanzas o RRHH que actualicen el cronograma para poder mostrarte las fechas."
        )

    hoy = timezone.localdate()
    mes = hoy.month

    # Mapear mes -> campos del cronograma
    campos = {
        1: ("enero_texto", "enero_fecha"),
        2: ("febrero_texto", "febrero_fecha"),
        3: ("marzo_texto", "marzo_fecha"),
        4: ("abril_texto", "abril_fecha"),
        5: ("mayo_texto", "mayo_fecha"),
        6: ("junio_texto", "junio_fecha"),
        7: ("julio_texto", "julio_fecha"),
        8: ("agosto_texto", "agosto_fecha"),
        9: ("septiembre_texto", "septiembre_fecha"),
        10: ("octubre_texto", "octubre_fecha"),
        11: ("noviembre_texto", "noviembre_fecha"),
        12: ("diciembre_texto", "diciembre_fecha"),
    }

    campo_txt, campo_fecha = campos[mes]
    texto = getattr(cron, campo_txt, None)
    fecha = getattr(cron, campo_fecha, None)

    nombre_meses = [
        "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    mes_nombre = nombre_meses[mes]

    if not texto and not fecha:
        return (
            f"Para el mes de {mes_nombre} aún no hay una fecha de pago configurada.\n"
            "Consulta con Finanzas o RRHH para que actualicen el cronograma."
        )

    lineas: list[str] = []
    lineas.append("💵 *Cronograma de pago / corte de producción*")
    lineas.append(f"Mes: {mes_nombre.capitalize()}")
    if fecha:
        lineas.append(f"- Fecha estimada de pago: {fecha.strftime('%d-%m-%Y')}")
    if texto:
        lineas.append(f"- Detalle: {texto}")
    lineas.append("")
    lineas.append(
        "Ten en cuenta que estas fechas pueden ajustarse según la operación o feriados."
    )

    return "\n".join(lineas)


def responder_info_sitio_por_codigo(codigo: str) -> str:
    """
    Busca un sitio por distintos campos (id_claro, id_sites, id_sites_new).
    """

    codigo = (codigo or "").strip()
    if not codigo:
        return "Necesito que me digas algún identificador del sitio (ID Claro, ID Sites, etc.)."

    sitio = (
        SitioMovil.objects.filter(
            Q(id_claro__iexact=codigo)
            | Q(id_sites__iexact=codigo)
            | Q(id_sites_new__iexact=codigo)
        )
        .order_by("id_sites")
        .first()
    )

    if not sitio:
        return (
            f"No encontré un sitio con código `{codigo}`.\n"
            "Prueba indicarme el ID Claro, el ID Sites o el ID Sites New exactamente como aparece en la ficha."
        )

    lineas: list[str] = []
    lineas.append("📍 *Información del sitio*")
    lineas.append("")
    lineas.append(f"- Nombre: {sitio.nombre or 'Sin nombre'}")
    lineas.append(f"- ID Sites: {sitio.id_sites}")
    lineas.append(f"- ID Claro: {sitio.id_claro or 'Sin ID Claro'}")
    lineas.append(f"- ID Sites New: {sitio.id_sites_new or 'Sin ID Sites New'}")
    lineas.append(f"- Región: {sitio.region or 'Sin región'}")
    lineas.append(f"- Dirección: {sitio.direccion or 'Sin dirección'}")

    # Seguridad / acceso
    if any(
        [
            sitio.candado_bt,
            sitio.condiciones_acceso,
            sitio.claves,
            sitio.llaves,
            sitio.cantidad_llaves,
            sitio.guardias,
        ]
    ):
        lineas.append("")
        lineas.append("🔐 *Accesos / seguridad*")
        if sitio.candado_bt:
            lineas.append(f"- Candado BT: {sitio.candado_bt}")
        if sitio.condiciones_acceso:
            lineas.append(f"- Condiciones de acceso: {sitio.condiciones_acceso}")
        if sitio.claves:
            lineas.append(f"- Claves: {sitio.claves}")
        if sitio.llaves:
            lineas.append(f"- Llaves: {sitio.llaves}")
        if sitio.cantidad_llaves:
            lineas.append(f"- Cantidad de llaves: {sitio.cantidad_llaves}")
        if sitio.guardias:
            lineas.append(f"- Guardias: {sitio.guardias}")

    # Link Google Maps
    if sitio.latitud is not None and sitio.longitud is not None:
        lat = str(sitio.latitud).replace(",", ".")
        lng = str(sitio.longitud).replace(",", ".")
        maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
        lineas.append("")
        lineas.append(f"🌐 Google Maps: {maps_url}")

    return "\n".join(lineas)


def responder_direccion_basura() -> str:
    """
    Lugar donde se bota la basura. Para no acoplarlo al código,
    lo leemos desde settings si existe.
    """
    url = getattr(settings, "BOT_GZ_URL_BASURA", "").strip()
    texto = getattr(settings, "BOT_GZ_TEXTO_BASURA", "").strip()

    if not url and not texto:
        return (
            "El lugar donde se botan los residuos aún no está configurado en el bot.\n"
            "Pide a administración que configure `BOT_GZ_URL_BASURA` o `BOT_GZ_TEXTO_BASURA` en settings."
        )

    lineas: list[str] = []
    lineas.append("🗑️ *Lugar definido para eliminación de residuos*")
    if texto:
        lineas.append(texto)
    if url:
        lineas.append("")
        lineas.append(f"📍 Ubicación en Google Maps: {url}")

    return "\n".join(lineas)


def responder_proyectos_aprobados_mes(usuario: CustomUser) -> str:
    """
    Proyectos aprobados por supervisor en el mes de producción actual.
    (Más adelante podemos aceptar 'del mes X' con NLP).
    """
    hoy = timezone.localdate()
    mes_actual = hoy.strftime("%Y-%m")  # depende de cómo guardes mes_produccion

    servicios = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado="aprobado_supervisor",
            mes_produccion__icontains=mes_actual.split("-")[1],  # simple, porque tienes texto
        )
        .distinct()
        .order_by("du")
    )

    if not servicios.exists():
        return (
            "No tienes proyectos aprobados por supervisor para el mes actual.\n"
            "Si los aprueban más adelante, te podré mostrar el detalle acá."
        )

    lineas: list[str] = []
    lineas.append("✅ *Proyectos aprobados por supervisor (mes actual)*")
    lineas.append(f"Cantidad de proyectos: {servicios.count()}")
    lineas.append("")

    for s in servicios[:25]:
        du = s.du or "Sin DU"
        id_claro = s.id_claro or "Sin ID Claro"
        monto = s.monto_mmoo or s.monto_cotizado
        lineas.append(f"- DU {du} / ID Claro {id_claro} / ${_formato_monto(monto)}")

    if servicios.count() > 25:
        lineas.append("")
        lineas.append("Mostrando solo los primeros 25 proyectos.")

    return "\n".join(lineas)


def responder_proyectos_pendientes_supervisor(usuario: CustomUser) -> str:
    """
    Proyectos del técnico en estado 'en_revision_supervisor'.
    """
    servicios = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado="en_revision_supervisor",
        )
        .distinct()
        .order_by("du")
    )

    if not servicios.exists():
        return "No tienes proyectos pendientes de aprobación del supervisor en este momento ✅."

    lineas: list[str] = []
    lineas.append("🟡 *Proyectos pendientes de aprobación del supervisor*")
    lineas.append(f"Cantidad de proyectos: {servicios.count()}")
    lineas.append("")

    for s in servicios[:25]:
        du = s.du or "Sin DU"
        id_claro = s.id_claro or "Sin ID Claro"
        detalle = (s.detalle_tarea or "").strip()
        if len(detalle) > 80:
            detalle = detalle[:77] + "..."
        lineas.append(f"- DU {du} / ID Claro {id_claro}")
        lineas.append(f"  {detalle}")
        lineas.append("")

    return "\n".join(lineas)


def responder_rendicion_por_bot_pendiente() -> str:
    """
    Placeholder para el flujo de rendición de gasto por el bot.
    Lo dejamos declarado para no romper el router del engine.
    """
    return (
        "El flujo de rendición de gastos por el bot todavía no está activo.\n"
        "Lo vamos a ir habilitando paso a paso para que puedas declarar tus gastos "
        "solo escribiendo en este chat 🧾."
    )
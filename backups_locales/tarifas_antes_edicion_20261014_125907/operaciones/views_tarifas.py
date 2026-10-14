from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from facturacion.models import Proyecto
from usuarios.decoradores import rol_requerido

from .models import (ActividadProyecto, ServicioCotizado,
                     TarifaActividadTecnico, TarifaTecnico)
from .services.tarifas import normalizar_mes_produccion

# ============================================================
# CONFIGURACIÓN
# ============================================================

INICIO_TARIFAS = "2026-10"


# ============================================================
# VALIDACIONES
# ============================================================


def validar_monto_tarifa(valor):
    """
    Valida el monto de una tarifa individual.

    Retorna Decimal cuando el monto es válido.
    """

    try:
        monto = Decimal(str(valor).strip())

        if not monto.is_finite():
            raise ValueError

        if monto <= 0:
            raise ValueError

        if monto.as_tuple().exponent < -2:
            raise ValueError

        if monto >= Decimal("10000000000"):
            raise ValueError

        return monto

    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Introduce un monto válido mayor que cero.")


# ============================================================
# OBTENER TRABAJADORES ACTIVOS
# ============================================================


def obtener_tecnicos_disponibles():
    """
    Obtiene trabajadores activos con rol usuario.

    El modelo de usuarios utiliza una relación
    ManyToMany llamada roles.

    No utiliza el campo inexistente 'rol'.
    """

    User = get_user_model()

    return (
        User.objects.filter(
            roles__nombre__iexact="usuario",
            is_active=True,
        )
        .distinct()
        .order_by(
            "first_name",
            "last_name",
            "username",
        )
    )


# ============================================================
# PROTECCIÓN PARA ELIMINACIÓN
# ============================================================


def tarifa_tiene_servicios_asignados(tarifa):
    """
    Verifica si el trabajador tiene servicios asignados
    desde el mes de vigencia de la tarifa.

    Si no es posible determinar el mes de un servicio,
    se bloquea la eliminación por seguridad.

    Esta función no modifica servicios ni pagos.
    """

    servicios = ServicioCotizado.objects.filter(
        trabajadores_asignados=tarifa.tecnico,
    ).only(
        "id",
        "mes_produccion",
    )

    for servicio in servicios:

        mes = normalizar_mes_produccion(servicio.mes_produccion)

        if mes is None:
            return True

        if mes >= tarifa.mes_vigencia:
            return True

    return False


# ============================================================
# VALIDAR DATOS DE ACTIVIDAD
# ============================================================


def validar_datos_actividad(request, tecnicos):
    """
    Valida todos los datos recibidos para crear o actualizar
    una actividad con tarifas individuales.

    No escribe información en la base de datos.

    Retorna un diccionario con los datos validados.
    """

    # --------------------------------------------------------
    # PROYECTO
    # --------------------------------------------------------

    proyecto_id = (request.POST.get("proyecto") or "").strip()

    if not proyecto_id.isdigit():
        raise ValueError("Selecciona un proyecto válido.")

    proyecto = Proyecto.objects.filter(pk=int(proyecto_id)).first()

    if proyecto is None:
        raise ValueError("El proyecto seleccionado no existe.")

    # --------------------------------------------------------
    # ACTIVIDAD
    # --------------------------------------------------------

    actividad_id = (request.POST.get("actividad_id") or "").strip()

    nombre_actividad = (request.POST.get("nombre_actividad") or "").strip()

    actividad_existente = None

    if actividad_id:

        if not actividad_id.isdigit():
            raise ValueError("La actividad seleccionada no es válida.")

        actividad_existente = ActividadProyecto.objects.filter(
            pk=int(actividad_id),
            proyecto=proyecto,
        ).first()

        if actividad_existente is None:
            raise ValueError("La actividad no pertenece al proyecto seleccionado.")

        if not actividad_existente.activa:
            raise ValueError("La actividad seleccionada está inactiva.")

        nombre_actividad = actividad_existente.nombre

    else:

        nombre_actividad = " ".join(nombre_actividad.split())

        if not nombre_actividad:
            raise ValueError("Introduce el nombre de la actividad.")

        if len(nombre_actividad) > 255:
            raise ValueError("El nombre de la actividad es demasiado largo.")

        actividad_duplicada = ActividadProyecto.objects.filter(
            proyecto=proyecto,
            nombre__iexact=nombre_actividad,
        ).first()

        if actividad_duplicada is not None:
            raise ValueError(
                "Esta actividad ya existe. "
                "Selecciónala para registrar nuevas tarifas."
            )

    # --------------------------------------------------------
    # PRECIO DE REFERENCIA
    # --------------------------------------------------------

    precio_referencia = validar_monto_tarifa(request.POST.get("precio_referencia"))

    # --------------------------------------------------------
    # MES DE VIGENCIA
    # --------------------------------------------------------

    mes = normalizar_mes_produccion(request.POST.get("mes_vigencia"))

    if mes is None or mes < INICIO_TARIFAS:
        raise ValueError("La vigencia debe ser octubre de 2026 o posterior.")

    # --------------------------------------------------------
    # TÉCNICOS SELECCIONADOS
    # --------------------------------------------------------

    tecnicos_ids = request.POST.getlist("tecnicos_seleccionados")

    if not tecnicos_ids:
        raise ValueError("Selecciona al menos un técnico.")

    ids_normalizados = []

    for tecnico_id in tecnicos_ids:

        tecnico_id = str(tecnico_id).strip()

        if not tecnico_id.isdigit():
            raise ValueError("Se recibió un identificador de técnico inválido.")

        ids_normalizados.append(int(tecnico_id))

    if len(ids_normalizados) != len(set(ids_normalizados)):
        raise ValueError("Hay trabajadores seleccionados más de una vez.")

    tecnicos_validos = list(tecnicos.filter(pk__in=ids_normalizados))

    if len(tecnicos_validos) != len(ids_normalizados):
        raise ValueError(
            "Uno o más trabajadores no están activos " "o no tienen el rol de técnico."
        )

    # --------------------------------------------------------
    # TARIFAS INDIVIDUALES
    # --------------------------------------------------------

    tarifas_individuales = []

    for tecnico in tecnicos_validos:

        campo_monto = f"monto_tecnico_{tecnico.pk}"

        monto_texto = (request.POST.get(campo_monto) or "").strip()

        if not monto_texto:
            raise ValueError(f"Falta la tarifa de {tecnico}.")

        try:
            monto = validar_monto_tarifa(monto_texto)

        except ValueError:
            raise ValueError(f"La tarifa de {tecnico} no es válida.")

        tarifas_individuales.append(
            {
                "tecnico": tecnico,
                "monto": monto,
            }
        )

    # --------------------------------------------------------
    # RESULTADO
    # --------------------------------------------------------

    return {
        "proyecto": proyecto,
        "actividad_existente": actividad_existente,
        "nombre_actividad": nombre_actividad,
        "precio_referencia": precio_referencia,
        "mes": mes,
        "tarifas_individuales": tarifas_individuales,
    }


# ============================================================
# GUARDAR ACTIVIDAD Y TARIFAS
# ============================================================


def guardar_actividad_con_tarifas(request, tecnicos):
    """
    Crea una actividad o utiliza una existente.

    Registra las tarifas individuales de todos los
    técnicos seleccionados en una sola transacción.

    Cada tarifa nueva crea un registro histórico.

    No modifica servicios, producción ni pagos.
    """

    datos = validar_datos_actividad(
        request,
        tecnicos,
    )

    with transaction.atomic():

        actividad = datos["actividad_existente"]

        # ----------------------------------------------------
        # CREAR ACTIVIDAD
        # ----------------------------------------------------

        if actividad is None:

            actividad = ActividadProyecto.objects.create(
                proyecto=datos["proyecto"],
                nombre=datos["nombre_actividad"],
                precio_referencia=datos["precio_referencia"],
                activa=True,
            )

        # ----------------------------------------------------
        # ACTUALIZAR PRECIO DE REFERENCIA
        # ----------------------------------------------------

        else:

            actividad = ActividadProyecto.objects.select_for_update().get(
                pk=actividad.pk
            )

            actividad.precio_referencia = datos["precio_referencia"]

            actividad.save(update_fields=["precio_referencia"])

        # ----------------------------------------------------
        # REGISTRAR TARIFAS INDIVIDUALES
        # ----------------------------------------------------

        for item in datos["tarifas_individuales"]:

            TarifaActividadTecnico.objects.create(
                actividad=actividad,
                tecnico=item["tecnico"],
                mes_vigencia=datos["mes"],
                monto=item["monto"],
                registrado_por=request.user,
            )

    return actividad, len(datos["tarifas_individuales"])


# ============================================================
# OBTENER ÚLTIMAS TARIFAS POR ACTIVIDAD Y TÉCNICO
# ============================================================


def obtener_tarifas_actividad_vigentes():
    """
    Obtiene la última tarifa registrada por cada
    combinación de actividad y técnico.

    Mantiene los registros históricos sin modificarlos.
    """

    ultimas = {}

    tarifas = TarifaActividadTecnico.objects.select_related(
        "actividad",
        "actividad__proyecto",
        "tecnico",
    ).order_by(
        "-mes_vigencia",
        "-fecha_registro",
        "-id",
    )

    for tarifa in tarifas:

        clave = (
            tarifa.actividad_id,
            tarifa.tecnico_id,
        )

        if clave not in ultimas:
            ultimas[clave] = tarifa

    return list(ultimas.values())


# ============================================================
# ADMINISTRACIÓN DE TARIFAS — ADMIN Y PM
# ============================================================


@login_required
@rol_requerido("admin", "pm")
def administrar_tarifas(request):
    """
    Pantalla principal de tarifas.

    Permite administrar dos sistemas independientes:

    1. Tarifas individuales anteriores.
    2. Tarifas por proyecto y actividad.

    No modifica producción ni pagos.
    """

    tecnicos = obtener_tecnicos_disponibles()

    # ========================================================
    # PROCESAR FORMULARIOS
    # ========================================================

    if request.method == "POST":

        tipo_formulario = (request.POST.get("tipo_formulario") or "").strip()

        # ====================================================
        # NUEVO SISTEMA: ACTIVIDADES
        # ====================================================

        if tipo_formulario == "actividad":

            try:

                actividad, cantidad = guardar_actividad_con_tarifas(
                    request,
                    tecnicos,
                )

            except ValueError as error:

                messages.error(
                    request,
                    str(error),
                )

            else:

                messages.success(
                    request,
                    (
                        f"Actividad '{actividad.nombre}' "
                        f"guardada correctamente. "
                        f"Se registraron {cantidad} "
                        f"tarifas individuales."
                    ),
                )

                return redirect("operaciones:administrar_tarifas")

        # ====================================================
        # SISTEMA ANTERIOR: TARIFA INDIVIDUAL
        # ====================================================

        elif tipo_formulario in (
            "",
            "tarifa_individual",
        ):

            tecnico_id = (request.POST.get("tecnico") or "").strip()

            mes = normalizar_mes_produccion(request.POST.get("mes_vigencia"))

            monto_texto = (request.POST.get("monto") or "").strip()

            tecnico = None

            if tecnico_id.isdigit():

                tecnico = tecnicos.filter(pk=int(tecnico_id)).first()

            # ------------------------------------------------
            # VALIDAR TRABAJADOR
            # ------------------------------------------------

            if tecnico is None:

                messages.error(
                    request,
                    "Selecciona un técnico válido.",
                )

            # ------------------------------------------------
            # VALIDAR VIGENCIA
            # ------------------------------------------------

            elif mes is None or mes < INICIO_TARIFAS:

                messages.error(
                    request,
                    ("La vigencia debe ser octubre " "de 2026 o posterior."),
                )

            # ------------------------------------------------
            # VALIDAR MONTO
            # ------------------------------------------------

            else:

                try:

                    monto = validar_monto_tarifa(monto_texto)

                except ValueError as error:

                    messages.error(
                        request,
                        str(error),
                    )

                # --------------------------------------------
                # REGISTRAR NUEVA TARIFA
                # --------------------------------------------

                else:

                    with transaction.atomic():

                        TarifaTecnico.objects.create(
                            tecnico=tecnico,
                            mes_vigencia=mes,
                            monto=monto,
                            registrado_por=request.user,
                        )

                    messages.success(
                        request,
                        (
                            f"Tarifa registrada para {tecnico}. "
                            f"Vigencia: {mes}. "
                            f"Monto: ${monto:,.0f}."
                        ),
                    )

                    return redirect("operaciones:administrar_tarifas")

        else:

            messages.error(
                request,
                "El formulario enviado no es válido.",
            )

    # ========================================================
    # PROYECTOS Y ACTIVIDADES
    # ========================================================

    proyectos = Proyecto.objects.all().order_by(
        "nombre",
        "id",
    )

    actividades = (
        ActividadProyecto.objects.select_related("proyecto")
        .filter(activa=True)
        .order_by(
            "proyecto__nombre",
            "nombre",
        )
    )

    # ========================================================
    # ÚLTIMAS TARIFAS DEL SISTEMA ANTERIOR
    # ========================================================

    ultimas = {}

    tarifas = TarifaTecnico.objects.select_related(
        "tecnico",
    ).order_by(
        "-mes_vigencia",
        "-fecha_registro",
        "-id",
    )

    for tarifa in tarifas:

        if tarifa.tecnico_id not in ultimas:

            ultimas[tarifa.tecnico_id] = tarifa

    tarifas_vigentes = list(ultimas.values())

    # ========================================================
    # HISTORIAL DEL SISTEMA ANTERIOR
    # ========================================================

    historial = TarifaTecnico.objects.select_related(
        "tecnico",
        "registrado_por",
    ).order_by("-fecha_registro", "-id",)[:100]

    # ========================================================
    # NUEVAS TARIFAS POR ACTIVIDAD
    # ========================================================

    tarifas_actividad_vigentes = obtener_tarifas_actividad_vigentes()

    historial_actividades = TarifaActividadTecnico.objects.select_related(
        "actividad",
        "actividad__proyecto",
        "tecnico",
        "registrado_por",
    ).order_by("-fecha_registro", "-id",)[:100]

    # ========================================================
    # RENDERIZAR PANTALLA
    # ========================================================

    return render(
        request,
        "operaciones/administrar_tarifas.html",
        {
            # Técnicos
            "tecnicos": tecnicos,
            # Proyectos y actividades
            "proyectos": proyectos,
            "actividades": actividades,
            # Tarifas anteriores
            "tarifas_vigentes": tarifas_vigentes,
            "historial": historial,
            # Tarifas por actividad
            "tarifas_actividad_vigentes": (tarifas_actividad_vigentes),
            "historial_actividades": (historial_actividades),
            # Mes sugerido
            "mes_actual_tarifas": max(
                INICIO_TARIFAS,
                timezone.localdate().strftime("%Y-%m"),
            ),
            # Permisos
            "es_admin_tarifas": (
                request.user.is_superuser
                or getattr(
                    request.user,
                    "es_admin_general",
                    False,
                )
            ),
        },
    )


# ============================================================
# ELIMINAR TARIFA — SOLO ADMIN
# ============================================================


@login_required
@rol_requerido("admin")
@require_POST
def eliminar_tarifa(request, pk):
    """
    Elimina una tarifa desde el modal de confirmación.

    Solo administradores.

    La eliminación:
    - Requiere POST.
    - Está protegida por CSRF.
    - Utiliza una transacción.
    - Bloquea tarifas relacionadas con servicios
      asignados desde su mes de vigencia.

    No modifica directamente producción ni pagos.
    """

    with transaction.atomic():

        # ----------------------------------------------------
        # BLOQUEAR REGISTRO DURANTE LA OPERACIÓN
        # ----------------------------------------------------

        tarifa = get_object_or_404(
            TarifaTecnico.objects.select_for_update().select_related(
                "tecnico",
                "registrado_por",
            ),
            pk=pk,
        )

        # ----------------------------------------------------
        # VERIFICAR SERVICIOS ASIGNADOS
        # ----------------------------------------------------

        if tarifa_tiene_servicios_asignados(tarifa):

            messages.error(
                request,
                (
                    "No se puede eliminar esta tarifa porque "
                    "el trabajador tiene servicios asignados "
                    "desde su mes de vigencia."
                ),
            )

            return redirect("operaciones:administrar_tarifas")

        # ----------------------------------------------------
        # ELIMINAR TARIFA
        # ----------------------------------------------------

        tecnico = str(tarifa.tecnico)

        mes = tarifa.mes_vigencia

        tarifa.delete()

    # ========================================================
    # CONFIRMACIÓN
    # ========================================================

    messages.success(
        request,
        (
            "Tarifa eliminada correctamente. "
            f"Trabajador: {tecnico}. "
            f"Vigencia: {mes}."
        ),
    )

    return redirect("operaciones:administrar_tarifas")

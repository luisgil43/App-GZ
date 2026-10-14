from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
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
    Valida un monto positivo con un máximo de dos decimales.
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


def validar_mes_tarifa(valor):
    """
    Valida un mes de vigencia YYYY-MM.
    """

    mes = normalizar_mes_produccion(valor)

    if mes is None or mes < INICIO_TARIFAS:
        raise ValueError("La vigencia debe ser octubre de 2026 o posterior.")

    return mes


def validar_identificador(valor, mensaje):
    """
    Valida un identificador positivo.
    """

    identificador = str(valor or "").strip()

    if not identificador.isdigit():
        raise ValueError(mensaje)

    identificador = int(identificador)

    if identificador <= 0:
        raise ValueError(mensaje)

    return identificador


def normalizar_nombre_actividad(valor):
    """
    Elimina espacios sobrantes del nombre.
    """

    nombre = " ".join(str(valor or "").split())

    if not nombre:
        raise ValueError("Introduce el nombre de la actividad.")

    if len(nombre) > 255:
        raise ValueError("El nombre de la actividad es demasiado largo.")

    return nombre


def usuario_es_admin_tarifas(usuario):
    """
    Comprueba si el usuario tiene permisos de administrador.

    Se utiliza únicamente para mostrar controles en la UI.
    La protección real de eliminación permanece en la vista.
    """

    if usuario.is_superuser:
        return True

    if getattr(usuario, "es_admin_general", False):
        return True

    return usuario.roles.filter(nombre__iexact="admin").exists()


# ============================================================
# TRABAJADORES ACTIVOS
# ============================================================


def obtener_tecnicos_disponibles():
    """
    Trabajadores activos con rol usuario.
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
# PROTECCIÓN DE TARIFAS ANTERIORES
# ============================================================


def tarifa_tiene_servicios_asignados(tarifa):
    """
    Comprueba si existen servicios del trabajador desde
    la vigencia de una tarifa del sistema anterior.

    Se conserva para mantener compatibilidad con
    eliminar_tarifa().
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
# VALIDACIÓN DE ACTIVIDADES
# ============================================================


def validar_datos_actividad(request, tecnicos):
    """
    Valida los datos del formulario principal.

    No escribe información en la base de datos.
    """

    proyecto_id = validar_identificador(
        request.POST.get("proyecto"),
        "Selecciona un proyecto válido.",
    )

    proyecto = Proyecto.objects.filter(pk=proyecto_id).first()

    if proyecto is None:
        raise ValueError("El proyecto seleccionado no existe.")

    actividad_id = (request.POST.get("actividad_id") or "").strip()

    nombre_actividad = (request.POST.get("nombre_actividad") or "").strip()

    actividad_existente = None

    if actividad_id:

        actividad_id = validar_identificador(
            actividad_id,
            "La actividad seleccionada no es válida.",
        )

        actividad_existente = ActividadProyecto.objects.filter(
            pk=actividad_id,
            proyecto=proyecto,
        ).first()

        if actividad_existente is None:
            raise ValueError("La actividad no pertenece al proyecto seleccionado.")

        if not actividad_existente.activa:
            raise ValueError("La actividad seleccionada está inactiva.")

        nombre_actividad = actividad_existente.nombre

    else:

        nombre_actividad = normalizar_nombre_actividad(nombre_actividad)

        duplicada = ActividadProyecto.objects.filter(
            proyecto=proyecto,
            nombre__iexact=nombre_actividad,
        ).exists()

        if duplicada:
            raise ValueError(
                "Esta actividad ya existe. "
                "Selecciónala para registrar nuevas tarifas."
            )

    precio_referencia = validar_monto_tarifa(request.POST.get("precio_referencia"))

    mes = validar_mes_tarifa(request.POST.get("mes_vigencia"))

    tecnicos_ids = request.POST.getlist("tecnicos_seleccionados")

    if not tecnicos_ids:
        raise ValueError("Selecciona al menos un técnico.")

    ids_normalizados = []

    for tecnico_id in tecnicos_ids:

        identificador = validar_identificador(
            tecnico_id,
            "Se recibió un identificador de técnico inválido.",
        )

        ids_normalizados.append(identificador)

    if len(ids_normalizados) != len(set(ids_normalizados)):
        raise ValueError("Hay trabajadores seleccionados más de una vez.")

    tecnicos_validos = list(tecnicos.filter(pk__in=ids_normalizados))

    if len(tecnicos_validos) != len(ids_normalizados):
        raise ValueError(
            "Uno o más trabajadores no están activos " "o no tienen el rol requerido."
        )

    tarifas_individuales = []

    for tecnico in tecnicos_validos:

        campo = f"monto_tecnico_{tecnico.pk}"

        monto_texto = (request.POST.get(campo) or "").strip()

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

    return {
        "proyecto": proyecto,
        "actividad_existente": actividad_existente,
        "nombre_actividad": nombre_actividad,
        "precio_referencia": precio_referencia,
        "mes": mes,
        "tarifas_individuales": tarifas_individuales,
    }


# ============================================================
# CREAR ACTIVIDAD Y TARIFAS
# ============================================================


def guardar_actividad_con_tarifas(request, tecnicos):
    """
    Crea o configura una actividad.

    Las modificaciones de tarifas crean registros nuevos.
    No modifica servicios ni pagos.
    """

    datos = validar_datos_actividad(
        request,
        tecnicos,
    )

    with transaction.atomic():

        actividad = datos["actividad_existente"]

        if actividad is None:

            actividad = ActividadProyecto.objects.create(
                proyecto=datos["proyecto"],
                nombre=datos["nombre_actividad"],
                precio_referencia=datos["precio_referencia"],
                activa=True,
            )

        else:

            actividad = ActividadProyecto.objects.select_for_update().get(
                pk=actividad.pk
            )

            if not actividad.activa:
                raise ValueError("La actividad seleccionada está inactiva.")

            if actividad.precio_referencia != datos["precio_referencia"]:

                actividad.precio_referencia = datos["precio_referencia"]

                actividad.save(
                    update_fields=[
                        "precio_referencia",
                    ]
                )

        cantidad_registrada = 0
        cantidad_sin_cambios = 0

        for item in datos["tarifas_individuales"]:

            ultima_tarifa = (
                TarifaActividadTecnico.objects.filter(
                    actividad=actividad,
                    tecnico=item["tecnico"],
                )
                .order_by(
                    "-mes_vigencia",
                    "-fecha_registro",
                    "-id",
                )
                .first()
            )

            if (
                ultima_tarifa is not None
                and ultima_tarifa.mes_vigencia == datos["mes"]
                and ultima_tarifa.monto == item["monto"]
            ):

                cantidad_sin_cambios += 1
                continue

            TarifaActividadTecnico.objects.create(
                actividad=actividad,
                tecnico=item["tecnico"],
                mes_vigencia=datos["mes"],
                monto=item["monto"],
                registrado_por=request.user,
            )

            cantidad_registrada += 1

    return (
        actividad,
        cantidad_registrada,
        cantidad_sin_cambios,
    )


# ============================================================
# EDICIÓN CONJUNTA DE ACTIVIDAD Y TARIFA
# ============================================================


def editar_tarifa_actividad(request, tecnicos):
    """
    Edita en una misma operación:

    - Nombre de la actividad.
    - Monto individual.
    - Mes de vigencia.

    El cambio de nombre afecta a la actividad compartida
    por todos los trabajadores asociados.

    El cambio de tarifa crea un registro histórico nuevo.

    No modifica servicios, montos aprobados ni pagos.

    PROTECCIÓN:
    No permite editar tarifas desactivadas.
    """

    tarifa_id = validar_identificador(
        (request.POST.get("tarifa_id") or request.POST.get("tarifa_actividad_id")),
        "Selecciona una tarifa válida para editar.",
    )

    monto = validar_monto_tarifa(
        (request.POST.get("monto") or request.POST.get("nuevo_monto"))
    )

    mes_texto = (request.POST.get("mes_vigencia") or "").strip()

    nombre_texto = request.POST.get("nuevo_nombre")

    if nombre_texto is not None:
        nuevo_nombre = normalizar_nombre_actividad(nombre_texto)
    else:
        nuevo_nombre = None

    # ============================================================
    # TRANSACCIÓN
    # ============================================================

    with transaction.atomic():

        tarifa_original = get_object_or_404(
            TarifaActividadTecnico.objects.select_related(
                "actividad",
                "tecnico",
            ),
            pk=tarifa_id,
        )

        actividad = ActividadProyecto.objects.select_for_update().get(
            pk=tarifa_original.actividad_id
        )

        # ========================================================
        # VALIDAR ACTIVIDAD
        # ========================================================

        if not actividad.activa:
            raise ValueError("No se puede editar una actividad inactiva.")

        # ========================================================
        # VALIDAR TRABAJADOR
        # ========================================================

        if not tecnicos.filter(pk=tarifa_original.tecnico_id).exists():

            raise ValueError(
                "El trabajador no está activo " "o no tiene el rol requerido."
            )

        # ========================================================
        # OBTENER ÚLTIMA TARIFA
        # ========================================================

        ultima_tarifa = (
            TarifaActividadTecnico.objects.filter(
                actividad=actividad,
                tecnico_id=tarifa_original.tecnico_id,
            )
            .order_by(
                "-mes_vigencia",
                "-fecha_registro",
                "-id",
            )
            .first()
        )

        if ultima_tarifa is None:
            raise ValueError("No se encontró la tarifa del trabajador.")

        # ========================================================
        # PROTECCIÓN CONTRA TARIFAS DESACTIVADAS
        # ========================================================

        if not ultima_tarifa.activa:
            raise ValueError(
                "Esta tarifa está desactivada. " "Debes activarla antes de modificarla."
            )

        # ========================================================
        # VALIDAR QUE SEA LA TARIFA MÁS RECIENTE
        # ========================================================

        if ultima_tarifa.pk != tarifa_original.pk:
            raise ValueError(
                "Esta tarifa ya fue modificada. "
                "Actualiza la página antes de editar nuevamente."
            )

        # ========================================================
        # VALIDAR VIGENCIA
        # ========================================================

        mes = validar_mes_tarifa(mes_texto or ultima_tarifa.mes_vigencia)

        # ========================================================
        # CAMBIO DE NOMBRE
        # ========================================================

        cambio_nombre = False

        if nuevo_nombre is not None:

            if actividad.nombre != nuevo_nombre:

                duplicada = (
                    ActividadProyecto.objects.filter(
                        proyecto=actividad.proyecto,
                        nombre__iexact=nuevo_nombre,
                    )
                    .exclude(pk=actividad.pk)
                    .exists()
                )

                if duplicada:
                    raise ValueError(
                        "Ya existe otra actividad con ese nombre "
                        "dentro del mismo proyecto."
                    )

                actividad.nombre = nuevo_nombre

                actividad.save(
                    update_fields=[
                        "nombre",
                    ]
                )

                cambio_nombre = True

        # ========================================================
        # DETECTAR CAMBIOS DE TARIFA
        # ========================================================

        cambio_tarifa = (
            ultima_tarifa.monto != monto or ultima_tarifa.mes_vigencia != mes
        )

        # ========================================================
        # REGISTRAR NUEVA TARIFA SIN SOBRESCRIBIR HISTORIAL
        # ========================================================

        if cambio_tarifa:

            nueva_tarifa = TarifaActividadTecnico.objects.create(
                actividad=actividad,
                tecnico=ultima_tarifa.tecnico,
                mes_vigencia=mes,
                monto=monto,
                activa=True,
                registrado_por=request.user,
            )

        else:

            nueva_tarifa = ultima_tarifa

    # ============================================================
    # RESULTADO
    # ============================================================

    return {
        "tarifa": nueva_tarifa,
        "cambio_nombre": cambio_nombre,
        "cambio_tarifa": cambio_tarifa,
    }


# ============================================================
# EDITAR NOMBRE DE ACTIVIDAD — COMPATIBILIDAD
# ============================================================


def editar_nombre_actividad(request):
    """
    Conserva compatibilidad con formularios anteriores.
    """

    actividad_id = validar_identificador(
        request.POST.get("actividad_id"),
        "Selecciona una actividad válida.",
    )

    nuevo_nombre = normalizar_nombre_actividad(
        (request.POST.get("nombre_actividad") or request.POST.get("nuevo_nombre"))
    )

    with transaction.atomic():

        actividad = get_object_or_404(
            ActividadProyecto.objects.select_for_update(),
            pk=actividad_id,
        )

        if not actividad.activa:
            raise ValueError("No se puede editar una actividad inactiva.")

        duplicada = (
            ActividadProyecto.objects.filter(
                proyecto=actividad.proyecto,
                nombre__iexact=nuevo_nombre,
            )
            .exclude(pk=actividad.pk)
            .exists()
        )

        if duplicada:
            raise ValueError(
                "Ya existe otra actividad con ese nombre " "dentro del mismo proyecto."
            )

        if actividad.nombre == nuevo_nombre:
            return actividad, False

        actividad.nombre = nuevo_nombre

        actividad.save(update_fields=["nombre"])

    return actividad, True


# ============================================================
# TARIFAS REGISTRADAS
# ============================================================


def obtener_tarifas_actividad_vigentes():
    """
    Obtiene el registro más reciente por actividad y técnico.

    IMPORTANTE:
    No se filtra por activa=True antes de seleccionar
    la última tarifa.

    Así evitamos recuperar automáticamente una tarifa
    histórica cuando la última está desactivada.
    """

    ultimas = {}

    tarifas = (
        TarifaActividadTecnico.objects.select_related(
            "actividad",
            "actividad__proyecto",
            "tecnico",
        )
        .filter(
            actividad__activa=True,
        )
        .order_by(
            "-mes_vigencia",
            "-fecha_registro",
            "-id",
        )
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
# HISTORIAL CON VALORES ANTERIORES
# ============================================================


def obtener_historial_cambios_actividad():
    """
    Construye una presentación del historial existente.

    No crea ni modifica registros.

    Compara cada registro con el anterior de la misma
    actividad y trabajador, siguiendo la secuencia
    de registro.

    Nota:
    El modelo actual no almacena nombres anteriores
    de actividades, por lo que esos cambios no pueden
    reconstruirse retroactivamente.
    """

    registros = list(
        TarifaActividadTecnico.objects.select_related(
            "actividad",
            "actividad__proyecto",
            "tecnico",
            "registrado_por",
        ).order_by(
            "fecha_registro",
            "id",
        )
    )

    anteriores = {}
    historial = []

    for tarifa in registros:

        clave = (
            tarifa.actividad_id,
            tarifa.tecnico_id,
        )

        anterior = anteriores.get(clave)

        if anterior is None:

            tipo = "Inicial"

        elif (
            anterior.monto != tarifa.monto
            and anterior.mes_vigencia != tarifa.mes_vigencia
        ):

            tipo = "Monto y vigencia"

        elif anterior.monto != tarifa.monto:

            tipo = "Cambio de monto"

        elif anterior.mes_vigencia != tarifa.mes_vigencia:

            tipo = "Cambio de vigencia"

        else:

            tipo = "Registro"

        historial.append(
            {
                "tarifa": tarifa,
                "anterior": anterior,
                "tipo": tipo,
            }
        )

        anteriores[clave] = tarifa

    historial.reverse()

    return historial[:100]


# ============================================================
# ADMINISTRACIÓN DE TARIFAS
# ============================================================


@login_required
@rol_requerido("admin", "pm")
def administrar_tarifas(request):
    """
    Pantalla principal de administración.

    Administradores y PM pueden configurar y editar.
    La eliminación permanece restringida a admin.
    """

    tecnicos = obtener_tecnicos_disponibles()

    if request.method == "POST":

        tipo_formulario = (request.POST.get("tipo_formulario") or "").strip()

        # ----------------------------------------------------
        # CREAR O CONFIGURAR ACTIVIDAD
        # ----------------------------------------------------

        if tipo_formulario == "actividad":

            try:

                (
                    actividad,
                    cantidad,
                    sin_cambios,
                ) = guardar_actividad_con_tarifas(
                    request,
                    tecnicos,
                )

            except (ValueError, IntegrityError) as error:

                if isinstance(error, IntegrityError):

                    messages.error(
                        request,
                        "No se pudo guardar la actividad. "
                        "Verifica que no exista otra actividad "
                        "con el mismo nombre en el proyecto.",
                    )

                else:

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
                        f"Tarifas registradas: {cantidad}. "
                        f"Sin cambios: {sin_cambios}."
                    ),
                )

                return redirect("operaciones:administrar_tarifas")

        # ----------------------------------------------------
        # EDITAR ACTIVIDAD Y TARIFA
        # ----------------------------------------------------

        elif tipo_formulario in (
            "editar_tarifa_actividad",
            "editar_tarifa",
        ):

            try:

                resultado = editar_tarifa_actividad(
                    request,
                    tecnicos,
                )

            except (ValueError, IntegrityError) as error:

                if isinstance(error, IntegrityError):

                    messages.error(
                        request,
                        "No se pudo guardar la edición. "
                        "Comprueba que el nombre de la actividad "
                        "no esté duplicado.",
                    )

                else:

                    messages.error(
                        request,
                        str(error),
                    )

            else:

                tarifa = resultado["tarifa"]

                cambio_nombre = resultado["cambio_nombre"]

                cambio_tarifa = resultado["cambio_tarifa"]

                if cambio_nombre or cambio_tarifa:

                    detalles = []

                    if cambio_nombre:
                        detalles.append("nombre de actividad")

                    if cambio_tarifa:
                        detalles.append("tarifa y/o vigencia")

                    messages.success(
                        request,
                        (
                            "Cambios guardados correctamente: "
                            + ", ".join(detalles)
                            + ". "
                            + f"Trabajador: {tarifa.tecnico}. "
                            + f"Tarifa: ${tarifa.monto:,.0f}. "
                            + f"Vigencia: {tarifa.mes_vigencia}."
                        ),
                    )

                else:

                    messages.info(
                        request,
                        "No se detectaron cambios.",
                    )

                return redirect("operaciones:administrar_tarifas")

        # ----------------------------------------------------
        # EDICIÓN DE NOMBRE ANTERIOR
        # ----------------------------------------------------

        elif tipo_formulario in (
            "editar_actividad",
            "editar_nombre_actividad",
        ):

            try:

                (
                    actividad,
                    modificada,
                ) = editar_nombre_actividad(request)

            except (ValueError, IntegrityError) as error:

                messages.error(
                    request,
                    str(error),
                )

            else:

                if modificada:

                    messages.success(
                        request,
                        ("Nombre de actividad actualizado: " f"{actividad.nombre}."),
                    )

                else:

                    messages.info(
                        request,
                        "El nombre no presentó cambios.",
                    )

                return redirect("operaciones:administrar_tarifas")

        # ----------------------------------------------------
        # TARIFA INDIVIDUAL DEL SISTEMA ANTERIOR
        # ----------------------------------------------------

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

            if tecnico is None:

                messages.error(
                    request,
                    "Selecciona un técnico válido.",
                )

            elif mes is None or mes < INICIO_TARIFAS:

                messages.error(
                    request,
                    "La vigencia debe ser octubre " "de 2026 o posterior.",
                )

            else:

                try:

                    monto = validar_monto_tarifa(monto_texto)

                except ValueError as error:

                    messages.error(
                        request,
                        str(error),
                    )

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
    # DATOS PARA LA PANTALLA
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

    # --------------------------------------------------------
    # TARIFAS ANTERIORES
    # --------------------------------------------------------

    ultimas = {}

    tarifas = TarifaTecnico.objects.select_related("tecnico").order_by(
        "-mes_vigencia",
        "-fecha_registro",
        "-id",
    )

    for tarifa in tarifas:

        if tarifa.tecnico_id not in ultimas:
            ultimas[tarifa.tecnico_id] = tarifa

    tarifas_vigentes = list(ultimas.values())

    historial = TarifaTecnico.objects.select_related(
        "tecnico",
        "registrado_por",
    ).order_by("-fecha_registro", "-id",)[:100]

    # --------------------------------------------------------
    # TARIFAS POR ACTIVIDAD
    # --------------------------------------------------------

    tarifas_actividad_vigentes = obtener_tarifas_actividad_vigentes()

    historial_actividades = TarifaActividadTecnico.objects.select_related(
        "actividad",
        "actividad__proyecto",
        "tecnico",
        "registrado_por",
    ).order_by("-fecha_registro", "-id",)[:100]

    historial_cambios = obtener_historial_cambios_actividad()

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "operaciones/administrar_tarifas.html",
        {
            "tecnicos": tecnicos,
            "proyectos": proyectos,
            "actividades": actividades,
            "tarifas_vigentes": tarifas_vigentes,
            "historial": historial,
            "tarifas_actividad_vigentes": (tarifas_actividad_vigentes),
            "historial_actividades": (historial_actividades),
            "historial_cambios": historial_cambios,
            "mes_actual_tarifas": max(
                INICIO_TARIFAS,
                timezone.localdate().strftime("%Y-%m"),
            ),
            "es_admin_tarifas": (usuario_es_admin_tarifas(request.user)),
        },
    )


# ============================================================
# ELIMINAR TARIFA DEL SISTEMA ANTERIOR
# ============================================================


@login_required
@rol_requerido("admin")
@require_POST
def eliminar_tarifa(request, pk):
    """
    Conserva el comportamiento anterior.

    Solo administradores.
    """

    with transaction.atomic():

        tarifa = get_object_or_404(
            TarifaTecnico.objects.select_for_update().select_related(
                "tecnico",
                "registrado_por",
            ),
            pk=pk,
        )

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

        tecnico = str(tarifa.tecnico)

        mes = tarifa.mes_vigencia

        tarifa.delete()

    messages.success(
        request,
        (
            "Tarifa eliminada correctamente. "
            f"Trabajador: {tecnico}. "
            f"Vigencia: {mes}."
        ),
    )

    return redirect("operaciones:administrar_tarifas")


# ============================================================
# ELIMINAR TARIFA POR ACTIVIDAD
# ============================================================


@login_required
@rol_requerido("admin")
@require_POST
def eliminar_tarifa_actividad(request, pk):
    """
    Endpoint protegido para eliminación de tarifas por actividad.

    No elimina registros históricos.

    La eliminación real requiere implementar primero
    un mecanismo de desactivación que el resolvedor
    de tarifas de producción pueda reconocer.
    """

    tarifa = get_object_or_404(
        TarifaActividadTecnico.objects.select_related(
            "actividad",
            "tecnico",
        ),
        pk=pk,
    )

    messages.error(
        request,
        (
            "No se eliminó la tarifa de "
            f"{tarifa.tecnico}. "
            "La eliminación por actividad permanece "
            "bloqueada para proteger los registros "
            "históricos de producción y pagos."
        ),
    )

    return redirect("operaciones:administrar_tarifas")

# ============================================================
# ACTIVAR / DESACTIVAR TARIFA POR ACTIVIDAD
# ============================================================


@login_required
@rol_requerido("admin")
@require_POST
def cambiar_estado_tarifa_actividad(request, pk):
    """
    Activa o desactiva una tarifa individual.

    No elimina registros históricos.
    No modifica montos de servicios ni pagos anteriores.
    """

    accion = (request.POST.get("accion") or "").strip()

    if accion not in ("activar", "desactivar"):
        messages.error(
            request,
            "La acción solicitada no es válida.",
        )
        return redirect("operaciones:administrar_tarifas")

    with transaction.atomic():

        tarifa = get_object_or_404(
            TarifaActividadTecnico.objects.select_for_update().select_related(
                "actividad", "tecnico"
            ),
            pk=pk,
        )

        if accion == "activar":

            if not tarifa.actividad.activa:
                messages.error(
                    request,
                    "No se puede activar una tarifa " "de una actividad inactiva.",
                )
                return redirect("operaciones:administrar_tarifas")

            if tarifa.activa:
                messages.info(
                    request,
                    "La tarifa ya se encuentra activa.",
                )
                return redirect("operaciones:administrar_tarifas")

            tarifa.activa = True
            tarifa.fecha_desactivacion = None
            tarifa.desactivada_por = None

            tarifa.save(
                update_fields=[
                    "activa",
                    "fecha_desactivacion",
                    "desactivada_por",
                ]
            )

            messages.success(
                request,
                "Tarifa activada correctamente.",
            )

        else:

            if not tarifa.activa:
                messages.info(
                    request,
                    "La tarifa ya se encuentra desactivada.",
                )
                return redirect("operaciones:administrar_tarifas")

            tarifa.activa = False
            tarifa.fecha_desactivacion = timezone.now()
            tarifa.desactivada_por = request.user

            tarifa.save(
                update_fields=[
                    "activa",
                    "fecha_desactivacion",
                    "desactivada_por",
                ]
            )

            messages.success(
                request,
                "Tarifa desactivada correctamente. "
                "Los registros históricos permanecen intactos.",
            )

    return redirect("operaciones:administrar_tarifas")

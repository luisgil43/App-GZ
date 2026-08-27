# operaciones/views.py

import calendar
import csv
import io
import json
import locale
import logging
import re
import unicodedata
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import requests
import xlwt
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import (Case, Count, F, FloatField, IntegerField, Q, Sum,
                              Value, When)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.html import escape
from django.utils.timezone import is_aware, now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from facturacion.models import CartolaMovimiento
from notificaciones.services import notificar_asignacion_servicio_tecnicos
from operaciones.forms import AsignarTrabajadoresForm
from operaciones.models import SitioMovil, SitiosTablaMetadata
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser
from usuarios.utils import \
    crear_notificacion  # asegúrate de tener esta función

from .forms import MovimientoUsuarioForm  # crearemos este form
from .forms import (ServicioCotizadoForm, SitioMovilForm, validar_rut_chileno,
                    verificar_rut_sii)
from .models import (RequisitoFoto, ServicioCotizado, SesionFotos,
                     SesionFotoTecnico, SitioMovil)
from .views_fotos import _get_or_create_sesion, _norm_title

# Configurar locale para nombres de meses en español
try:
    locale.setlocale(locale.LC_TIME, 'es_CL.utf8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_TIME, 'es_ES.utf8')
    except locale.Error:
        locale.setlocale(locale.LC_TIME, '')  # Usa el del sistema

from django.db import transaction
from django.utils import timezone

from .models import RequisitoFoto, SesionFotoTecnico


def _reset_asignacion_tecnico(asignacion):
    """
    Devuelve la asignación individual del técnico a estado 'asignado'
    para forzar nueva aceptación.
    """
    asignacion.estado = 'asignado'

    update_fields = ['estado']

    if hasattr(asignacion, 'aceptado_en'):
        asignacion.aceptado_en = None
        update_fields.append('aceptado_en')

    if hasattr(asignacion, 'finalizado_en'):
        asignacion.finalizado_en = None
        update_fields.append('finalizado_en')

    if hasattr(asignacion, 'reintento_habilitado'):
        asignacion.reintento_habilitado = False
        update_fields.append('reintento_habilitado')

    asignacion.save(update_fields=update_fields)


def _clonar_requisitos_a_asignacion(origen_asignacion, destino_asignacion):
    """
    Copia requisitos activos desde una asignación existente hacia una nueva,
    evitando duplicados por la constraint (tecnico_sesion + titulo_norm + activo).
    """
    if not origen_asignacion or not destino_asignacion:
        return

    requisitos = (
        RequisitoFoto.objects
        .filter(tecnico_sesion=origen_asignacion, activo=True)
        .order_by('orden', 'id')
    )

    for req in requisitos:
        RequisitoFoto.objects.get_or_create(
            tecnico_sesion=destino_asignacion,
            titulo=req.titulo,
            defaults={
                'descripcion': req.descripcion,
                'obligatorio': req.obligatorio,
                'orden': req.orden,
                'activo': req.activo,
            }
        )

    def _calcular_resumen_saldos(movimientos_qs):
        """
        Calcula los indicadores financieros mostrados al usuario.

        Reglas:
        - Saldo disponible:
          histórico acumulado de abonos aprobados menos gastos aprobados
          por Finanzas.

        - Rendido del mes:
          gastos aprobados por Finanzas cuya fecha real del gasto pertenece
          al mes actual.

        - Pendiente del mes:
          gastos del mes que todavía esperan aprobación del Supervisor,
          PM o Finanzas.

        - Rechazado del mes:
          gastos del mes rechazados por Supervisor, PM o Finanzas.

        - Abonos pendientes:
          abonos que todavía esperan aceptación del usuario.

        Para determinar el mes se usa fecha_transaccion.
        Si fecha_transaccion está vacía, se utiliza fecha como respaldo.
        """
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)

        if inicio_mes.month == 12:
            inicio_mes_siguiente = inicio_mes.replace(
                year=inicio_mes.year + 1,
                month=1,
            )
        else:
            inicio_mes_siguiente = inicio_mes.replace(
                month=inicio_mes.month + 1,
            )

        meses_es = {
            1: "enero",
            2: "febrero",
            3: "marzo",
            4: "abril",
            5: "mayo",
            6: "junio",
            7: "julio",
            8: "agosto",
            9: "septiembre",
            10: "octubre",
            11: "noviembre",
            12: "diciembre",
        }

        resumen_mes = f"{meses_es[inicio_mes.month].capitalize()} " f"{inicio_mes.year}"

        # Utiliza la fecha real del gasto.
        # Para registros antiguos sin fecha_transaccion, usa fecha.
        filtro_mes_actual = Q(
            fecha_transaccion__gte=inicio_mes,
            fecha_transaccion__lt=inicio_mes_siguiente,
        ) | Q(
            fecha_transaccion__isnull=True,
            fecha__date__gte=inicio_mes,
            fecha__date__lt=inicio_mes_siguiente,
        )

        gastos = movimientos_qs.exclude(tipo__categoria="abono")
        abonos = movimientos_qs.filter(tipo__categoria="abono")

        # ========================================================
        # Saldo histórico actualmente disponible
        # ========================================================

        total_abonos_aprobados = (
            abonos.filter(status="aprobado_abono_usuario").aggregate(
                total=Sum("abonos")
            )["total"]
            or 0
        )

        total_gastos_aprobados = (
            gastos.filter(status="aprobado_finanzas").aggregate(total=Sum("cargos"))[
                "total"
            ]
            or 0
        )

        saldo_disponible = total_abonos_aprobados - total_gastos_aprobados

        # ========================================================
        # Rendiciones del mes aprobadas por Finanzas
        # ========================================================

        saldo_rendido_mes = (
            gastos.filter(
                filtro_mes_actual,
                status="aprobado_finanzas",
            ).aggregate(
                total=Sum("cargos")
            )["total"]
            or 0
        )

        # ========================================================
        # Rendiciones del mes pendientes de aprobación
        # ========================================================

        estados_pendientes = [
            "pendiente_supervisor",
            "aprobado_supervisor",
            "aprobado_pm",
        ]

        saldo_pendiente_mes = (
            gastos.filter(
                filtro_mes_actual,
                status__in=estados_pendientes,
            ).aggregate(
                total=Sum("cargos")
            )["total"]
            or 0
        )

        # ========================================================
        # Rendiciones del mes rechazadas
        # ========================================================

        estados_rechazados = [
            "rechazado_supervisor",
            "rechazado_pm",
            "rechazado_finanzas",
        ]

        saldo_rechazado_mes = (
            gastos.filter(
                filtro_mes_actual,
                status__in=estados_rechazados,
            ).aggregate(
                total=Sum("cargos")
            )["total"]
            or 0
        )

        # ========================================================
        # Abonos que el usuario todavía no ha aceptado
        # ========================================================

        abonos_pendientes = (
            abonos.filter(status="pendiente_abono_usuario").aggregate(
                total=Sum("abonos")
            )["total"]
            or 0
        )

        return {
            "saldo_disponible": saldo_disponible,
            "saldo_rendido_mes": saldo_rendido_mes,
            "saldo_pendiente_mes": saldo_pendiente_mes,
            "saldo_rechazado_mes": saldo_rechazado_mes,
            "abonos_pendientes": abonos_pendientes,
            "resumen_mes": resumen_mes,
        }


def _sincronizar_asignaciones_sesion(servicio, tecnicos_actuales_ids, reset_para_ids=None):
    """
    Asegura que exista una SesionFotoTecnico por cada técnico actualmente asignado.
    Si reset_para_ids viene informado, esas asignaciones vuelven a 'asignado'.

    Retorna:
      sesion, asignaciones_map
    """
    if reset_para_ids is None:
        reset_para_ids = set()

    sesion = _get_or_create_sesion(servicio)

    existentes = {
        a.tecnico_id: a
        for a in sesion.asignaciones.select_related('tecnico').all()
    }

    # Tomamos una asignación existente como base para clonar requisitos a técnicos nuevos
    asignacion_base = None
    for a in existentes.values():
        asignacion_base = a
        break

    for tecnico_id in tecnicos_actuales_ids:
        asignacion = existentes.get(tecnico_id)

        if not asignacion:
            asignacion = SesionFotoTecnico.objects.create(
                sesion=sesion,
                tecnico_id=tecnico_id,
                estado='asignado'
            )
            existentes[tecnico_id] = asignacion

            if asignacion_base:
                _clonar_requisitos_a_asignacion(asignacion_base, asignacion)

        if tecnico_id in reset_para_ids:
            _reset_asignacion_tecnico(asignacion)

    return sesion, existentes


logger = logging.getLogger(__name__)

@login_required
@rol_requerido('usuario')
def buscar_mi_sitio(request):
    id_sitio = request.GET.get("id")
    sitio = None
    buscado = False

    if id_sitio:
        buscado = True
        try:
            obj = SitioMovil.objects.get(id_claro=id_sitio)

            sitio = {}
            for field in obj._meta.fields:
                if field.name != 'id':
                    valor = getattr(obj, field.name)
                    # Normalizar coordenadas si fueran string (por seguridad)
                    if field.name.lower() in ['latitud', 'longitud'] and isinstance(valor, str):
                        valor = valor.replace(",", ".")
                    sitio[field.verbose_name] = str(valor)

        except SitioMovil.DoesNotExist:
            sitio = None

    return render(request, 'operaciones/buscar_mi_sitio.html', {
        'sitio': sitio,
        'buscado': buscado
    })

def _registrar_actualizacion_tabla_sitios(user):
    """
    Guarda la fecha y usuario de la última modificación
    realizada sobre la tabla maestra de sitios.

    Utilizamos siempre el registro PK=1 como metadata global.
    """
    SitiosTablaMetadata.objects.update_or_create(
        pk=1,
        defaults={
            "ultima_actualizacion": timezone.now(),
            "actualizado_por": user,
        },
    )


@login_required
@rol_requerido("pm", "admin", "facturacion", "supervisor")
def listar_sitios(request):
    id_claro = request.GET.get("id_claro", "")
    id_new = request.GET.get("id_new", "")

    # ==========================
    # CANTIDAD POR PÁGINA
    # ==========================
    raw_cantidad = request.GET.get("cantidad", "10")

    if raw_cantidad == "todos":
        per_page = 100
        cantidad = "100"

    else:
        try:
            per_page = int(raw_cantidad)

        except (TypeError, ValueError):
            per_page = 10
            cantidad = "10"

        else:
            if per_page < 1:
                per_page = 10
                cantidad = "10"

            elif per_page > 100:
                per_page = 100
                cantidad = "100"

            else:
                cantidad = raw_cantidad

    page_number = request.GET.get("page", 1)

    # ==========================
    # QUERY
    # ==========================
    sitios = SitioMovil.objects.all().order_by("id_sites")

    if id_claro:
        sitios = sitios.filter(id_claro__icontains=id_claro)

    if id_new:
        sitios = sitios.filter(id_sites_new__icontains=id_new)

    # ==========================
    # PAGINACIÓN
    # ==========================
    paginator = Paginator(
        sitios,
        per_page,
    )

    pagina = paginator.get_page(page_number)

    # ==========================
    # ÚLTIMA ACTUALIZACIÓN
    # ==========================
    metadata_sitios = (
        SitiosTablaMetadata.objects.select_related("actualizado_por")
        .filter(pk=1)
        .first()
    )

    return render(
        request,
        "operaciones/listar_sitios.html",
        {
            "sitios": pagina,
            "id_claro": id_claro,
            "id_new": id_new,
            "cantidad": cantidad,
            "pagina": pagina,
            "metadata_sitios": metadata_sitios,
        },
    )


@login_required
@rol_requerido("pm", "admin", "facturacion", "supervisor")
def editar_sitio(request, pk: int):
    """
    Edita un Sitio Móvil.

    Soporta `next` en query para volver exactamente
    a la lista/filtros/página desde donde se editó.
    """

    sitio = get_object_or_404(
        SitioMovil,
        pk=pk,
    )

    next_url = request.GET.get("next")

    if request.method == "POST":
        form = SitioMovilForm(
            request.POST,
            instance=sitio,
        )

        if form.is_valid():
            form.save()

            _registrar_actualizacion_tabla_sitios(request.user)

            messages.success(
                request,
                "Sitio actualizado correctamente.",
            )

            return redirect(next_url or reverse("operaciones:listar_sitios"))

        messages.error(
            request,
            "Revisa los campos del formulario.",
        )

    else:
        form = SitioMovilForm(instance=sitio)

    return render(
        request,
        "operaciones/editar_sitio.html",
        {
            "form": form,
            "sitio": sitio,
            "next": next_url,
        },
    )


@login_required
@rol_requerido("admin")
def eliminar_sitio(request, pk: int):
    sitio = get_object_or_404(
        SitioMovil,
        pk=pk,
    )

    next_url = request.GET.get("next")

    if request.method == "POST":
        sitio.delete()

        _registrar_actualizacion_tabla_sitios(request.user)

        messages.success(
            request,
            "Sitio eliminado correctamente.",
        )

        return redirect(next_url or reverse("operaciones:listar_sitios"))

    return render(
        request,
        "operaciones/eliminar_sitio.html",
        {
            "sitio": sitio,
            "next": next_url,
        },
    )


def _sitios_import_cache_key(user_id, token):
    return f"gz:sitios_import_preview:{user_id}:{token}"


def _sitios_clean_value(value):
    """
    Limpia valores provenientes del Excel.

    IMPORTANTE:
    Los valores que representan ausencia de información
    se convierten a None.

    En una actualización, None significa:
    "no modificar lo que ya existe".
    """

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    value_lower = value.lower().strip()

    valores_vacios = {
        "nan",
        "none",
        "null",
        "-",
        "--",
    }

    if value_lower in valores_vacios:
        return None

    return value


def _sitios_clean_decimal(value):
    value = _sitios_clean_value(value)

    if value in (None, ""):
        return None

    try:
        return float(str(value).strip().replace(",", "."))

    except (TypeError, ValueError):
        return None


def _sitios_clean_int_or_text(value):
    value = _sitios_clean_value(value)

    if value in (None, ""):
        return None

    try:
        numero = float(str(value).strip().replace(",", "."))

        if numero.is_integer():
            return str(int(numero))

    except (TypeError, ValueError):
        pass

    return str(value).strip()


def _sitios_get_col(row, *names):
    """
    Busca una columna del Excel de forma flexible.

    Ejemplos:
    - Dirección / Direccion
    - ID Sites NEW / ID NEW
    - Tipo de Zona / Tipo Zona
    - Condición de acceso / Condiciones de acceso
    """

    # ==========================
    # COINCIDENCIA EXACTA
    # ==========================
    for name in names:
        if name in row:
            return row.get(name)

    # ==========================
    # COINCIDENCIA NORMALIZADA
    # ==========================
    norm_map = {}

    for col in row.index:
        norm_map[_norm_col_sitios(col)] = col

    for name in names:
        key = _norm_col_sitios(name)

        real_col = norm_map.get(key)

        if real_col is not None:
            return row.get(real_col)

    return None

CONDICIONES_ACCESO_MAP = {
    "0": "Sin Información",
    "1": "Libre Acceso",
    "2": "Correos - Confirmación",
    "3": "Correos-Sin Confirmación",
    "4": "Llamadas",
    "5": "Formularios",
    "6": "Certificación",
}

def _normalizar_condicion_acceso(value):
    """
    Convierte tanto códigos numéricos como descripciones
    al nombre oficial utilizado internamente.

    Ejemplos:

    1
    1.0
    "Libre Acceso"
    "LIBRE ACCESO"

    -> "Libre Acceso"

    Si llega un texto desconocido, lo conserva.
    """

    value = _sitios_clean_value(
        value
    )

    if value in (None, ""):
        return None

    texto = str(
        value
    ).strip()

    # ==========================
    # INTENTAR COMO CÓDIGO
    # ==========================
    try:
        numero = float(
            texto.replace(",", ".")
        )

        if numero.is_integer():
            codigo = str(
                int(numero)
            )

            descripcion = CONDICIONES_ACCESO_MAP.get(
                codigo
            )

            if descripcion:
                return descripcion

    except (TypeError, ValueError):
        pass

    # ==========================
    # INTENTAR COMO TEXTO
    # ==========================
    normalizado = _norm_col_sitios(
        texto
    )

    equivalencias = {
        # 0
        "sin informacion": "Sin Información",
        "sin info": "Sin Información",

        # 1
        "libre acceso": "Libre Acceso",

        # 2
        "correos confirmacion": "Correos - Confirmación",
        "correo confirmacion": "Correos - Confirmación",
        "correos con confirmacion": "Correos - Confirmación",
        "correo con confirmacion": "Correos - Confirmación",

        # 3
        "correos sin confirmacion": "Correos-Sin Confirmación",
        "correo sin confirmacion": "Correos-Sin Confirmación",

        # 4
        "llamadas": "Llamadas",
        "llamada": "Llamadas",

        # 5
        "formularios": "Formularios",
        "formulario": "Formularios",

        # 6
        "certificacion": "Certificación",
    }

    descripcion = equivalencias.get(
        normalizado
    )

    if descripcion:
        return descripcion

    # Si en el futuro aparece otra condición,
    # no destruimos el valor.
    return texto


def _normalizar_tipo_zona(value):
    """
    Normaliza Tipo de Zona.

    U       -> Urbano
    Urbano  -> Urbano

    R       -> Rural
    Rural   -> Rural

    #N/D / No identificado -> No_Identificado
    """

    value = _sitios_clean_value(
        value
    )

    if value in (None, ""):
        return None

    texto = str(
        value
    ).strip()

    normalizado = _norm_col_sitios(
        texto
    )

    equivalencias = {
        "u": "Urbano",
        "urbano": "Urbano",

        "r": "Rural",
        "rural": "Rural",

        "#n/d": "No_Identificado",
        "n/d": "No_Identificado",
        "nd": "No_Identificado",
        "no identificado": "No_Identificado",
        "no identificada": "No_Identificado",
        "no identificado": "No_Identificado",
    }

    resultado = equivalencias.get(
        normalizado
    )

    if resultado:
        return resultado

    return texto


def _norm_col_sitios(value):
    value = str(value or "").strip().lower()

    value = value.replace("á", "a")
    value = value.replace("é", "e")
    value = value.replace("í", "i")
    value = value.replace("ó", "o")
    value = value.replace("ú", "u")
    value = value.replace("ñ", "n")

    value = value.replace(".", "")
    value = value.replace("_", " ")
    value = value.replace("-", " ")

    value = " ".join(value.split())

    return value


def _leer_excel_sitios(archivo):
    """
    Lee el Excel de sitios.

    Prioridad:
    1. Hoja llamada Colocalizados.
    2. Primera hoja disponible.
    """

    xls = pd.ExcelFile(
        archivo
    )

    sheet_name = None

    for name in xls.sheet_names:
        if _norm_col_sitios(name) == "colocalizados":
            sheet_name = name
            break

    if not sheet_name:
        sheet_name = xls.sheet_names[0]

    df = pd.read_excel(
        archivo,
        sheet_name=sheet_name,
    )

    # Eliminar únicamente filas
    # completamente vacías.
    df = df.dropna(
        how="all"
    )

    return df, sheet_name


def _row_sitio_to_data(row):
    """
    Convierte una fila del Excel al formato del modelo SitioMovil.

    Reglas principales:

    - ID Sites identifica el sitio.
    - Condiciones de acceso acepta código o descripción.
    - Tipo de Zona acepta U/R o descripción.
    - Valores vacíos quedan como None.
    - None NO significa borrar durante una actualización.
    """

    id_sites = _sitios_clean_value(
        _sitios_get_col(
            row,
            "ID Sites",
            "ID Site",
            "ID",
        )
    )

    if not id_sites:
        return None

    # ==========================
    # CANDADO BT
    # ==========================
    candado_bt = _sitios_clean_value(
        _sitios_get_col(
            row,
            "Candado BT",
            "Candado",
            "Tipo de candado",
        )
    )

    # ==========================
    # CONDICIONES DE ACCESO
    # ==========================
    condiciones_acceso = _normalizar_condicion_acceso(
        _sitios_get_col(
            row,
            "Condiciones de acceso",
            "Condición de acceso",
            "Condicion de acceso",
            "Acceso",
        )
    )

    # ==========================
    # TIPO DE ZONA
    # ==========================
    tipo_zona = _normalizar_tipo_zona(
        _sitios_get_col(
            row,
            "Tipo de Zona",
            "Tipo Zona",
            "Tipo de zona",
            "Zona",
        )
    )

    data = {
        "id_sites": id_sites,
        "id_claro": _sitios_clean_value(
            _sitios_get_col(
                row,
                "ID Claro",
                "Id Claro",
                "ID CLARO",
            )
        ),
        "id_sites_new": _sitios_clean_value(
            _sitios_get_col(
                row,
                "ID Sites NEW",
                "ID NEW",
                "ID Sites New",
            )
        ),
        "region": _sitios_clean_int_or_text(
            _sitios_get_col(
                row,
                "Región",
                "Region",
            )
        ),
        "nombre": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Nombre",
            )
        ),
        "direccion": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Direccion",
                "Dirección",
            )
        ),
        "latitud": _sitios_clean_decimal(
            _sitios_get_col(
                row,
                "Latitud",
            )
        ),
        "longitud": _sitios_clean_decimal(
            _sitios_get_col(
                row,
                "Longitud",
            )
        ),
        "comuna": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Comuna",
            )
        ),
        "tipo_construccion": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Tipo de contruccion",
                "Tipo de construccion",
                "Tipo de construcción",
                "Tipo construccion",
                "Construcción",
                "Construccion",
            )
        ),
        "altura": _sitios_clean_int_or_text(
            _sitios_get_col(
                row,
                "Altura",
            )
        ),
        "tipo_zona": tipo_zona,
        "candado_bt": candado_bt,
        "condiciones_acceso": condiciones_acceso,
        "claves": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Claves",
            )
        ),
        "llaves": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Llaves",
            )
        ),
        "cantidad_llaves": _sitios_clean_int_or_text(
            _sitios_get_col(
                row,
                "Cantidad de Llaves",
                "Cantidad Llaves",
            )
        ),
        "observaciones_generales": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Observaciones Generales",
                "Observaciones",
            )
        ),
        "zonas_conflictivas": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Sitios zonas conflictivas",
                "Zonas Conflictivas",
                "Zonas conflictivas",
            )
        ),
        "alarmas": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Alarmas",
            )
        ),
        "guardias": _sitios_clean_value(
            _sitios_get_col(
                row,
                "Guardias",
            )
        ),
    }

    return data


def _campos_import_sitios_por_modo(modo):
    """
    modo=acceso:
        Actualiza únicamente información relacionada
        con acceso al sitio.

    modo=completo:
        Actualiza toda la ficha del sitio.

    IMPORTANTE:
        Los valores vacíos nunca reemplazan
        información existente.
    """

    campos_acceso = [
        "candado_bt",
        "condiciones_acceso",
        "claves",
        "llaves",
        "cantidad_llaves",
        "observaciones_generales",
        "zonas_conflictivas",
        "alarmas",
        "guardias",
    ]

    campos_completo = [
        "id_claro",
        "id_sites_new",
        "region",
        "nombre",
        "direccion",
        "latitud",
        "longitud",
        "comuna",
        "tipo_construccion",
        "altura",
        "tipo_zona",
        "candado_bt",
        "condiciones_acceso",
        "claves",
        "llaves",
        "cantidad_llaves",
        "observaciones_generales",
        "zonas_conflictivas",
        "alarmas",
        "guardias",
    ]

    if modo == "completo":
        return campos_completo

    return campos_acceso


def _normalizar_para_comparar(value):
    """
    Convierte valores a una representación uniforme
    para evitar detectar falsos cambios.
    """

    if value is None:
        return ""

    value = str(value).strip()

    if value.lower() in {
        "none",
        "nan",
        "null",
    }:
        return ""

    return value


def _generar_preview_import_sitios(df, modo):
    """
    Genera el preview SIN modificar la base de datos.

    REGLA NO DESTRUCTIVA:
    Si un valor nuevo viene vacío, NO se considera cambio.

    Ejemplo:

    BD:
        claves = "1309 - 7394"

    Excel nuevo:
        claves = vacío

    Resultado:
        no aparece ningún cambio y se mantiene
        "1309 - 7394".
    """

    campos = _campos_import_sitios_por_modo(
        modo
    )

    preview = []
    errores = []

    total_nuevos = 0
    total_actualizados = 0
    total_sin_cambios = 0
    total_errores = 0

    for index, row in df.iterrows():
        fila_excel = int(
            index
        ) + 2

        data = _row_sitio_to_data(
            row
        )

        if not data:
            total_errores += 1

            errores.append(
                {
                    "fila": fila_excel,
                    "error": "Fila sin ID Sites válido.",
                }
            )

            continue

        id_sites = data.get(
            "id_sites"
        )

        sitio = (
            SitioMovil.objects
            .filter(
                id_sites__iexact=id_sites
            )
            .first()
        )

        # ==================================================
        # SITIO NUEVO
        # ==================================================
        if not sitio:
            total_nuevos += 1

            cambios = []

            # Para preview de un sitio nuevo mostramos
            # todos los valores disponibles.
            campos_nuevo = [
                "id_claro",
                "id_sites_new",
                "region",
                "nombre",
                "direccion",
                "latitud",
                "longitud",
                "comuna",
                "tipo_construccion",
                "altura",
                "tipo_zona",
                "candado_bt",
                "condiciones_acceso",
                "claves",
                "llaves",
                "cantidad_llaves",
                "observaciones_generales",
                "zonas_conflictivas",
                "alarmas",
                "guardias",
            ]

            for campo in campos_nuevo:
                nuevo = data.get(
                    campo
                )

                if nuevo in (None, ""):
                    continue

                cambios.append(
                    {
                        "campo": campo,
                        "antes": "—",
                        "despues": nuevo,
                    }
                )

            preview.append(
                {
                    "fila": fila_excel,
                    "id_sites": id_sites,
                    "id_claro": (
                        data.get("id_claro")
                        or "—"
                    ),
                    "nombre": (
                        data.get("nombre")
                        or "—"
                    ),
                    "estado": "nuevo",
                    "cambios": cambios,
                    "data": data,
                }
            )

            continue

        # ==================================================
        # SITIO EXISTENTE
        # ==================================================
        cambios = []

        for campo in campos:
            nuevo = data.get(
                campo
            )

            # ==============================================
            # REGLA FUNDAMENTAL
            # ==============================================
            #
            # VACÍO = NO MODIFICAR
            #
            # No importa si:
            # - la celda está vacía;
            # - la columna no existe;
            # - llegó NaN;
            # - llegó None;
            # - llegó null;
            # - llegó "-".
            #
            # ==============================================
            if nuevo in (None, ""):
                continue

            anterior = getattr(
                sitio,
                campo,
                None,
            )

            anterior_cmp = _normalizar_para_comparar(
                anterior
            )

            nuevo_cmp = _normalizar_para_comparar(
                nuevo
            )

            if anterior_cmp != nuevo_cmp:
                cambios.append(
                    {
                        "campo": campo,
                        "antes": (
                            anterior
                            if anterior not in (None, "")
                            else "—"
                        ),
                        "despues": nuevo,
                    }
                )

        if cambios:
            total_actualizados += 1
            estado = "actualizar"

        else:
            total_sin_cambios += 1
            estado = "sin_cambios"

        preview.append(
            {
                "fila": fila_excel,
                "id_sites": id_sites,

                "id_claro": (
                    data.get("id_claro")
                    or sitio.id_claro
                    or "—"
                ),

                "nombre": (
                    data.get("nombre")
                    or sitio.nombre
                    or "—"
                ),

                "estado": estado,
                "cambios": cambios,
                "data": data,
            }
        )

    resumen = {
        "total_filas": len(df),
        "nuevos": total_nuevos,
        "actualizados": total_actualizados,
        "sin_cambios": total_sin_cambios,
        "errores": total_errores,
    }

    return (
        preview,
        resumen,
        errores,
    )


def _aplicar_import_sitios(preview, modo):
    """
    Aplica la importación definitivamente.

    REGLA PRINCIPAL:
    Un valor vacío NUNCA reemplaza un valor existente.
    """

    campos = _campos_import_sitios_por_modo(modo)

    creados = 0
    actualizados = 0
    sin_cambios = 0

    # Campos que podemos utilizar cuando
    # necesitamos crear un sitio completamente nuevo.
    campos_sitio_nuevo = [
        "id_claro",
        "id_sites_new",
        "region",
        "nombre",
        "direccion",
        "latitud",
        "longitud",
        "comuna",
        "tipo_construccion",
        "altura",
        "tipo_zona",
        "candado_bt",
        "condiciones_acceso",
        "claves",
        "llaves",
        "cantidad_llaves",
        "observaciones_generales",
        "zonas_conflictivas",
        "alarmas",
        "guardias",
    ]

    with transaction.atomic():

        for item in preview:
            estado = item.get("estado")

            data = item.get("data") or {}

            if estado == "sin_cambios":
                sin_cambios += 1
                continue

            id_sites = data.get("id_sites")

            if not id_sites:
                continue

            sitio = SitioMovil.objects.filter(id_sites__iexact=id_sites).first()

            # =============================================
            # CREAR SITIO NUEVO
            # =============================================
            if not sitio:
                sitio = SitioMovil(id_sites=id_sites)

                for campo in campos_sitio_nuevo:
                    nuevo = data.get(campo)

                    # Para un sitio nuevo tampoco necesitamos
                    # meter None explícitamente.
                    if nuevo in (None, ""):
                        continue

                    setattr(
                        sitio,
                        campo,
                        nuevo,
                    )

                sitio.save()

                creados += 1
                continue

            # =============================================
            # ACTUALIZAR SITIO EXISTENTE
            # =============================================
            hubo_cambio = False
            campos_update = []

            for campo in campos:
                nuevo = data.get(campo)

                # =========================================
                # PROTECCIÓN NO DESTRUCTIVA
                # =========================================
                if nuevo in (None, ""):
                    continue

                anterior = getattr(
                    sitio,
                    campo,
                    None,
                )

                anterior_cmp = _normalizar_para_comparar(anterior)

                nuevo_cmp = _normalizar_para_comparar(nuevo)

                if anterior_cmp == nuevo_cmp:
                    continue

                setattr(
                    sitio,
                    campo,
                    nuevo,
                )

                campos_update.append(campo)

                hubo_cambio = True

            if hubo_cambio:
                sitio.save(update_fields=campos_update)

                actualizados += 1

            else:
                sin_cambios += 1

    return {
        "creados": creados,
        "actualizados": actualizados,
        "sin_cambios": sin_cambios,
    }


@login_required
@rol_requerido("admin")
def importar_sitios_excel(request):
    import uuid

    token = request.POST.get("token") or ""

    accion = request.POST.get("accion") or ""

    modo = request.POST.get("modo") or "acceso"

    if modo not in {
        "acceso",
        "completo",
    }:
        modo = "acceso"

    # =====================================================
    # CONFIRMAR IMPORTACIÓN
    # =====================================================
    if request.method == "POST" and accion == "confirmar":
        if not token:
            messages.error(
                request,
                "No se encontró el preview de importación.",
            )

            return redirect("operaciones:importar_sitios")

        cache_key = _sitios_import_cache_key(
            request.user.id,
            token,
        )

        payload = cache.get(cache_key)

        if not payload:
            messages.error(
                request,
                ("El preview expiró o no existe. " "Vuelve a subir el archivo."),
            )

            return redirect("operaciones:importar_sitios")

        preview = payload.get("preview") or []

        modo = payload.get("modo") or modo

        try:
            resultado = _aplicar_import_sitios(
                preview,
                modo,
            )

            # Registrar usuario/fecha solamente
            # después de aplicar correctamente.
            if resultado["creados"] > 0 or resultado["actualizados"] > 0:
                _registrar_actualizacion_tabla_sitios(request.user)

            cache.delete(cache_key)

            messages.success(
                request,
                (
                    "Importación aplicada correctamente. "
                    f"Nuevos: {resultado['creados']}. "
                    f"Actualizados: {resultado['actualizados']}. "
                    f"Sin cambios: {resultado['sin_cambios']}."
                ),
            )

            return redirect("operaciones:listar_sitios")

        except Exception as e:
            messages.error(
                request,
                f"Ocurrió un error aplicando la importación: {e}",
            )

            return redirect("operaciones:importar_sitios")

    # =====================================================
    # CANCELAR PREVIEW
    # =====================================================
    if request.method == "POST" and accion == "cancelar":
        if token:
            cache.delete(
                _sitios_import_cache_key(
                    request.user.id,
                    token,
                )
            )

        messages.info(
            request,
            "Importación cancelada.",
        )

        return redirect("operaciones:importar_sitios")

    # =====================================================
    # GENERAR PREVIEW
    # =====================================================
    if request.method == "POST" and request.FILES.get("archivo"):
        archivo = request.FILES["archivo"]

        try:
            df, sheet_name = _leer_excel_sitios(archivo)

            (
                preview,
                resumen,
                errores,
            ) = _generar_preview_import_sitios(
                df,
                modo,
            )

            token = uuid.uuid4().hex

            cache_key = _sitios_import_cache_key(
                request.user.id,
                token,
            )

            cache.set(
                cache_key,
                {
                    "preview": preview,
                    "resumen": resumen,
                    "errores": errores,
                    "modo": modo,
                    "sheet_name": sheet_name,
                },
                timeout=60 * 30,
            )

            preview_mostrar = preview[:200]

            return render(
                request,
                "operaciones/importar_sitios.html",
                {
                    "preview": preview_mostrar,
                    "preview_total": len(preview),
                    "resumen": resumen,
                    "errores": errores[:50],
                    "errores_total": len(errores),
                    "token": token,
                    "modo": modo,
                    "sheet_name": sheet_name,
                },
            )

        except Exception as e:
            messages.error(
                request,
                f"Ocurrió un error al leer el archivo: {e}",
            )

            return redirect("operaciones:importar_sitios")

    return render(
        request,
        "operaciones/importar_sitios.html",
        {
            "modo": "acceso",
        },
    )


@login_required
@rol_requerido("admin")
def descargar_formato_sitios_excel(request):
    """
    Descarga el formato oficial para importar
    o actualizar la tabla maestra de sitios.
    """

    columnas = [
        "ID Sites",
        "ID Claro",
        "ID Sites NEW",
        "Región",
        "Nombre",
        "Direccion",
        "Latitud",
        "Longitud",
        "Comuna",
        "Tipo de contruccion",
        "Altura",
        "Tipo de Zona",
        "Candado BT",
        "Condiciones de acceso",
        "Claves",
        "Llaves",
        "Cantidad de Llaves",
        "Observaciones Generales",
        "Sitios zonas conflictivas",
        "Alarmas",
        "Guardias",
    ]

    ejemplo = [
        "CL-13-00421-05",
        "13_094",
        "CL-13-SN-00421-05",
        "13",
        "Vicuña Mackenna",
        "Vasconia 71",
        "-33.494717",
        "-70.620000",
        "SAN JOAQUÍN",
        "MP",
        "36",
        "Urbano",
        "NA",
        "Libre Acceso",
        "1309 - 7394 - 1394",
        "Sin Llaves",
        "0",
        "Observación general del sitio",
        "No Aplica",
        "No Aplica",
        "No Aplica",
    ]

    df = pd.DataFrame(
        [ejemplo],
        columns=columnas,
    )

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl",
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Colocalizados",
        )

        workbook = writer.book
        worksheet = writer.sheets["Colocalizados"]

        worksheet.freeze_panes = "A2"

        for col_idx, column_name in enumerate(
            columnas,
            start=1,
        ):
            cell = worksheet.cell(
                row=1,
                column=col_idx,
            )

            cell.value = column_name

            try:
                cell.font = cell.font.copy(bold=True)
            except Exception:
                pass

            width = max(
                len(str(column_name)) + 4,
                14,
            )

            worksheet.column_dimensions[cell.column_letter].width = min(
                width,
                35,
            )

    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        "attachment; " 'filename="formato_importacion_sitios.xlsx"'
    )

    return response


@login_required
@rol_requerido("pm", "admin", "facturacion")
def listar_servicios_pm(request):
    """
    Bandeja general de servicios cotizados.

    REGLA PRINCIPAL
    ==========================================================

    Esta pantalla muestra servicios operativos en cualquiera de
    los estados válidos del flujo de cotizaciones.

    Se excluyen los registros especiales de ajustes:

        - ajuste_bono
        - ajuste_adelanto
        - ajuste_descuento

    Puede filtrarse por:

        - DU
        - ID Claro
        - ID NEW
        - Mes de producción
        - Estado

    Los filtros Excel se aplican antes de paginar.

    excel_global_json se construye sobre todo el queryset
    filtrado, no solamente sobre la página actual.

    La tabla continúa recargándose por AJAX reemplazando
    únicamente #zonaTabla.
    """

    import json
    from urllib.parse import urlencode

    from django.core.paginator import Paginator

    # ========================================================
    # ESTADOS OPERATIVOS VISIBLES
    # ========================================================

    ESTADOS_VISIBLES = [
        "cotizado",
        "aprobado_pendiente",
        "asignado",
        "en_progreso",
        "en_revision_supervisor",
        "finalizado_trabajador",
        "rechazado_supervisor",
        "aprobado_supervisor",
    ]

    # ========================================================
    # QUERYSET BASE
    # ========================================================

    servicios = ServicioCotizado.objects.filter(
        estado__in=ESTADOS_VISIBLES,
    ).order_by(
        "-fecha_creacion",
        "-id",
    )

    # ========================================================
    # FILTROS RÁPIDOS NORMALES POR URL
    # ========================================================

    du_raw = (
        request.GET.get(
            "du",
        )
        or ""
    ).strip()

    id_claro = (
        request.GET.get(
            "id_claro",
        )
        or ""
    ).strip()

    id_new = (
        request.GET.get(
            "id_new",
        )
        or ""
    ).strip()

    mes_produccion = (
        request.GET.get(
            "mes_produccion",
        )
        or ""
    ).strip()

    estado = (
        request.GET.get(
            "estado",
        )
        or ""
    ).strip()

    # ========================================================
    # APLICAR FILTROS RÁPIDOS
    # ========================================================

    du = du_raw

    if du:
        du = (
            du.strip()
            .upper()
            .replace(
                "DU",
                "",
            )
        )

        servicios = servicios.filter(
            du__iexact=du,
        )

    if id_claro:
        servicios = servicios.filter(
            id_claro__icontains=id_claro,
        )

    if id_new:
        servicios = servicios.filter(
            id_new__icontains=id_new,
        )

    if mes_produccion:
        servicios = servicios.filter(
            mes_produccion__icontains=mes_produccion,
        )

    if estado and estado in ESTADOS_VISIBLES:
        servicios = servicios.filter(
            estado=estado,
        )

    servicios = servicios.distinct()

    # ========================================================
    # HELPERS EXCEL
    # ========================================================

    def money_clp_label(
        n,
    ):
        try:
            return f"$ {int(n or 0):,} CLP".replace(
                ",",
                ".",
            )

        except Exception:
            return "$ 0 CLP"

    def uf_label(
        n,
    ):
        try:
            val = float(
                n or 0,
            )

            if val.is_integer():
                return f"UF {int(val):,}".replace(
                    ",",
                    ".",
                )

            return (
                f"UF {val:,.2f}".replace(
                    ",",
                    "X",
                )
                .replace(
                    ".",
                    ",",
                )
                .replace(
                    "X",
                    ".",
                )
            )

        except Exception:
            return "UF 0"

    def status_label(
        servicio,
    ):
        labels = {
            "cotizado": "Cotizado (pendiente aprobación)",
            "aprobado_pendiente": "Aprobada, pendiente por asignar",
            "asignado": "Asignado",
            "en_progreso": "En progreso",
            "en_revision_supervisor": "En revisión supervisor",
            "finalizado_trabajador": "Finalizado trabajador",
            "rechazado_supervisor": "Rechazado por supervisor",
            "aprobado_supervisor": "Aprobado por supervisor",
        }

        return labels.get(
            servicio.estado,
            servicio.estado,
        )

    def excel_value_for_servicio(
        servicio,
        col,
    ):
        col = str(
            col,
        )

        if col == "0":
            return f"DU{servicio.du}" if servicio.du is not None else "—"

        if col == "1":
            return str(servicio.id_claro or "—")

        if col == "2":
            return str(servicio.region or "—")

        if col == "3":
            return str(servicio.mes_produccion or "—")

        if col == "4":
            return str(servicio.id_new or "—")

        if col == "5":
            return str(servicio.detalle_tarea or "—")

        if col == "6":
            return uf_label(
                servicio.monto_cotizado,
            )

        if col == "7":
            return money_clp_label(
                servicio.monto_mmoo,
            )

        if col == "8":
            return status_label(
                servicio,
            )

        if col == "9":
            return "Acciones"

        return ""

    # ========================================================
    # FILTROS EXCEL GLOBALES
    # ========================================================

    excel_filters_raw = (
        request.GET.get(
            "excel_filters",
        )
        or ""
    ).strip()

    try:
        excel_filters = (
            json.loads(
                excel_filters_raw,
            )
            if excel_filters_raw
            else {}
        )

    except json.JSONDecodeError:
        excel_filters = {}

    servicios_list = list(
        servicios,
    )

    # ========================================================
    # LABEL DE ESTADO PARA EL TEMPLATE
    # ========================================================

    for servicio in servicios_list:
        servicio.estado_pm_label = status_label(
            servicio,
        )

    # ========================================================
    # APLICAR FILTROS EXCEL
    # ========================================================

    if excel_filters:

        filtered_list = []

        for servicio in servicios_list:

            ok = True

            for col, values in excel_filters.items():

                values_set = set(
                    values or [],
                )

                if not values_set:
                    continue

                label = excel_value_for_servicio(
                    servicio,
                    col,
                )

                if label not in values_set:
                    ok = False
                    break

            if ok:
                filtered_list.append(
                    servicio,
                )

        servicios_list = filtered_list

    # ========================================================
    # GLOBALES PARA PANEL EXCEL
    # ========================================================

    excel_global = {}

    for col in range(
        10,
    ):

        vals = set()

        for servicio in servicios_list:

            vals.add(
                excel_value_for_servicio(
                    servicio,
                    str(col),
                )
                or "(Vacías)"
            )

        excel_global[col] = sorted(
            vals,
        )

    excel_global_json = json.dumps(
        excel_global,
    )

    # ========================================================
    # PAGINACIÓN
    # ========================================================

    cantidad_param = request.GET.get(
        "cantidad",
        "10",
    )

    if cantidad_param == "todos":

        per_page = 100
        cantidad = "100"

    else:

        try:
            per_page = max(
                5,
                min(
                    int(cantidad_param),
                    100,
                ),
            )

            cantidad = str(
                per_page,
            )

        except ValueError:
            per_page = 10
            cantidad = "10"

    paginator = Paginator(
        servicios_list,
        per_page,
    )

    page_number = (
        request.GET.get(
            "page",
        )
        or 1
    )

    pagina = paginator.get_page(
        page_number,
    )

    # ========================================================
    # MANTENER PARÁMETROS
    # ========================================================

    keep_params = {}

    if cantidad:
        keep_params["cantidad"] = cantidad

    if du_raw:
        keep_params["du"] = du_raw

    if id_claro:
        keep_params["id_claro"] = id_claro

    if id_new:
        keep_params["id_new"] = id_new

    if mes_produccion:
        keep_params["mes_produccion"] = mes_produccion

    if estado and estado in ESTADOS_VISIBLES:
        keep_params["estado"] = estado

    if excel_filters_raw:
        keep_params["excel_filters"] = excel_filters_raw

    qs_keep = urlencode(
        keep_params,
    )

    # ========================================================
    # ESTADOS DISPONIBLES PARA FILTROS
    # ========================================================

    estado_choices = [
        (
            "cotizado",
            "Cotizado (pendiente aprobación)",
        ),
        (
            "aprobado_pendiente",
            "Aprobada, pendiente por asignar",
        ),
        (
            "asignado",
            "Asignado",
        ),
        (
            "en_progreso",
            "En progreso",
        ),
        (
            "en_revision_supervisor",
            "En revisión supervisor",
        ),
        (
            "finalizado_trabajador",
            "Finalizado trabajador",
        ),
        (
            "rechazado_supervisor",
            "Rechazado por supervisor",
        ),
        (
            "aprobado_supervisor",
            "Aprobado por supervisor",
        ),
    ]

    # ========================================================
    # RENDER
    # ========================================================

    return render(
        request,
        "operaciones/listar_servicios_pm.html",
        {
            "pagina": pagina,
            "cantidad": cantidad,
            "filtros": {
                "du": du_raw,
                "id_claro": id_claro,
                "mes_produccion": mes_produccion,
                "id_new": id_new,
                "estado": (estado if estado in ESTADOS_VISIBLES else ""),
            },
            "estado_choices": estado_choices,
            "excel_global_json": excel_global_json,
            "qs_keep": qs_keep,
        },
    )


@login_required
@rol_requerido("pm", "admin")
@require_POST
@transaction.atomic
def accion_masiva_cotizaciones_pm(
    request,
):
    """
    Acción masiva de cotizaciones pendientes.

    PERMISOS
    ==========================================================

    PM:
        puede aprobar masivamente.

    ADMIN:
        puede aprobar masivamente;
        puede eliminar masivamente.

    Solamente se procesan cotizaciones que todavía estén:

        estado = "cotizado"
    """

    accion = (
        request.POST.get(
            "accion",
            "",
        )
        .strip()
        .lower()
    )

    servicio_ids = request.POST.getlist(
        "servicio_ids",
    )

    # ========================================================
    # VALIDACIÓN DE SELECCIÓN
    # ========================================================

    if not servicio_ids:

        messages.warning(
            request,
            "Debes seleccionar al menos una cotización.",
        )

        return redirect(
            "operaciones:listar_servicios_pm",
        )

    # ========================================================
    # SEGURIDAD DE ACCIONES
    # ========================================================

    es_admin = bool(
        request.user.is_superuser
        or request.user.es_admin_general
    )

    es_pm = bool(
        getattr(
            request.user,
            "es_pm",
            False,
        )
    )

    # --------------------------------------------------------
    # APROBAR:
    # PM + ADMIN
    # --------------------------------------------------------

    if accion == "aprobar":

        if not (
            es_admin
            or es_pm
        ):

            messages.error(
                request,
                (
                    "No tienes permisos para aprobar "
                    "cotizaciones masivamente."
                ),
            )

            return redirect(
                "operaciones:listar_servicios_pm",
            )

    # --------------------------------------------------------
    # ELIMINAR:
    # SOLO ADMIN
    # --------------------------------------------------------

    elif accion == "eliminar":

        if not es_admin:

            messages.error(
                request,
                (
                    "Solo un administrador puede eliminar "
                    "cotizaciones masivamente."
                ),
            )

            return redirect(
                "operaciones:listar_servicios_pm",
            )

    else:

        messages.error(
            request,
            "La acción masiva seleccionada no es válida.",
        )

        return redirect(
            "operaciones:listar_servicios_pm",
        )

    # ========================================================
    # BLOQUEAR Y OBTENER REGISTROS
    # ========================================================

    servicios = list(
        ServicioCotizado.objects
        .select_for_update()
        .filter(
            pk__in=servicio_ids,
            estado="cotizado",
        )
        .order_by(
            "id",
        )
    )

    if not servicios:

        messages.warning(
            request,
            (
                "Las cotizaciones seleccionadas ya no están "
                "disponibles para esta acción."
            ),
        )

        return redirect(
            "operaciones:listar_servicios_pm",
        )

    # ========================================================
    # APROBAR
    # ========================================================

    if accion == "aprobar":

        cantidad = 0

        for servicio in servicios:

            servicio.estado = "aprobado_pendiente"

            servicio.pm_aprueba = request.user

            servicio.save(
                update_fields=[
                    "estado",
                    "pm_aprueba",
                ]
            )

            cantidad += 1

        messages.success(
            request,
            (
                f"{cantidad} cotización(es) "
                "fueron aprobadas correctamente."
            ),
        )

    # ========================================================
    # ELIMINAR
    # ========================================================

    elif accion == "eliminar":

        cantidad = len(
            servicios,
        )

        for servicio in servicios:
            servicio.delete()

        messages.success(
            request,
            (
                f"{cantidad} cotización(es) "
                "fueron eliminadas correctamente."
            ),
        )

    # ========================================================
    # RETORNO
    # ========================================================

    next_url = (
        request.POST.get(
            "next",
            "",
        )
        .strip()
    )

    if next_url:
        return redirect(
            next_url,
        )

    return redirect(
        "operaciones:listar_servicios_pm",
    )


@login_required
@rol_requerido(
    "pm",
    "admin",
    "facturacion",
)
def crear_servicio_cotizado(
    request,
):
    if request.method == "POST":

        form = ServicioCotizadoForm(request.POST)

        if form.is_valid():

            servicio = form.save(commit=False)

            servicio.creado_por = request.user

            servicio.estado = "cotizado"

            # ====================================================
            # VINCULAR A EJECUCIÓN MENSUAL EXACTA
            # ====================================================

            servicio.sitio_planificado = obtener_sitio_planificado_para_servicio(
                id_claro=(servicio.id_claro),
                mes_produccion=(servicio.mes_produccion),
            )

            servicio.save()

            if servicio.sitio_planificado_id:

                messages.success(
                    request,
                    (
                        f"DU{servicio.du} creado y "
                        "vinculado correctamente con "
                        "su planificación mensual."
                    ),
                )

            else:

                messages.warning(
                    request,
                    (
                        f"DU{servicio.du} fue creado, "
                        "pero no existe una planificación "
                        "mensual exacta para "
                        f"{servicio.id_claro} / "
                        f"{servicio.mes_produccion}. "
                        "El servicio quedó sin vínculo."
                    ),
                )

            return redirect("operaciones:listar_servicios_pm")

    else:

        form = ServicioCotizadoForm()

    return render(
        request,
        "operaciones/crear_servicio_cotizado.html",
        {
            "form": form,
        },
    )


@login_required
@rol_requerido("pm", "admin", "facturacion")
def editar_servicio_cotizado(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if next_url and not next_url.startswith("/"):
        next_url = ""

    # --- Permitir edición siempre a PM, Admin y Facturación ---
    if servicio.estado not in ["cotizado", "aprobado_pendiente"] and not (
        request.user.is_superuser or request.user.es_facturacion or request.user.es_pm
    ):
        messages.error(
            request, "No puedes editar esta cotización porque ya fue asignada."
        )
        return redirect(next_url or "operaciones:listar_servicios_pm")

    if request.method == "POST":
        form = ServicioCotizadoForm(request.POST, instance=servicio)

        if form.is_valid():
            servicio = form.save(commit=False)

            if servicio.id_claro:
                sitio = SitioMovil.objects.filter(id_claro=servicio.id_claro).first()

                if sitio:
                    servicio.id_new = sitio.id_sites_new
                    servicio.region = sitio.region

            servicio.save()
            messages.success(request, "Cotización actualizada correctamente.")
            return redirect(next_url or "operaciones:listar_servicios_pm")

        else:
            messages.error(request, "Corrige los errores en el formulario.")

    else:
        form = ServicioCotizadoForm(instance=servicio)

    return render(
        request,
        "operaciones/editar_servicio_cotizado.html",
        {
            "form": form,
            "servicio": servicio,
            "next_url": next_url,
        },
    )


@login_required
@rol_requerido("pm", "admin", "facturacion")
def eliminar_servicio_cotizado(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if next_url and not next_url.startswith("/"):
        next_url = ""

    # Validar estado permitido
    if servicio.estado not in ["cotizado", "aprobado_pendiente"] and not (
        request.user.is_superuser or request.user.es_facturacion
    ):
        messages.error(
            request, "No puedes eliminar esta cotización porque ya fue asignada."
        )
        return redirect(next_url or "operaciones:listar_servicios_pm")

    if request.method == "POST":
        servicio.delete()
        messages.success(request, "Cotización eliminada correctamente.")
        return redirect(next_url or "operaciones:listar_servicios_pm")

    servicio.delete()
    messages.success(request, "Cotización eliminada correctamente.")
    return redirect(next_url or "operaciones:listar_servicios_pm")


def obtener_datos_sitio(request):
    id_claro = request.GET.get('id_claro')
    try:
        sitio = SitioMovil.objects.get(id_claro=id_claro)
        data = {
            'region': sitio.region,
            'id_new': sitio.id_sites_new  # <- nombre correcto del campo
        }
        return JsonResponse(data)
    except SitioMovil.DoesNotExist:
        return JsonResponse({'error': 'No encontrado'}, status=404)


@login_required
@rol_requerido("pm", "admin", "facturacion")
@require_POST
def aprobar_cotizacion(request, pk):
    cotizacion = get_object_or_404(ServicioCotizado, pk=pk)

    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if next_url and not next_url.startswith("/"):
        next_url = ""

    # solo permite aprobar si está en 'cotizado'
    if cotizacion.estado != "cotizado":
        messages.warning(request, "Esta cotización ya no está en estado 'cotizado'.")
        return redirect(next_url or "operaciones:listar_servicios_pm")

    cotizacion.estado = "aprobado_pendiente"
    cotizacion.pm_aprueba = request.user
    cotizacion.save()

    du_formateado = f"DU{str(cotizacion.du).zfill(8)}"

    # Notificar supervisores reales
    supervisores = CustomUser.objects.filter(roles__nombre="supervisor", is_active=True)

    for supervisor in supervisores:
        crear_notificacion(
            usuario=supervisor,
            mensaje=f"Se ha aprobado una nueva cotización {du_formateado}.",
            url=reverse("operaciones:asignar_cotizacion", args=[cotizacion.pk]),
        )

    messages.success(request, f"Cotización {du_formateado} aprobada correctamente.")

    return redirect(next_url or "operaciones:listar_servicios_pm")

# ============================================================
# BUSCAR SITIO PLANIFICADO PARA SERVICIO COTIZADO
# ============================================================


def obtener_sitio_planificado_para_servicio(
    *,
    id_claro,
    mes_produccion,
):
    """
    Busca la ejecución mensual exacta de un sitio.

    REGLA:
        ID Claro
        +
        año de producción
        +
        mes de producción

    No utiliza:
        - último SitioPlanificado
        - último ServicioCotizado
        - fecha de creación
        - estado histórico

    Si no existe una coincidencia exacta o existe más de una,
    devuelve None.
    """

    import unicodedata

    from planificacion.models import SitioPlanificado

    MESES = {
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

    id_claro = str(id_claro or "").strip()

    if not id_claro:
        return None

    texto = str(mes_produccion or "").strip().lower()

    if not texto:
        return None

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caracter for caracter in texto if not unicodedata.combining(caracter)
    )

    texto = texto.strip()

    anio = None
    mes = None

    # ========================================================
    # YYYY-MM
    # ========================================================

    if len(texto) == 7 and texto[4] == "-":

        try:

            anio = int(texto[:4])

            mes = int(texto[5:7])

        except ValueError:

            anio = None
            mes = None

    # ========================================================
    # MM/YYYY
    # ========================================================

    elif "/" in texto:

        partes = [parte.strip() for parte in texto.split("/")]

        if len(partes) == 2:

            try:

                mes = int(partes[0])

                anio = int(partes[1])

            except ValueError:

                anio = None
                mes = None

    # ========================================================
    # AGOSTO 2026
    # ========================================================

    else:

        partes = texto.replace(
            "-",
            " ",
        ).split()

        if len(partes) >= 2:

            mes = MESES.get(partes[0])

            try:

                anio = int(partes[-1])

            except ValueError:

                anio = None

    if not anio or not mes or not 1 <= mes <= 12:
        return None

    candidatos = list(
        SitioPlanificado.objects.filter(
            sitio__id_claro=id_claro,
            planificacion__anio=anio,
            planificacion__mes=mes,
        )
        .select_related(
            "sitio",
            "planificacion",
        )
        .order_by(
            "id",
        )[:2]
    )

    if len(candidatos) != 1:
        return None

    return candidatos[0]


@login_required
@rol_requerido(
    "pm",
    "admin",
    "facturacion",
)
def importar_cotizaciones(
    request,
):
    if request.method == "POST" and request.FILES.get("archivo"):

        archivo = request.FILES["archivo"]

        try:

            # ====================================================
            # CARGAR ARCHIVO
            # ====================================================

            if archivo.name.endswith(".csv"):

                df = pd.read_csv(archivo)

            else:

                df = pd.read_excel(archivo)

            # ====================================================
            # ENCABEZADOS
            # ====================================================

            encabezados_validos = {
                "ID CLARO": "id_claro",
                "Id Claro": "id_claro",
                "REGION": "region",
                "REGIÓN": "region",
                "MES PRODUCCION": "mes_produccion",
                "Mes Producción": "mes_produccion",
                "ID NEW": "id_new",
                "DETALLE TAREA": "detalle_tarea",
                "MONTO COTIZADO": "monto_cotizado",
                "MONTO MMOO": "monto_mmoo",
            }

            df.rename(
                columns=encabezados_validos,
                inplace=True,
            )

            columnas_requeridas = [
                "id_claro",
                "mes_produccion",
                "detalle_tarea",
                "monto_cotizado",
            ]

            for col in columnas_requeridas:

                if col not in df.columns:

                    messages.error(
                        request,
                        ("Falta la columna requerida: " f"{col}"),
                    )

                    return redirect("operaciones:listar_servicios_pm")

            # ====================================================
            # RESULTADOS
            # ====================================================

            cotizaciones_omitidas = []

            cotizaciones_creadas = []

            cotizaciones_vinculadas = 0

            cotizaciones_sin_planificacion = []

            # ====================================================
            # FILAS
            # ====================================================

            for _, row in df.iterrows():

                id_claro = str(row["id_claro"]).strip()

                # ================================================
                # REGION
                # ================================================

                region = (
                    row["region"]
                    if ("region" in row and not pd.isna(row["region"]))
                    else (id_claro.split("_")[0] if "_" in id_claro else "13")
                )

                # ================================================
                # ID NEW
                # ================================================

                if "id_new" in row and not pd.isna(row["id_new"]):

                    id_new = row["id_new"]

                else:

                    try:

                        sitio = SitioMovil.objects.get(id_claro=id_claro)

                        id_new = sitio.id_sites_new

                    except SitioMovil.DoesNotExist:

                        messages.warning(
                            request,
                            (
                                "No se encontró ID NEW "
                                "para ID CLARO "
                                f"{id_claro}. Se omitió."
                            ),
                        )

                        continue

                # ================================================
                # MES PRODUCCIÓN
                # ================================================

                valor = row["mes_produccion"]

                if isinstance(
                    valor,
                    (
                        datetime,
                        pd.Timestamp,
                    ),
                ):

                    mes_produccion = valor.strftime("%B %Y").capitalize()

                else:

                    try:

                        fecha_parseada = pd.to_datetime(
                            str(valor),
                            dayfirst=True,
                            errors="coerce",
                        )

                        mes_produccion = (
                            fecha_parseada.strftime("%B %Y").capitalize()
                            if not pd.isna(fecha_parseada)
                            else str(valor).capitalize()
                        )

                    except Exception:

                        mes_produccion = str(valor).capitalize()

                # ================================================
                # EVITAR DUPLICADO
                # ================================================

                existente = (
                    ServicioCotizado.objects.filter(mes_produccion=mes_produccion)
                    .filter(models.Q(id_claro=id_claro) | models.Q(id_new=id_new))
                    .first()
                )

                if existente:

                    cotizaciones_omitidas.append(
                        {
                            "id_claro": id_claro,
                            "id_new": id_new,
                            "mes_produccion": mes_produccion,
                            "du": existente.du,
                            "estado": (existente.get_estado_display()),
                        }
                    )

                    continue

                # ================================================
                # BUSCAR EJECUCIÓN MENSUAL EXACTA
                # ================================================

                sitio_planificado = obtener_sitio_planificado_para_servicio(
                    id_claro=id_claro,
                    mes_produccion=mes_produccion,
                )

                # ================================================
                # CREAR SERVICIO
                # ================================================

                servicio = ServicioCotizado.objects.create(
                    id_claro=id_claro,
                    region=region,
                    mes_produccion=mes_produccion,
                    id_new=id_new,
                    detalle_tarea=row["detalle_tarea"],
                    monto_cotizado=row["monto_cotizado"],
                    monto_mmoo=(
                        row["monto_mmoo"]
                        if ("monto_mmoo" in row and not pd.isna(row["monto_mmoo"]))
                        else None
                    ),
                    estado="cotizado",
                    creado_por=request.user,
                    sitio_planificado=(sitio_planificado),
                )

                if sitio_planificado:

                    cotizaciones_vinculadas += 1

                else:

                    cotizaciones_sin_planificacion.append(
                        {
                            "servicio_id": (servicio.pk),
                            "du": (servicio.du),
                            "id_claro": (id_claro),
                            "mes_produccion": (mes_produccion),
                        }
                    )

                cotizaciones_creadas.append((f"{id_claro} - " f"{mes_produccion}"))

            # ====================================================
            # CONFLICTOS EXISTENTES
            # ====================================================

            if cotizaciones_omitidas:

                request.session["cotizaciones_omitidas"] = cotizaciones_omitidas

                messages.warning(
                    request,
                    ("Se detectaron cotizaciones " "ya registradas."),
                )

                return redirect("operaciones:" "advertencia_cotizaciones_omitidas")

            # ====================================================
            # RESULTADO
            # ====================================================

            mensaje = (
                "Se importaron correctamente "
                f"{len(cotizaciones_creadas)} "
                "cotizaciones. "
                f"{cotizaciones_vinculadas} "
                "quedaron vinculadas automáticamente "
                "a su planificación mensual."
            )

            if cotizaciones_sin_planificacion:

                mensaje += (
                    f" {len(cotizaciones_sin_planificacion)} "
                    "no tenían una planificación mensual "
                    "exacta disponible y quedaron sin vínculo."
                )

            messages.success(
                request,
                mensaje,
            )

            return redirect("operaciones:listar_servicios_pm")

        except Exception as e:

            messages.error(
                request,
                f"Error al importar: {e}",
            )

            return redirect("operaciones:listar_servicios_pm")

    return render(
        request,
        "operaciones/importar_cotizaciones.html",
    )


@login_required
@rol_requerido('pm', 'admin', 'facturacion')
def advertencia_cotizaciones_omitidas(request):
    cotizaciones = request.session.get('cotizaciones_omitidas', [])

    if request.method == 'POST':
        if 'continuar' in request.POST:
            del request.session['cotizaciones_omitidas']
            messages.info(
                request, "Las cotizaciones omitidas fueron ignoradas. Las demás se importaron correctamente.")
            return redirect('operaciones:listar_servicios_pm')
        else:
            del request.session['cotizaciones_omitidas']
            messages.warning(request, "La importación fue cancelada.")
            return redirect('operaciones:listar_servicios_pm')

    return render(request, 'operaciones/advertencia_duplicados.html', {
        'cotizaciones': cotizaciones
    })


@login_required
@rol_requerido("supervisor", "admin", "facturacion", "pm")
def listar_servicios_supervisor(request):
    """
    Vista supervisor con filtros tipo Excel globales por columna.

    Importante:
    - Los filtros Excel se aplican antes de paginar.
    - excel_global_json se construye sobre todo el queryset filtrado, no solo sobre la página.
    - La tabla se recarga por AJAX reemplazando solo #zonaTabla.
    """
    import json
    from urllib.parse import urlencode

    from django.core.paginator import Paginator
    from django.db.models import Case, IntegerField, Q, Value, When

    estado_prioridad = Case(
        When(estado="aprobado_pendiente", then=Value(1)),
        When(estado__in=["asignado", "en_progreso"], then=Value(2)),
        When(
            estado__in=["en_revision_supervisor", "finalizado_trabajador"],
            then=Value(3),
        ),
        When(
            estado__in=[
                "informe_subido",
                "finalizado",
                "aprobado_supervisor",
                "rechazado_supervisor",
            ],
            then=Value(4),
        ),
        default=Value(5),
        output_field=IntegerField(),
    )

    servicios = (
        ServicioCotizado.objects.filter(
            estado__in=[
                "aprobado_pendiente",
                "asignado",
                "en_progreso",
                "finalizado_trabajador",
                "en_revision_supervisor",
                "aprobado_supervisor",
                "rechazado_supervisor",
                "informe_subido",
                "finalizado",
            ]
        )
        .exclude(estado__in=["ajuste_bono", "ajuste_adelanto", "ajuste_descuento"])
        .prefetch_related(
            "trabajadores_asignados",
            "sesion_fotos__asignaciones__tecnico",
        )
        .annotate(prioridad=estado_prioridad)
        .order_by("prioridad", "-du")
    )

    # ---------------- Filtros rápidos normales ----------------
    du_raw = (request.GET.get("du") or "").strip()
    id_claro = (request.GET.get("id_claro") or "").strip()
    id_new = (request.GET.get("id_new") or "").strip()
    mes_produccion = (request.GET.get("mes_produccion") or "").strip()
    estado = (request.GET.get("estado") or "").strip()

    du = du_raw

    if du:
        du = du.strip().upper().replace("DU", "")
        servicios = servicios.filter(du__iexact=du)

    if id_claro:
        servicios = servicios.filter(id_claro__icontains=id_claro)

    if id_new:
        servicios = servicios.filter(id_new__icontains=id_new)

    if mes_produccion:
        servicios = servicios.filter(mes_produccion__icontains=mes_produccion)

    if estado:
        servicios = servicios.filter(estado=estado)

    servicios = servicios.distinct()

    # ---------------- Helpers Excel ----------------
    def money_label(n):
        try:
            return f"$ {int(n or 0):,} CLP".replace(",", ".")
        except Exception:
            return "$ 0 CLP"

    def asignados_lista(servicio):
        vals = []

        try:
            for tecnico in servicio.trabajadores_asignados.all():
                nombre = (tecnico.get_full_name() or tecnico.username or "").strip()
                if nombre:
                    vals.append(nombre)
        except Exception:
            pass

        return vals

    def asignados_label(servicio):
        vals = asignados_lista(servicio)
        return ", ".join(vals) if vals else "Sin asignar"

    def fecha_fin_label(servicio):
        try:
            if (
                servicio.estado == "aprobado_supervisor"
                and servicio.fecha_aprobacion_supervisor
            ):
                return servicio.fecha_aprobacion_supervisor.strftime("%d/%m/%Y %H:%M")

            if servicio.fecha_fin:
                return servicio.fecha_fin.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass

        return "—"

    def status_label(servicio):
        if servicio.estado == "aprobado_pendiente":
            return "Aprobado por PM"

        if servicio.estado == "asignado":
            return "Asignado"

        if servicio.estado == "en_progreso":
            return "En ejecución"

        if servicio.estado in ["en_revision_supervisor", "finalizado_trabajador"]:
            return "Pendiente revisión supervisor"

        if servicio.estado == "rechazado_supervisor":
            return "Rechazado por Supervisor"

        if servicio.estado == "aprobado_supervisor":
            return "Aprobado por Supervisor"

        if servicio.estado == "ajuste_bono":
            return "Bono"

        if servicio.estado == "ajuste_adelanto":
            return "Adelanto"

        if servicio.estado == "ajuste_descuento":
            return "Descuento"

        return str(servicio.estado or "—")

    def excel_value_for_servicio(servicio, col):
        col = str(col)

        if col == "0":
            return f"DU{servicio.du}" if servicio.du is not None else "—"

        if col == "1":
            return str(servicio.id_claro or "—")

        if col == "2":
            return str(servicio.region or "—")

        if col == "3":
            return str(servicio.mes_produccion or "—")

        if col == "4":
            return str(servicio.id_new or "—")

        if col == "5":
            return str(servicio.detalle_tarea or "—")

        if col == "6":
            return money_label(servicio.monto_mmoo)

        if col == "7":
            return asignados_label(servicio)

        if col == "8":
            return fecha_fin_label(servicio)

        if col == "9":
            return status_label(servicio)

        if col == "10":
            return "Acciones"

        return ""

    def excel_values_for_servicio(servicio, col):
        """
        Devuelve todos los valores posibles por columna para filtrar.
        En ASIGNADOS devuelve:
        - cada técnico individual
        - y también el texto combinado
        """
        col = str(col)

        if col == "7":
            individuales = asignados_lista(servicio)

            if not individuales:
                return ["Sin asignar"]

            combinado = ", ".join(individuales)
            vals = list(individuales)

            if combinado and combinado not in vals:
                vals.append(combinado)

            return vals

        return [excel_value_for_servicio(servicio, col)]

    # ---------------- Filtros Excel globales ----------------
    excel_filters_raw = (request.GET.get("excel_filters") or "").strip()

    try:
        excel_filters = json.loads(excel_filters_raw) if excel_filters_raw else {}
    except json.JSONDecodeError:
        excel_filters = {}

    if not isinstance(excel_filters, dict):
        excel_filters = {}

    servicios_base_excel = list(servicios)

    def pasa_filtros_excel(servicio, filtros, skip_col=None):
        """
        Aplica filtros Excel.
        skip_col permite construir las opciones de una columna ignorando
        el filtro de esa misma columna, para poder acumular varios valores.
        """
        if not filtros:
            return True

        for col, values in filtros.items():
            col = str(col)

            if skip_col is not None and col == str(skip_col):
                continue

            values_set = {str(v).strip() for v in (values or []) if str(v).strip()}

            if not values_set:
                continue

            row_values = {
                str(v).strip()
                for v in excel_values_for_servicio(servicio, col)
                if str(v).strip()
            }

            if not row_values.intersection(values_set):
                return False

        return True

    if excel_filters:
        servicios_list = [
            servicio
            for servicio in servicios_base_excel
            if pasa_filtros_excel(servicio, excel_filters)
        ]
    else:
        servicios_list = servicios_base_excel

    # ---------------- Globales para panel Excel ----------------
    excel_global = {}

    for col in range(11):
        vals = set()

        for servicio in servicios_base_excel:
            if not pasa_filtros_excel(servicio, excel_filters, skip_col=str(col)):
                continue

            for value in excel_values_for_servicio(servicio, str(col)):
                vals.add(value or "(Vacías)")

        excel_global[col] = sorted(vals, key=lambda x: str(x).lower())

    excel_global_json = json.dumps(excel_global, ensure_ascii=False)

    # ---------------- Paginación ----------------
    cantidad_param = request.GET.get("cantidad", "10")

    if cantidad_param == "todos":
        per_page = 100
        cantidad = "100"
    else:
        try:
            per_page = max(5, min(int(cantidad_param), 100))
            cantidad = str(per_page)
        except ValueError:
            per_page = 10
            cantidad = "10"

    paginator = Paginator(servicios_list, per_page)
    page_number = request.GET.get("page") or 1
    pagina = paginator.get_page(page_number)

    # ---------------- Info por fila ----------------
    pagina_info = []

    estados_aceptados = {
        "en_proceso",
        "en_revision_supervisor",
        "aprobado_supervisor",
        "aprobado_pm",
    }

    for servicio in pagina:
        asignados = list(servicio.trabajadores_asignados.all())
        asignados_ids = {u.id for u in asignados}

        aceptados = []
        pendientes = []

        try:
            sesion = servicio.sesion_fotos
        except SesionFotos.DoesNotExist:
            sesion = None

        asignaciones = list(sesion.asignaciones.all()) if sesion else []

        asignaciones_map = {
            a.tecnico_id: a for a in asignaciones if a.tecnico_id in asignados_ids
        }

        for tecnico in asignados:
            asg = asignaciones_map.get(tecnico.id)

            if asg and asg.estado in estados_aceptados:
                aceptados.append(tecnico)
            else:
                pendientes.append(tecnico)

        pagina_info.append(
            {
                "servicio": servicio,
                "aceptados_lista": aceptados,
                "pendientes_lista": pendientes,
                "aceptados_count": len(aceptados),
                "pendientes_count": len(pendientes),
                "total_count": len(asignados),
            }
        )

    # ---------------- Mantener parámetros ----------------
    keep_params = {}

    if cantidad:
        keep_params["cantidad"] = cantidad

    if du_raw:
        keep_params["du"] = du_raw

    if id_claro:
        keep_params["id_claro"] = id_claro

    if id_new:
        keep_params["id_new"] = id_new

    if mes_produccion:
        keep_params["mes_produccion"] = mes_produccion

    if estado:
        keep_params["estado"] = estado

    if excel_filters_raw:
        keep_params["excel_filters"] = excel_filters_raw

    qs_keep = urlencode(keep_params)

    return render(
        request,
        "operaciones/listar_servicios_supervisor.html",
        {
            "pagina_info": pagina_info,
            "pagina": pagina,
            "cantidad": cantidad,
            "filtros": {
                "du": du_raw,
                "id_claro": id_claro,
                "id_new": id_new,
                "mes_produccion": mes_produccion,
                "estado": estado,
            },
            "estado_choices": ServicioCotizado.ESTADOS,
            "excel_global_json": excel_global_json,
            "qs_keep": qs_keep,
        },
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def reabrir_servicio(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    next_url = request.POST.get("next") or request.GET.get("next") or ""

    if next_url and not next_url.startswith("/"):
        next_url = ""

    if servicio.estado != "aprobado_supervisor":
        messages.error(
            request, "Solo se pueden reabrir servicios aprobados por el supervisor."
        )
        return redirect(next_url or "operaciones:listar_servicios_supervisor")

    motivo = (request.POST.get("motivo") or "").strip()
    if not motivo:
        messages.error(request, "Debes indicar un motivo para reabrir el servicio.")
        return redirect(next_url or "operaciones:listar_servicios_supervisor")

    with transaction.atomic():
        servicio.motivo_rechazo = motivo
        servicio.supervisor_aprobo = None
        servicio.supervisor_rechazo = None
        servicio.tecnico_finalizo = None
        servicio.tecnico_aceptado = None
        servicio.estado = "asignado"
        servicio.save(
            update_fields=[
                "motivo_rechazo",
                "supervisor_aprobo",
                "supervisor_rechazo",
                "tecnico_finalizo",
                "tecnico_aceptado",
                "estado",
            ]
        )

        sesion = _get_or_create_sesion(servicio)

        qs = SesionFotoTecnico.objects.filter(sesion=sesion)
        update_vals = {"estado": "asignado"}

        if hasattr(SesionFotoTecnico, "aceptado_en"):
            update_vals["aceptado_en"] = None

        if hasattr(SesionFotoTecnico, "finalizado_en"):
            update_vals["finalizado_en"] = None

        if hasattr(SesionFotoTecnico, "rechazado_en"):
            update_vals["rechazado_en"] = None

        qs.update(**update_vals)

    messages.success(request, f"Servicio DU{servicio.du} reabierto. Motivo: {motivo}")

    return redirect(next_url or "operaciones:listar_servicios_supervisor")


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@csrf_exempt
def actualizar_motivo_rechazo(request, pk):
    if request.method == 'POST':
        servicio = get_object_or_404(ServicioCotizado, pk=pk)
        nuevo_motivo = request.POST.get('motivo', '').strip()
        servicio.motivo_rechazo = nuevo_motivo
        servicio.save()
        return JsonResponse({'success': True, 'motivo': nuevo_motivo})
    return JsonResponse({'success': False}, status=400)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def asignar_trabajadores(request, pk):
    """
    Soporta que pk venga como:
      - ID real (ServicioCotizado.id)
      - o DU (ServicioCotizado.du), con o sin ceros (83 / 00000083)

    Modos:
      - reasignar: reemplaza lista completa
      - agregar: mantiene los actuales y solo agrega nuevos
    """

    next_url = (
        request.POST.get("next")
        or request.GET.get("next")
        or ""
    )

    if next_url and not next_url.startswith("/"):
        next_url = ""

    cotizacion = ServicioCotizado.objects.filter(pk=pk).first()

    if not cotizacion:
        du_raw = str(pk).strip()

        candidates = []
        if du_raw:
            candidates.append(du_raw)

            try:
                candidates.append(str(int(du_raw)))
            except Exception:
                pass

            if du_raw.isdigit():
                candidates.append(du_raw.zfill(8))

        cotizacion = (
            ServicioCotizado.objects
            .filter(du__in=list(dict.fromkeys(candidates)))
            .first()
        )

    if not cotizacion:
        messages.error(
            request,
            "No se encontró la cotización (ID/DU). Puede haber sido eliminada o no existe."
        )
        return redirect(next_url or 'operaciones:listar_servicios_supervisor')

    modo = (request.GET.get('modo') or request.POST.get('modo') or 'reasignar').strip().lower()
    if modo not in {'reasignar', 'agregar'}:
        modo = 'reasignar'

    if request.method == 'POST':
        form = AsignarTrabajadoresForm(request.POST)
        dejar_pendiente = request.POST.get('dejar_pendiente_asignacion') == '1'

        if dejar_pendiente:
            with transaction.atomic():
                cotizacion.trabajadores_asignados.clear()

                cotizacion.estado = 'aprobado_pendiente'
                cotizacion.supervisor_asigna = request.user
                cotizacion.tecnico_aceptado = None
                cotizacion.tecnico_finalizo = None
                cotizacion.supervisor_aprobo = None
                cotizacion.supervisor_rechazo = None

                update_fields = [
                    'estado',
                    'supervisor_asigna',
                    'tecnico_aceptado',
                    'tecnico_finalizo',
                    'supervisor_aprobo',
                    'supervisor_rechazo',
                ]

                if hasattr(cotizacion, 'fecha_aprobacion_supervisor'):
                    cotizacion.fecha_aprobacion_supervisor = None
                    update_fields.append('fecha_aprobacion_supervisor')

                cotizacion.save(update_fields=update_fields)

                sesion = _get_or_create_sesion(cotizacion)

                qs = SesionFotoTecnico.objects.filter(sesion=sesion)
                vals = {'estado': 'asignado'}

                if hasattr(SesionFotoTecnico, 'aceptado_en'):
                    vals['aceptado_en'] = None

                if hasattr(SesionFotoTecnico, 'finalizado_en'):
                    vals['finalizado_en'] = None

                if hasattr(SesionFotoTecnico, 'reintento_habilitado'):
                    vals['reintento_habilitado'] = False

                qs.update(**vals)

            messages.success(
                request,
                "El servicio quedó nuevamente pendiente por asignar. Se quitaron todos los técnicos y se limpió la aceptación previa."
            )
            return redirect(next_url or 'operaciones:listar_servicios_supervisor')

        if form.is_valid():
            seleccionados = form.cleaned_data['trabajadores']

            old_ids = set(cotizacion.trabajadores_asignados.values_list('id', flat=True))
            selected_ids = set(seleccionados.values_list('id', flat=True))

            if modo == 'agregar':
                removed_ids = old_ids - selected_ids

                if removed_ids:
                    messages.error(
                        request,
                        "En modo 'agregar técnicos' no puedes quitar técnicos actuales. Usa 'Reasignar' si quieres reemplazar responsables."
                    )
                    return render(request, 'operaciones/asignar_trabajadores.html', {
                        'cotizacion': cotizacion,
                        'form': form,
                        'modo': modo,
                        'next_url': next_url,
                    })

                final_ids = old_ids | selected_ids
            else:
                final_ids = selected_ids

            added_ids = final_ids - old_ids
            removed_ids = old_ids - final_ids

            primera_asignacion = not old_ids

            es_reasignacion_real = (
                not primera_asignacion
                and modo == 'reasignar'
                and bool(removed_ids)
            )

            solo_agregando = (
                not primera_asignacion
                and not removed_ids
                and bool(added_ids)
            )

            with transaction.atomic():
                cotizacion.trabajadores_asignados.set(final_ids)

                update_fields = ['supervisor_asigna']
                cotizacion.supervisor_asigna = request.user

                if primera_asignacion:
                    if cotizacion.estado == 'aprobado_pendiente':
                        cotizacion.estado = 'asignado'
                        update_fields.append('estado')

                    _sincronizar_asignaciones_sesion(
                        cotizacion,
                        tecnicos_actuales_ids=final_ids,
                        reset_para_ids=set(final_ids),
                    )

                elif es_reasignacion_real:
                    cotizacion.estado = 'asignado'
                    cotizacion.tecnico_aceptado = None
                    cotizacion.tecnico_finalizo = None
                    cotizacion.supervisor_aprobo = None
                    cotizacion.supervisor_rechazo = None

                    update_fields.extend([
                        'estado',
                        'tecnico_aceptado',
                        'tecnico_finalizo',
                        'supervisor_aprobo',
                        'supervisor_rechazo',
                    ])

                    if hasattr(cotizacion, 'fecha_aprobacion_supervisor'):
                        cotizacion.fecha_aprobacion_supervisor = None
                        update_fields.append('fecha_aprobacion_supervisor')

                    _sincronizar_asignaciones_sesion(
                        cotizacion,
                        tecnicos_actuales_ids=final_ids,
                        reset_para_ids=set(final_ids),
                    )

                elif solo_agregando:
                    _sincronizar_asignaciones_sesion(
                        cotizacion,
                        tecnicos_actuales_ids=final_ids,
                        reset_para_ids=set(added_ids),
                    )

                else:
                    _sincronizar_asignaciones_sesion(
                        cotizacion,
                        tecnicos_actuales_ids=final_ids,
                        reset_para_ids=set(),
                    )

                cotizacion.save(update_fields=update_fields)

            if primera_asignacion or es_reasignacion_real:
                usuarios_a_notificar = list(CustomUser.objects.filter(id__in=final_ids))
            else:
                usuarios_a_notificar = list(CustomUser.objects.filter(id__in=added_ids))

            for trabajador in usuarios_a_notificar:
                crear_notificacion(
                    usuario=trabajador,
                    mensaje=f"Se te ha asignado una nueva tarea: DU{str(cotizacion.du).zfill(8)}.",
                    url=reverse('operaciones:mis_servicios_tecnico'),
                )

            try:
                if usuarios_a_notificar:
                    enlace_app = request.build_absolute_uri(
                        reverse('operaciones:mis_servicios_tecnico')
                    )

                    logs = notificar_asignacion_servicio_tecnicos(
                        servicio=cotizacion,
                        actor=request.user,
                        url=enlace_app,
                        extra={
                            "du": cotizacion.du,
                            "id_claro": cotizacion.id_claro,
                        },
                    )

                    for log in logs:
                        logger.info(
                            "Telegram asignación servicio DU%s -> usuario_id=%s status=%s error=%s",
                            str(cotizacion.du).zfill(8),
                            log.usuario_id,
                            log.status,
                            getattr(log, "error", ""),
                        )

            except Exception:
                logger.exception("Error enviando notificación Telegram de asignación")

            if primera_asignacion:
                messages.success(request, "Trabajadores asignados correctamente.")
            elif es_reasignacion_real:
                messages.success(
                    request,
                    "Servicio reasignado correctamente. El estado volvió a 'asignado' y los técnicos actuales deben aceptar nuevamente."
                )
            elif solo_agregando:
                messages.success(
                    request,
                    "Técnicos agregados correctamente. Los nuevos deben aceptar su asignación."
                )
            else:
                messages.success(request, "Asignación actualizada correctamente.")

            return redirect(next_url or 'operaciones:listar_servicios_supervisor')

    else:
        inicial_ids = list(cotizacion.trabajadores_asignados.values_list('id', flat=True))
        form = AsignarTrabajadoresForm(
            initial={"trabajadores": inicial_ids}
        )

    return render(request, 'operaciones/asignar_trabajadores.html', {
        'cotizacion': cotizacion,
        'form': form,
        'modo': modo,
        'next_url': next_url,
    })


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def exportar_servicios_supervisor(request):
    servicios = ServicioCotizado.objects.filter(
        estado__in=[
            'aprobado_pendiente', 'asignado', 'en_proceso',
            'en_revision_supervisor',
            'rechazado_supervisor', 'aprobado_supervisor'
        ]
    )

    data = []
    for s in servicios:
        asignados = ', '.join(
            [f"{u.first_name} {u.last_name}" for u in s.trabajadores_asignados.all()]
        )
        data.append({
            'DU': f'DU{s.du}',
            'ID Claro': s.id_claro,
            'Región': s.region,
            'Mes Producción': s.mes_produccion or '',
            'ID NEW': s.id_new,
            'Detalle Tarea': s.detalle_tarea,
            'Monto MMOO': float(s.monto_mmoo) if s.monto_mmoo else 0,
            'Asignados': asignados,
            'Fecha Fin': s.fecha_aprobacion_supervisor.strftime("%d-%m-%Y") if s.fecha_aprobacion_supervisor else '',
            'Estado': dict(s.ESTADOS).get(s.estado, s.estado),
        })

    df = pd.DataFrame(data)
    columnas = [
        'DU', 'ID Claro', 'Región', 'Mes Producción',
        'ID NEW', 'Detalle Tarea', 'Monto MMOO',
        'Asignados', 'Fecha Fin', 'Estado'
    ]
    df = df[columnas]

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=servicios_supervisor.xlsx'
    df.to_excel(response, index=False)
    return response


@login_required
@rol_requerido("usuario")
def mis_servicios_tecnico(request):
    """
    Muestra todos los servicios asignados al técnico autenticado.

    Orden:
    1. Mes actual.
    2. Meses anteriores, desde el más reciente.
    3. Meses futuros, desde el más cercano.
    4. Meses que no puedan interpretarse.

    Dentro de cada mes:
    1. En progreso.
    2. Finalizado por trabajador.
    3. Asignado.
    4. Otros estados.

    También permite:
    - filtrar por las columnas principales;
    - seleccionar la cantidad de filas;
    - paginar sin perder los filtros.
    """

    usuario = request.user
    hoy = timezone.localdate()

    # ============================================================
    # CONFIGURACIÓN DE MESES
    # ============================================================

    MESES_ES = {
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

    indice_mes_actual = hoy.year * 12 + hoy.month

    def _normalizar_texto(valor):
        """
        Normaliza un texto para comparar meses sin depender de
        mayúsculas, espacios o tildes.
        """

        texto = str(valor or "").strip().lower()

        texto = unicodedata.normalize(
            "NFKD",
            texto,
        )

        texto = "".join(
            caracter for caracter in texto if not unicodedata.combining(caracter)
        )

        return re.sub(
            r"\s+",
            " ",
            texto,
        )

    def _extraer_anio_mes(valor):
        """
        Convierte diferentes formatos de mes_produccion en:

            (año, mes)

        Reconoce:
        - Julio 2026
        - julio de 2026
        - 2026-07
        - 2026/07
        - 07-2026
        - 07/2026
        """

        texto = _normalizar_texto(valor)

        if not texto:
            return None

        # YYYY-MM o YYYY/MM
        coincidencia = re.fullmatch(
            r"(\d{4})[-/](\d{1,2})",
            texto,
        )

        if coincidencia:
            anio = int(coincidencia.group(1))

            mes = int(coincidencia.group(2))

            if 1 <= mes <= 12:
                return anio, mes

        # MM-YYYY o MM/YYYY
        coincidencia = re.fullmatch(
            r"(\d{1,2})[-/](\d{4})",
            texto,
        )

        if coincidencia:
            mes = int(coincidencia.group(1))

            anio = int(coincidencia.group(2))

            if 1 <= mes <= 12:
                return anio, mes

        # Julio 2026 o Julio de 2026
        coincidencia = re.fullmatch(
            r"([a-z]+)(?:\s+de)?\s+(\d{4})",
            texto,
        )

        if coincidencia:
            nombre_mes = coincidencia.group(1)

            anio = int(coincidencia.group(2))

            mes = MESES_ES.get(nombre_mes)

            if mes:
                return anio, mes

        return None

    def _prioridad_estado(servicio):
        """
        Define el orden operativo dentro de cada mes.
        """

        prioridades = {
            "en_progreso": 1,
            "finalizado_trabajador": 2,
            "asignado": 3,
        }

        return prioridades.get(
            servicio.estado,
            4,
        )

    def _du_numerico(servicio):
        """
        Convierte el DU a número para que 50 sea mayor que 9,
        aunque el campo esté guardado como texto.
        """

        texto = str(servicio.du or "")

        numeros = re.sub(
            r"\D",
            "",
            texto,
        )

        try:
            return int(numeros)

        except (
            TypeError,
            ValueError,
        ):
            return 0

    def _clave_orden_servicio(servicio):
        """
        Categorías de orden:

        0 = mes actual
        1 = meses anteriores
        2 = meses futuros
        3 = mes no reconocido
        """

        anio_mes = _extraer_anio_mes(servicio.mes_produccion)

        prioridad_estado = _prioridad_estado(servicio)

        du_numerico = _du_numerico(servicio)

        if not anio_mes:
            return (
                3,
                0,
                prioridad_estado,
                -du_numerico,
            )

        anio, mes = anio_mes

        indice_servicio = anio * 12 + mes

        # Mes actual.
        if indice_servicio == indice_mes_actual:
            return (
                0,
                0,
                prioridad_estado,
                -du_numerico,
            )

        # Meses anteriores: más reciente primero.
        if indice_servicio < indice_mes_actual:
            return (
                1,
                -indice_servicio,
                prioridad_estado,
                -du_numerico,
            )

        # Meses futuros: más cercano primero.
        return (
            2,
            indice_servicio,
            prioridad_estado,
            -du_numerico,
        )

    def _clave_orden_mes_texto(valor):
        """
        Ordena los valores del selector de meses.

        Primero el mes actual, después los anteriores y finalmente
        los futuros o no reconocidos.
        """

        anio_mes = _extraer_anio_mes(valor)

        if not anio_mes:
            return (
                3,
                0,
                str(valor or ""),
            )

        anio, mes = anio_mes

        indice = anio * 12 + mes

        if indice == indice_mes_actual:
            return (
                0,
                0,
                "",
            )

        if indice < indice_mes_actual:
            return (
                1,
                -indice,
                "",
            )

        return (
            2,
            indice,
            "",
        )

    # ============================================================
    # FILTROS GET
    # ============================================================

    filtro_du = request.GET.get(
        "du",
        "",
    ).strip()

    filtro_id_claro = request.GET.get(
        "id_claro",
        "",
    ).strip()

    filtro_region = request.GET.get(
        "region",
        "",
    ).strip()

    filtro_mes = request.GET.get(
        "mes_produccion",
        "",
    ).strip()

    filtro_id_new = request.GET.get(
        "id_new",
        "",
    ).strip()

    filtro_detalle = request.GET.get(
        "detalle",
        "",
    ).strip()

    filtro_estado = request.GET.get(
        "estado",
        "",
    ).strip()

    cantidad = request.GET.get(
        "cantidad",
        "10",
    ).strip()

    cantidades_permitidas = {
        "10",
        "20",
        "50",
        "100",
    }

    if cantidad not in cantidades_permitidas:
        cantidad = "20"

    cantidad_int = int(cantidad)

    # ============================================================
    # ESTADOS QUE NO DEBEN APARECER
    # ============================================================

    AJUSTES_SET = {
        "ajuste_bono",
        "ajuste_adelanto",
        "ajuste_descuento",
    }

    # ============================================================
    # CONSULTA BASE
    # ============================================================

    queryset_base = (
        ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
        )
        .exclude(
            estado__in=[
                "cotizado",
                "aprobado_supervisor",
            ]
            + list(AJUSTES_SET)
        )
        .prefetch_related(
            "trabajadores_asignados",
        )
        .select_related(
            "tecnico_aceptado",
            "tecnico_finalizo",
            "supervisor_aprobo",
            "supervisor_rechazo",
        )
        .distinct()
    )

    # Opciones de meses antes de aplicar el filtro de mes.
    meses_disponibles = list(
        queryset_base.exclude(
            mes_produccion__isnull=True,
        )
        .exclude(
            mes_produccion="",
        )
        .values_list(
            "mes_produccion",
            flat=True,
        )
        .distinct()
    )

    meses_disponibles.sort(key=_clave_orden_mes_texto)

    # ============================================================
    # APLICAR FILTROS
    # ============================================================

    if filtro_du:
        du_limpio = re.sub(
            r"(?i)^du",
            "",
            filtro_du,
        ).strip()

        queryset_base = queryset_base.filter(
            Q(du__icontains=filtro_du) | Q(du__icontains=du_limpio)
        )

    if filtro_id_claro:
        queryset_base = queryset_base.filter(
            id_claro__icontains=filtro_id_claro,
        )

    if filtro_region:
        queryset_base = queryset_base.filter(
            region__icontains=filtro_region,
        )

    if filtro_mes:
        queryset_base = queryset_base.filter(
            mes_produccion=filtro_mes,
        )

    if filtro_id_new:
        queryset_base = queryset_base.filter(
            id_new__icontains=filtro_id_new,
        )

    if filtro_detalle:
        queryset_base = queryset_base.filter(
            detalle_tarea__icontains=filtro_detalle,
        )

    if filtro_estado:
        queryset_base = queryset_base.filter(
            estado=filtro_estado,
        )

    # ============================================================
    # CONVERTIR Y ORDENAR
    # ============================================================

    servicios_ordenados = list(queryset_base)

    servicios_ordenados.sort(key=_clave_orden_servicio)

    total_resultados = len(servicios_ordenados)

    # ============================================================
    # PAGINACIÓN
    # ============================================================

    paginator = Paginator(
        servicios_ordenados,
        cantidad_int,
    )

    pagina = paginator.get_page(
        request.GET.get(
            "page",
            1,
        )
    )

    servicios_pagina = list(pagina.object_list)

    # Query string utilizado por los enlaces de paginación.
    parametros_sin_pagina = request.GET.copy()

    parametros_sin_pagina.pop(
        "page",
        None,
    )

    query_sin_pagina = parametros_sin_pagina.urlencode()

    # ============================================================
    # SITIOS RELACIONADOS DE LA PÁGINA ACTUAL
    # ============================================================

    ids_claro = {
        str(servicio.id_claro).strip()
        for servicio in servicios_pagina
        if servicio.id_claro
    }

    sitios_por_id_claro = {
        str(sitio.id_claro).strip(): sitio
        for sitio in SitioMovil.objects.filter(
            id_claro__in=ids_claro,
        )
    }

    def _obtener_valor_coordenada(
        objeto,
        nombres_posibles,
    ):
        """
        Busca una coordenada considerando distintos nombres
        posibles de campo.
        """

        if not objeto:
            return None

        for nombre in nombres_posibles:
            valor = getattr(
                objeto,
                nombre,
                None,
            )

            if valor not in (
                None,
                "",
            ):
                return valor

        return None

    def _normalizar_coordenada(valor):
        """
        Convierte la coordenada a un texto válido para Google Maps.
        """

        if valor in (
            None,
            "",
        ):
            return None

        texto = (
            str(valor)
            .strip()
            .replace(
                ",",
                ".",
            )
        )

        try:
            return str(float(texto))

        except (
            TypeError,
            ValueError,
        ):
            return None

    def _construir_google_maps_url(sitio):
        """
        Construye el enlace directo a Google Maps.
        """

        latitud = _obtener_valor_coordenada(
            sitio,
            [
                "latitud",
                "latitude",
                "lat",
                "coordenada_latitud",
            ],
        )

        longitud = _obtener_valor_coordenada(
            sitio,
            [
                "longitud",
                "longitude",
                "lng",
                "lon",
                "coordenada_longitud",
            ],
        )

        latitud = _normalizar_coordenada(latitud)

        longitud = _normalizar_coordenada(longitud)

        if not latitud or not longitud:
            return None

        return (
            "https://www.google.com/maps/search/" f"?api=1&query={latitud},{longitud}"
        )

    # ============================================================
    # PREPARAR INFORMACIÓN DE LA PÁGINA
    # ============================================================

    servicios_info = []

    for servicio in servicios_pagina:

        # ========================================================
        # MONTO MMOO POR TÉCNICO
        # ========================================================

        monto_total = servicio.monto_mmoo or servicio.monto_cotizado or Decimal("0")

        if not isinstance(
            monto_total,
            Decimal,
        ):
            try:
                monto_total = Decimal(str(monto_total))

            except Exception:
                monto_total = Decimal("0")

        total_tecnicos = servicio.trabajadores_asignados.count() or 1

        try:
            monto_tecnico = (monto_total / Decimal(total_tecnicos)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        except Exception:
            monto_tecnico = Decimal("0.00")

        monto_str = f"{monto_tecnico:.2f}"

        # ========================================================
        # ASIGNACIÓN INDIVIDUAL
        # ========================================================

        sesion = _get_or_create_sesion(servicio)

        asignacion = sesion.asignaciones.filter(
            tecnico=usuario,
        ).first()

        if not asignacion:
            asignacion = SesionFotoTecnico.objects.create(
                sesion=sesion,
                tecnico=usuario,
                estado="asignado",
            )

        yo_acepte = asignacion.estado == "en_proceso"

        puedo_aceptar = asignacion.estado == "asignado"

        # ========================================================
        # CORRECCIÓN DE CONSISTENCIA
        # ========================================================

        if yo_acepte and servicio.estado == "asignado":
            servicio.estado = "en_progreso"

            if not servicio.tecnico_aceptado_id:
                servicio.tecnico_aceptado = usuario

            servicio.save(
                update_fields=[
                    "estado",
                    "tecnico_aceptado",
                ]
            )

        # ========================================================
        # CONTEO DE ACEPTACIONES
        # ========================================================

        assigned_ids = list(
            servicio.trabajadores_asignados.values_list(
                "id",
                flat=True,
            )
        )

        if assigned_ids:
            aceptados = sesion.asignaciones.filter(
                aceptado_en__isnull=False,
                tecnico_id__in=assigned_ids,
            ).count()

            total = sesion.asignaciones.filter(
                tecnico_id__in=assigned_ids,
            ).count()

        else:
            aceptados = 0
            total = 0

        # ========================================================
        # UBICACIÓN
        # ========================================================

        id_claro_normalizado = (
            str(servicio.id_claro).strip() if servicio.id_claro else ""
        )

        sitio = sitios_por_id_claro.get(id_claro_normalizado)

        maps_url = _construir_google_maps_url(sitio)

        anio_mes_servicio = _extraer_anio_mes(servicio.mes_produccion)

        es_mes_actual = anio_mes_servicio == (
            hoy.year,
            hoy.month,
        )

        # ========================================================
        # TÉCNICO QUE FINALIZÓ
        # ========================================================

        nombre_tecnico_finalizo = ""

        if servicio.tecnico_finalizo_id:
            nombre_tecnico_finalizo = (
                servicio.tecnico_finalizo.get_full_name()
                or servicio.tecnico_finalizo.username
            )

        elif servicio.estado == "finalizado_trabajador":
            nombre_tecnico_finalizo = usuario.get_full_name() or usuario.username

        servicios_info.append(
            {
                "servicio": servicio,
                "monto_tecnico": monto_tecnico,
                "monto_str": monto_str,
                "yo_acepte": yo_acepte,
                "puedo_aceptar": puedo_aceptar,
                "aceptados": aceptados,
                "total": total,
                "sitio": sitio,
                "maps_url": maps_url,
                "tiene_ubicacion": bool(maps_url),
                "es_mes_actual": es_mes_actual,
                "nombre_tecnico_finalizo": (nombre_tecnico_finalizo),
            }
        )

    # ============================================================
    # CONTEXTO
    # ============================================================

    filtros = {
        "du": filtro_du,
        "id_claro": filtro_id_claro,
        "region": filtro_region,
        "mes_produccion": filtro_mes,
        "id_new": filtro_id_new,
        "detalle": filtro_detalle,
        "estado": filtro_estado,
    }

    estados_disponibles = [
        (
            "en_progreso",
            "En ejecución",
        ),
        (
            "finalizado_trabajador",
            "Pendiente revisión del supervisor",
        ),
        (
            "asignado",
            "Pendiente por aceptar",
        ),
        (
            "en_revision_supervisor",
            "En revisión supervisor",
        ),
        (
            "rechazado_supervisor",
            "Rechazado por supervisor",
        ),
    ]

    return render(
        request,
        "operaciones/mis_servicios_tecnico.html",
        {
            "servicios_info": servicios_info,
            "pagina": pagina,
            "paginator": paginator,
            "filtros": filtros,
            "cantidad": cantidad,
            "total_resultados": total_resultados,
            "meses_disponibles": meses_disponibles,
            "estados_disponibles": estados_disponibles,
            "query_sin_pagina": query_sin_pagina,
            "mes_actual_fecha": hoy.replace(day=1),
        },
    )


@login_required
@rol_requerido('usuario')
def ir_a_upload_fotos(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "No tienes permiso en este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    sesion = _get_or_create_sesion(servicio)
    a = sesion.asignaciones.filter(tecnico=request.user).first()
    if not a:
        a = SesionFotoTecnico.objects.create(
            sesion=sesion, tecnico=request.user, estado='asignado'
        )

    # ✅ Sólo puede entrar si ya aceptó (en_proceso) o si fue rechazado con reintento
    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        messages.info(
            request, "Debes aceptar tu asignación antes de subir fotos.")
        return redirect('operaciones:mis_servicios_tecnico')

    return redirect('operaciones:fotos_upload', pk=a.pk)


from django.utils import timezone


@login_required
@rol_requerido('usuario')
def aceptar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    # Debe ser un técnico asignado a este servicio
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "No tienes permiso para aceptar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Estados donde ya no corresponde aceptar
    estados_bloqueados = {
        'finalizado_trabajador',
        'en_revision_supervisor',
        'aprobado_supervisor',
        'informe_subido',
        'finalizado',
    }
    if servicio.estado in estados_bloqueados:
        messages.warning(request, "Este servicio ya no está disponible para aceptar.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Estados desde los que SÍ se puede aceptar
    estados_permitidos = {'asignado', 'en_progreso', 'rechazado_supervisor'}
    if servicio.estado not in estados_permitidos:
        messages.warning(request, "Este servicio no se puede aceptar en su estado actual.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Crear/obtener sesión de fotos del servicio
    sesion = _get_or_create_sesion(servicio)

    # Mi asignación individual dentro de la sesión
    asignacion, _ = SesionFotoTecnico.objects.get_or_create(
        sesion=sesion,
        tecnico=request.user,
        defaults={'estado': 'asignado'}
    )

    # Si ya estaba en otro estado distinto de 'asignado'
    # solo la "reiniciamos" a 'asignado' cuando venimos de un rechazo.
    if asignacion.estado != 'asignado':
        if servicio.motivo_rechazo and servicio.estado in ['asignado', 'en_progreso', 'rechazado_supervisor']:
            asignacion.estado = 'asignado'
            if hasattr(asignacion, 'aceptado_en'):
                asignacion.aceptado_en = None
            asignacion.save(update_fields=['estado'] + (['aceptado_en'] if hasattr(asignacion, 'aceptado_en') else []))
        else:
            messages.info(request, "Ya habías aceptado esta asignación.")
            return redirect('operaciones:mis_servicios_tecnico')

    # Marcar mi aceptación
    asignacion.estado = 'en_proceso'
    if hasattr(asignacion, 'aceptado_en'):
        asignacion.aceptado_en = timezone.now()
        asignacion.save(update_fields=['estado', 'aceptado_en'])
    else:
        asignacion.save(update_fields=['estado'])

    # Pasar el servicio a EN PROGRESO si aún no lo está (incluye caso rechazado_supervisor)
    if servicio.estado != 'en_progreso':
        servicio.estado = 'en_progreso'
        servicio.tecnico_aceptado = request.user
        servicio.save(update_fields=['estado', 'tecnico_aceptado'])

    messages.success(request, "Has aceptado el servicio. Ya puedes subir fotos.")
    return redirect('operaciones:mis_servicios_tecnico')

@login_required
@rol_requerido('usuario')
def finalizar_servicio(request, servicio_id):
    servicio = get_object_or_404(ServicioCotizado, id=servicio_id)

    # Debe ser un técnico asignado
    if request.user not in servicio.trabajadores_asignados.all():
        messages.error(request, "Solo los técnicos asignados pueden finalizar este servicio.")
        return redirect('operaciones:mis_servicios_tecnico')

    if servicio.estado != 'en_progreso':
        messages.warning(request, "Este servicio no está en progreso.")
        return redirect('operaciones:mis_servicios_tecnico')

    # Asegurar sesión y la asignación del usuario
    sesion = _get_or_create_sesion(servicio)
    a = sesion.asignaciones.filter(tecnico=request.user).first()
    if not a:
        a = SesionFotoTecnico.objects.create(
            sesion=sesion,
            tecnico=request.user,
            estado='asignado'
        )

    # 🔧 Imports locales
    import re
    import unicodedata

    from django.db import transaction

    from .models import EvidenciaFoto, RequisitoFoto

    # ==================== Helpers locales (activos + norma) ====================
    def _norm_title(s: str) -> str:
        s = (s or "").strip().lower()
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = re.sub(r"\s+", " ", s)
        return s

    def _canon_requisitos_por_norma():
        """
        Reúne TODOS los requisitos ACTIVO=True de la sesión por título normalizado (norm):
          norm -> {"id","titulo","obligatorio","orden","ids": set(ids_equivalentes)}
        """
        canon_by_norm = {}
        qs = (RequisitoFoto.objects
              .filter(tecnico_sesion__sesion=sesion, activo=True)
              .values("id", "titulo", "obligatorio", "orden"))
        for r in qs:
            norm = _norm_title(r["titulo"])
            b = canon_by_norm.get(norm)
            if not b:
                canon_by_norm[norm] = {
                    "id": r["id"], "titulo": r["titulo"], "obligatorio": r["obligatorio"],
                    "orden": r["orden"], "ids": {r["id"]}
                }
            else:
                b["ids"].add(r["id"])
                if (r["orden"], r["id"]) < (b["orden"], b["id"]):
                    b["id"] = r["id"]
                    b["titulo"] = r["titulo"]
                    b["obligatorio"] = r["obligatorio"]
                    b["orden"] = r["orden"]
        return canon_by_norm

    def _global_done_por_norma(canon_by_norm: dict):
        """
        True si existe al menos UNA evidencia para cualquiera de los IDs del bloque (norma).
        """
        done = {norm: False for norm in canon_by_norm.keys()}
        if not canon_by_norm:
            return done
        all_ids = [rid for b in canon_by_norm.values() for rid in b["ids"]]
        ids_with_ev = set(
            EvidenciaFoto.objects
            .filter(requisito_id__in=all_ids)
            .values_list("requisito_id", flat=True)
        )
        for norm, b in canon_by_norm.items():
            if any(rid in ids_with_ev for rid in b["ids"]):
                done[norm] = True
        return done
    # ==========================================================================

    # 1) Validar fotos requeridas a nivel proyecto (solo ACTIVO=True y por NORMA)
    canon = _canon_requisitos_por_norma()
    done_by_norm = _global_done_por_norma(canon)

    missing_titles = []
    for norm, b in sorted(canon.items(), key=lambda x: (x[1]["orden"], x[1]["id"])):
        if b["obligatorio"] and not done_by_norm.get(norm, False):
            missing_titles.append(b["titulo"])

    if missing_titles:
        messages.error(
            request,
            "No puedes finalizar: faltan fotos requeridas de " + ", ".join(missing_titles) +
            ". Carga las evidencias para continuar."
        )
        return redirect('operaciones:fotos_upload', pk=a.pk)

    # 2) Validar que TODOS los técnicos asignados (actualmente asignados) hayan aceptado
    assigned_ids = list(servicio.trabajadores_asignados.values_list('id', flat=True))
    for asg in sesion.asignaciones.filter(tecnico_id__in=assigned_ids):
        # si está todavía en "asignado" y sin aceptado_en ⇒ NO ha aceptado
        if asg.estado == "asignado" and not getattr(asg, "aceptado_en", None):
            messages.error(request, "Aún hay técnicos sin aceptar la asignación. No se puede finalizar.")
            return redirect('operaciones:fotos_upload', pk=a.pk)

    # 3) Si todo ok, mover a revisión de supervisor
    now_ = timezone.now()
    with transaction.atomic():
        sesion.asignaciones.update(estado="en_revision_supervisor", finalizado_en=now_)
        sesion.estado = "en_revision_supervisor"
        sesion.save(update_fields=["estado"])

        servicio.estado = "en_revision_supervisor"
        servicio.tecnico_finalizo = request.user
        servicio.save(update_fields=["estado", "tecnico_finalizo"])

    messages.success(request, "Enviado a revisión del supervisor (proyecto completo).")
    return redirect('operaciones:mis_servicios_tecnico')

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def aprobar_asignacion(request, pk):
    servicio = get_object_or_404(ServicioCotizado, pk=pk)

    if servicio.estado == 'asignado':
        servicio.estado = 'en_progreso'
    elif servicio.estado == 'finalizado_trabajador':
        servicio.estado = 'aprobado_supervisor'
        servicio.supervisor_aprobo = request.user
        servicio.fecha_aprobacion_supervisor = now()
    else:
        messages.warning(
            request, "Este servicio no está en un estado aprobable.")
        return redirect('operaciones:listar_servicios_supervisor')

    servicio.save()
    messages.success(request, "Aprobación realizada correctamente.")
    return redirect('operaciones:listar_servicios_supervisor')


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def rechazar_asignacion(request, pk):
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo', '').strip()
        servicio = get_object_or_404(ServicioCotizado, pk=pk)

        if servicio.estado in ['asignado', 'finalizado_trabajador']:
            servicio.estado = 'rechazado_supervisor'
            servicio.motivo_rechazo = motivo
            servicio.supervisor_rechazo = request.user
            servicio.save()

            messages.error(
                request, f"Asignación rechazada correctamente. Motivo: {motivo}")
        else:
            messages.warning(
                request, "Este servicio no está en un estado válido para rechazo.")
    else:
        messages.error(request, "Acceso inválido al rechazo.")

    return redirect('operaciones:listar_servicios_supervisor')


from datetime import datetime

import xlwt
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import is_aware

from facturacion.models import CartolaMovimiento

# ==========================================================
# Helpers KM (declaración inmediata + aprobación posterior)
# ==========================================================

def _cartola_tiene_campo_km():
    return any(f.name == "kilometraje" for f in CartolaMovimiento._meta.fields)


def _normalizar_km(valor):
    """
    Convierte kilometraje a int.
    Acepta: 1200, "1.200", "1,200", "1200 km"
    """
    if valor in (None, ""):
        return None
    try:
        s = str(valor).strip()
        s = "".join(ch for ch in s if ch.isdigit())
        if not s:
            return None
        km = int(s)
        return km if km >= 0 else None
    except Exception:
        return None


def _validar_km_no_regresivo(usuario, fecha_transaccion, km_nuevo, exclude_mov_id=None):
    """
    Regla:
    - Para una fecha >= anterior, el km no puede ser menor al último km conocido antes/igual a esa fecha.
    - Para una fecha <= posterior, el km no puede ser mayor al primer km conocido después/igual a esa fecha.
    Esto evita casos tipo: ayer 1200 / hoy 900.
    """
    if km_nuevo is None or not _cartola_tiene_campo_km():
        return True, None

    qs = CartolaMovimiento.objects.filter(
        usuario=usuario,
        fecha_transaccion__isnull=False,
    ).exclude(tipo__categoria='abono')

    # Solo comparar contra movimientos que sí tengan km
    qs = qs.exclude(kilometraje__isnull=True)

    if exclude_mov_id:
        qs = qs.exclude(pk=exclude_mov_id)

    # vecino anterior (misma fecha o menor)
    anterior = (
        qs.filter(fecha_transaccion__lte=fecha_transaccion)
        .order_by('-fecha_transaccion', '-id')
        .first()
    )

    if anterior and anterior.kilometraje is not None and km_nuevo < int(anterior.kilometraje):
        return False, (
            f"El kilometraje ({km_nuevo}) no puede ser menor al último registrado "
            f"({int(anterior.kilometraje)}) del {anterior.fecha_transaccion.strftime('%d-%m-%Y')}."
        )

    # vecino posterior (misma fecha o mayor)
    posterior = (
        qs.filter(fecha_transaccion__gte=fecha_transaccion)
        .order_by('fecha_transaccion', 'id')
        .first()
    )

    if posterior and posterior.kilometraje is not None and km_nuevo > int(posterior.kilometraje):
        return False, (
            f"El kilometraje ({km_nuevo}) no puede ser mayor a un registro posterior "
            f"({int(posterior.kilometraje)}) del {posterior.fecha_transaccion.strftime('%d-%m-%Y')}."
        )

    return True, None


def _registrar_km_en_flota_pendiente(movimiento, request_user=None):
    """
    Hook para flota al MOMENTO DE DECLARAR (pendiente de aprobación).
    Si no existe integración aún, no rompe el flujo.
    """
    if not _cartola_tiene_campo_km():
        return

    km = getattr(movimiento, "kilometraje", None)
    if km in (None, ""):
        return

    # 🔌 Integra aquí con tu app de flota (cuando tengas el servicio listo)
    # Ideal: guardar registro "pendiente_aprobacion=True"
    try:
        # Ejemplo (descomenta cuando exista):
        # from flota.services import registrar_kilometraje_desde_rendicion
        # registrar_kilometraje_desde_rendicion(
        #     usuario=movimiento.usuario,
        #     fecha=movimiento.fecha_transaccion or movimiento.fecha.date(),
        #     kilometraje=int(km),
        #     cartola_movimiento=movimiento,
        #     aprobado=False,
        #     registrado_por=request_user,
        # )
        pass
    except Exception:
        # No bloqueamos la rendición por un error de integración de flota
        pass


def _confirmar_km_en_flota_si_aplica(movimiento, request_user=None):
    """
    Hook para flota cuando finanzas APRUEBA.
    Ideal: marcar el km pendiente como aprobado/confirmado.
    """
    if not _cartola_tiene_campo_km():
        return

    km = getattr(movimiento, "kilometraje", None)
    if km in (None, ""):
        return

    try:
        # Ejemplo (descomenta cuando exista):
        # from flota.services import confirmar_kilometraje_rendicion
        # confirmar_kilometraje_rendicion(
        #     cartola_movimiento=movimiento,
        #     aprobado_por=request_user,
        # )
        pass
    except Exception:
        # No bloqueamos aprobación financiera por integración externa
        pass


# ==========================================================
# Usuario
# ==========================================================


@login_required
def mis_rendiciones(request):
    from datetime import datetime

    from django.core.exceptions import ValidationError
    from django.core.paginator import Paginator
    from django.db import transaction
    from django.db.models import Q, Sum
    from django.utils import timezone

    from flota.models import VehicleService

    user = request.user

    def _calcular_resumen_saldos(movimientos_qs):
        """
        Calcula los indicadores financieros mostrados al usuario.

        Todos los movimientos recibidos pertenecen exclusivamente
        al usuario autenticado porque el queryset se construye con:

            CartolaMovimiento.objects.filter(usuario=user)

        Indicadores:

        - saldo_disponible:
          histórico de abonos aprobados menos rendiciones aprobadas
          por Finanzas.

        - saldo_rendido_mes:
          rendiciones del mes actual aprobadas por Finanzas.

        - saldo_pendiente_mes:
          rendiciones del mes actual que siguen en aprobación.

        - saldo_pendiente_anterior:
          rendiciones de meses anteriores que todavía siguen
          pendientes de aprobación.

        - saldo_rechazado_mes:
          rendiciones del mes actual rechazadas.

        - saldo_rechazado_anterior:
          rendiciones rechazadas de meses anteriores que todavía
          no han sido corregidas.

        - abonos_pendientes:
          abonos pendientes de aceptación por parte del usuario.

        Para determinar el mes se utiliza fecha_transaccion.
        En registros antiguos sin fecha_transaccion se utiliza fecha.
        """
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)

        if inicio_mes.month == 12:
            inicio_mes_siguiente = inicio_mes.replace(
                year=inicio_mes.year + 1,
                month=1,
            )
        else:
            inicio_mes_siguiente = inicio_mes.replace(
                month=inicio_mes.month + 1,
            )

        nombres_meses = {
            1: "Enero",
            2: "Febrero",
            3: "Marzo",
            4: "Abril",
            5: "Mayo",
            6: "Junio",
            7: "Julio",
            8: "Agosto",
            9: "Septiembre",
            10: "Octubre",
            11: "Noviembre",
            12: "Diciembre",
        }

        resumen_mes = f"{nombres_meses[inicio_mes.month]} " f"{inicio_mes.year}"

        # ========================================================
        # Filtros de fecha
        # ========================================================

        # Mes actual usando fecha real del gasto.
        # Para registros antiguos sin fecha_transaccion se usa fecha.
        filtro_mes_actual = Q(
            fecha_transaccion__gte=inicio_mes,
            fecha_transaccion__lt=inicio_mes_siguiente,
        ) | Q(
            fecha_transaccion__isnull=True,
            fecha__date__gte=inicio_mes,
            fecha__date__lt=inicio_mes_siguiente,
        )

        # Movimientos pertenecientes a meses anteriores.
        filtro_meses_anteriores = Q(
            fecha_transaccion__lt=inicio_mes,
        ) | Q(
            fecha_transaccion__isnull=True,
            fecha__date__lt=inicio_mes,
        )

        # ========================================================
        # Separar abonos y rendiciones
        # ========================================================

        abonos = movimientos_qs.filter(
            tipo__categoria="abono",
        )

        rendiciones = movimientos_qs.exclude(
            tipo__categoria="abono",
        )

        estados_pendientes = [
            "pendiente_supervisor",
            "aprobado_supervisor",
            "aprobado_pm",
        ]

        estados_rechazados = [
            "rechazado_supervisor",
            "rechazado_pm",
            "rechazado_finanzas",
        ]

        # ========================================================
        # Saldo disponible histórico
        # ========================================================

        total_abonos_aprobados = (
            abonos.filter(
                status="aprobado_abono_usuario",
            ).aggregate(
                total=Sum("abonos"),
            )["total"]
            or 0
        )

        total_rendiciones_aprobadas = (
            rendiciones.filter(
                status="aprobado_finanzas",
            ).aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        saldo_disponible = total_abonos_aprobados - total_rendiciones_aprobadas

        # ========================================================
        # Rendiciones aprobadas durante el mes actual
        # ========================================================

        rendidas_mes_qs = rendiciones.filter(
            filtro_mes_actual,
            status="aprobado_finanzas",
        )

        saldo_rendido_mes = (
            rendidas_mes_qs.aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        cantidad_rendido_mes = rendidas_mes_qs.count()

        # ========================================================
        # Rendiciones pendientes del mes actual
        # ========================================================

        pendientes_mes_qs = rendiciones.filter(
            filtro_mes_actual,
            status__in=estados_pendientes,
        )

        saldo_pendiente_mes = (
            pendientes_mes_qs.aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        cantidad_pendiente_mes = pendientes_mes_qs.count()

        # ========================================================
        # Rendiciones pendientes de meses anteriores
        # ========================================================

        pendientes_anteriores_qs = rendiciones.filter(
            filtro_meses_anteriores,
            status__in=estados_pendientes,
        )

        saldo_pendiente_anterior = (
            pendientes_anteriores_qs.aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        cantidad_pendiente_anterior = pendientes_anteriores_qs.count()

        # Total de rendiciones que todavía siguen pendientes,
        # sin importar el mes al que pertenecen.
        saldo_pendiente_total = (
            saldo_pendiente_mes
            + saldo_pendiente_anterior
        )

        cantidad_pendiente_total = (
            cantidad_pendiente_mes
            + cantidad_pendiente_anterior
        )

        # Saldo pendiente por rendir:
        # muestra cuánto quedaría disponible después de considerar
        # también las rendiciones que siguen en revisión.
        #
        # Este valor es informativo y NO modifica el saldo disponible real.
        # El saldo disponible real solo se descuenta cuando Finanzas aprueba.
        saldo_pendiente_por_rendir = (
            saldo_disponible
            - saldo_pendiente_total
        )

        # ========================================================
        # Rendiciones rechazadas durante el mes actual
        # ========================================================

        rechazadas_mes_qs = rendiciones.filter(
            filtro_mes_actual,
            status__in=estados_rechazados,
        )

        saldo_rechazado_mes = (
            rechazadas_mes_qs.aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        cantidad_rechazado_mes = rechazadas_mes_qs.count()

        # ========================================================
        # Rendiciones rechazadas de meses anteriores
        # ========================================================

        rechazadas_anteriores_qs = rendiciones.filter(
            filtro_meses_anteriores,
            status__in=estados_rechazados,
        )

        saldo_rechazado_anterior = (
            rechazadas_anteriores_qs.aggregate(
                total=Sum("cargos"),
            )["total"]
            or 0
        )

        cantidad_rechazado_anterior = rechazadas_anteriores_qs.count()

        # Total de rendiciones que permanecen rechazadas,
        # sin importar el mes al que pertenecen.
        saldo_rechazado_total = saldo_rechazado_mes + saldo_rechazado_anterior

        cantidad_rechazado_total = cantidad_rechazado_mes + cantidad_rechazado_anterior

        # ========================================================
        # Abonos pendientes de aceptación
        # ========================================================

        abonos_pendientes_qs = abonos.filter(
            status="pendiente_abono_usuario",
        )

        abonos_pendientes = (
            abonos_pendientes_qs.aggregate(
                total=Sum("abonos"),
            )["total"]
            or 0
        )

        cantidad_abonos_pendientes = abonos_pendientes_qs.count()

        return {
            "saldo_disponible": saldo_disponible,
            "saldo_rendido_mes": saldo_rendido_mes,
            "cantidad_rendido_mes": cantidad_rendido_mes,
            "saldo_pendiente_mes": saldo_pendiente_mes,
            "cantidad_pendiente_mes": cantidad_pendiente_mes,
            "saldo_pendiente_anterior": saldo_pendiente_anterior,
            "cantidad_pendiente_anterior": cantidad_pendiente_anterior,
            "saldo_pendiente_total": saldo_pendiente_total,
            "cantidad_pendiente_total": cantidad_pendiente_total,
            "saldo_pendiente_por_rendir": saldo_pendiente_por_rendir,
            "saldo_rechazado_mes": saldo_rechazado_mes,
            "cantidad_rechazado_mes": cantidad_rechazado_mes,
            "saldo_rechazado_anterior": saldo_rechazado_anterior,
            "cantidad_rechazado_anterior": cantidad_rechazado_anterior,
            "saldo_rechazado_total": saldo_rechazado_total,
            "cantidad_rechazado_total": cantidad_rechazado_total,
            "abonos_pendientes": abonos_pendientes,
            "cantidad_abonos_pendientes": cantidad_abonos_pendientes,
            "resumen_mes": resumen_mes,
        }

    def _map_legacy_service_type(tipo_servicio_obj):
        """
        Mapea el tipo configurable de flota al choice legacy de
        VehicleService.service_type.

        VehicleService.service_type continúa siendo obligatorio.
        """
        if not tipo_servicio_obj:
            return "otro"

        n = (
            (
                getattr(
                    tipo_servicio_obj,
                    "name",
                    "",
                )
                or ""
            )
            .strip()
            .lower()
        )

        if "combustible" in n:
            return "combustible"

        if "aceite" in n:
            return "aceite"

        if "neumatic" in n or "neumát" in n:
            return "neumaticos"

        if (
            "revision tecnica" in n
            or "revisión técnica" in n
            or "revision_tecnica" in n
        ):
            return "revision_tecnica"

        if "permiso" in n and "circul" in n:
            return "permiso_circulacion"

        return "otro"

    def _es_tipo_servicios(tipo_obj):
        """
        Verifica si el tipo de movimiento corresponde a Servicios
        utilizando únicamente el nombre del TipoGasto.
        """
        if not tipo_obj:
            return False

        nombre = (
            (
                getattr(
                    tipo_obj,
                    "nombre",
                    None,
                )
                or getattr(
                    tipo_obj,
                    "name",
                    None,
                )
                or ""
            )
            .strip()
            .lower()
        )

        return "servicio" in nombre

    def _validar_no_futuro(
        fecha_tx,
        hora_servicio=None,
        es_servicio=False,
    ):
        """
        Valida que:
        - fecha_transaccion no sea futura.
        - Si corresponde a flota, fecha y hora no sean futuras.

        Devuelve:
            (ok: bool, mensaje: str | None)
        """
        if not fecha_tx:
            return True, None

        now_local = timezone.localtime(timezone.now())

        hoy_local = now_local.date()

        if fecha_tx > hoy_local:
            return (
                False,
                "No puedes registrar una rendición con fecha futura.",
            )

        if es_servicio and hora_servicio:
            try:
                dt_servicio = datetime.combine(
                    fecha_tx,
                    hora_servicio,
                )

                tz = timezone.get_current_timezone()

                if timezone.is_naive(dt_servicio):
                    dt_servicio = timezone.make_aware(
                        dt_servicio,
                        tz,
                    )

                if dt_servicio > now_local:
                    return (
                        False,
                        "No puedes registrar una rendición con una "
                        "hora de servicio futura.",
                    )

            except Exception:
                pass

        return True, None

    def _render_con_error(form_obj):
        """
        Renderiza nuevamente la pantalla cuando el formulario tiene
        un error, manteniendo tabla, paginación y resumen de saldos.
        """
        movimientos_error = (
            CartolaMovimiento.objects.filter(
                usuario=user,
            )
            .select_related(
                "proyecto",
                "tipo",
                "vehiculo_flota",
                "tipo_servicio_flota",
                "servicio_flota",
            )
            .order_by("-fecha")
        )

        paginator_error = Paginator(
            movimientos_error,
            10,
        )

        pagina_error = paginator_error.get_page(1)

        resumen_saldos_error = _calcular_resumen_saldos(movimientos_error)

        return render(
            request,
            "operaciones/mis_rendiciones.html",
            {
                "pagina": pagina_error,
                "cantidad": "10",
                "form": form_obj,
                **resumen_saldos_error,
            },
        )

    # ============================================================
    # Crear nueva rendición
    # ============================================================

    if request.method == "POST":
        form = MovimientoUsuarioForm(
            request.POST,
            request.FILES,
            user=request.user,
        )

        if form.is_valid():
            cd = form.cleaned_data

            last_mov = (
                CartolaMovimiento.objects.filter(
                    usuario=user,
                )
                .order_by("-id")
                .first()
            )

            def norm(value):
                return (value or "").strip()

            is_duplicate = False

            if last_mov:
                is_duplicate = (
                    getattr(
                        last_mov,
                        "proyecto_id",
                        None,
                    )
                    == getattr(
                        cd.get("proyecto"),
                        "id",
                        None,
                    )
                    and getattr(
                        last_mov,
                        "tipo_id",
                        None,
                    )
                    == getattr(
                        cd.get("tipo"),
                        "id",
                        None,
                    )
                    and getattr(
                        last_mov,
                        "numero_doc",
                        None,
                    )
                    == cd.get("numero_doc")
                    and getattr(
                        last_mov,
                        "cargos",
                        None,
                    )
                    == cd.get("cargos")
                    and norm(
                        getattr(
                            last_mov,
                            "rut_factura",
                            "",
                        )
                    )
                    == norm(cd.get("rut_factura"))
                    and norm(
                        getattr(
                            last_mov,
                            "observaciones",
                            "",
                        )
                    )
                    == norm(cd.get("observaciones"))
                    and getattr(
                        last_mov,
                        "fecha_transaccion",
                        None,
                    )
                    == cd.get("fecha_transaccion")
                )

            if is_duplicate:
                messages.warning(
                    request,
                    "Esta rendición ya fue registrada hace unos "
                    "instantes. No se creó un duplicado.",
                )

                return redirect("operaciones:mis_rendiciones")

            try:
                with transaction.atomic():
                    mov = form.save(commit=False)
                    mov.usuario = user
                    mov.fecha = timezone.now()
                    mov.status = "pendiente_supervisor"
                    mov.comprobante = cd.get("comprobante")

                    # Validación del kilometraje antiguo, si existe.
                    if _cartola_tiene_campo_km():
                        km_nuevo = _normalizar_km(cd.get("kilometraje"))

                        if km_nuevo is not None:
                            ok_km, msg_km = _validar_km_no_regresivo(
                                usuario=user,
                                fecha_transaccion=cd.get("fecha_transaccion"),
                                km_nuevo=km_nuevo,
                            )

                            if not ok_km:
                                form.add_error(
                                    "kilometraje",
                                    msg_km,
                                )

                                return _render_con_error(form)

                            setattr(
                                mov,
                                "kilometraje",
                                km_nuevo,
                            )

                    tipo_mov = cd.get("tipo")

                    es_rendicion_flota = bool(
                        _es_tipo_servicios(tipo_mov)
                        and cd.get("vehiculo_flota")
                        and cd.get("tipo_servicio_flota")
                        and cd.get("fecha_servicio_flota")
                        and (cd.get("hora_servicio_flota") is not None)
                    )

                    # Validación de fecha y hora futura.
                    ok_no_futuro, msg_no_futuro = _validar_no_futuro(
                        fecha_tx=cd.get("fecha_transaccion"),
                        hora_servicio=cd.get("hora_servicio_flota"),
                        es_servicio=es_rendicion_flota,
                    )

                    if not ok_no_futuro:
                        if es_rendicion_flota and cd.get("hora_servicio_flota"):
                            form.add_error(
                                "hora_servicio_flota",
                                msg_no_futuro,
                            )
                        else:
                            form.add_error(
                                "fecha_transaccion",
                                msg_no_futuro,
                            )

                        raise ValidationError("Fecha/hora futura no permitida.")

                    # Guardar la rendición para obtener el PK.
                    mov.save()

                    # Crear servicio de flota automáticamente.
                    if es_rendicion_flota:
                        vehiculo = cd.get("vehiculo_flota")

                        tipo_servicio_flota = cd.get("tipo_servicio_flota")

                        fecha_servicio = cd.get("fecha_servicio_flota")

                        hora_servicio = cd.get("hora_servicio_flota")

                        km_servicio = cd.get("kilometraje_servicio_flota")

                        monto_servicio = cd.get("cargos") or 0

                        (
                            ok_flota_km,
                            msg_flota_km,
                            ultimo_ref,
                            servicio_conflicto,
                        ) = _validar_km_servicio_flota_vs_ultimo(
                            vehicle_id=vehiculo.id,
                            fecha_servicio=fecha_servicio,
                            hora_servicio=hora_servicio,
                            km_nuevo=km_servicio,
                        )

                        if not ok_flota_km:
                            try:
                                rendicion_conflicto = (
                                    CartolaMovimiento.objects.filter(
                                        servicio_flota=servicio_conflicto
                                    )
                                    .only(
                                        "id",
                                        "status",
                                    )
                                    .first()
                                    if servicio_conflicto
                                    else None
                                )

                            except Exception:
                                rendicion_conflicto = None

                            estados_no_editables = {
                                "aprobado_supervisor",
                                "aprobado_finanzas",
                                "aprobado_abono_usuario",
                                "aprobado",
                            }

                            if (
                                rendicion_conflicto
                                and getattr(
                                    rendicion_conflicto,
                                    "status",
                                    None,
                                )
                                in estados_no_editables
                            ):
                                form.add_error(
                                    "kilometraje_servicio_flota",
                                    (
                                        f"{msg_flota_km} "
                                        "La rendición anterior ya fue "
                                        "aprobada y no se puede editar. "
                                        "Debes solicitar que la rechacen "
                                        "para corregirla."
                                    ),
                                )
                            else:
                                form.add_error(
                                    "kilometraje_servicio_flota",
                                    (
                                        f"{msg_flota_km} "
                                        "Edita el registro anterior o "
                                        "modifica la hora del servicio."
                                    ),
                                )

                            raise ValidationError("Kilometraje de flota inválido.")

                        legacy_type = _map_legacy_service_type(tipo_servicio_flota)

                        servicio = VehicleService.objects.create(
                            vehicle=vehiculo,
                            service_type=legacy_type,
                            service_type_obj=tipo_servicio_flota,
                            title=f"Rendición #{mov.pk}",
                            service_date=fecha_servicio,
                            service_time=hora_servicio,
                            kilometraje_declarado=(
                                km_servicio if km_servicio not in (None, "") else None
                            ),
                            monto=monto_servicio,
                            notes=(
                                f"Creado desde rendición "
                                f"#{mov.pk} por "
                                f"{user.get_full_name() or user.username}. "
                                f"Obs: "
                                f"{cd.get('observaciones') or ''}"
                            ).strip(),
                        )

                        mov.servicio_flota = servicio
                        mov.vehiculo_flota = vehiculo
                        mov.tipo_servicio_flota = tipo_servicio_flota
                        mov.fecha_servicio_flota = fecha_servicio
                        mov.hora_servicio_flota = hora_servicio
                        mov.kilometraje_servicio_flota = (
                            km_servicio if km_servicio not in (None, "") else None
                        )
                        mov.tipo_servicio_flota_snapshot = getattr(
                            tipo_servicio_flota,
                            "name",
                            None,
                        )

                        mov.save(
                            update_fields=[
                                "servicio_flota",
                                "vehiculo_flota",
                                "tipo_servicio_flota",
                                "fecha_servicio_flota",
                                "hora_servicio_flota",
                                "kilometraje_servicio_flota",
                                "tipo_servicio_flota_snapshot",
                            ]
                        )

                    _registrar_km_en_flota_pendiente(
                        mov,
                        request_user=request.user,
                    )

                messages.success(
                    request,
                    "Rendición registrada correctamente.",
                )

                return redirect("operaciones:mis_rendiciones")

            except ValidationError as e:
                try:
                    if hasattr(e, "message_dict"):
                        for field, errs in e.message_dict.items():
                            for err in errs:
                                form.add_error(
                                    (field if field in form.fields else None),
                                    str(err),
                                )
                    else:
                        if not form.non_field_errors() and not any(
                            form.errors.values()
                        ):
                            form.add_error(
                                None,
                                str(e),
                            )

                except Exception:
                    form.add_error(
                        None,
                        str(e),
                    )

                return _render_con_error(form)

    else:
        # Pasar user en GET para filtrar vehículos asignados.
        form = MovimientoUsuarioForm(user=request.user)

    # ============================================================
    # Paginación
    # ============================================================

    raw_cantidad = request.GET.get(
        "cantidad",
        "10",
    )

    if raw_cantidad == "todos":
        per_page = 100
        cantidad = "100"

    else:
        try:
            per_page = int(raw_cantidad)

        except (TypeError, ValueError):
            per_page = 10
            cantidad = "10"

        else:
            if per_page < 1:
                per_page = 10
                cantidad = "10"

            elif per_page > 100:
                per_page = 100
                cantidad = "100"

            else:
                cantidad = raw_cantidad

    # Este filtro garantiza que cada usuario solo vea sus propios
    # movimientos, saldos, pendientes y rechazos.
    movimientos = (
        CartolaMovimiento.objects.filter(
            usuario=user,
        )
        .select_related(
            "proyecto",
            "tipo",
            "vehiculo_flota",
            "tipo_servicio_flota",
            "servicio_flota",
        )
        .order_by("-fecha")
    )

    paginator = Paginator(
        movimientos,
        per_page,
    )

    page_number = request.GET.get("page")

    pagina = paginator.get_page(page_number)

    resumen_saldos = _calcular_resumen_saldos(movimientos)

    return render(
        request,
        "operaciones/mis_rendiciones.html",
        {
            "pagina": pagina,
            "cantidad": cantidad,
            "form": form,
            **resumen_saldos,
        },
    )


@login_required
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Abono aprobado correctamente.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def rechazar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if request.method == "POST":
        motivo = request.POST.get("motivo", "")
        mov.status = "rechazado_abono_usuario"
        mov.motivo_rechazo = motivo
        mov.save()
        messages.error(request, "Abono rechazado y enviado a Finanzas para revisión.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def editar_rendicion(request, pk):
    from datetime import datetime

    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    # ✅ No permitir edición si ya fue aprobada
    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_supervisor', 'aprobado_finanzas', 'aprobado']:
        messages.error(
            request,
            "Esta rendición ya fue aprobada y no se puede editar. Debes solicitar que la rechacen."
        )
        return redirect('operaciones:mis_rendiciones')

    # Helpers locales
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone

    from flota.models import Vehicle, VehicleService

    def _map_legacy_service_type(tipo_servicio_obj):
        if not tipo_servicio_obj:
            return "otro"

        n = (getattr(tipo_servicio_obj, "name", "") or "").strip().lower()

        if "combustible" in n:
            return "combustible"
        if "aceite" in n:
            return "aceite"
        if "neumatic" in n or "neumát" in n:
            return "neumaticos"
        if "revision tecnica" in n or "revisión técnica" in n or "revision_tecnica" in n:
            return "revision_tecnica"
        if "permiso" in n and "circul" in n:
            return "permiso_circulacion"

        return "otro"

    def _es_tipo_servicios(tipo_obj):
        if not tipo_obj:
            return False
        nombre = (getattr(tipo_obj, "nombre", None) or getattr(tipo_obj, "name", None) or "").strip().lower()
        categoria = (getattr(tipo_obj, "categoria", None) or "").strip().lower()
        return ("servicio" in nombre) or (categoria == "servicios")

    def _validar_no_futuro(fecha_tx, hora_servicio=None, es_servicio=False):
        """
        Valida que:
        - fecha_transaccion no sea futura
        - si es servicio (flota), fecha+hora del servicio no sea futura
        """
        if not fecha_tx:
            return True, None

        now_local = timezone.localtime(timezone.now())
        hoy_local = now_local.date()

        if fecha_tx > hoy_local:
            return False, "No puedes registrar una rendición con fecha futura."

        if es_servicio and hora_servicio:
            try:
                dt_servicio = datetime.combine(fecha_tx, hora_servicio)
                tz = timezone.get_current_timezone()
                dt_servicio = timezone.make_aware(dt_servicio, tz) if timezone.is_naive(dt_servicio) else dt_servicio

                if dt_servicio > now_local:
                    return False, "No puedes registrar una rendición con una hora de servicio futura."
            except Exception:
                pass

        return True, None

    def _sync_vehicle_from_rendicion_flota(obj_mov):
        """
        Sincroniza odómetro y último movimiento del vehículo desde la rendición flota.
        - No baja odómetro si el KM es menor (strict=False).
        """
        try:
            vehiculo = getattr(obj_mov, "vehiculo_flota", None)
            km = getattr(obj_mov, "kilometraje_servicio_flota", None)

            if not vehiculo or km in (None, ""):
                return

            km = int(km)

            fecha = getattr(obj_mov, "fecha_servicio_flota", None) or getattr(obj_mov, "fecha_transaccion", None)
            hora = getattr(obj_mov, "hora_servicio_flota", None)

            if fecha and hora:
                dt_naive = timezone.datetime.combine(fecha, hora)
                dt_mov = timezone.make_aware(dt_naive, timezone.get_current_timezone())
            elif fecha:
                dt_naive = timezone.datetime.combine(fecha, timezone.datetime.min.time())
                dt_mov = timezone.make_aware(dt_naive, timezone.get_current_timezone())
            else:
                dt_mov = timezone.now()

            # ✅ Odometer event + update (sin bajar km si es menor)
            vehiculo.update_kilometraje(
                nuevo_km=km,
                source="rendicion",
                ref=f"Rendición #{obj_mov.pk}",
                strict=False,
            )

            # ✅ Último movimiento
            Vehicle.objects.filter(pk=vehiculo.pk).update(
                last_movement_at=dt_mov,
                updated_at=timezone.now(),
            )

        except Exception:
            # No romper la edición por un problema de sync de flota
            pass

    if request.method == 'POST':
        # ✅ IMPORTANTE: pasar user al form
        form = MovimientoUsuarioForm(request.POST, request.FILES, instance=rendicion, user=request.user)

        if form.is_valid():
            # --- Detectar cambios ---
            campos_editados = []
            for field in form.changed_data:
                if field not in ['status', 'actualizado']:
                    campos_editados.append(field)

            # Si cambió algo y estaba rechazado, restablecer estado
            if campos_editados and rendicion.status in [
                'rechazado_abono_usuario', 'rechazado_supervisor', 'rechazado_pm', 'rechazado_finanzas'
            ]:
                rendicion.status = 'pendiente_supervisor'

            obj = form.save(commit=False)

            # ✅ Mantener/reemplazar comprobante (acepta cualquiera de los 3 inputs)
            nuevo_comprobante = (
                request.FILES.get("comprobante")
                or request.FILES.get("comprobante_archivo")
                or request.FILES.get("comprobante_foto")
            )
            if nuevo_comprobante:
                obj.comprobante = nuevo_comprobante

            # ✅ Validación de kilometraje "legacy" (si existe campo kilometraje en Cartola)
            if _cartola_tiene_campo_km():
                km_nuevo = _normalizar_km(form.cleaned_data.get("kilometraje"))
                if km_nuevo is not None:
                    ok_km, msg_km = _validar_km_no_regresivo(
                        usuario=request.user,
                        fecha_transaccion=form.cleaned_data.get("fecha_transaccion"),
                        km_nuevo=km_nuevo,
                        exclude_mov_id=rendicion.pk,
                    )
                    if not ok_km:
                        form.add_error('kilometraje', msg_km)
                        return render(
                            request,
                            'operaciones/editar_rendicion.html',
                            {'form': form, 'rendicion': rendicion}
                        )

                    setattr(obj, "kilometraje", km_nuevo)

            # ==========================================================
            # ✅ Sync edición con FLOTA (si tipo = Servicios)
            # ==========================================================
            cd = form.cleaned_data
            tipo_mov = cd.get("tipo")
            es_rendicion_flota = _es_tipo_servicios(tipo_mov)

            # ✅ Validación fecha/hora no futura
            ok_no_futuro, msg_no_futuro = _validar_no_futuro(
                fecha_tx=cd.get("fecha_transaccion"),
                hora_servicio=cd.get("hora_servicio_flota"),
                es_servicio=es_rendicion_flota,
            )
            if not ok_no_futuro:
                if es_rendicion_flota and cd.get("hora_servicio_flota"):
                    form.add_error("hora_servicio_flota", msg_no_futuro)
                else:
                    form.add_error("fecha_transaccion", msg_no_futuro)

                return render(
                    request,
                    'operaciones/editar_rendicion.html',
                    {'form': form, 'rendicion': rendicion}
                )

            # Si es servicio, la fecha de servicio SIEMPRE se toma desde fecha_transaccion
            if es_rendicion_flota and cd.get("fecha_transaccion"):
                obj.fecha_servicio_flota = cd.get("fecha_transaccion")

            if es_rendicion_flota:
                vehiculo = cd.get("vehiculo_flota")
                tipo_servicio_flota = cd.get("tipo_servicio_flota")
                fecha_servicio = cd.get("fecha_transaccion")
                hora_servicio = cd.get("hora_servicio_flota")
                km_servicio = cd.get("kilometraje_servicio_flota")
                monto_servicio = cd.get("cargos") or 0

                if not vehiculo:
                    form.add_error("vehiculo_flota", "Selecciona un vehículo.")
                if not tipo_servicio_flota:
                    form.add_error("tipo_servicio_flota", "Selecciona un tipo de servicio.")
                if not hora_servicio:
                    form.add_error("hora_servicio_flota", "Ingresa la hora del servicio.")
                if km_servicio in (None, ""):
                    form.add_error("kilometraje_servicio_flota", "Ingresa el kilometraje del servicio.")

                if form.errors:
                    return render(
                        request,
                        'operaciones/editar_rendicion.html',
                        {'form': form, 'rendicion': rendicion}
                    )

                # ✅ Validación cronológica KM de FLOTA
                exclude_service_id = obj.servicio_flota_id if getattr(obj, "servicio_flota_id", None) else None
                ok_flota_km, msg_flota_km, ultimo_ref, servicio_conflicto = _validar_km_servicio_flota_vs_ultimo(
                    vehicle_id=vehiculo.id,
                    fecha_servicio=fecha_servicio,
                    hora_servicio=hora_servicio,
                    km_nuevo=km_servicio,
                    exclude_service_id=exclude_service_id,
                )

                if not ok_flota_km:
                    rendicion_conflicto = None
                    try:
                        if servicio_conflicto:
                            rendicion_conflicto = (
                                CartolaMovimiento.objects
                                .filter(servicio_flota=servicio_conflicto)
                                .only("id", "status")
                                .first()
                            )
                    except Exception:
                        rendicion_conflicto = None

                    if rendicion_conflicto and getattr(rendicion_conflicto, "status", None) in {
                        "aprobado_supervisor", "aprobado_finanzas", "aprobado_abono_usuario", "aprobado"
                    }:
                        form.add_error(
                            "kilometraje_servicio_flota",
                            (
                                f"{msg_flota_km} La rendición anterior ya fue aprobada y no se puede editar. "
                                f"Debes solicitar que la rechacen."
                            )
                        )
                    else:
                        form.add_error(
                            "kilometraje_servicio_flota",
                            f"{msg_flota_km} Edita el registro anterior o modifica la hora del servicio."
                        )

                    return render(
                        request,
                        'operaciones/editar_rendicion.html',
                        {'form': form, 'rendicion': rendicion}
                    )

            try:
                with transaction.atomic():
                    # Guardar status si lo tocamos arriba
                    obj.status = rendicion.status
                    obj.save()

                    # --- Si es servicio flota, crear/actualizar VehicleService vinculado ---
                    if es_rendicion_flota:
                        vehiculo = cd.get("vehiculo_flota")
                        tipo_servicio_flota = cd.get("tipo_servicio_flota")
                        fecha_servicio = cd.get("fecha_transaccion")
                        hora_servicio = cd.get("hora_servicio_flota")
                        km_servicio = cd.get("kilometraje_servicio_flota")
                        monto_servicio = cd.get("cargos") or 0

                        legacy_type = _map_legacy_service_type(tipo_servicio_flota)

                        servicio = getattr(obj, "servicio_flota", None)

                        if servicio:
                            servicio.vehicle = vehiculo
                            servicio.service_type = legacy_type
                            servicio.service_type_obj = tipo_servicio_flota
                            servicio.title = f"Rendición #{obj.pk}"
                            servicio.service_date = fecha_servicio
                            servicio.service_time = hora_servicio
                            servicio.kilometraje_declarado = km_servicio if km_servicio not in (None, "") else None
                            servicio.monto = monto_servicio
                            servicio.notes = (
                                f"Editado desde rendición #{obj.pk} por "
                                f"{request.user.get_full_name() or request.user.username}. "
                                f"Obs: {cd.get('observaciones') or ''}"
                            ).strip()
                            servicio.save()
                        else:
                            servicio = VehicleService.objects.create(
                                vehicle=vehiculo,
                                service_type=legacy_type,
                                service_type_obj=tipo_servicio_flota,
                                title=f"Rendición #{obj.pk}",
                                service_date=fecha_servicio,
                                service_time=hora_servicio,
                                kilometraje_declarado=km_servicio if km_servicio not in (None, "") else None,
                                monto=monto_servicio,
                                notes=(
                                    f"Creado desde edición de rendición #{obj.pk} por "
                                    f"{request.user.get_full_name() or request.user.username}. "
                                    f"Obs: {cd.get('observaciones') or ''}"
                                ).strip(),
                            )

                        # Snapshot + vínculo
                        obj.servicio_flota = servicio
                        obj.vehiculo_flota = vehiculo
                        obj.tipo_servicio_flota = tipo_servicio_flota
                        obj.fecha_servicio_flota = fecha_servicio
                        obj.hora_servicio_flota = hora_servicio
                        obj.kilometraje_servicio_flota = km_servicio if km_servicio not in (None, "") else None
                        obj.tipo_servicio_flota_snapshot = getattr(tipo_servicio_flota, "name", None)

                        obj.save(update_fields=[
                            "servicio_flota",
                            "vehiculo_flota",
                            "tipo_servicio_flota",
                            "fecha_servicio_flota",
                            "hora_servicio_flota",
                            "kilometraje_servicio_flota",
                            "tipo_servicio_flota_snapshot",
                        ])

                        # ✅ NUEVO: sync explícito de flota desde rendición editada
                        _sync_vehicle_from_rendicion_flota(obj)

                    else:
                        # Si dejó de ser tipo Servicios, limpiar datos flota
                        campos_limpiar = []
                        for fld in [
                            "vehiculo_flota",
                            "tipo_servicio_flota",
                            "fecha_servicio_flota",
                            "hora_servicio_flota",
                            "kilometraje_servicio_flota",
                            "tipo_servicio_flota_snapshot",
                        ]:
                            if hasattr(obj, fld):
                                setattr(obj, fld, None)
                                campos_limpiar.append(fld)

                        # Desvincular servicio histórico si así lo quieres
                        if hasattr(obj, "servicio_flota"):
                            obj.servicio_flota = None
                            campos_limpiar.append("servicio_flota")

                        if campos_limpiar:
                            obj.save(update_fields=campos_limpiar)

                    # ✅ Mantener tu lógica existente de km pendiente
                    _registrar_km_en_flota_pendiente(obj, request_user=request.user)

                messages.success(request, "Rendición actualizada correctamente.")
                return redirect('operaciones:mis_rendiciones')

            except ValidationError as e:
                try:
                    if hasattr(e, "message_dict"):
                        for field, errs in e.message_dict.items():
                            for err in errs:
                                form.add_error(field if field in form.fields else None, str(err))
                    else:
                        form.add_error(None, str(e))
                except Exception:
                    form.add_error(None, str(e))

            except Exception as e:
                form.add_error(None, f"No se pudo actualizar la rendición/servicio de flota: {e}")

    else:
        # ✅ IMPORTANTE: pasar user al form
        form = MovimientoUsuarioForm(instance=rendicion, user=request.user)

    return render(request, 'operaciones/editar_rendicion.html', {
        'form': form,
        'rendicion': rendicion,
    })

@login_required
def eliminar_rendicion(request, pk):
    from django.db import transaction
    from django.db.models import Max
    from django.utils import timezone

    from flota.models import VehicleOdometerEvent, VehicleService

    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "No puedes eliminar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

    def _recalcular_km_y_ultimo_movimiento(vehicle_id: int):
        """
        Recalcula kilometraje_actual del vehículo usando el mayor KM disponible
        (services + odometer_events), y actualiza last_movement_at desde el último service.
        """
        from flota.models import Vehicle

        # max km desde servicios
        max_km_service = (
            VehicleService.objects
            .filter(vehicle_id=vehicle_id, kilometraje_declarado__isnull=False)
            .aggregate(m=Max('kilometraje_declarado'))
            .get('m')
        ) or 0

        # max km desde eventos
        max_km_event = (
            VehicleOdometerEvent.objects
            .filter(vehicle_id=vehicle_id)
            .aggregate(m=Max('new_km'))
            .get('m')
        ) or 0

        nuevo_km = max(int(max_km_service), int(max_km_event))

        # último movimiento: desde el último servicio (por fecha + created_at)
        last_service = (
            VehicleService.objects
            .filter(vehicle_id=vehicle_id)
            .order_by('-service_date', '-created_at', '-pk')
            .first()
        )

        last_movement_at = None
        if last_service:
            if last_service.service_time:
                dt_naive = timezone.datetime.combine(last_service.service_date, last_service.service_time)
            else:
                dt_naive = timezone.datetime.combine(last_service.service_date, timezone.datetime.min.time())
            last_movement_at = timezone.make_aware(dt_naive, timezone.get_current_timezone())

        Vehicle.objects.filter(pk=vehicle_id).update(
            kilometraje_actual=nuevo_km,
            last_movement_at=last_movement_at,
            updated_at=timezone.now(),
        )

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # --- Si es rendición flota, borrar huella en flota ---
                servicio = getattr(rendicion, "servicio_flota", None)
                vehiculo_id = getattr(rendicion, "vehiculo_flota_id", None) or (servicio.vehicle_id if servicio else None)

                if servicio:
                    # 1) borrar odometer events creados por el servicio (source=servicio, ref=Servicio #code)
                    try:
                        if getattr(servicio, "service_code", None) is not None:
                            VehicleOdometerEvent.objects.filter(
                                vehicle_id=servicio.vehicle_id,
                                source="servicio",
                                reference=f"Servicio #{servicio.service_code}"
                            ).delete()
                    except Exception:
                        pass

                    # 2) borrar el servicio
                    try:
                        servicio.delete()
                    except Exception:
                        pass

                # 3) borrar odometer event creado por la rendición (source=rendicion, ref=Rendición #id)
                if vehiculo_id:
                    try:
                        VehicleOdometerEvent.objects.filter(
                            vehicle_id=vehiculo_id,
                            source="rendicion",
                            reference=f"Rendición #{rendicion.pk}"
                        ).delete()
                    except Exception:
                        pass

                # 4) borrar la rendición
                rendicion.delete()

                # 5) recalcular km del vehículo (si aplica)
                if vehiculo_id:
                    _recalcular_km_y_ultimo_movimiento(vehiculo_id)

            messages.success(request, "Rendición eliminada correctamente.")
            return redirect('operaciones:mis_rendiciones')

        except Exception as e:
            messages.error(request, f"No se pudo eliminar la rendición correctamente: {e}")
            return redirect('operaciones:mis_rendiciones')

    return render(request, 'operaciones/eliminar_rendicion.html', {'rendicion': rendicion})

# ==========================================================
# Supervisor / PM / Finanzas
# ==========================================================


@login_required
def vista_rendiciones(request):
    user = request.user

    # =========================================================
    # 1) DETECCIÓN DE ROLES (robusta)
    # =========================================================
    user_groups = set()
    try:
        user_groups = set(user.groups.values_list('name', flat=True))
    except Exception:
        user_groups = set()

    prop_es_supervisor = bool(getattr(user, 'es_supervisor', False))
    prop_es_pm = bool(getattr(user, 'es_pm', False))
    prop_es_facturacion = bool(getattr(user, 'es_facturacion', False))

    grp_es_supervisor = any(g.lower() in {'supervisor'} for g in user_groups)
    grp_es_pm = any(g.lower() in {'pm'} for g in user_groups)
    grp_es_facturacion = any(g.lower() in {'facturacion', 'finanzas'} for g in user_groups)

    es_superuser = user.is_superuser
    es_supervisor = prop_es_supervisor or grp_es_supervisor
    es_pm = prop_es_pm or grp_es_pm
    es_facturacion = prop_es_facturacion or grp_es_facturacion

    # =========================================================
    # 2) BASE QUERYSET
    # =========================================================
    base_qs = CartolaMovimiento.objects.select_related(
        'usuario',
        'proyecto',
        'tipo',
        'vehiculo_flota',
        'tipo_servicio_flota',
        'servicio_flota',
        'aprobado_por_supervisor',
        'aprobado_por_pm',
        'aprobado_por_finanzas',
    )

    # =========================================================
    # 3) FILTRO POR ROLES (EXPLÍCITO POR ESTADOS)
    # =========================================================
    if es_superuser:
        movimientos = base_qs.all()
    else:
        q_roles = Q()

        if es_supervisor:
            q_roles |= (
                Q(status__in=['pendiente_supervisor', 'rechazado_supervisor']) &
                ~Q(tipo__categoria='abono')
            )

        if es_pm:
            q_roles |= (
                Q(status="aprobado_supervisor")
                & ~Q(tipo__categoria="abono")
            )

        if es_facturacion:
            q_roles |= Q(status__in=['aprobado_pm', 'rechazado_finanzas', 'aprobado_finanzas'])

        q_roles |= Q(
            status__in=['pendiente_abono_usuario', 'aprobado_abono_usuario', 'rechazado_abono_usuario'],
            usuario=user
        )

        movimientos = base_qs.filter(q_roles).distinct() if q_roles else base_qs.none()

    # =========================================================
    # 4) PRIORIDAD DE ORDEN SEGÚN ROL (multirol)
    # =========================================================
    pending_statuses = []
    if es_supervisor:
        pending_statuses.append('pendiente_supervisor')
    if es_pm:
        pending_statuses.append('aprobado_supervisor')  # pendiente PM
    if es_facturacion:
        pending_statuses.append('aprobado_pm')          # pendiente Finanzas

    if pending_statuses:
        prioridad_rol_expr = Case(
            *[When(status=s, then=Value(i)) for i, s in enumerate(pending_statuses)],
            default=Value(99),
            output_field=IntegerField(),
        )
    else:
        prioridad_rol_expr = Value(99, output_field=IntegerField())

    movimientos = movimientos.annotate(
        prioridad_rol=prioridad_rol_expr,
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado', then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        ),
    )

    # =========================================================
    # 5) FILTROS EXCEL (backend) - SI ESTÁS USANDO excel_filters
    # =========================================================
    excel_filters_raw = request.GET.get('excel_filters', '').strip()
    excel_filters = {}

    if excel_filters_raw:
        try:
            parsed = json.loads(excel_filters_raw)
            if isinstance(parsed, dict):
                excel_filters = parsed
        except Exception:
            excel_filters = {}

    # Map de columnas -> campo backend
    # (solo referencia; filtramos manual para calzar EXACTO con data-excel-value)
    backend_filter_map = {
        "0": "usuario__full_name",
        "1": "fecha",
        "2": "fecha_transaccion",
        "3": "proyecto",
        "4": "cargos",
        "5": "tipo",
        "6": "vehiculo_flota",
        "7": "tipo_servicio_flota_nombre",
        "8": "fecha_servicio_flota",            # manual (incluye hora)
        "9": "kilometraje_servicio_flota",      # manual (incluye KM)
        "10": "servicio_flota",                 # manual (incluye #)
        "11": "observaciones",
        "12": "numero_doc",                     # ✅ NUEVO: Número de documento
        "13": "comprobante",                    # manual (Ver/—)
        "14": "status",                         # manual (label)
    }

    # =========================================================
    # 6) FILTRO EXCEL MANUAL (EN MEMORIA) PARA GARANTIZAR COINCIDENCIA EXACTA CON LA TABLA
    # =========================================================
    if excel_filters:
        ids_ok = []

        for mov in movimientos:
            nombre_val = (
                mov.usuario.get_full_name() if callable(getattr(mov.usuario, "get_full_name", None))
                else str(getattr(mov.usuario, "get_full_name", "") or "")
            ).strip() or "—"

            fecha_val = mov.fecha.strftime('%d-%m-%Y') if mov.fecha else "—"
            fecha_real_val = (mov.fecha_transaccion.strftime('%d-%m-%Y') if getattr(mov, 'fecha_transaccion', None) else fecha_val)

            proyecto_val = str(mov.proyecto or "—")

            monto_val = f"${mov.cargos or 0:,.0f}".replace(",", ".")

            tipo_val = str(mov.tipo or "—")

            if getattr(mov, 'vehiculo_flota', None):
                vf = mov.vehiculo_flota
                vehiculo_val = f"{vf.patente}{' · ' + str(vf.marca) + ' ' + str(vf.modelo) if getattr(vf, 'marca', None) else ''}"
            else:
                vehiculo_val = "—"

            tipo_serv_val = str(getattr(mov, 'tipo_servicio_flota_nombre', None) or "—")

            if getattr(mov, 'fecha_servicio_flota', None):
                fecha_hora_val = mov.fecha_servicio_flota.strftime('%d-%m-%Y')
                if getattr(mov, 'hora_servicio_flota', None):
                    fecha_hora_val += " " + mov.hora_servicio_flota.strftime('%I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')
            else:
                fecha_hora_val = "—"

            km_raw = getattr(mov, 'kilometraje_servicio_flota', None)
            km_val = f"{km_raw:,} KM".replace(",", ".") if km_raw else "—"

            if getattr(mov, 'servicio_flota', None):
                sf = mov.servicio_flota
                servicio_val = f"#{getattr(sf, 'service_code', None) or sf.id}"
            else:
                servicio_val = "—"

            observ_val = str(mov.observaciones or "—")

            # ✅ NUEVO: número de documento
            numero_doc_val = str(getattr(mov, "numero_doc", None) or "—")

            comprobante_val = "Ver" if getattr(mov, 'comprobante', None) else "—"

            status_map = {
                'pendiente_supervisor': 'Pendiente aprobación del Supervisor',
                'aprobado_supervisor': 'Pendiente aprobación del PM',
                'rechazado_supervisor': 'Rechazado por Supervisor',
                'aprobado_pm': 'Pendiente aprobación de Finanzas',
                'rechazado_pm': 'Rechazado por PM',
                'aprobado_finanzas': 'Aprobado por Finanzas',
                'rechazado_finanzas': 'Rechazado por Finanzas',
                'pendiente_abono_usuario': 'Pendiente aprobación del Usuario',
                'aprobado_abono_usuario': 'Abono aprobado por Usuario',
                'rechazado_abono_usuario': 'Abono rechazado por Usuario',
            }
            try:
                estado_fallback = mov.get_status_display() if hasattr(mov, "get_status_display") else str(mov.status)
            except Exception:
                estado_fallback = str(mov.status)
            estado_val = status_map.get(mov.status, estado_fallback)

            # ✅ IMPORTANTE: índices deben calzar con TU TABLA (0..14, sin Acciones)
            # 0 Nombre
            # 1 Fecha
            # 2 Fecha real del gasto
            # 3 Proyecto
            # 4 Monto
            # 5 Tipo
            # 6 Vehículo
            # 7 Tipo servicio flota
            # 8 Fecha/Hora servicio
            # 9 KM servicio
            # 10 Servicio flota
            # 11 Observaciones
            # 12 Número de documento
            # 13 Comprobante
            # 14 Estado
            row_vals = {
                "0": nombre_val,
                "1": fecha_val,
                "2": fecha_real_val,
                "3": proyecto_val,
                "4": monto_val,
                "5": tipo_val,
                "6": vehiculo_val,
                "7": tipo_serv_val,
                "8": fecha_hora_val,
                "9": km_val,
                "10": servicio_val,
                "11": observ_val,
                "12": numero_doc_val,
                "13": comprobante_val,
                "14": estado_val,
            }

            ok = True
            for col_idx, vals in excel_filters.items():
                if not isinstance(vals, list) or not vals:
                    continue
                vals_norm = {str(v).strip() for v in vals}
                if str(row_vals.get(str(col_idx), "—")).strip() not in vals_norm:
                    ok = False
                    break

            if ok:
                ids_ok.append(mov.id)

        movimientos = movimientos.filter(id__in=ids_ok)

    # =========================================================
    # 7) ORDEN FINAL
    # =========================================================
    movimientos = movimientos.order_by(
        'prioridad_rol',
        'orden_status',
        '-fecha_transaccion',
        '-fecha',
        '-id',
    )

    # =========================================================
    # 8) TOTALES
    # =========================================================
    total = movimientos.aggregate(total=Sum('cargos'))['total'] or 0
    pendientes = movimientos.filter(status__startswith='pendiente').aggregate(total=Sum('cargos'))['total'] or 0
    rechazados = movimientos.filter(status__startswith='rechazado').aggregate(total=Sum('cargos'))['total'] or 0

    # =========================================================
    # 9) GLOBAL DISTINCTS PARA PANEL EXCEL (sobre queryset filtrado por rol, antes de paginar)
    # =========================================================
    def _estado_label(mov):
        return {
            'pendiente_supervisor': 'Pendiente aprobación del Supervisor',
            'aprobado_supervisor': 'Pendiente aprobación del PM',
            'rechazado_supervisor': 'Rechazado por Supervisor',
            'aprobado_pm': 'Pendiente aprobación de Finanzas',
            'rechazado_pm': 'Rechazado por PM',
            'aprobado_finanzas': 'Aprobado por Finanzas',
            'rechazado_finanzas': 'Rechazado por Finanzas',
            'pendiente_abono_usuario': 'Pendiente aprobación del Usuario',
            'aprobado_abono_usuario': 'Abono aprobado por Usuario',
            'rechazado_abono_usuario': 'Abono rechazado por Usuario',
        }.get(mov.status, mov.get_status_display() if hasattr(mov, 'get_status_display') else str(mov.status))

    # ✅ CLAVE: ahora son 0..14 (sin Acciones)
    excel_global = {str(i): set() for i in range(15)}  # 0..14

    for mov in movimientos:
        excel_global["0"].add((mov.usuario.get_full_name() if callable(getattr(mov.usuario, "get_full_name", None)) else str(mov.usuario)).strip() or "—")
        excel_global["1"].add(mov.fecha.strftime('%d-%m-%Y') if mov.fecha else "—")
        excel_global["2"].add(mov.fecha_transaccion.strftime('%d-%m-%Y') if getattr(mov, 'fecha_transaccion', None) else (mov.fecha.strftime('%d-%m-%Y') if mov.fecha else "—"))
        excel_global["3"].add(str(mov.proyecto or "—"))
        excel_global["4"].add(f"${mov.cargos or 0:,.0f}".replace(",", "."))
        excel_global["5"].add(str(mov.tipo or "—"))

        if getattr(mov, 'vehiculo_flota', None):
            vf = mov.vehiculo_flota
            excel_global["6"].add(f"{vf.patente}{' · ' + str(vf.marca) + ' ' + str(vf.modelo) if getattr(vf, 'marca', None) else ''}")
        else:
            excel_global["6"].add("—")

        excel_global["7"].add(str(getattr(mov, 'tipo_servicio_flota_nombre', None) or "—"))

        if getattr(mov, 'fecha_servicio_flota', None):
            fh = mov.fecha_servicio_flota.strftime('%d-%m-%Y')
            if getattr(mov, 'hora_servicio_flota', None):
                fh += " " + mov.hora_servicio_flota.strftime('%I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')
            excel_global["8"].add(fh)
        else:
            excel_global["8"].add("—")

        excel_global["9"].add(f"{mov.kilometraje_servicio_flota:,} KM".replace(",", ".") if getattr(mov, 'kilometraje_servicio_flota', None) else "—")

        if getattr(mov, 'servicio_flota', None):
            sf = mov.servicio_flota
            excel_global["10"].add(f"#{getattr(sf, 'service_code', None) or sf.id}")
        else:
            excel_global["10"].add("—")

        excel_global["11"].add(str(mov.observaciones or "—"))

        # ✅ NUEVO: número de documento
        excel_global["12"].add(str(getattr(mov, "numero_doc", None) or "—"))

        excel_global["13"].add("Ver" if getattr(mov, 'comprobante', None) else "—")
        excel_global["14"].add(_estado_label(mov))

    excel_global_json = json.dumps({k: sorted(list(v)) for k, v in excel_global.items()}, ensure_ascii=False)

    # =========================================================
    # 10) PAGINACIÓN
    # =========================================================
    cantidad_param = request.GET.get('cantidad', '10')
    try:
        if cantidad_param == 'todos':
            page_size = 100
        else:
            page_size = max(5, min(int(cantidad_param), 100))
    except ValueError:
        cantidad_param = '10'
        page_size = 10

    paginator = Paginator(movimientos, page_size)
    page_number = request.GET.get('page') or 1
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_param,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
        'excel_global_json': excel_global_json,
        'excel_filters_raw': excel_filters_raw,
    })


@login_required
def aprobar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    user = request.user
    changed = False

    # Flujo de aprobaciones
    if getattr(user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
        mov.status = 'aprobado_supervisor'
        mov.aprobado_por_supervisor = user
        changed = True

    elif getattr(user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
        mov.status = 'aprobado_pm'
        mov.aprobado_por_pm = user
        changed = True

    elif getattr(user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
        mov.status = 'aprobado_finanzas'
        mov.aprobado_por_finanzas = user

        # ✅ Si tu modelo ya tiene historial/campos, los dejamos listos aquí también
        if hasattr(mov, 'aprobado_finanzas_en'):
            mov.aprobado_finanzas_en = timezone.now()
        if hasattr(mov, 'en_historial'):
            mov.en_historial = True
        if hasattr(mov, 'historial_enviado_el'):
            mov.historial_enviado_el = timezone.now()
        if hasattr(mov, 'historial_enviado_por'):
            mov.historial_enviado_por = user

        changed = True

        # ✅ Confirmar KM en flota cuando finanzas aprueba
        _confirmar_km_en_flota_si_aplica(mov, request_user=user)

    if changed:
        mov.motivo_rechazo = ''  # limpiar rechazo previo si lo hubiera
        mov.save()
        messages.success(request, "Movimiento aprobado correctamente.")
    else:
        messages.warning(request, "No puedes aprobar este movimiento en su estado actual.")

    next_url = (
        request.POST.get('next')
        or request.GET.get('next')
        or request.META.get('HTTP_REFERER')
        or reverse('operaciones:vista_rendiciones')
    )
    return redirect(next_url)


@login_required
def rechazar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)

    if request.method == 'POST':
        motivo = (request.POST.get('motivo_rechazo') or '').strip()
        if not motivo:
            messages.error(request, "Debe ingresar el motivo del rechazo.")
        else:
            changed = False

            if getattr(request.user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
                changed = True
            elif getattr(request.user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
                changed = True
            elif getattr(request.user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                mov.aprobado_por_finanzas = request.user
                changed = True

            if changed:
                mov.motivo_rechazo = motivo
                mov.save()
                messages.success(request, "Movimiento rechazado correctamente.")
            else:
                messages.warning(request, "No puedes rechazar este movimiento en su estado actual.")

    next_url = (
        request.POST.get('next')
        or request.GET.get('next')
        or request.META.get('HTTP_REFERER')
        or reverse('operaciones:vista_rendiciones')
    )
    return redirect(next_url)


# ==========================================================
# AJAX RUT
# ==========================================================

@csrf_exempt
def validar_rut_ajax(request):
    """Valida el RUT desde AJAX y devuelve estado."""
    rut = request.POST.get("rut", "")
    if not validar_rut_chileno(rut):
        return JsonResponse({"ok": False, "error": "El RUT ingresado no es válido."})
    razon_social = verificar_rut_sii(rut)
    if not razon_social:
        return JsonResponse({"ok": False, "error": "El RUT no está registrado en el SII."})
    return JsonResponse({"ok": True, "mensaje": "RUT válido"})


# ==========================================================
# Exports
# ==========================================================


@login_required
@rol_requerido('pm')
def exportar_rendiciones_pm(request):
    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="rendiciones_pm.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Rendiciones PM')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = [
        "Nombre",
        "Fecha",
        "Fecha real del gasto",
        "Vehículo",               # ✅ nuevo
        "Hora servicio",          # ✅ nuevo
        "Kilometraje servicio",   # ✅ nuevo
        "Proyecto",
        "Monto",
        "Tipo",
        "Tipo servicio (Flota)",  # ✅ nuevo
        "Observaciones",
        "Estado",
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        fecha_real_excel = getattr(mov, "fecha_transaccion", None)
        if isinstance(fecha_real_excel, datetime):
            if is_aware(fecha_real_excel):
                fecha_real_excel = fecha_real_excel.astimezone().replace(tzinfo=None)
            fecha_real_excel = fecha_real_excel.date()

        hora_servicio = getattr(mov, "hora_servicio_flota", None)
        hora_servicio_txt = hora_servicio.strftime("%H:%M") if hora_servicio else ""

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)
        ws.write(row_num, 2, fecha_real_excel if fecha_real_excel else "", date_style)

        ws.write(row_num, 3, str(getattr(mov, "vehiculo_flota", "") or ""))
        ws.write(row_num, 4, hora_servicio_txt)
        ws.write(row_num, 5, float(getattr(mov, "kilometraje_servicio_flota", 0) or 0))

        ws.write(row_num, 6, str(mov.proyecto))
        ws.write(row_num, 7, float(mov.cargos or 0))
        ws.write(row_num, 8, str(mov.tipo or ""))
        ws.write(row_num, 9, str(getattr(mov, "tipo_servicio_flota", "") or ""))
        ws.write(row_num, 10, str(mov.observaciones or ""))
        ws.write(row_num, 11, mov.get_status_display())

    wb.save(response)
    return response


@login_required
def exportar_mis_rendiciones(request):
    """
    Exporta exclusivamente las rendiciones y abonos del usuario autenticado.

    El archivo incluye:
    - una hoja de resumen con sus saldos;
    - saldo disponible actual;
    - saldo pendiente por rendir;
    - rendiciones pendientes del mes y de meses anteriores;
    - rendiciones rechazadas del mes y de meses anteriores;
    - una hoja con el detalle de todos sus movimientos;
    - motivo del rechazo.

    Reglas financieras:
    - El saldo disponible solo descuenta rendiciones aprobadas por Finanzas.
    - Las rendiciones pendientes no descuentan todavía el saldo disponible.
    - El saldo pendiente por rendir muestra cuánto quedaría después de
      considerar también las rendiciones que siguen en revisión.
    - Las rendiciones rechazadas no descuentan ningún saldo.
    """

    user = request.user

    # IMPORTANTE:
    # La exportación queda limitada únicamente al usuario autenticado.
    movimientos = list(
        CartolaMovimiento.objects.filter(
            usuario=user,
        )
        .select_related(
            "tipo",
            "proyecto",
            "vehiculo_flota",
        )
        .order_by("-fecha")
    )

    hoy = timezone.localdate()
    anio_actual = hoy.year
    mes_actual = hoy.month

    estados_pendientes_gasto = {
        "pendiente_supervisor",
        "aprobado_supervisor",
        "aprobado_pm",
    }

    estados_rechazados_gasto = {
        "rechazado_supervisor",
        "rechazado_pm",
        "rechazado_finanzas",
    }

    # ============================================================
    # Funciones auxiliares
    # ============================================================

    def normalizar_fecha(valor):
        """
        Convierte datetime o date en una fecha sin zona horaria,
        compatible con xlwt.
        """
        if isinstance(valor, datetime):
            if is_aware(valor):
                valor = valor.astimezone().replace(tzinfo=None)

            return valor.date()

        return valor

    def fecha_real_movimiento(movimiento):
        """
        Usa fecha_transaccion como fecha principal del gasto.

        Para movimientos antiguos que no tengan fecha_transaccion,
        utiliza la fecha de registro como respaldo.
        """
        fecha_real = getattr(
            movimiento,
            "fecha_transaccion",
            None,
        )

        if fecha_real:
            return normalizar_fecha(fecha_real)

        return normalizar_fecha(
            getattr(
                movimiento,
                "fecha",
                None,
            )
        )

    def pertenece_mes_actual(movimiento):
        """
        Indica si el movimiento corresponde al mes actual usando
        la fecha real del gasto.
        """
        fecha_real = fecha_real_movimiento(movimiento)

        return bool(
            fecha_real
            and fecha_real.year == anio_actual
            and fecha_real.month == mes_actual
        )

    def pertenece_mes_anterior(movimiento):
        """
        Indica si el movimiento pertenece a un mes anterior
        respecto del mes actual.
        """
        fecha_real = fecha_real_movimiento(movimiento)

        if not fecha_real:
            return False

        return (
            fecha_real.year,
            fecha_real.month,
        ) < (
            anio_actual,
            mes_actual,
        )

    def categoria_movimiento(movimiento):
        """
        Devuelve la categoría configurada en el tipo de movimiento:
        normalmente 'abono' o una categoría de gasto.
        """
        tipo = getattr(
            movimiento,
            "tipo",
            None,
        )

        categoria = (
            getattr(
                tipo,
                "categoria",
                "",
            )
            or ""
        )

        return str(categoria).strip().lower()

    def es_abono(movimiento):
        return categoria_movimiento(movimiento) == "abono"

    def monto_gasto(movimiento):
        return float(
            getattr(
                movimiento,
                "cargos",
                0,
            )
            or 0
        )

    def monto_abono(movimiento):
        return float(
            getattr(
                movimiento,
                "abonos",
                0,
            )
            or 0
        )

    def texto_cantidad(cantidad, singular, plural):
        """
        Devuelve un texto simple con singular o plural correcto.
        """
        nombre = singular if cantidad == 1 else plural

        return f"{cantidad} {nombre}"

    def obtener_tipo_servicio_flota(movimiento):
        """
        Intenta mostrar el nombre legible del tipo de servicio.
        Mantiene compatibilidad con registros que solo tengan el valor.
        """
        nombre = getattr(
            movimiento,
            "tipo_servicio_flota_nombre",
            None,
        )

        if nombre:
            return str(nombre)

        display_method = getattr(
            movimiento,
            "get_tipo_servicio_flota_display",
            None,
        )

        if callable(display_method):
            try:
                return str(display_method() or "")
            except (AttributeError, TypeError, ValueError):
                pass

        return str(
            getattr(
                movimiento,
                "tipo_servicio_flota",
                "",
            )
            or ""
        )

    # ============================================================
    # Cálculo de saldos históricos
    # ============================================================

    abonos_aprobados_historicos = 0.0
    gastos_aprobados_historicos = 0.0

    # Mes actual
    rendido_mes = 0.0
    pendiente_mes = 0.0
    rechazado_mes = 0.0

    cantidad_rendido_mes = 0
    cantidad_pendiente_mes = 0
    cantidad_rechazado_mes = 0

    # Meses anteriores
    pendiente_anterior = 0.0
    rechazado_anterior = 0.0

    cantidad_pendiente_anterior = 0
    cantidad_rechazado_anterior = 0

    # Abonos
    abonos_pendientes = 0.0
    cantidad_abonos_pendientes = 0

    for mov in movimientos:
        status = str(
            getattr(
                mov,
                "status",
                "",
            )
            or ""
        )

        movimiento_es_abono = es_abono(mov)
        es_del_mes = pertenece_mes_actual(mov)
        es_anterior = pertenece_mes_anterior(mov)

        monto_actual_gasto = monto_gasto(mov)
        monto_actual_abono = monto_abono(mov)

        # --------------------------------------------------------
        # Abonos históricos
        # --------------------------------------------------------

        if movimiento_es_abono:
            if status == "aprobado_abono_usuario":
                abonos_aprobados_historicos += monto_actual_abono

            elif status == "pendiente_abono_usuario":
                abonos_pendientes += monto_actual_abono
                cantidad_abonos_pendientes += 1

            continue

        # --------------------------------------------------------
        # Saldo disponible histórico
        # --------------------------------------------------------

        # El saldo disponible solo se descuenta cuando Finanzas aprueba.
        if status == "aprobado_finanzas":
            gastos_aprobados_historicos += monto_actual_gasto

        # --------------------------------------------------------
        # Mes actual
        # --------------------------------------------------------

        if es_del_mes:
            if status == "aprobado_finanzas":
                rendido_mes += monto_actual_gasto
                cantidad_rendido_mes += 1

            elif status in estados_pendientes_gasto:
                pendiente_mes += monto_actual_gasto
                cantidad_pendiente_mes += 1

            elif status in estados_rechazados_gasto:
                rechazado_mes += monto_actual_gasto
                cantidad_rechazado_mes += 1

        # --------------------------------------------------------
        # Meses anteriores que todavía requieren seguimiento
        # --------------------------------------------------------

        elif es_anterior:
            if status in estados_pendientes_gasto:
                pendiente_anterior += monto_actual_gasto
                cantidad_pendiente_anterior += 1

            elif status in estados_rechazados_gasto:
                rechazado_anterior += monto_actual_gasto
                cantidad_rechazado_anterior += 1

    # ============================================================
    # Totales principales
    # ============================================================

    saldo_disponible = abonos_aprobados_historicos - gastos_aprobados_historicos

    saldo_pendiente_total = pendiente_mes + pendiente_anterior

    cantidad_pendiente_total = cantidad_pendiente_mes + cantidad_pendiente_anterior

    saldo_rechazado_total = rechazado_mes + rechazado_anterior

    cantidad_rechazado_total = cantidad_rechazado_mes + cantidad_rechazado_anterior

    # Valor informativo:
    # cuánto quedaría después de considerar todas las rendiciones
    # que todavía están pendientes de aprobación.
    saldo_pendiente_por_rendir = saldo_disponible - saldo_pendiente_total

    # ============================================================
    # Respuesta
    # ============================================================

    response = HttpResponse(
        content_type="application/vnd.ms-excel",
    )

    response["Content-Disposition"] = 'attachment; filename="mis_rendiciones.xls"'

    response["X-Content-Type-Options"] = "nosniff"

    wb = xlwt.Workbook(
        encoding="utf-8",
    )

    # ============================================================
    # Estilos
    # ============================================================

    title_style = xlwt.easyxf(
        "font: bold on, height 320, colour white;"
        "pattern: pattern solid, fore_colour dark_blue;"
        "align: horiz center, vert center;"
    )

    section_style = xlwt.easyxf(
        "font: bold on, height 240, colour white;"
        "pattern: pattern solid, fore_colour teal;"
        "align: horiz left, vert center;"
    )

    header_style = xlwt.easyxf(
        "font: bold on, colour white;"
        "pattern: pattern solid, fore_colour blue_gray;"
        "align: horiz center, vert center;"
        "borders: left thin, right thin, top thin, bottom thin;"
    )

    label_style = xlwt.easyxf(
        "font: bold on;"
        "pattern: pattern solid, fore_colour gray25;"
        "borders: left thin, right thin, top thin, bottom thin;"
    )

    value_style = xlwt.easyxf("borders: left thin, right thin, top thin, bottom thin;")

    money_style = xlwt.easyxf(
        "font: bold on;" "borders: left thin, right thin, top thin, bottom thin;",
        num_format_str="$#,##0;[Red]-$#,##0",
    )

    money_normal_style = xlwt.easyxf(
        "borders: left thin, right thin, top thin, bottom thin;",
        num_format_str="$#,##0;[Red]-$#,##0",
    )

    date_style = xlwt.easyxf(
        "borders: left thin, right thin, top thin, bottom thin;",
        num_format_str="DD-MM-YYYY",
    )

    pending_style = xlwt.easyxf(
        "pattern: pattern solid, fore_colour light_yellow;"
        "borders: left thin, right thin, top thin, bottom thin;"
    )

    rejected_style = xlwt.easyxf(
        "font: colour dark_red;"
        "pattern: pattern solid, fore_colour rose;"
        "borders: left thin, right thin, top thin, bottom thin;"
    )

    approved_style = xlwt.easyxf(
        "font: colour dark_green;"
        "pattern: pattern solid, fore_colour light_green;"
        "borders: left thin, right thin, top thin, bottom thin;"
    )

    estimated_positive_style = xlwt.easyxf(
        "font: bold on, colour dark_green;"
        "pattern: pattern solid, fore_colour light_green;"
        "borders: left thin, right thin, top thin, bottom thin;",
        num_format_str="$#,##0;[Red]-$#,##0",
    )

    estimated_negative_style = xlwt.easyxf(
        "font: bold on, colour dark_red;"
        "pattern: pattern solid, fore_colour rose;"
        "borders: left thin, right thin, top thin, bottom thin;",
        num_format_str="$#,##0;[Red]-$#,##0",
    )

    # ============================================================
    # Hoja 1: Resumen
    # ============================================================

    ws_resumen = wb.add_sheet(
        "Resumen",
    )

    ws_resumen.col(0).width = 10000
    ws_resumen.col(1).width = 6500
    ws_resumen.col(2).width = 13000

    ws_resumen.write_merge(
        0,
        0,
        0,
        2,
        "RESUMEN DE MIS RENDICIONES",
        title_style,
    )

    nombre_usuario = user.get_full_name() or user.get_username()

    ws_resumen.write(
        2,
        0,
        "Usuario",
        label_style,
    )

    ws_resumen.write(
        2,
        1,
        nombre_usuario,
        value_style,
    )

    ws_resumen.write(
        3,
        0,
        "Mes del resumen",
        label_style,
    )

    ws_resumen.write(
        3,
        1,
        hoy.strftime("%m-%Y"),
        value_style,
    )

    ws_resumen.write_merge(
        5,
        5,
        0,
        2,
        "SALDOS PRINCIPALES",
        section_style,
    )

    resumen_saldos = [
        (
            "Saldo disponible actual",
            saldo_disponible,
            (
                "Abonos aprobados históricos menos rendiciones "
                "aprobadas por Finanzas."
            ),
            money_style,
        ),
        (
            "Rendiciones pendientes de aprobación",
            saldo_pendiente_total,
            (
                f"{texto_cantidad(cantidad_pendiente_total, 'rendición', 'rendiciones')} "
                "esperando aprobación de Supervisor, PM o Finanzas."
            ),
            money_style,
        ),
        (
            "Saldo pendiente por rendir",
            saldo_pendiente_por_rendir,
            (
                "Saldo disponible actual menos todas las rendiciones "
                "que todavía están pendientes de aprobación."
            ),
            (
                estimated_negative_style
                if saldo_pendiente_por_rendir < 0
                else estimated_positive_style
            ),
        ),
    ]

    fila_resumen = 6

    for titulo, monto, explicacion, estilo_monto in resumen_saldos:
        ws_resumen.write(
            fila_resumen,
            0,
            titulo,
            label_style,
        )

        ws_resumen.write(
            fila_resumen,
            1,
            monto,
            estilo_monto,
        )

        ws_resumen.write(
            fila_resumen,
            2,
            explicacion,
            value_style,
        )

        fila_resumen += 1

    # ============================================================
    # Resumen del mes actual
    # ============================================================

    ws_resumen.write_merge(
        fila_resumen + 1,
        fila_resumen + 1,
        0,
        2,
        "RESUMEN DEL MES ACTUAL",
        section_style,
    )

    fila_resumen += 2

    resumen_mes = [
        (
            "Rendido este mes",
            rendido_mes,
            (
                f"{texto_cantidad(cantidad_rendido_mes, 'rendición', 'rendiciones')} "
                "aprobada por Finanzas durante el mes."
                if cantidad_rendido_mes == 1
                else (
                    f"{texto_cantidad(cantidad_rendido_mes, 'rendición', 'rendiciones')} "
                    "aprobadas por Finanzas durante el mes."
                )
            ),
            money_style,
        ),
        (
            "Pendiente este mes",
            pendiente_mes,
            (
                f"{texto_cantidad(cantidad_pendiente_mes, 'rendición', 'rendiciones')} "
                "del mes esperando aprobación."
            ),
            money_style,
        ),
        (
            "Rechazado este mes",
            rechazado_mes,
            (
                f"{texto_cantidad(cantidad_rechazado_mes, 'rendición', 'rendiciones')} "
                "del mes requiere corrección."
                if cantidad_rechazado_mes == 1
                else (
                    f"{texto_cantidad(cantidad_rechazado_mes, 'rendición', 'rendiciones')} "
                    "del mes requieren corrección."
                )
            ),
            money_style,
        ),
        (
            "Abonos pendientes de aprobación",
            abonos_pendientes,
            (
                f"{texto_cantidad(cantidad_abonos_pendientes, 'abono', 'abonos')} "
                "pendiente de aceptación."
                if cantidad_abonos_pendientes == 1
                else (
                    f"{texto_cantidad(cantidad_abonos_pendientes, 'abono', 'abonos')} "
                    "pendientes de aceptación."
                )
            ),
            money_style,
        ),
    ]

    for titulo, monto, explicacion, estilo_monto in resumen_mes:
        ws_resumen.write(
            fila_resumen,
            0,
            titulo,
            label_style,
        )

        ws_resumen.write(
            fila_resumen,
            1,
            monto,
            estilo_monto,
        )

        ws_resumen.write(
            fila_resumen,
            2,
            explicacion,
            value_style,
        )

        fila_resumen += 1

    # ============================================================
    # Movimientos anteriores pendientes o rechazados
    # ============================================================

    ws_resumen.write_merge(
        fila_resumen + 1,
        fila_resumen + 1,
        0,
        2,
        "MOVIMIENTOS DE MESES ANTERIORES",
        section_style,
    )

    fila_resumen += 2

    ws_resumen.write(
        fila_resumen,
        0,
        "Pendientes de meses anteriores",
        label_style,
    )

    ws_resumen.write(
        fila_resumen,
        1,
        pendiente_anterior,
        money_style,
    )

    ws_resumen.write(
        fila_resumen,
        2,
        (
            f"{texto_cantidad(cantidad_pendiente_anterior, 'rendición', 'rendiciones')} "
            "todavía en proceso de aprobación."
        ),
        pending_style,
    )

    fila_resumen += 1

    ws_resumen.write(
        fila_resumen,
        0,
        "Rechazadas de meses anteriores",
        label_style,
    )

    ws_resumen.write(
        fila_resumen,
        1,
        rechazado_anterior,
        money_style,
    )

    ws_resumen.write(
        fila_resumen,
        2,
        (
            f"{texto_cantidad(cantidad_rechazado_anterior, 'rendición', 'rendiciones')} "
            "todavía requiere corrección."
            if cantidad_rechazado_anterior == 1
            else (
                f"{texto_cantidad(cantidad_rechazado_anterior, 'rendición', 'rendiciones')} "
                "todavía requieren corrección."
            )
        ),
        rejected_style,
    )

    fila_resumen += 1

    # ============================================================
    # Aclaraciones
    # ============================================================

    ws_resumen.write_merge(
        fila_resumen + 1,
        fila_resumen + 1,
        0,
        2,
        "ACLARACIONES",
        section_style,
    )

    ws_resumen.write(
        fila_resumen + 2,
        0,
        "Saldo disponible",
        label_style,
    )

    ws_resumen.write(
        fila_resumen + 2,
        1,
        ("Solo disminuye cuando Finanzas aprueba definitivamente " "una rendición."),
        approved_style,
    )

    ws_resumen.write(
        fila_resumen + 3,
        0,
        "Saldo pendiente por rendir",
        label_style,
    )

    ws_resumen.write(
        fila_resumen + 3,
        1,
        (
            "Es una referencia de cuánto quedaría disponible después "
            "de considerar también las rendiciones pendientes."
        ),
        pending_style,
    )

    ws_resumen.write(
        fila_resumen + 4,
        0,
        "Pendiente",
        label_style,
    )

    ws_resumen.write(
        fila_resumen + 4,
        1,
        (
            "El gasto sigue dentro del proceso de aprobación y todavía "
            "no reduce el saldo disponible actual."
        ),
        pending_style,
    )

    ws_resumen.write(
        fila_resumen + 5,
        0,
        "Rechazado",
        label_style,
    )

    ws_resumen.write(
        fila_resumen + 5,
        1,
        ("El gasto necesita corrección y no reduce el saldo " "disponible."),
        rejected_style,
    )

    # ============================================================
    # Hoja 2: Movimientos
    # ============================================================

    ws = wb.add_sheet(
        "Movimientos",
    )

    columns = [
        "Nombre",
        "Fecha registro",
        "Fecha real del gasto",
        "Vehículo",
        "Hora servicio",
        "Kilometraje servicio",
        "Proyecto",
        "Tipo",
        "Categoría",
        "Tipo servicio (Flota)",
        "RUT Factura",
        "Tipo documento",
        "Número documento",
        "Gasto",
        "Abono",
        "Estado",
        "Situación",
        "Motivo del rechazo",
        "Observaciones",
    ]

    column_widths = [
        7000,
        4200,
        4800,
        7000,
        3500,
        5200,
        8500,
        6000,
        4000,
        6500,
        4500,
        5000,
        5000,
        4200,
        4200,
        8500,
        4500,
        11000,
        11000,
    ]

    for col_num, column_title in enumerate(columns):
        ws.write(
            0,
            col_num,
            column_title,
            header_style,
        )

        ws.col(col_num).width = column_widths[col_num]

    ws.set_panes_frozen(True)
    ws.set_horz_split_pos(1)

    for row_num, mov in enumerate(
        movimientos,
        start=1,
    ):
        fecha_registro = normalizar_fecha(
            getattr(
                mov,
                "fecha",
                None,
            )
        )

        fecha_real = fecha_real_movimiento(mov)

        hora_servicio = getattr(
            mov,
            "hora_servicio_flota",
            None,
        )

        hora_servicio_txt = hora_servicio.strftime("%H:%M") if hora_servicio else ""

        status = str(
            getattr(
                mov,
                "status",
                "",
            )
            or ""
        )

        if status in estados_rechazados_gasto or status == "rechazado_abono_usuario":
            situacion = "Rechazado"
            row_status_style = rejected_style

        elif status in estados_pendientes_gasto or status == "pendiente_abono_usuario":
            situacion = "Pendiente"
            row_status_style = pending_style

        elif status in {
            "aprobado_finanzas",
            "aprobado_abono_usuario",
        }:
            situacion = "Aprobado"
            row_status_style = approved_style

        else:
            situacion = "Otro"
            row_status_style = value_style

        ws.write(
            row_num,
            0,
            nombre_usuario,
            value_style,
        )

        if fecha_registro:
            ws.write(
                row_num,
                1,
                fecha_registro,
                date_style,
            )
        else:
            ws.write(
                row_num,
                1,
                "",
                value_style,
            )

        if fecha_real:
            ws.write(
                row_num,
                2,
                fecha_real,
                date_style,
            )
        else:
            ws.write(
                row_num,
                2,
                "",
                value_style,
            )

        ws.write(
            row_num,
            3,
            str(
                getattr(
                    mov,
                    "vehiculo_flota",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            4,
            hora_servicio_txt,
            value_style,
        )

        ws.write(
            row_num,
            5,
            float(
                getattr(
                    mov,
                    "kilometraje_servicio_flota",
                    0,
                )
                or 0
            ),
            value_style,
        )

        ws.write(
            row_num,
            6,
            str(
                getattr(
                    mov,
                    "proyecto",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            7,
            str(
                getattr(
                    mov,
                    "tipo",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            8,
            categoria_movimiento(mov).title(),
            value_style,
        )

        ws.write(
            row_num,
            9,
            obtener_tipo_servicio_flota(mov),
            value_style,
        )

        ws.write(
            row_num,
            10,
            str(
                getattr(
                    mov,
                    "rut_factura",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            11,
            str(
                getattr(
                    mov,
                    "tipo_doc",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            12,
            str(
                getattr(
                    mov,
                    "numero_doc",
                    "",
                )
                or ""
            ),
            value_style,
        )

        ws.write(
            row_num,
            13,
            monto_gasto(mov),
            money_normal_style,
        )

        ws.write(
            row_num,
            14,
            monto_abono(mov),
            money_normal_style,
        )

        ws.write(
            row_num,
            15,
            mov.get_status_display(),
            row_status_style,
        )

        ws.write(
            row_num,
            16,
            situacion,
            row_status_style,
        )

        ws.write(
            row_num,
            17,
            str(
                getattr(
                    mov,
                    "motivo_rechazo",
                    "",
                )
                or ""
            ),
            row_status_style,
        )

        ws.write(
            row_num,
            18,
            str(
                getattr(
                    mov,
                    "observaciones",
                    "",
                )
                or ""
            ),
            value_style,
        )

    wb.save(response)

    return response


# ==========================================================
# Helpers KM Flota (por vehículo + fecha/hora de servicio)
# ==========================================================

def _dt_servicio_naive(fecha_servicio, hora_servicio):
    """
    Construye datetime naive para comparar cronológicamente.
    Si hora viene vacía, usa 00:00.
    """
    if not fecha_servicio:
        return None
    h = hora_servicio or time(0, 0)
    return datetime.combine(fecha_servicio, h)


def _ultimo_servicio_flota_vehicle(vehicle_id, exclude_service_id=None):
    """
    Devuelve el último servicio registrado para un vehículo,
    ordenado por fecha + hora + PK (para referencia visual).
    """
    from flota.models import VehicleService

    qs = VehicleService.objects.filter(vehicle_id=vehicle_id)

    if exclude_service_id:
        qs = qs.exclude(pk=exclude_service_id)

    return qs.order_by("-service_date", "-service_time", "-pk").first()


def _validar_km_servicio_flota_vs_ultimo(vehicle_id, fecha_servicio, hora_servicio, km_nuevo, exclude_service_id=None):
    """
    Valida kilometraje de flota en orden cronológico (por vehículo):
    - No puede ser menor que el registro anterior (<= fecha/hora nueva)
    - No puede ser mayor que el registro posterior (>= fecha/hora nueva)

    Retorna:
      (ok, msg, ultimo_abs, servicio_conflicto)
    """
    from flota.models import VehicleService

    if not vehicle_id or km_nuevo in (None, "") or fecha_servicio is None:
        return True, None, None, None

    try:
        km_nuevo = int(km_nuevo)
    except (TypeError, ValueError):
        return False, "El kilometraje debe ser numérico.", None, None

    if km_nuevo < 0:
        return False, "El kilometraje no puede ser negativo.", None, None

    dt_nuevo = _dt_servicio_naive(fecha_servicio, hora_servicio)
    if dt_nuevo is None:
        return True, None, None, None

    qs = VehicleService.objects.filter(vehicle_id=vehicle_id)

    if exclude_service_id:
        qs = qs.exclude(pk=exclude_service_id)

    # Solo comparar contra servicios que tengan km
    qs = qs.exclude(kilometraje_declarado__isnull=True)

    # Referencia visual: último absoluto del vehículo (para mostrar en UI)
    ultimo_abs = qs.order_by("-service_date", "-service_time", "-pk").first()

    # Vecino anterior (cronológicamente <= nuevo)
    anterior = (
        qs.filter(
            Q(service_date__lt=fecha_servicio) |
            Q(service_date=fecha_servicio, service_time__lte=(hora_servicio or time(0, 0)))
        )
        .order_by("-service_date", "-service_time", "-pk")
        .first()
    )

    # Vecino posterior (cronológicamente >= nuevo)
    posterior = (
        qs.filter(
            Q(service_date__gt=fecha_servicio) |
            Q(service_date=fecha_servicio, service_time__gte=(hora_servicio or time(0, 0)))
        )
        .order_by("service_date", "service_time", "pk")
        .first()
    )

    # Regla 1: no regresivo respecto al anterior
    if anterior and anterior.kilometraje_declarado is not None:
        if km_nuevo < int(anterior.kilometraje_declarado):
            hora_txt = anterior.service_time.strftime("%I:%M %p").lower() if anterior.service_time else "12:00 a.m."
            return (
                False,
                (
                    f"El kilometraje ({km_nuevo}) no puede ser menor al registro anterior "
                    f"({int(anterior.kilometraje_declarado)}) del "
                    f"{anterior.service_date.strftime('%d-%m-%Y')} {hora_txt}."
                ),
                ultimo_abs,
                anterior,  # 👈 servicio en conflicto
            )

    # Regla 2: no puede superar un registro posterior
    if posterior and posterior.kilometraje_declarado is not None:
        dt_posterior = _dt_servicio_naive(posterior.service_date, posterior.service_time)
        if dt_posterior and dt_nuevo and dt_posterior > dt_nuevo:
            if km_nuevo > int(posterior.kilometraje_declarado):
                hora_txt = posterior.service_time.strftime("%I:%M %p").lower() if posterior.service_time else "12:00 a.m."
                return (
                    False,
                    (
                        f"El kilometraje ({km_nuevo}) no puede ser mayor a un registro posterior "
                        f"({int(posterior.kilometraje_declarado)}) del "
                        f"{posterior.service_date.strftime('%d-%m-%Y')} {hora_txt}."
                    ),
                    ultimo_abs,
                    posterior,  # 👈 servicio en conflicto
                )

    return True, None, ultimo_abs, None
@login_required
@require_GET
def validar_km_servicio_flota_ajax(request):
    """
    Valida en vivo el KM del servicio de flota contra la línea de tiempo del vehículo.
    Usa fecha_transaccion como fecha de servicio (porque fecha_servicio_flota va oculta).

    Además, si hay conflicto de KM, devuelve el ID de la rendición asociada al servicio
    en conflicto (si existe) para que el frontend pueda mostrar link directo a editar.
    """
    from facturacion.models import CartolaMovimiento
    from flota.models import Vehicle

    vehicle_id = request.GET.get("vehiculo_id")
    fecha_txt = (request.GET.get("fecha") or "").strip()   # viene de fecha_transaccion
    hora_txt = (request.GET.get("hora") or "").strip()
    km_txt = (request.GET.get("km") or "").strip()

    if not vehicle_id:
        return JsonResponse({"ok": True, "skip": True, "msg": ""})

    # Validar vehículo existente
    try:
        vehicle = Vehicle.objects.get(pk=vehicle_id)
    except Vehicle.DoesNotExist:
        return JsonResponse({"ok": False, "msg": "Vehículo inválido."}, status=400)

    # Parse fecha
    fecha_servicio = None
    if fecha_txt:
        try:
            fecha_servicio = datetime.strptime(fecha_txt, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse({"ok": False, "msg": "Fecha inválida."}, status=400)

    # Parse hora
    hora_servicio = None
    if hora_txt:
        try:
            hora_servicio = datetime.strptime(hora_txt, "%H:%M").time()
        except ValueError:
            return JsonResponse({"ok": False, "msg": "Hora inválida."}, status=400)

    # Parse km
    km_nuevo = None
    if km_txt:
        km_nuevo = _normalizar_km(km_txt)
        if km_nuevo is None and km_txt.strip():
            return JsonResponse({"ok": False, "msg": "Kilometraje inválido."}, status=400)

    ultimo = _ultimo_servicio_flota_vehicle(vehicle.id)

    def _fmt_hora_ampm(t):
        if not t:
            return "12:00 a.m."
        return t.strftime("%I:%M %p").lower()

    # Si aún faltan datos para validar, devolvemos solo referencia del último
    if fecha_servicio is None or hora_servicio is None or km_nuevo is None:
        data = {"ok": True, "skip": True, "msg": ""}
        if ultimo and ultimo.kilometraje_declarado is not None:
            data["ultimo"] = {
                "km": int(ultimo.kilometraje_declarado),
                "fecha": ultimo.service_date.strftime("%d-%m-%Y") if ultimo.service_date else "",
                "hora": _fmt_hora_ampm(ultimo.service_time),
            }
        return JsonResponse(data)

    # 👇 OJO: este helper ahora debe retornar 4 valores
    # (ok, msg, ultimo_ref, servicio_conflicto)
    ok, msg, ultimo_ref, servicio_conflicto = _validar_km_servicio_flota_vs_ultimo(
        vehicle_id=vehicle.id,
        fecha_servicio=fecha_servicio,
        hora_servicio=hora_servicio,
        km_nuevo=km_nuevo,
    )

    resp = {
        "ok": ok,
        "msg": msg or "",
        "vehicle": vehicle.patente,
    }

    if ultimo_ref and ultimo_ref.kilometraje_declarado is not None:
        resp["ultimo"] = {
            "km": int(ultimo_ref.kilometraje_declarado),
            "fecha": ultimo_ref.service_date.strftime("%d-%m-%Y") if ultimo_ref.service_date else "",
            "hora": _fmt_hora_ampm(ultimo_ref.service_time),
        }

    # ✅ Si hubo conflicto, devolver mov_id de la rendición asociada al servicio en conflicto
    if not ok and servicio_conflicto:
        mov_conflicto = (
            CartolaMovimiento.objects
            .filter(servicio_flota_id=servicio_conflicto.id)
            .only("id")
            .first()
        )

        if mov_conflicto:
            if "ultimo" not in resp:
                resp["ultimo"] = {}
            resp["ultimo"]["mov_id"] = mov_conflicto.id

    return JsonResponse(resp)

def _validar_no_futuro(fecha_tx, hora_servicio=None, es_servicio=False):
    """
    Valida que:
    - fecha_transaccion no sea futura
    - si es servicio (flota), fecha+hora del servicio no sea futura
    Devuelve: (ok: bool, mensaje: str|None)
    """
    from datetime import datetime

    from django.utils import timezone

    if not fecha_tx:
        return True, None

    now_local = timezone.localtime(timezone.now())
    hoy_local = now_local.date()

    # 1) Fecha futura (cualquier rendición)
    if fecha_tx > hoy_local:
        return False, "No puedes registrar una rendición con fecha futura."

    # 2) Fecha/Hora futura (solo servicios flota)
    if es_servicio and hora_servicio:
        try:
            dt_servicio = datetime.combine(fecha_tx, hora_servicio)
            tz = timezone.get_current_timezone()
            dt_servicio = timezone.make_aware(dt_servicio, tz) if timezone.is_naive(dt_servicio) else dt_servicio

            if dt_servicio > now_local:
                return False, "No puedes registrar una rendición con una hora de servicio futura."
        except Exception:
            # Si por algún motivo falla el parse/combine, no rompemos la operación aquí.
            # La validación de formulario ya debería cubrir formato de hora.
            pass

    return True, None

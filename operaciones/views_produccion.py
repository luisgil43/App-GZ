# operaciones/views_produccion.py
from django.urls import reverse
from datetime import date, datetime
from django.db.models import Max, IntegerField, Value
from django.db.models import Max, IntegerField
from django.db.models.functions import Cast, Replace
from django.contrib.auth import get_user_model
from .utils.http import login_required_json
from django.http import JsonResponse
import datetime as dt
import mimetypes
import uuid
from .models import MonthlyPayment  # <-- CAMBIA esto por tu modelo real
from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.core.paginator import Paginator
from reportlab.lib import colors
import logging
from django.conf import settings
import io
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from datetime import datetime
from usuarios.decoradores import rol_requerido
import os
from botocore.client import Config
import boto3
from uuid import uuid4
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.db import transaction
from .models import ServicioCotizado, MonthlyPayment
from django.db.models import Q, Sum, F
import re
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from .models import ServicioCotizado

from datetime import date


from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.utils import timezone
from django.contrib.auth import get_user_model  # 👈 agregado
from django.db.models import Q
from urllib.parse import urlencode
from decimal import Decimal
from datetime import date
import re

from .models import ServicioCotizado


@login_required
def produccion_admin(request):
    """
    Producción aprobada (por técnico) + ajustes (bono/adelanto/descuento).
    - Filas por técnico asignado; monto prorrateado: monto_mmoo / N_tecnicos
    - Filtros: du, id_claro, id_new, mes_produccion, estado (opcional),
               f_tecnico (nombre/usuario) y f_region (opcional).
    """
    def _month_token(dt) -> str:
        d = dt.date() if hasattr(dt, "date") else dt
        return f"{d.year:04d}-{d.month:02d}"

    current_month = _yyyy_mm(timezone.localdate())

    # ---------- Filtros GET ----------
    filtros = {
        "du": (request.GET.get("du") or "").strip(),
        "id_claro": (request.GET.get("id_claro") or "").strip(),
        "id_new": (request.GET.get("id_new") or "").strip(),
        "mes_produccion": (request.GET.get("mes_produccion") or "").strip(),
        "estado": (request.GET.get("estado") or "").strip(),
    }
    f_tecnico = (request.GET.get("f_tecnico") or "").strip()
    f_region = (request.GET.get("f_region") or "").strip()
    cantidad = request.GET.get("cantidad", "10")

    # ✅ incluir también los ajustes
    estados_ok = {
        "aprobado_supervisor",
        "aprobado_pm",
        "aprobado_finanzas",
        "ajuste_bono",
        "ajuste_adelanto",
        "ajuste_descuento",
    }

    qs = (
        ServicioCotizado.objects
        .filter(estado__in=estados_ok)
        .prefetch_related('trabajadores_asignados')
        .order_by('-fecha_aprobacion_supervisor', '-id')
    )

    # ---------- Aplicar filtros de cabecera ----------
    if filtros["du"]:
        qs = qs.filter(du__icontains=filtros["du"])
    if filtros["id_claro"]:
        qs = qs.filter(id_claro__icontains=filtros["id_claro"])
    if filtros["id_new"]:
        qs = qs.filter(id_new__icontains=filtros["id_new"])
    if filtros["mes_produccion"]:
        qs = qs.filter(mes_produccion__icontains=filtros["mes_produccion"])
    if filtros["estado"]:
        if filtros["estado"] in estados_ok:
            qs = qs.filter(estado=filtros["estado"])
        else:
            qs = qs.none()
    if f_region:
        qs = qs.filter(region__icontains=f_region)

    # ---------- Construir filas por técnico (prorrateo) ----------
    filas = []
    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        n = len(asignados) or 1
        total = Decimal(str(s.monto_mmoo or 0))
        parte = (total / n).quantize(Decimal('0.01'))

        if asignados:
            for tec in asignados:
                if f_tecnico:
                    target = f_tecnico.lower()
                    full_name = ((tec.first_name or "") + " " +
                                 (tec.last_name or "")).strip().lower()
                    username = (tec.username or "").lower()
                    if target not in full_name and target not in username:
                        continue

                filas.append({
                    "id": s.id,  # 👈 necesario para acciones
                    "du": s.du,
                    "id_claro": s.id_claro or "",
                    "region": s.region or "",
                    "mes_produccion": s.mes_produccion or "",
                    "id_new": s.id_new or "",
                    "detalle_tarea": s.detalle_tarea or "",
                    "monto_tecnico": parte,
                    "tecnico": tec,
                    "fecha_fin": s.fecha_aprobacion_supervisor,
                    "status": s.estado,
                })
        else:
            filas.append({
                "id": s.id,  # 👈 necesario también aquí
                "du": s.du,
                "id_claro": s.id_claro or "",
                "region": s.region or "",
                "mes_produccion": s.mes_produccion or "",
                "id_new": s.id_new or "",
                "detalle_tarea": s.detalle_tarea or "",
                "monto_tecnico": parte,
                "tecnico": None,
                "fecha_fin": s.fecha_aprobacion_supervisor,
                "status": s.estado,
            })

    # ---------- Ordenar: mes actual hacia abajo ----------
    MONTHS_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
        "noviembre": 11, "diciembre": 12,
    }

    def parse_mes_token(txt: str) -> int | None:
        """
        Devuelve YYYYMM (p.ej. 202509) a partir de mes_produccion.
        Acepta: '2025-09', '2025/9', '09-2025', 'septiembre 2025', 'Sep 2025', etc.
        """
        if not txt:
            return None
        s = str(txt).strip().lower()

        m = re.search(r"(\d{4})[/-](\d{1,2})", s)  # YYYY-MM o YYYY/MM
        if m:
            y, mm = int(m.group(1)), int(m.group(2))
            return y * 100 + mm

        m = re.search(r"(\d{1,2})[/-](\d{4})", s)  # MM-YYYY o MM/YYYY
        if m:
            mm, y = int(m.group(1)), int(m.group(2))
            return y * 100 + mm

        m = re.search(r"([a-záéíóúü]{3,})\s+(\d{4})", s)  # nombre mes + año
        if m:
            name = m.group(1)
            y = int(m.group(2))
            for full, num in MONTHS_ES.items():
                if name.startswith(full[:3]):
                    return y * 100 + num
        return None

    for r in filas:
        ym = parse_mes_token(r["mes_produccion"])
        r["_ym_key"] = ym if ym is not None else -1

    # Normalizar fecha_fin a date para evitar comparar datetime vs date
        fin = r["fecha_fin"]
        if isinstance(fin, datetime):
            dkey = fin.date()
        elif isinstance(fin, date):
            dkey = fin
        else:
            dkey = date(1900, 1, 1)
        r["_date_key"] = dkey

# Orden final: mes (YYYYMM) desc y, a igualdad, fecha_fin desc
    filas.sort(key=lambda x: (x["_ym_key"], x["_date_key"]), reverse=True)

    # ---------- Paginación ----------
    if cantidad != "todos":
        try:
            per_page = max(5, min(int(cantidad), 100))
        except ValueError:
            per_page = 10
        paginator = Paginator(filas, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)
    else:
        pagina = filas

    estado_choices = [
        ('aprobado_supervisor', 'Aprobado por Supervisor'),
        ('aprobado_pm', 'Aprobado por PM'),
        ('aprobado_finanzas', 'Aprobado por Finanzas'),
    ]

    filters_dict = {
        **filtros,
        "f_tecnico": f_tecnico,
        "f_region": f_region,
        "cantidad": cantidad,
    }
    filters_qs = urlencode({k: v for k, v in filters_dict.items() if v})

    # Usuarios activos para el modal de Ajuste
    User = get_user_model()
    usuarios = User.objects.filter(is_active=True).order_by(
        'first_name', 'last_name', 'username')

    return render(request, "operaciones/produccion_admin.html", {
        "current_month": current_month,
        "pagina": pagina,
        "cantidad": cantidad,
        "filtros": filtros,
        "estado_choices": estado_choices,
        "f_tecnico": f_tecnico,
        "f_region": f_region,
        "filters_qs": filters_qs,
        "usuarios": usuarios,
    })


@login_required
def exportar_produccion_admin(request):
    """
    Export a Excel (respeta filtros). Una fila por técnico con monto prorrateado.
    """
    filtros = {
        "du": (request.GET.get("du") or "").strip(),
        "id_claro": (request.GET.get("id_claro") or "").strip(),
        "id_new": (request.GET.get("id_new") or "").strip(),
        "mes_produccion": (request.GET.get("mes_produccion") or "").strip(),
        "estado": (request.GET.get("estado") or "").strip(),
    }
    f_tecnico = (request.GET.get("f_tecnico") or "").strip()
    f_region = (request.GET.get("f_region") or "").strip()

    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    qs = (
        ServicioCotizado.objects
        .filter(estado__in=estados_ok)
        .prefetch_related('trabajadores_asignados')
        .order_by('-fecha_aprobacion_supervisor', '-id')
    )

    if filtros["du"]:
        qs = qs.filter(du__icontains=filtros["du"])
    if filtros["id_claro"]:
        qs = qs.filter(id_claro__icontains=filtros["id_claro"])
    if filtros["id_new"]:
        qs = qs.filter(id_new__icontains=filtros["id_new"])
    if filtros["mes_produccion"]:
        qs = qs.filter(mes_produccion__icontains=filtros["mes_produccion"])
    if filtros["estado"]:
        if filtros["estado"] in estados_ok:
            qs = qs.filter(estado=filtros["estado"])
        else:
            qs = qs.none()
    if f_region:
        qs = qs.filter(region__icontains=f_region)

    # Armar filas export
    filas = []
    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        n = len(asignados) or 1
        total = Decimal(str(s.monto_mmoo or 0))
        parte = (total / n).quantize(Decimal('0.01'))

        if asignados:
            for tec in asignados:
                if f_tecnico:
                    target = f_tecnico.lower()
                    full_name = ((tec.first_name or "") + " " +
                                 (tec.last_name or "")).strip().lower()
                    username = (tec.username or "").lower()
                    if target not in full_name and target not in username:
                        continue

                filas.append((
                    f"DU{s.du}",
                    s.id_claro or "",
                    s.region or "",
                    s.mes_produccion or "",
                    s.id_new or "",
                    s.detalle_tarea or "",
                    (tec.get_full_name() or tec.username) if tec else "",
                    s.fecha_aprobacion_supervisor.strftime(
                        "%d/%m/%Y %H:%M") if s.fecha_aprobacion_supervisor else "",
                    s.estado,
                    float(parte),
                ))
        else:
            filas.append((
                f"DU{s.du}",
                s.id_claro or "",
                s.region or "",
                s.mes_produccion or "",
                s.id_new or "",
                s.detalle_tarea or "",
                "",
                s.fecha_aprobacion_supervisor.strftime(
                    "%d/%m/%Y %H:%M") if s.fecha_aprobacion_supervisor else "",
                s.estado,
                float(parte),
            ))

    # Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Producción"
    headers = [
        "DU", "ID CLARO", "REGIÓN", "MES PRODUCCIÓN", "ID NEW", "DETALLE TAREA",
        "TÉCNICO", "FECHA FIN", "STATUS", "MONTO TÉCNICO (CLP)"
    ]
    ws.append(headers)
    for row in filas:
        ws.append(row)

    # Auto ancho
    for col in ws.columns:
        max_len = 0
        letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[letter].width = min(max(10, max_len + 2), 60)

    # Formato numérico última columna
    last_col = len(headers)
    for col_cells in ws.iter_cols(min_col=last_col, max_col=last_col, min_row=2, values_only=False):
        for c in col_cells:
            c.number_format = '#,##0.00'

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename="produccion_aprobada.xlsx"'
    wb.save(resp)
    return resp


def _yyyy_mm(dt) -> str:
    d = dt.date() if hasattr(dt, "date") else dt
    return f"{d.year:04d}-{d.month:02d}"


def _month_aliases(month_token: str) -> list[str]:
    """
    Devuelve alias de búsqueda para un mes. Soporta:
      - 'YYYY-MM'  -> también 'Mes Año' en español (p.ej. 'Septiembre 2025')
      - cualquier otro texto queda tal cual
    """
    s = (month_token or "").strip()
    if not s:
        return []
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if not m:
        return [s]  # ya es texto ('Septiembre 2025', etc.)
    y = int(m.group(1))
    mm = int(m.group(2))
    meses_es = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    if 1 <= mm <= 12:
        nombre_es = meses_es[mm - 1]
        return [s, f"{nombre_es} {y}"]
    return [s]


# === Estados producción + ajustes ===
EST_PROD = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
AJ_POS = {"ajuste_bono", "ajuste_adelanto"}        # SUMAN
AJ_NEG = {"ajuste_descuento"}                      # SOLO DESCUENTO RESTA
ESTADOS_PROD_Y_AJUSTES = EST_PROD | AJ_POS | AJ_NEG


def _monto_firmado_por_estado(monto, estado: str) -> Decimal:
    base = Decimal(str(monto or 0))
    if estado in AJ_NEG:
        return -abs(base)     # descuento resta
    if estado in AJ_POS:
        return abs(base)     # bono/adelanto suman
    return base               # producción normal


@login_required
def produccion_totales_a_pagar(request):
    """
    Totales a pagar por TÉCNICO (MENSUAL) a partir de ServiciosCotizados aprobados
    + ajustes (bono/adelanto/descuento).

    - Estados producción: aprobado_supervisor / aprobado_pm / aprobado_finanzas
    - Ajustes: ajuste_bono (suma), ajuste_adelanto (suma), ajuste_descuento (resta)
    - Prorrateo: monto_mmoo / N_tecnicos
    - Filtros: f_month (YYYY-MM o 'Mes Año' o 'Mes de Año'), f_tecnico, f_project, f_region
    - Desglose: lista de DUs/ajustes con su subtotal prorrateado.
    """
    current_month = _yyyy_mm(timezone.localdate())

    f_month = (request.GET.get("f_month") or "").strip()
    f_tecnico = (request.GET.get("f_tecnico") or "").strip()
    f_project = (request.GET.get("f_project") or "").strip()
    f_region = (request.GET.get("f_region") or "").strip()
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()

    month_to_use = f_month or current_month

    # ---- Estados incluidos ----
    EST_PROD = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    AJ_POS = {"ajuste_bono", "ajuste_adelanto"}        # SUMAN
    AJ_NEG = {"ajuste_descuento"}                      # RESTA
    estados_ok = EST_PROD | AJ_POS | AJ_NEG

    qs = (
        ServicioCotizado.objects
        .filter(estado__in=estados_ok)
        .prefetch_related("trabajadores_asignados")
        .order_by("-fecha_aprobacion_supervisor", "-id")
    )

    # ---- Filtro MES: acepta 'YYYY-MM', 'Mes Año' y 'Mes de Año' ----
    MONTHS_ES = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]

    def _aliases_for_month(token: str) -> list[str]:
        token = (token or "").strip()
        if not token:
            return []
        out = set([token])

        # YYYY-MM o YYYY/M
        m = re.search(r"^\s*(\d{4})[/-](\d{1,2})\s*$", token)
        if m:
            y = int(m.group(1))
            mm = int(m.group(2))
            mm2 = str(mm).zfill(2)
            name = MONTHS_ES[mm-1]
            out.update({
                f"{y}-{mm2}", f"{y}/{mm}",
                f"{name} {y}", f"{name.capitalize()} {y}",
                f"{name} de {y}", f"{name.capitalize()} de {y}",
                f"{mm}-{y}", f"{mm2}-{y}", f"{mm}/{y}",
            })
            return list(out)

        # "mes (de) año"
        m = re.search(
            r"^\s*([a-záéíóúü]+)\s+(?:de\s+)?(\d{4})\s*$", token.lower())
        if m:
            name = m.group(1)
            y = int(m.group(2))
            # buscar índice de mes por prefijo
            idx = next((i for i, n in enumerate(MONTHS_ES)
                       if name.startswith(n[:3])), None)
            if idx is not None:
                mm = idx+1
                mm2 = str(mm).zfill(2)
                full = MONTHS_ES[idx]
                out.update({
                    f"{y}-{mm2}", f"{y}/{mm}",
                    f"{full} {y}", f"{full.capitalize()} {y}",
                    f"{full} de {y}", f"{full.capitalize()} de {y}",
                    f"{mm}-{y}", f"{mm2}-{y}", f"{mm}/{y}",
                })
            return list(out)

        return list(out)

    if month_to_use:
        try:
            aliases = _month_aliases(month_to_use)  # si ya tienes este helper
        except NameError:
            aliases = _aliases_for_month(month_to_use)

        cond = Q()
        for a in aliases:
            cond |= Q(mes_produccion__icontains=a)
        qs = qs.filter(cond)

    # Proyecto (id_claro/id_new/detalle)
    if f_project:
        qs = qs.filter(
            Q(id_claro__icontains=f_project) |
            Q(id_new__icontains=f_project) |
            Q(detalle_tarea__icontains=f_project)
        )

    if f_region:
        qs = qs.filter(region__icontains=f_region)

    # ---- Agregación mensual por técnico con detalle de DUs + ajustes ----
    agregados: dict[int, dict] = {}

    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        if not asignados:
            continue

        n = len(asignados)
        base = Decimal(str(s.monto_mmoo or 0))

        # Normalizar signo según estado
        if s.estado in AJ_NEG:
            firmado = -abs(base)        # adelanto / descuento restan
        elif s.estado in AJ_POS:
            firmado = abs(base)        # bono suma
        else:
            firmado = base              # producción tal cual

        parte = (firmado / n).quantize(Decimal("0.01"))

        # Etiqueta para el detalle (columna "DU")
        du_label, project_id = _fmt_du_and_pid(s)

        for tec in asignados:
            # Filtro por técnico (nombre/username)
            if f_tecnico:
                target = f_tecnico.lower()
                full_name = ((tec.first_name or "") + " " +
                             (tec.last_name or "")).strip().lower()
                username = (tec.username or "").lower()
                if target not in full_name and target not in username:
                    continue

            display_name = tec.get_full_name() or tec.username

            bucket = agregados.setdefault(tec.id, {
                "tecnico": tec,
                "display_name": display_name,
                "week": month_to_use,           # etiqueta “Mes de pago”
                "amount": Decimal("0.00"),
                "details": [],
                # compat con layout actual:
                "status": "",
                "receipt": None,
            })
            bucket["amount"] += parte
            bucket["details"].append({
                "project_id": s.id_claro or s.id_new or du_label,
                "du": du_label,
                "subtotal": parte,
            })

    # -> lista ordenada por nombre
    rows = list(agregados.values())
    rows.sort(key=lambda r: (
        (r["tecnico"].last_name or "").lower(),
        (r["tecnico"].first_name or "").lower(),
        (r["tecnico"].username or "").lower(),
    ))

    # Paginación
    if cantidad == "todos":
        pagina = rows
    else:
        try:
            per_page = max(5, min(100, int(cantidad)))
        except ValueError:
            per_page = 10
            cantidad = "10"
        paginator = Paginator(rows, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)

    filters_qs = urlencode({
        k: v for k, v in {
            "f_month": month_to_use,
            "f_tecnico": f_tecnico,
            "f_project": f_project,
            "f_region":  f_region,
            "cantidad":  cantidad,
        }.items() if v
    })

    return render(request, "operaciones/produccion_totales_admin.html", {
        "current_month": current_month,
        "selected_month": month_to_use,
        "top": rows,               # Parte superior “Este mes”
        "pagina": pagina,          # Historial (mismo dataset, paginado)
        "cantidad": cantidad,
        "f_month": month_to_use,
        "f_tecnico": f_tecnico,
        "f_project": f_project,
        "f_region": f_region,
        "filters_qs": filters_qs,
    })


@login_required
def exportar_totales_produccion(request):
    """
    Exporta a Excel los totales a pagar (mensual) por técnico con detalle por DU/ajuste.
    Respeta filtros de mes (acepta YYYY-MM y alias), técnico, proyecto y región.
    """
    current_month = _yyyy_mm(timezone.localdate())

    f_month = (request.GET.get("f_month") or "").strip() or current_month
    f_tecnico = (request.GET.get("f_tecnico") or "").strip()
    f_project = (request.GET.get("f_project") or "").strip()
    f_region = (request.GET.get("f_region") or "").strip()

    # Query base con ajustes
    qs = (
        ServicioCotizado.objects
        .filter(estado__in=ESTADOS_PROD_Y_AJUSTES)
        .prefetch_related("trabajadores_asignados")
        .order_by("-fecha_aprobacion_supervisor", "-id")
    )

    # Filtro por mes con alias
    if f_month:
        cond_m = Q()
        for a in _month_aliases(f_month):
            cond_m |= Q(mes_produccion__icontains=a)
        qs = qs.filter(cond_m)

    if f_project:
        qs = qs.filter(
            Q(id_claro__icontains=f_project) |
            Q(id_new__icontains=f_project) |
            Q(detalle_tarea__icontains=f_project)
        )
    if f_region:
        qs = qs.filter(region__icontains=f_region)

    agregados: dict[int, dict] = {}
    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        if not asignados:
            continue
        n = len(asignados)

        firmado = _monto_firmado_por_estado(s.monto_mmoo, s.estado)
        parte = (Decimal(str(firmado)) / n).quantize(Decimal("0.01"))

        du_label, project_id = _fmt_du_and_pid(s)

        for tec in asignados:
            if f_tecnico:
                target = f_tecnico.lower()
                full_name = ((tec.first_name or "") + " " +
                             (tec.last_name or "")).strip().lower()
                username = (tec.username or "").lower()
                if target not in full_name and target not in username:
                    continue
            b = agregados.setdefault(tec.id, {
                "tecnico": tec,
                "week": f_month,
                "amount": Decimal("0.00"),
                "details": [],
            })
            b["amount"] += parte
            b["details"].append({
                "project_id": project_id,
                "du": du_label,
                "subtotal": parte,
            })

    rows = list(agregados.values())
    rows.sort(key=lambda r: (
        (r["tecnico"].last_name or "").lower(),
        (r["tecnico"].first_name or "").lower(),
        (r["tecnico"].username or "").lower(),
    ))

    # ---- Excel ----
    wb = Workbook()
    ws = wb.active
    ws.title = "Totales a pagar"

    ws.append(["Técnico", "Mes", "Total (CLP)"])
    for r in rows:
        tech_name = r["tecnico"].get_full_name() or r["tecnico"].username
        ws.append([tech_name, r["week"], float(r["amount"])])

    # Hoja de detalle
    ws2 = wb.create_sheet("Detalle por DU")
    ws2.append(["Técnico", "Mes", "Proyecto (ID)", "DU", "Subtotal (CLP)"])
    for r in rows:
        tech_name = r["tecnico"].get_full_name() or r["tecnico"].username
        for d in r["details"]:
            ws2.append([tech_name, r["week"], d["project_id"],
                       d["du"], float(d["subtotal"])])

    # Anchos auto
    for sheet in (ws, ws2):
        for col in sheet.columns:
            max_len = 0
            letter = get_column_letter(col[0].column)
            for cell in col:
                try:
                    max_len = max(max_len, len(str(cell.value or "")))
                except Exception:
                    pass
            sheet.column_dimensions[letter].width = min(
                max(12, max_len + 2), 60)

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename="totales_a_pagar_mensual.xlsx"'
    wb.save(resp)
    return resp


ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL",
                             "https://s3.us-east-1.wasabisys.com"),
        region_name=getattr(settings, "AWS_S3_REGION_NAME", "us-east-1"),
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={
                      "addressing_style": "path"}),
        verify=getattr(settings, "AWS_S3_VERIFY", True),
    )


def _format_du(du) -> str:
    """Devuelve DU normalizado:
       - si ya viene con prefijo 'DU' -> lo deja igual
       - si viene solo número/texto  -> le agrega 'DU'
       - si viene vacío               -> '—'
    """
    s = str(du or '').strip()
    if not s:
        return '—'
    return s if s.upper().startswith('DU') else f'DU{s}'


def _fmt_du_and_pid(s) -> tuple[str, str]:
    """
    Devuelve (du_label, project_id) sin duplicar DU como proyecto y,
    si el registro es un AJUSTE y no tiene proyecto, usa una etiqueta
    legible: 'Descuento' / 'Bono' / 'Adelanto'.
    """
    du_label = _format_du(getattr(s, 'du', None))  # 'DU000001' o '—'

    # Proyecto real si existe
    project = (getattr(s, 'id_claro', '') or getattr(
        s, 'id_new', '') or '').strip()

    # Si no hay proyecto, y es ajuste, usamos el nombre del ajuste
    if not project or project in {'-', '—'}:
        estado = getattr(s, 'estado', '')
        ajuste_map = {
            'ajuste_descuento': 'Descuento',
            'ajuste_bono': 'Bono',
            'ajuste_adelanto': 'Adelanto',
        }
        project = ajuste_map.get(estado, '—')

    # Si hay DU, devolvemos DU + proyecto (si lo hay)
    if du_label != '—':
        return du_label, project

    # Si NO hay DU, usamos el proyecto como fallback en ambos
    return project, project


def _sync_monthly_totals(month: str, create_missing: bool = True) -> dict:
    aliases = _month_aliases(month)
    cond = Q()
    for a in aliases:
        cond |= Q(mes_produccion__icontains=a)

    totals: dict[int, Decimal] = {}

    servicios = (
        ServicioCotizado.objects
        .filter(estado__in=ESTADOS_PROD_Y_AJUSTES)   # ← incluir ajustes
        .filter(cond)
        .prefetch_related("trabajadores_asignados")
        .only("id", "du", "id_claro", "id_new", "monto_mmoo", "estado")
    )

    for s in servicios:
        asignados = list(s.trabajadores_asignados.all())
        if not asignados:
            continue
        n = len(asignados)
        firmado = _monto_firmado_por_estado(s.monto_mmoo, s.estado)
        parte = (Decimal(str(firmado)) / n).quantize(Decimal("0.01"))
        for tec in asignados:
            totals[tec.id] = totals.get(tec.id, Decimal("0.00")) + parte

    existing = {(mp.technician_id, mp.month): mp
                for mp in MonthlyPayment.objects.filter(month=month)}
    updated = created = deleted = 0

    for tech_id, amount in totals.items():
        key = (tech_id, month)
        mp = existing.pop(key, None)
        if mp:
            if mp.amount != amount:
                mp.amount = amount
                save_fields = ["amount", "updated_at"]
                if mp.status == "approved_user":
                    mp.status = "pending_payment"
                    save_fields.append("status")
                mp.save(update_fields=save_fields)
                updated += 1
        else:
            if create_missing and amount != 0:
                MonthlyPayment.objects.create(
                    technician_id=tech_id,
                    month=month,
                    amount=amount,
                    status="pending_user",
                )
                created += 1

    for _key, mp in existing.items():
        if mp.status != "paid":
            mp.delete()
            deleted += 1

    return {"updated": updated, "created": created, "deleted": deleted}


@login_required
def admin_monthly_payments(request):
    """
    Pagos mensuales (ADMIN):
    - TOP: mes seleccionado (no pagados) a partir de MonthlyPayment (ya sincronizado con ajustes).
    - HISTORIAL: sólo registros pagados (status='paid').
    - Adjunta 'details' por DU/ajuste para cada fila, prorrateado y con signo.
    """
    current_month = _yyyy_mm(timezone.localdate())

    # Filtros
    f_month = (request.GET.get("f_month") or "").strip()
    f_tecnico = (request.GET.get("f_tecnico") or "").strip()
    f_project = (request.GET.get("f_project")
                 or "").strip()   # opcional (no DB)
    f_region = (request.GET.get("f_region")
                or "").strip()     # opcional (no DB)
    f_du = (request.GET.get("f_du") or "").strip()
    f_paid_month = (request.GET.get("f_paid_month") or "").strip()
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()

    month_to_use = f_month or current_month

    # 1) Sincroniza el mes seleccionado (crea/actualiza MonthlyPayment con AJUSTES)
    _sync_monthly_totals(month_to_use, create_missing=True)

    # Helper: desglose por DU/AJUSTE para (tecnico, mes)
    def _details_for(tech_id: int, month: str):
        aliases = _month_aliases(month)
        cond = Q()
        for a in aliases:
            cond |= Q(mes_produccion__icontains=a)

        detalles = []
        servicios = (
            ServicioCotizado.objects
            .filter(estado__in=ESTADOS_PROD_Y_AJUSTES)   # ← incluir ajustes
            .filter(cond)
            .prefetch_related("trabajadores_asignados")
            .only("du", "id_claro", "id_new", "monto_mmoo", "estado")
        )
        for s in servicios:
            tecs = list(s.trabajadores_asignados.all())
            if not tecs or not any(t.id == tech_id for t in tecs):
                continue

            firmado = _monto_firmado_por_estado(s.monto_mmoo, s.estado)
            parte = (Decimal(str(firmado)) / len(tecs)
                     ).quantize(Decimal("0.01"))

            du_label, project_id = _fmt_du_and_pid(s)

            detalles.append({
                "du": du_label,
                "project_id": project_id,
                "subtotal": parte,
            })
        return detalles

    # 2) TOP (este mes): no pagados
    top_qs = (
        MonthlyPayment.objects
        .filter(month=month_to_use, amount__gt=0)
        .exclude(status="paid")
        .select_related("technician")
        .order_by("status", "technician__first_name", "technician__last_name")
    )
    if f_tecnico:
        top_qs = top_qs.filter(
            Q(technician__first_name__icontains=f_tecnico) |
            Q(technician__last_name__icontains=f_tecnico) |
            Q(technician__username__icontains=f_tecnico)
        )

    top = list(top_qs)
    for r in top:
        r.display_name = (r.technician.get_full_name()
                          or r.technician.username)
        r.week = r.month
        r.details = _details_for(r.technician_id, r.month)

    # 3) HISTORIAL (sólo pagados)
    bottom_qs = (
        MonthlyPayment.objects
        .filter(status="paid")
        .select_related("technician")
        .order_by("-paid_month", "-month", "technician__first_name", "technician__last_name")
    )
    if f_month:
        bottom_qs = bottom_qs.filter(month=month_to_use)
    if f_paid_month:
        bottom_qs = bottom_qs.filter(paid_month__icontains=f_paid_month)
    if f_tecnico:
        bottom_qs = bottom_qs.filter(
            Q(technician__first_name__icontains=f_tecnico) |
            Q(technician__last_name__icontains=f_tecnico) |
            Q(technician__username__icontains=f_tecnico)
        )

    # — DU: filtrado en memoria (antes de paginar) usando details
    def _match_du(detalles, needle: str) -> bool:
        if not needle:
            return True
        n = needle.strip().lower()
        for d in detalles:
            du_text = (d.get("du") or "").lower()
            pid = (d.get("project_id") or "").lower()
            if n in du_text or n in pid:
                return True
            if du_text.startswith("du") and du_text[2:] == n:
                return True
        return False

    if f_du:
        tmp = []
        for mp in bottom_qs:
            dets = _details_for(mp.technician_id, mp.month)
            if _match_du(dets, f_du):
                mp.details = dets
                tmp.append(mp)
        bottom_list = tmp
    else:
        bottom_list = list(bottom_qs)

    # Paginación del historial
    if cantidad == "todos":
        pagina = bottom_list
    else:
        try:
            per_page = max(5, min(100, int(cantidad)))
        except ValueError:
            per_page = 10
            cantidad = "10"
        paginator = Paginator(bottom_list, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)

    # Asegurar detalles en visibles
    page_items = list(pagina) if not isinstance(pagina, list) else pagina
    for r in page_items:
        r.display_name = (r.technician.get_full_name()
                          or r.technician.username)
        r.week = r.month
        if not hasattr(r, "details"):
            r.details = _details_for(r.technician_id, r.month)

    filters_qs = urlencode({
        k: v for k, v in {
            "f_month": month_to_use if f_month else "",
            "f_tecnico": f_tecnico,
            "f_project": f_project,
            "f_region": f_region,
            "f_du": f_du,
            "f_paid_month": f_paid_month,
            "cantidad": cantidad,
        }.items() if v
    })

    return render(request, "operaciones/produccion_totales_admin.html", {
        "current_month": current_month,
        "selected_month": month_to_use,
        "top": top,
        "pagina": pagina,
        "cantidad": cantidad,
        "f_month": month_to_use if f_month else "",
        "f_tecnico": f_tecnico,
        "f_project": f_project,
        "f_region": f_region,
        "f_du": f_du,
        "f_paid_month": f_paid_month,
        "filters_qs": filters_qs,
    })


log = logging.getLogger('presign_receipt_monthly')


@require_POST
@login_required
def presign_receipt_monthly(request, pk: int):
    pay_id = pk
    log.debug('HIT presign_receipt_monthly pay_id=%s user=%s ajax=%s',
              pay_id, getattr(request.user, 'username', None),
              request.headers.get('X-Requested-With'))
    obj = get_object_or_404(MonthlyPayment, pk=pay_id)
    filename = request.POST.get(
        "filename") or f"receipt-{uuid.uuid4().hex}.pdf"
    base, ext = os.path.splitext(filename)
    ext = (ext or ".pdf").lower()
    key = f"operaciones/pagos/{obj.month}/{obj.technician_id}/receipt_{uuid.uuid4().hex}{ext}"

    s3 = _s3_client()
    fields = {"acl": "private", "success_action_status": "201"}
    conditions = [
        {"acl": "private"},
        {"success_action_status": "201"},
        ["content-length-range", 0, 25 * 1024 * 1024],
    ]
    post = s3.generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=600,
    )
    endpoint = settings.AWS_S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    post["url"] = f"{endpoint}/{bucket}"
    log.debug('presign OK url=%s', post["url"])
    return JsonResponse({"post": post, "key": key})


log = logging.getLogger('confirm_receipt_monthly')


@require_POST
@login_required
def confirm_receipt_monthly(request, pay_id: int = None, pk: int = None):
    # Acepta ambos nombres desde la URL
    pay_id = pay_id or pk
    log.debug('HIT confirm_receipt_monthly id=%s user=%s',
              pay_id, getattr(request.user, 'username', None))
    try:
        obj = get_object_or_404(MonthlyPayment, pk=pay_id)

        key = (request.POST.get("key") or "").strip()
        log.debug('confirm key=%s', key)
        if not key:
            return JsonResponse({"ok": False, "error": "missing key"}, status=400)

        obj.receipt.name = key
        obj.status = "paid"
        obj.paid_month = _yyyy_mm(timezone.localdate())
        obj.save(update_fields=["receipt", "status",
                 "paid_month", "updated_at"])

        log.debug('confirm OK receipt=%s status=paid paid_month=%s',
                  obj.receipt.name, obj.paid_month)
        return JsonResponse({"ok": True})

    except Exception as e:
        log.exception('confirm ERROR id=%s', pay_id)
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_POST
@login_required
@transaction.atomic
def admin_unpay_monthly(request, pk: int):
    mp = get_object_or_404(MonthlyPayment, pk=pk)

    # 🚩 Señales de que esperan JSON
    xrw = (request.headers.get('X-Requested-With') or
           request.META.get('HTTP_X_REQUESTED_WITH') or '')
    accept = (request.headers.get('Accept') or '')
    is_ajax = (
        # 👈 parámetro explícito
        request.GET.get('ajax') == '1'
        or xrw.lower() == 'xmlhttprequest'                   # case-insensitive
        or 'application/json' in accept.lower()
        or 'json' in accept.lower()
    )

    if mp.status != "paid":
        if is_ajax:
            return JsonResponse({"ok": False, "error": "only_paid_can_be_reverted"}, status=400)
        messages.info(
            request, "Sólo se puede revertir pagos en estado 'Pagado'.")
        return redirect("operaciones:admin_monthly_payments")

    # borrar recibo si existe (ignorar errores silenciosamente)
    try:
        if mp.receipt:
            mp.receipt.delete(save=False)
    except Exception:
        pass

    mp.receipt = None
    mp.paid_month = ""
    mp.status = "pending_payment"
    mp.save(update_fields=["receipt", "paid_month", "status", "updated_at"])

    if is_ajax:
        return JsonResponse({"ok": True})

    messages.success(request, "Pago revertido. Vuelve a 'Pendiente de pago'.")
    return redirect("operaciones:admin_monthly_payments")


@login_required
def user_monthly_payments(request):
    """
    Vista del técnico:
    - Sincroniza el registro del mes actual (con ajustes).
    - Lista sus MonthlyPayment con desglose (DU/AJUSTE) y TOTAL (amount/display_amount).
    """
    current_month = _yyyy_mm(timezone.localdate())
    _sync_monthly_totals(current_month, create_missing=True)

    mine = (
        MonthlyPayment.objects
        .filter(technician=request.user)
        .order_by("-month")
    )

    # Prepara desglose y totales por mes (para este usuario)
    by_month = {mp.month for mp in mine}
    details: dict[str, list] = {}
    totals: dict[str, Decimal] = {}

    for m in by_month:
        aliases = _month_aliases(m)
        cond = Q()
        for a in aliases:
            cond |= Q(mes_produccion__icontains=a)

        servicios = (
            ServicioCotizado.objects
            .filter(estado__in=ESTADOS_PROD_Y_AJUSTES)   # ← incluir ajustes
            .filter(cond)
            .prefetch_related("trabajadores_asignados")
            .only("du", "id_claro", "id_new", "monto_mmoo", "estado")
        )

        tot_m = Decimal("0.00")
        det_m = []

        for s in servicios:
            tecs = list(s.trabajadores_asignados.all())
            if not tecs or not any(t.id == request.user.id for t in tecs):
                continue

            firmado = _monto_firmado_por_estado(s.monto_mmoo, s.estado)
            parte = (Decimal(str(firmado)) / len(tecs)
                     ).quantize(Decimal("0.01"))

            du_label, project_id = _fmt_du_and_pid(s)

            det_m.append({
                "du": du_label,
                "project_id": project_id,
                "subtotal": parte,
            })
            tot_m += parte

        if det_m:
            details[m] = det_m
        totals[m] = tot_m

    # Inyecta 'details' y 'display_amount' en cada MonthlyPayment
    for mp in mine:
        mp.details = details.get(mp.month, [])
        if hasattr(mp, "amount") and mp.amount is not None:
            mp.display_amount = Decimal(
                str(mp.amount)).quantize(Decimal("0.01"))
        else:
            mp.display_amount = totals.get(
                mp.month, Decimal("0.00")).quantize(Decimal("0.01"))

    return render(request, "operaciones/pagos_usuario_mensual.html", {
        "current_month": current_month,
        "mine": mine,
    })


@login_required
@require_POST
@transaction.atomic
def user_approve_monthly(request, pk: int):
    mp = get_object_or_404(MonthlyPayment, pk=pk, technician=request.user)
    if mp.status != "pending_user":
        messages.info(
            request, "Sólo puedes aprobar cuando está 'Pendiente de mi aprobación'.")
        return redirect("operaciones:user_monthly_payments")

    mp.reject_reason = ""
    mp.status = "pending_payment"
    mp.save(update_fields=["status", "reject_reason", "updated_at"])
    messages.success(request, "Monto aprobado. Esperando pago.")
    return redirect("operaciones:user_monthly_payments")


@login_required
@require_POST
@transaction.atomic
def user_reject_monthly(request, pk: int):
    mp = get_object_or_404(MonthlyPayment, pk=pk, technician=request.user)
    reason = (request.POST.get("reason") or "").strip()
    if mp.status != "pending_user":
        messages.info(
            request, "Sólo puedes rechazar cuando está 'Pendiente de mi aprobación'.")
        return redirect("operaciones:user_monthly_payments")
    if not reason:
        messages.error(request, "Debes indicar un motivo.")
        return redirect("operaciones:user_monthly_payments")

    mp.reject_reason = reason
    mp.status = "rejected_user"
    mp.save(update_fields=["status", "reject_reason", "updated_at"])
    messages.success(request, "Monto rechazado. Motivo guardado.")
    return redirect("operaciones:user_monthly_payments")

# --- NUEVA / ACTUALIZADA: Producción del TÉCNICO (flujo mensual) ---
# --- Mi PRODUCCIÓN (técnico) ---


APROBADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


# Asegúrate de tener estos importados en tu archivo real:
# from operaciones.models import ServicioCotizado
# from operaciones.const import APROBADOS_OK


def _yyyy_mm(dt) -> str:
    d = dt.date() if hasattr(dt, "date") else dt
    return f"{d.year:04d}-{d.month:02d}"


def _month_aliases(token: str) -> list[str]:
    s = (token or "").strip()
    if not s:
        return []
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if not m:
        return [s]
    y = int(m.group(1))
    mm = int(m.group(2))
    meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    if 1 <= mm <= 12:
        return [s, f"{meses[mm-1]} {y}"]
    return [s]


@login_required
def produccion_tecnico(request):
    current_month = _yyyy_mm(timezone.localdate())

    q_text = (request.GET.get("id_claro") or "").strip()
    q_month = (request.GET.get("mes_produccion") or "").strip()
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()

    # 👉 incluir ajustes para que se muestren al usuario
    AJUSTES = ["ajuste_bono", "ajuste_adelanto", "ajuste_descuento"]

    qs = (
        ServicioCotizado.objects
        .filter(
            Q(estado__in=APROBADOS_OK) | Q(estado__in=AJUSTES),
            trabajadores_asignados=request.user
        )
        .order_by("-fecha_aprobacion_supervisor", "-id")
        .prefetch_related("trabajadores_asignados")
    )

    # ====== Encabezado (mes actual) ======
    _y = int(current_month[:4])
    _m = int(current_month[5:7])
    _meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
              "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    current_month_label = f"{_meses[_m-1]} {_y}"

    total_current_month = Decimal("0.00")
    _cond_mes = Q()
    for alias in _month_aliases(current_month):
        _cond_mes |= Q(mes_produccion__icontains=alias)
    for s in qs.filter(_cond_mes):
        asignados_cm = list(s.trabajadores_asignados.all())
        if not asignados_cm:
            continue
        parte_cm = (Decimal(str(s.monto_mmoo or 0)) /
                    len(asignados_cm)).quantize(Decimal("0.01"))
        total_current_month += parte_cm
    # =====================================

    if q_month:
        cond = Q()
        for alias in _month_aliases(q_month):
            cond |= Q(mes_produccion__icontains=alias)
        qs = qs.filter(cond)

    if q_text:
        qs = qs.filter(Q(id_claro__icontains=q_text) |
                       Q(id_new__icontains=q_text) |
                       Q(detalle_tarea__icontains=q_text))

    filas, total_estimado = [], Decimal("0.00")
    for s in qs:
        asignados = list(s.trabajadores_asignados.all())
        if not asignados:
            continue
        parte = (Decimal(str(s.monto_mmoo or 0)) /
                 len(asignados)).quantize(Decimal("0.01"))
        filas.append({
            "id": s.id,
            "du": s.du,
            "id_claro": s.id_claro or "",
            "id_new": s.id_new or "",
            "region": s.region or "",
            "mes_produccion": s.mes_produccion or "",
            "detalle_tarea": s.detalle_tarea or "",
            "estado": s.estado or "",
            "monto_tecnico": parte,
        })
        total_estimado += parte

    if cantidad == "todos":
        pagina = filas
    else:
        try:
            per_page = max(5, min(100, int(cantidad)))
        except ValueError:
            per_page, cantidad = 10, "10"
        paginator = Paginator(filas, per_page)
        pagina = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "operaciones/produccion_tecnico.html", {
        "current_month": current_month,
        "current_month_label": current_month_label,
        "total_current_month": total_current_month,
        "pagina": pagina,
        "cantidad": cantidad,
        "id_claro": q_text,
        "mes_produccion": q_month,
        "total_estimado": total_estimado,
    })


logger = logging.getLogger(__name__)


@login_required
@rol_requerido('usuario')
def exportar_produccion_pdf(request):
    try:
        usuario = request.user
        id_new = request.GET.get("id_new", "")
        mes_produccion = request.GET.get("mes_produccion", "")
        filtro_pdf = request.GET.get("filtro_pdf", "mes_actual")

        # Traducción manual de meses
        meses_es = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        now_local = timezone.localtime()
        mes_actual = f"{meses_es[now.month]} {now.year}"

        # Texto de filtro
        if filtro_pdf == "mes_actual":
            filtro_seleccionado = f"Solo mes actual: {mes_actual}"
        elif filtro_pdf == "filtro_actual":
            filtro_seleccionado = f"Con filtros aplicados: {mes_produccion}" if mes_produccion else "Con filtros aplicados"
        else:
            filtro_seleccionado = "Toda la producción"

        # Query base
        servicios = ServicioCotizado.objects.filter(
            trabajadores_asignados=usuario,
            estado='aprobado_supervisor'
        )

        # Filtro según selección
        if filtro_pdf == "filtro_actual":
            if id_new:
                servicios = servicios.filter(id_new__icontains=id_new)
            if mes_produccion:
                servicios = servicios.filter(
                    mes_produccion__icontains=mes_produccion)
        elif filtro_pdf == "mes_actual":
            servicios = servicios.filter(mes_produccion__iexact=mes_actual)

        # Si no hay datos, lanzamos excepción
        if not servicios.exists():
            raise ValueError("No hay datos para exportar.")

        # Datos PDF
        produccion_data = []
        total_produccion = Decimal("0.0")
        for servicio in servicios:
            total_mmoo = servicio.monto_mmoo or Decimal("0.0")
            total_tecnicos = servicio.trabajadores_asignados.count()
            monto_tecnico = total_mmoo / \
                total_tecnicos if total_tecnicos else Decimal("0.0")

            produccion_data.append([
                f"DU{servicio.du}",
                servicio.id_new or "-",
                Paragraph(servicio.detalle_tarea or "-", ParagraphStyle(
                    'detalle_style', fontSize=9, leading=11, alignment=0)),
                f"{monto_tecnico:,.0f}".replace(",", ".")
            ])

            total_produccion += monto_tecnico

        # Generación PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                                topMargin=50, bottomMargin=50)
        elements = []
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="CenterTitle",
                   alignment=1, fontSize=16, spaceAfter=20))

        # Títulos
        elements.append(Paragraph(
            f"Producción del Técnico: {usuario.get_full_name()}", styles["CenterTitle"]))
        elements.append(Paragraph(
            f"<b>Total Producción:</b> ${total_produccion:,.0f} CLP".replace(",", "."), styles["Normal"]))
        elements.append(Paragraph(
            f"<i>El total corresponde a la selección:</i> {filtro_seleccionado}.", styles["Normal"]))
        elements.append(Paragraph(
            f"<b>Fecha de generación:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
        elements.append(Spacer(1, 12))

        # Tabla
        data = [["DU", "ID NEW", "Detalle",
                 "Producción (CLP)"]] + produccion_data
        table = Table(data, colWidths=[70, 100, 300, 80])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e7490")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.whitesmoke, colors.lightgrey]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.gray),
        ]))
        elements.append(table)

        # Firma
        elements.append(Spacer(1, 40))
        elements.append(
            Paragraph("<b>Firma del Técnico:</b>", styles["Normal"]))
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(
            f"__________________________<br/>{usuario.get_full_name()}", styles["Normal"]))

        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="produccion.pdf"'
        return response

    except Exception as e:
        logger.error(f"Error exportando PDF: {e}")
        return HttpResponse(f"Error generando PDF: {e}", status=500)


User = get_user_model()


# Helper: siguiente DU disponible (correlativo, sin duplicar)


def _next_du():
    last_num = (
        ServicioCotizado.objects
        .exclude(du__isnull=True)
        .exclude(du__exact="")
        .annotate(
            du_num=Cast(
                Replace("du", Value("DU"), Value("")),
                IntegerField()
            )
        )
        .aggregate(m=Max("du_num"))
        .get("m") or 0
    )
    return int(last_num) + 1


@require_POST
@login_required
@transaction.atomic
def crear_ajuste(request):
    """
    Crea un ajuste (bono / adelanto / descuento) para uno o más técnicos.
    - Si la petición es AJAX (X-Requested-With / Accept JSON / ?ajax=1):
        * setea messages igualmente (en sesión)
        * responde JSON con {'ok': ..., 'redirect': <url>}
        * el frontend debe hacer window.location = redirect
    - Si NO es AJAX: PRG clásico -> setea messages y redirect.
    """
    # ¿Es AJAX?
    xrw = (request.headers.get('X-Requested-With')
           or request.META.get('HTTP_X_REQUESTED_WITH') or '')
    accept = (request.headers.get('Accept') or '')
    is_ajax = (
        request.GET.get('ajax') == '1'
        or xrw.lower() == 'xmlhttprequest'
        or 'application/json' in accept.lower()
        or 'json' in accept.lower()
    )
    go_back = reverse('operaciones:produccion_admin')

    try:
        User = get_user_model()

        tipo = (request.POST.get('tipo') or '').strip()
        mes_texto = (request.POST.get('mes_texto') or '').strip()
        detalle = (request.POST.get('detalle') or '').strip()
        monto = Decimal(str(request.POST.get('monto') or '0')
                        ).quantize(Decimal('0.01'))
        asignados = request.POST.getlist('asignados')

        # Validaciones
        if not tipo or not mes_texto or not detalle or not asignados:
            msg = 'Faltan campos obligatorios.'
            messages.error(request, msg)
            if is_ajax:
                return JsonResponse({'ok': False, 'error': msg, 'redirect': go_back}, status=400)
            return redirect(go_back)

        if monto <= 0:
            msg = 'El monto debe ser mayor a 0.'
            messages.error(request, msg)
            if is_ajax:
                return JsonResponse({'ok': False, 'error': msg, 'redirect': go_back}, status=400)
            return redirect(go_back)

        if tipo not in {'ajuste_bono', 'ajuste_adelanto', 'ajuste_descuento'}:
            msg = 'Tipo inválido.'
            messages.error(request, msg)
            if is_ajax:
                return JsonResponse({'ok': False, 'error': msg, 'redirect': go_back}, status=400)
            return redirect(go_back)

        # Regla de signo: sólo descuento resta; bono y adelanto suman
        monto_mmoo = -monto if tipo == 'ajuste_descuento' else monto

        created_ids = []
        for uid in asignados:
            user = User.objects.filter(id=uid).first()
            if not user:
                continue

            du_text = f"DU{_next_du():08d}"  # DU correlativo "DU00000047"

            s = ServicioCotizado.objects.create(
                du=du_text,
                id_claro='-',
                region='-',
                mes_produccion=mes_texto,
                id_new='-',
                detalle_tarea=detalle,
                monto_cotizado=0,
                monto_mmoo=monto_mmoo,
                estado=tipo,
                fecha_aprobacion_supervisor=None,
            )
            s.trabajadores_asignados.add(user)
            created_ids.append(s.id)

        # OK: colocar message y responder según modo
        messages.success(request, 'Ajuste(s) creado(s) correctamente.')
        if is_ajax:
            return JsonResponse({'ok': True, 'created': created_ids, 'redirect': go_back})
        return redirect(go_back)

    except Exception as e:
        # Error inesperado -> message + redirect/JSON
        err = f'No se pudo crear el/los ajuste(s): {e}'
        messages.error(request, err)
        if is_ajax:
            return JsonResponse({'ok': False, 'error': err, 'redirect': go_back}, status=500)
        return redirect(go_back)

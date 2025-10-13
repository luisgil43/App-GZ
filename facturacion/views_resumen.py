# facturacion/views_resumen.py
from __future__ import annotations
from datetime import date, datetime, time as dtime
from dateutil.relativedelta import relativedelta
from decimal import Decimal

from django.db.models import Sum, Count, Value, DecimalField, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required

# Decorador de roles (si no existe, queda no-op)
try:
    from usuarios.decoradores import rol_requerido
except Exception:  # pragma: no cover
    def rol_requerido(*roles):
        def _decorator(fn):
            return fn
        return _decorator

# ===== Modelos (según tu proyecto) =====
from operaciones.models import ServicioCotizado
from facturacion.models import CartolaMovimiento

# ===== Estados agrupados =====
ESTADOS_MAP = {
    "cotizados": ["cotizado"],
    "en_proceso": ["aprobado_pendiente", "asignado", "en_progreso", "en_revision_supervisor"],
    "finalizados": ["aprobado_supervisor"],
}

# ===== Meses en español =====
MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]

# ===== Helpers fecha/mes =====


def _parse_month(s: str | None) -> date:
    """Convierte 'YYYY-MM' a date(día 1). Si None/invalid, 1er día del mes actual (TZ local)."""
    tz_today = timezone.localdate()
    if not s:
        return tz_today.replace(day=1)
    try:
        y, m = s.split("-")
        return date(int(y), int(m), 1)
    except Exception:
        return tz_today.replace(day=1)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_range(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    end = start + relativedelta(months=1)  # exclusivo
    return start, end


def _aware_range(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Devuelve datetimes 'aware' [start, end) en la zona horaria actual."""
    tz = timezone.get_current_timezone()
    start_dt = datetime.combine(start_date, dtime.min)
    end_dt = datetime.combine(end_date, dtime.min)
    if timezone.is_naive(start_dt):
        start_dt = timezone.make_aware(start_dt, tz)
    if timezone.is_naive(end_dt):
        end_dt = timezone.make_aware(end_dt, tz)
    return start_dt, end_dt


# ===== Agregadores seguros (Decimal) =====
_DEC_ZERO = Value(Decimal("0"), output_field=DecimalField(
    max_digits=18, decimal_places=2))


def _sum_decimal(qs, field: str) -> Decimal:
    """Suma un campo Decimal devolviendo Decimal(0) si no hay filas."""
    return qs.aggregate(total=Coalesce(Sum(field), _DEC_ZERO))["total"] or Decimal("0")


def _count(qs) -> int:
    return qs.aggregate(c=Coalesce(Count("id"), Value(0)))["c"] or 0

# ===== Querysets por mes =====


def _qs_servicios_mes_by_date(d: date):
    """
    Filtra ServicioCotizado para el mes de 'd' admitiendo DOS formatos en la DB:
    - 'YYYY-MM' (p.ej. '2025-07')
    - 'Mes YYYY' en español (p.ej. 'Julio 2025', 'julio 2025', etc.)
    """
    key = _month_key(d)           # 'YYYY-MM'
    mes_nombre = MESES_ES[d.month - 1]  # 'julio'
    year_str = str(d.year)

    return ServicioCotizado.objects.filter(
        Q(mes_produccion=key) | (Q(mes_produccion__icontains=mes_nombre)
                                 & Q(mes_produccion__icontains=year_str))
    )


def _qs_gastos_mes(start: date, end: date):
    """CartolaMovimiento en rango [start, end) (aware) y status aprobado_finanzas."""
    start_dt, end_dt = _aware_range(start, end)
    return CartolaMovimiento.objects.filter(
        fecha__gte=start_dt, fecha__lt=end_dt, status="aprobado_finanzas"
    )

# ========== Vista HTML ==========


@method_decorator(login_required, name="dispatch")
@method_decorator(rol_requerido("admin", "facturacion", "pm", "supervisor"), name="dispatch")
class View_resumen_dato(TemplateView):
    template_name = "facturacion/resumen.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base_month = _parse_month(self.request.GET.get("month"))
        ctx["month_str"] = _month_key(base_month)
        ctx["default_win_rate"] = 0.6  # tasa de cierre por defecto
        ctx["default_horizon"] = 3
        return ctx

# ========== Vista API JSON ==========


@method_decorator(login_required, name="dispatch")
@method_decorator(rol_requerido("admin", "facturacion", "pm", "supervisor"), name="dispatch")
class ViewResumenDatoAPI(View):
    """KPIs, gastos, series (6 meses) y proyección basada en cotizaciones abiertas."""

    def get(self, request: HttpRequest):
        # ---- Parámetros ----
        month_str = request.GET.get("month")
        base_month = _parse_month(month_str)
        start, end = _month_range(base_month)

        try:
            win_rate = float(request.GET.get("win_rate", "0.6"))
            horizon = max(1, int(request.GET.get("horizon", "3")))
        except Exception:
            win_rate, horizon = 0.6, 3

        # ---- KPIs mes seleccionado ----
        svc_mes = _qs_servicios_mes_by_date(start)
        def _qs_estado(qs, estados): return qs.filter(estado__in=estados)

        kpis = {}
        for k, estados in ESTADOS_MAP.items():
            qs = _qs_estado(svc_mes, estados)
            monto = _sum_decimal(qs, "monto_cotizado")
            kpis[k] = {"count": _count(qs), "amount": float(monto)}

        # ---- Gastos (mes vs anterior) ----
        gasto_mes_qs = _qs_gastos_mes(start, end)
        gasto_mes = _sum_decimal(gasto_mes_qs, "cargos") - \
            _sum_decimal(gasto_mes_qs, "abonos")

        prev_start, prev_end = start - relativedelta(months=1), start
        gasto_prev_qs = _qs_gastos_mes(prev_start, prev_end)
        gasto_prev = _sum_decimal(
            gasto_prev_qs, "cargos") - _sum_decimal(gasto_prev_qs, "abonos")

        # ---- Series últimos 6 meses ----
        months = []
        serie_cotizados, serie_proceso, serie_finalizados = [], [], []
        serie_monto_cot, serie_monto_proc, serie_monto_fin = [], [], []
        serie_gasto = []

        for i in range(5, -1, -1):  # 6 meses
            m = start - relativedelta(months=i)
            ms, me = _month_range(m)
            mkey = _month_key(ms)
            months.append(mkey)

            svc_m = _qs_servicios_mes_by_date(ms)
            qs_cot = _qs_estado(svc_m, ESTADOS_MAP["cotizados"])
            qs_pro = _qs_estado(svc_m, ESTADOS_MAP["en_proceso"])
            qs_fin = _qs_estado(svc_m, ESTADOS_MAP["finalizados"])

            serie_cotizados.append(_count(qs_cot))
            serie_proceso.append(_count(qs_pro))
            serie_finalizados.append(_count(qs_fin))

            serie_monto_cot.append(
                float(_sum_decimal(qs_cot, "monto_cotizado")))
            serie_monto_proc.append(
                float(_sum_decimal(qs_pro, "monto_cotizado")))
            serie_monto_fin.append(
                float(_sum_decimal(qs_fin, "monto_cotizado")))

            g_qs = _qs_gastos_mes(ms, me)
            g_val = _sum_decimal(g_qs, "cargos") - _sum_decimal(g_qs, "abonos")
            serie_gasto.append(float(g_val))

        # ---- Proyección (pipeline cotizaciones abiertas * tasa de cierre / horizonte) ----
        cot_abiertas = ServicioCotizado.objects.filter(
            estado__in=ESTADOS_MAP["cotizados"])
        pipeline_total = _sum_decimal(cot_abiertas, "monto_cotizado")

        proj_months, proj_values = [], []
        for h in range(1, horizon + 1):
            pm = start + relativedelta(months=h)
            proj_months.append(_month_key(pm))
            proj_values.append(
                float((pipeline_total * Decimal(str(win_rate))) / Decimal(horizon)))

        payload = {
            "month": _month_key(start),
            "kpis": kpis,
            "expenses": {
                "current": float(gasto_mes),
                "previous": float(gasto_prev),
                "delta": float(gasto_mes - gasto_prev),
                "pct_change": (float((gasto_mes - gasto_prev) / gasto_prev * 100) if gasto_prev else None),
            },
            "series": {
                "months": months,
                "counts": {
                    "cotizados": serie_cotizados,
                    "en_proceso": serie_proceso,
                    "finalizados": serie_finalizados,
                },
                "amounts": {
                    "cotizados": serie_monto_cot,
                    "en_proceso": serie_monto_proc,
                    "finalizados": serie_monto_fin,
                },
                "expenses": serie_gasto,
            },
            "projections": {
                "months": proj_months,
                "values": proj_values,
                "win_rate": win_rate,
                "horizon": horizon,
                "pipeline_total": float(pipeline_total),
            },
        }
        return JsonResponse(payload)

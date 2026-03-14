from __future__ import annotations

import mimetypes
import os
import re
from collections import defaultdict
from datetime import date
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from openpyxl import Workbook

from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

from .forms import (PrevencionDocumentCreateForm, PrevencionDocumentEditForm,
                    PrevencionDocumentReplaceForm, PrevencionDocumentTypeForm,
                    PrevencionNotificationSettingsForm)
from .models import (PrevencionDocument, PrevencionDocumentType,
                     PrevencionNotificationSettings)


def _badge_classes(status_code: str) -> str:
    return {
        "vigente": "bg-green-100 text-green-800",
        "proximo": "bg-yellow-100 text-yellow-800",
        "vencido": "bg-red-100 text-red-800",
        "sin_vencimiento": "bg-gray-100 text-gray-800",
    }.get(status_code, "bg-gray-100 text-gray-800")


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def dashboard_prevencion(request):
    """
    Listado principal:
    - Documentos Empresa:
        * muestra HISTORIAL visible
        * scope=empresa
        * scope=ambos -> también aparecen aquí
    - Documentos Trabajador:
        * muestra HISTORIAL visible
        * scope=trabajador
        * scope=ambos:
            - si apply_to_all_workers=True -> aparecen para todos los trabajadores activos
            - si apply_to_all_workers=False -> aparecen por trabajador asignado
    """
    today = timezone.localdate()

    docs_empresa = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(scope__in=["empresa", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    docs_worker = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(scope__in=["trabajador", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    empresa_by_type: dict[int, dict] = {}
    worker_map: dict[int, dict] = {}

    active_workers = list(
        CustomUser.objects
        .filter(is_active=True)
        .order_by("first_name", "last_name", "username")
    )

    order_rank = {"vigente": 0, "proximo": 1, "vencido": 2, "sin_vencimiento": 3}

    # =========================
    # EMPRESA: historial visible
    # =========================
    for d in docs_empresa:
        st = d.compute_status(today=today)
        d.status_code = st
        d.status_label_ui = d.status_label(today=today)
        d.badge_class = _badge_classes(st)
        d.remaining_days_ui = d.remaining_days(today=today)

        bucket = empresa_by_type.get(d.doc_type_id)
        if not bucket:
            bucket = {
                "type": d.doc_type,
                "docs": [],
                "history_count": 0,
            }
            empresa_by_type[d.doc_type_id] = bucket

        bucket["docs"].append(d)

    for bucket in empresa_by_type.values():
        bucket["docs"].sort(
            key=lambda x: (
                0 if getattr(x, "current", False) else 1,
                order_rank.get(getattr(x, "status_code", "sin_vencimiento"), 9),
                x.expiry_date or date.max,
                -(x.created_at.timestamp() if x.created_at else 0),
            )
        )
        bucket["history_count"] = len(bucket["docs"])

    empresa_rows = list(empresa_by_type.values())
    empresa_rows.sort(key=lambda r: (r["type"].name or "").lower())

    # =========================
    # TRABAJADOR: historial visible
    # =========================
    for d in docs_worker:
        st = d.compute_status(today=today)
        d.status_code = st
        d.status_label_ui = d.status_label(today=today)
        d.badge_class = _badge_classes(st)
        d.remaining_days_ui = d.remaining_days(today=today)

        if d.apply_to_all_workers and d.scope == "ambos":
            workers_for_doc = active_workers
        else:
            workers_for_doc = list(d.workers.all())

        for w in workers_for_doc:
            wb = worker_map.get(w.id)
            if not wb:
                wb = {
                    "worker": w,
                    "types": defaultdict(list),
                }
                worker_map[w.id] = wb

            wb["types"][d.doc_type_id].append(d)

    worker_rows = []
    for wb in worker_map.values():
        types_out = {}
        for type_id, items in wb["types"].items():
            items.sort(
                key=lambda x: (
                    0 if getattr(x, "current", False) else 1,
                    order_rank.get(getattr(x, "status_code", "sin_vencimiento"), 9),
                    x.expiry_date or date.max,
                    -(x.created_at.timestamp() if x.created_at else 0),
                )
            )
            types_out[type_id] = {
                "type": items[0].doc_type if items else PrevencionDocumentType.objects.get(pk=type_id),
                "docs": items,
            }

        wb["types"] = dict(types_out)
        worker_rows.append(wb)

    worker_rows.sort(
        key=lambda r: ((r["worker"].get_full_name() or r["worker"].username or "").lower())
    )

    settings_obj, _ = PrevencionNotificationSettings.objects.get_or_create(pk=1)

    return render(request, "prevencion/dashboard.html", {
        "empresa_rows": empresa_rows,
        "worker_rows": worker_rows,
        "settings_obj": settings_obj,
    })


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def document_create(request):
    if request.method == "POST":
        form = PrevencionDocumentCreateForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with transaction.atomic():
                    obj: PrevencionDocument = form.save(commit=False)
                    obj.scope = obj.doc_type.scope
                    obj.created_by = request.user
                    obj.current = True
                    obj.save()

                    workers = form.cleaned_data.get("workers")
                    apply_all = bool(form.cleaned_data.get("apply_to_all_workers"))

                    if obj.scope == "empresa":
                        obj.apply_to_all_workers = False
                        obj.save(update_fields=["apply_to_all_workers", "updated_at"])
                        obj.workers.clear()

                    elif obj.scope == "trabajador":
                        obj.apply_to_all_workers = False
                        obj.save(update_fields=["apply_to_all_workers", "updated_at"])
                        obj.workers.set(workers)

                    elif obj.scope == "ambos":
                        obj.apply_to_all_workers = apply_all
                        obj.save(update_fields=["apply_to_all_workers", "updated_at"])

                        if apply_all:
                            obj.workers.clear()
                        else:
                            obj.workers.set(workers)

                messages.success(request, "✅ Documento creado.")
                return redirect("prevencion:dashboard")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = PrevencionDocumentCreateForm()

    type_scopes = {
        str(t.id): t.scope
        for t in PrevencionDocumentType.objects.all().only("id", "scope")
    }

    return render(request, "prevencion/document_form.html", {
        "form": form,
        "mode": "create",
        "type_scopes": type_scopes,
    })


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def document_edit(request, pk: int):
    obj = get_object_or_404(
        PrevencionDocument.objects.select_related("doc_type").prefetch_related("workers"),
        pk=pk,
    )

    if request.method == "POST":
        form = PrevencionDocumentEditForm(request.POST, instance=obj)
        if form.is_valid():
            try:
                with transaction.atomic():
                    obj = form.save(commit=False)
                    workers = form.cleaned_data.get("workers")
                    apply_all = bool(form.cleaned_data.get("apply_to_all_workers"))

                    if obj.scope == "empresa":
                        obj.apply_to_all_workers = False

                    elif obj.scope == "trabajador":
                        obj.apply_to_all_workers = False

                    elif obj.scope == "ambos":
                        obj.apply_to_all_workers = apply_all

                    obj.save()

                    if obj.scope == "empresa":
                        obj.workers.clear()

                    elif obj.scope == "trabajador":
                        obj.workers.set(workers)

                    elif obj.scope == "ambos":
                        if obj.apply_to_all_workers:
                            obj.workers.clear()
                        else:
                            obj.workers.set(workers)

                messages.success(request, "✅ Documento actualizado.")
                return redirect("prevencion:dashboard")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = PrevencionDocumentEditForm(instance=obj)

    type_scopes = {
        str(t.id): t.scope
        for t in PrevencionDocumentType.objects.all().only("id", "scope")
    }

    return render(request, "prevencion/document_form.html", {
        "form": form,
        "mode": "edit",
        "doc": obj,
        "type_scopes": type_scopes,
    })


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def document_replace(request, pk: int):
    old = get_object_or_404(
        PrevencionDocument.objects.select_related("doc_type").prefetch_related("workers"),
        pk=pk,
    )

    current_workers = old.workers.all() if old.scope != "empresa" else None

    initial_data = {
        "no_requiere_vencimiento": old.no_requiere_vencimiento,
        "issue_date": old.issue_date.strftime("%Y-%m-%d") if old.issue_date else "",
        "expiry_date": old.expiry_date.strftime("%Y-%m-%d") if old.expiry_date else "",
        "apply_to_all_workers": old.apply_to_all_workers,
        "workers": current_workers,
    }

    if request.method == "POST":
        form = PrevencionDocumentReplaceForm(
            request.POST,
            request.FILES,
            scope=old.scope,
            current_workers=current_workers,
            current_apply_all=old.apply_to_all_workers,
        )
        if form.is_valid():
            try:
                with transaction.atomic():
                    new_file = form.cleaned_data.get("file")
                    workers = form.cleaned_data.get("workers")
                    apply_all = bool(form.cleaned_data.get("apply_to_all_workers"))

                    file_to_use = new_file if new_file else old.file

                    new_doc = PrevencionDocument.objects.create(
                        doc_type=old.doc_type,
                        scope=old.scope,
                        title=old.title,
                        file=file_to_use,
                        issue_date=form.cleaned_data.get("issue_date"),
                        expiry_date=form.cleaned_data.get("expiry_date"),
                        no_requiere_vencimiento=bool(form.cleaned_data.get("no_requiere_vencimiento")),
                        notify_enabled=True,
                        current=True,
                        created_by=request.user,
                        apply_to_all_workers=False,
                    )

                    if old.scope == "empresa":
                        new_doc.apply_to_all_workers = False
                        new_doc.save(update_fields=["apply_to_all_workers", "updated_at"])
                        new_doc.workers.clear()

                    elif old.scope == "trabajador":
                        new_doc.apply_to_all_workers = False
                        new_doc.save(update_fields=["apply_to_all_workers", "updated_at"])
                        new_doc.workers.set(workers)

                    elif old.scope == "ambos":
                        new_doc.apply_to_all_workers = apply_all
                        new_doc.save(update_fields=["apply_to_all_workers", "updated_at"])

                        if apply_all:
                            new_doc.workers.clear()
                        else:
                            new_doc.workers.set(workers)

                    old.current = False
                    old.notify_enabled = False
                    old.replaced_by = new_doc
                    old.save(update_fields=["current", "notify_enabled", "replaced_by", "updated_at"])

                if new_file:
                    messages.success(request, "✅ Documento reemplazado. La nueva versión quedó vigente.")
                else:
                    messages.success(
                        request,
                        "✅ Documento actualizado como nueva versión, reutilizando el archivo actual."
                    )

                return redirect("prevencion:dashboard")
            except Exception as e:
                form.add_error(None, f"No se pudo reemplazar: {e}")
    else:
        form = PrevencionDocumentReplaceForm(
            scope=old.scope,
            current_workers=current_workers,
            current_apply_all=old.apply_to_all_workers,
            initial=initial_data,
        )

    return render(request, "prevencion/document_replace.html", {
        "form": form,
        "old": old,
    })


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def document_delete(request, pk: int):
    obj = get_object_or_404(PrevencionDocument, pk=pk)
    if request.method == "POST":
        try:
            obj.delete()
            messages.success(request, "✅ Documento eliminado.")
        except Exception:
            messages.error(request, "No se pudo eliminar el documento.")
        return redirect("prevencion:dashboard")

    return render(request, "prevencion/document_delete.html", {"doc": obj})


@login_required
@rol_requerido("admin", "pm", "prevencion")
def type_manage(request, pk=None):
    types = PrevencionDocumentType.objects.all().order_by("-created_at")
    editing = None
    if pk is not None:
        editing = get_object_or_404(PrevencionDocumentType, pk=pk)

    if request.method == "POST":
        form = PrevencionDocumentTypeForm(request.POST, instance=editing)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Tipo de documento guardado.")
            return redirect("prevencion:type_manage")
    else:
        form = PrevencionDocumentTypeForm(instance=editing)

    can_edit_toggle = (
        request.user.es_admin_general
        or request.user.es_pm
        or request.user.es_prevencion
    )
    can_delete = request.user.es_admin_general

    return render(request, "prevencion/type_manage.html", {
        "types": types,
        "form": form,
        "editing": editing,
        "can_edit_toggle": can_edit_toggle,
        "can_delete": can_delete,
    })


@login_required
@rol_requerido("admin", "pm", "prevencion")
def type_toggle(request, pk: int):
    t = get_object_or_404(PrevencionDocumentType, pk=pk)
    t.is_active = not t.is_active
    t.save(update_fields=["is_active"])
    messages.success(request, f"✅ Tipo '{t.name}' actualizado.")
    return redirect("prevencion:type_manage")


@login_required
@rol_requerido("admin")
def type_delete(request, pk: int):
    t = get_object_or_404(PrevencionDocumentType, pk=pk)
    try:
        t.delete()
        messages.success(request, "✅ Tipo eliminado.")
    except Exception:
        messages.error(request, "No se puede eliminar este tipo porque está en uso.")
    return redirect("prevencion:type_manage")


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def notifications_edit(request):
    obj, _ = PrevencionNotificationSettings.objects.get_or_create(pk=1)

    if request.method == "POST":
        form = PrevencionNotificationSettingsForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ Notificaciones actualizadas.")
            return redirect("prevencion:dashboard")
    else:
        form = PrevencionNotificationSettingsForm(instance=obj)

    return render(request, "prevencion/notifications_form.html", {
        "form": form,
        "obj": obj,
    })


@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def document_download(request, pk: int):
    obj = get_object_or_404(PrevencionDocument, pk=pk)

    if not obj.file:
        raise Http404("Documento no disponible.")

    try:
        file_handle = obj.file.open("rb")
    except Exception:
        raise Http404("No se pudo abrir el documento.")

    filename = os.path.basename(obj.file.name or "") or f"documento_{obj.pk}"
    content_type, _ = mimetypes.guess_type(filename)
    content_type = content_type or "application/octet-stream"

    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )
    return response

@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def download_general_history_excel(request):
    today = timezone.localdate()

    wb = Workbook()
    ws_empresa = wb.active
    ws_empresa.title = "Documentos Empresa"

    ws_empresa.append([
        "Tipo documento",
        "Versión actual",
        "Alcance",
        "Aplica a todos",
        "Estado",
        "Fecha creación",
        "Fecha caducidad",
        "Días",
        "Notificar",
        "Trabajadores",
        "Archivo",
        "Subido por",
        "Fecha carga",
    ])

    docs_empresa = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(scope__in=["empresa", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    empresa_docs = []
    for d in docs_empresa:
        d.status_code = d.compute_status(today=today)
        d.status_label_ui = d.status_label(today=today)
        d.remaining_days_ui = d.remaining_days(today=today)
        empresa_docs.append(d)

    empresa_docs.sort(
        key=lambda x: (
            (x.doc_type.name or "").lower(),
            0 if getattr(x, "current", False) else 1,
            -(x.created_at.timestamp() if x.created_at else 0),
        )
    )

    for d in empresa_docs:
        ws_empresa.append([
            d.doc_type.name,
            "Sí" if d.current else "No",
            d.get_scope_display(),
            "Sí" if d.apply_to_all_workers else "No",
            d.status_label_ui,
            d.issue_date.strftime("%d-%m-%Y") if d.issue_date else "",
            d.expiry_date.strftime("%d-%m-%Y") if d.expiry_date else "",
            d.remaining_days_ui if d.remaining_days_ui is not None else "",
            "Sí" if d.notify_enabled else "No",
            d.workers_display() if d.scope in ["trabajador", "ambos"] and not d.apply_to_all_workers else (
                "Todos los trabajadores actuales y futuros" if d.apply_to_all_workers else ""
            ),
            d.file.url if d.file else "",
            d.created_by.get_full_name() if d.created_by and d.created_by.get_full_name() else (
                d.created_by.username if d.created_by else ""
            ),
            timezone.localtime(d.created_at).strftime("%d-%m-%Y %H:%M") if d.created_at else "",
        ])

    ws_worker = wb.create_sheet(title="Documentos Trabajador")

    ws_worker.append([
        "Trabajador",
        "Tipo documento",
        "Versión actual",
        "Aplica a todos",
        "Estado",
        "Fecha creación",
        "Fecha caducidad",
        "Días",
        "Notificar",
        "Archivo",
        "Subido por",
        "Fecha carga",
    ])

    active_workers = list(
        CustomUser.objects
        .filter(is_active=True)
        .order_by("first_name", "last_name", "username")
    )

    docs_worker = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(scope__in=["trabajador", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    rows_worker = []
    for d in docs_worker:
        d.status_code = d.compute_status(today=today)
        d.status_label_ui = d.status_label(today=today)
        d.remaining_days_ui = d.remaining_days(today=today)

        if d.apply_to_all_workers and d.scope == "ambos":
            workers_for_doc = active_workers
        else:
            workers_for_doc = list(d.workers.all())

        for w in workers_for_doc:
            rows_worker.append((
                (w.get_full_name() or w.username or "").lower(),
                (d.doc_type.name or "").lower(),
                0 if getattr(d, "current", False) else 1,
                -(d.created_at.timestamp() if d.created_at else 0),
                w,
                d,
            ))

    rows_worker.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    for _, __, ___, ____, w, d in rows_worker:
        ws_worker.append([
            w.get_full_name() or w.username,
            d.doc_type.name,
            "Sí" if d.current else "No",
            "Sí" if d.apply_to_all_workers else "No",
            d.status_label_ui,
            d.issue_date.strftime("%d-%m-%Y") if d.issue_date else "",
            d.expiry_date.strftime("%d-%m-%Y") if d.expiry_date else "",
            d.remaining_days_ui if d.remaining_days_ui is not None else "",
            "Sí" if d.notify_enabled else "No",
            d.file.url if d.file else "",
            d.created_by.get_full_name() if d.created_by and d.created_by.get_full_name() else (
                d.created_by.username if d.created_by else ""
            ),
            timezone.localtime(d.created_at).strftime("%d-%m-%Y %H:%M") if d.created_at else "",
        ])

    for ws in [ws_empresa, ws_worker]:
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                value = "" if cell.value is None else str(cell.value)
                if len(value) > max_len:
                    max_len = len(value)
            ws.column_dimensions[col_letter].width = min(max_len + 2, 45)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"prevencion_historial_general_{today.strftime('%Y%m%d')}.xlsx"

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def worker_document_zip(request, worker_id: int):
    import re
    import zipfile

    worker = get_object_or_404(CustomUser, pk=worker_id, is_active=True)
    mode = (request.GET.get("mode") or "current_only").strip().lower()
    today = timezone.localdate()

    if mode not in {"current_only", "current_and_no_expiry", "full_history"}:
        mode = "current_only"

    docs_qs = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(scope__in=["trabajador", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    docs_for_worker = []
    for d in docs_qs:
        include_doc = False

        if d.scope == "ambos" and d.apply_to_all_workers:
            include_doc = True
        elif d.workers.filter(pk=worker.pk).exists():
            include_doc = True

        if not include_doc:
            continue

        d.status_code = d.compute_status(today=today)
        d.status_label_ui = d.status_label(today=today)
        d.remaining_days_ui = d.remaining_days(today=today)

        docs_for_worker.append(d)

    docs_for_worker.sort(
        key=lambda x: (
            (x.doc_type.name or "").lower(),
            0 if getattr(x, "current", False) else 1,
            -(x.created_at.timestamp() if x.created_at else 0),
        )
    )

    filtered_docs = []
    for d in docs_for_worker:
        status_code = getattr(d, "status_code", d.compute_status(today=today))

        if mode == "full_history":
            filtered_docs.append(d)

        elif mode == "current_only":
            # Solo documentos actualmente utilizables:
            # Vigente + Próximo a vencer
            if status_code in {"vigente", "proximo"}:
                filtered_docs.append(d)

        elif mode == "current_and_no_expiry":
            # Vigente + Próximo a vencer + Sin vencimiento
            if status_code in {"vigente", "proximo", "sin_vencimiento"}:
                filtered_docs.append(d)

    output = BytesIO()

    worker_name = (worker.get_full_name() or worker.username or f"trabajador_{worker.id}").strip()
    worker_folder = re.sub(r'[\\/*?:"<>|]', "_", worker_name)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in filtered_docs:
            if not d.file:
                continue

            tipo_name = (d.doc_type.name or f"tipo_{d.doc_type_id}").strip()
            tipo_folder = re.sub(r'[\\/*?:"<>|]', "_", tipo_name)

            original_name = os.path.basename(d.file.name or "") or f"documento_{d.pk}"
            _, ext = os.path.splitext(original_name)

            if mode == "full_history":
                version_name = (
                    f"{d.created_at.strftime('%Y%m%d_%H%M') if d.created_at else 'sin_fecha'}"
                    f"__doc_{d.pk}{ext}"
                )
            else:
                version_name = original_name

            zip_path = f"{worker_folder}/{tipo_folder}/{version_name}"

            try:
                with d.file.open("rb") as fh:
                    zf.writestr(zip_path, fh.read())
            except Exception:
                continue

    output.seek(0)

    safe_worker = re.sub(r"[^\w\-. ]", "_", worker_folder).replace(" ", "_")
    mode_suffix = {
        "current_only": "solo_vigentes",
        "current_and_no_expiry": "vigentes_y_sin_caducidad",
        "full_history": "historial_completo",
    }[mode]

    filename = f"documentos_{safe_worker}_{mode_suffix}.zip"

    response = HttpResponse(output.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@login_required
@rol_requerido("admin", "pm", "supervisor", "prevencion")
def company_document_zip(request, type_id: int):
    import re
    import zipfile

    doc_type = get_object_or_404(PrevencionDocumentType, pk=type_id)
    mode = (request.GET.get("mode") or "current_only").strip().lower()
    today = timezone.localdate()

    if mode not in {"current_only", "current_and_no_expiry", "full_history"}:
        mode = "current_only"

    docs_qs = (
        PrevencionDocument.objects
        .select_related("doc_type", "created_by")
        .prefetch_related("workers")
        .filter(doc_type_id=type_id, scope__in=["empresa", "ambos"])
        .order_by("doc_type__name", "-created_at", "-id")
    )

    docs = []
    for d in docs_qs:
        d.status_code = d.compute_status(today=today)
        d.status_label_ui = d.status_label(today=today)
        d.remaining_days_ui = d.remaining_days(today=today)
        docs.append(d)

    docs.sort(
        key=lambda x: (
            (x.doc_type.name or "").lower(),
            0 if getattr(x, "current", False) else 1,
            -(x.created_at.timestamp() if x.created_at else 0),
        )
    )

    filtered_docs = []
    for d in docs:
        status_code = getattr(d, "status_code", d.compute_status(today=today))

        if mode == "full_history":
            filtered_docs.append(d)

        elif mode == "current_only":
            # Solo documentos actualmente utilizables:
            # Vigente + Próximo a vencer
            if status_code in {"vigente", "proximo"}:
                filtered_docs.append(d)

        elif mode == "current_and_no_expiry":
            # Vigente + Próximo a vencer + Sin vencimiento
            if status_code in {"vigente", "proximo", "sin_vencimiento"}:
                filtered_docs.append(d)

    output = BytesIO()

    type_name = (doc_type.name or f"tipo_{doc_type.id}").strip()
    type_folder = re.sub(r'[\\/*?:"<>|]', "_", type_name)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in filtered_docs:
            if not d.file:
                continue

            original_name = os.path.basename(d.file.name or "") or f"documento_{d.pk}"
            _, ext = os.path.splitext(original_name)

            if mode == "full_history":
                file_name = (
                    f"{d.created_at.strftime('%Y%m%d_%H%M') if d.created_at else 'sin_fecha'}"
                    f"__doc_{d.pk}{ext}"
                )
            else:
                file_name = original_name

            zip_path = f"{type_folder}/{file_name}"

            try:
                with d.file.open("rb") as fh:
                    zf.writestr(zip_path, fh.read())
            except Exception:
                continue

    output.seek(0)

    safe_name = re.sub(r"[^\w\-. ]", "_", type_folder).replace(" ", "_")
    mode_suffix = {
        "current_only": "solo_vigentes",
        "current_and_no_expiry": "vigentes_y_sin_caducidad",
        "full_history": "historial_completo",
    }[mode]

    filename = f"documentos_empresa_{safe_name}_{mode_suffix}.zip"

    response = HttpResponse(output.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
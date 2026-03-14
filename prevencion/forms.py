from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from usuarios.models import CustomUser

from .models import (PrevencionDocument, PrevencionDocumentType,
                     PrevencionNotificationSettings)


def _workers_queryset():
    return CustomUser.objects.filter(is_active=True).order_by(
        "first_name", "last_name", "username"
    )


class PrevencionDocumentTypeForm(forms.ModelForm):
    class Meta:
        model = PrevencionDocumentType
        fields = ["name", "scope", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "scope": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
        }


class PrevencionDocumentCreateForm(forms.ModelForm):
    workers = forms.ModelMultipleChoiceField(
        queryset=_workers_queryset(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Trabajadores (si aplica)",
    )

    class Meta:
        model = PrevencionDocument
        fields = [
            "doc_type",
            "title",
            "file",
            "no_requiere_vencimiento",
            "issue_date",
            "expiry_date",
            "apply_to_all_workers",
            "workers",
            "notify_enabled",
        ]
        widgets = {
            "doc_type": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "title": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "file": forms.ClearableFileInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
            "expiry_date": forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
        }

    def clean(self):
        cleaned = super().clean()
        doc_type: PrevencionDocumentType | None = cleaned.get("doc_type")
        no_req = bool(cleaned.get("no_requiere_vencimiento"))
        issue = cleaned.get("issue_date")
        expiry = cleaned.get("expiry_date")
        workers = cleaned.get("workers")
        apply_all = bool(cleaned.get("apply_to_all_workers"))

        if not doc_type:
            return cleaned

        scope = doc_type.scope

        if scope == "empresa":
            cleaned["apply_to_all_workers"] = False
            cleaned["workers"] = self.fields["workers"].queryset.none()

        elif scope == "trabajador":
            cleaned["apply_to_all_workers"] = False
            if not workers or workers.count() != 1:
                raise ValidationError(
                    "Para este tipo de documento debes seleccionar exactamente un trabajador."
                )

        elif scope == "ambos":
            if apply_all:
                cleaned["workers"] = self.fields["workers"].queryset.none()
            else:
                if not workers or workers.count() == 0:
                    raise ValidationError(
                        "Debes seleccionar al menos un trabajador o marcar que el documento aplica a todos."
                    )

        if no_req:
            cleaned["expiry_date"] = None
            return cleaned

        if not expiry:
            raise ValidationError(
                "Debes ingresar la fecha de caducidad o marcar 'No requiere vencimiento'."
            )

        if issue and expiry and expiry < issue:
            raise ValidationError(
                "La fecha de caducidad no puede ser menor a la fecha de creación."
            )

        return cleaned


class PrevencionDocumentEditForm(forms.ModelForm):
    workers = forms.ModelMultipleChoiceField(
        queryset=_workers_queryset(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Trabajadores (si aplica)",
    )

    class Meta:
        model = PrevencionDocument
        fields = [
            "title",
            "no_requiere_vencimiento",
            "issue_date",
            "expiry_date",
            "apply_to_all_workers",
            "workers",
            "notify_enabled",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "issue_date": forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
            "expiry_date": forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields["workers"].initial = self.instance.workers.all()
            self.fields["apply_to_all_workers"].initial = self.instance.apply_to_all_workers

    def clean(self):
        cleaned = super().clean()
        inst: PrevencionDocument = self.instance

        no_req = bool(cleaned.get("no_requiere_vencimiento"))
        issue = cleaned.get("issue_date")
        expiry = cleaned.get("expiry_date")
        workers = cleaned.get("workers")
        apply_all = bool(cleaned.get("apply_to_all_workers"))

        scope = inst.scope

        if scope == "empresa":
            cleaned["apply_to_all_workers"] = False
            cleaned["workers"] = self.fields["workers"].queryset.none()

        elif scope == "trabajador":
            cleaned["apply_to_all_workers"] = False
            if not workers or workers.count() != 1:
                raise ValidationError(
                    "Para este documento debes seleccionar exactamente un trabajador."
                )

        elif scope == "ambos":
            if apply_all:
                cleaned["workers"] = self.fields["workers"].queryset.none()
            else:
                if not workers or workers.count() == 0:
                    raise ValidationError(
                        "Debes seleccionar al menos un trabajador o marcar que el documento aplica a todos."
                    )

        if no_req:
            cleaned["expiry_date"] = None
            return cleaned

        if not expiry:
            raise ValidationError(
                "Debes ingresar la fecha de caducidad o marcar 'No requiere vencimiento'."
            )

        if issue and expiry and expiry < issue:
            raise ValidationError(
                "La fecha de caducidad no puede ser menor a la fecha de creación."
            )

        return cleaned


class PrevencionDocumentReplaceForm(forms.Form):
    file = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
    )
    no_requiere_vencimiento = forms.BooleanField(required=False)
    issue_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
    )
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "w-full border rounded-xl px-3 py-2"}),
    )
    apply_to_all_workers = forms.BooleanField(required=False)
    workers = forms.ModelMultipleChoiceField(
        queryset=_workers_queryset(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Trabajadores (si aplica)",
    )

    def __init__(self, *args, **kwargs):
        self.scope = kwargs.pop("scope", "empresa")
        current_workers = kwargs.pop("current_workers", None)
        current_apply_all = kwargs.pop("current_apply_all", False)
        super().__init__(*args, **kwargs)

        if current_workers is not None:
            self.fields["workers"].initial = current_workers
        self.fields["apply_to_all_workers"].initial = current_apply_all

    def clean(self):
        cleaned = super().clean()

        no_req = bool(cleaned.get("no_requiere_vencimiento"))
        issue = cleaned.get("issue_date")
        expiry = cleaned.get("expiry_date")
        workers = cleaned.get("workers")
        apply_all = bool(cleaned.get("apply_to_all_workers"))

        if self.scope == "empresa":
            cleaned["apply_to_all_workers"] = False
            cleaned["workers"] = self.fields["workers"].queryset.none()

        elif self.scope == "trabajador":
            cleaned["apply_to_all_workers"] = False
            if not workers or workers.count() != 1:
                raise ValidationError(
                    "Para este documento debes seleccionar exactamente un trabajador."
                )

        elif self.scope == "ambos":
            if apply_all:
                cleaned["workers"] = self.fields["workers"].queryset.none()
            else:
                if not workers or workers.count() == 0:
                    raise ValidationError(
                        "Debes seleccionar al menos un trabajador o marcar que el documento aplica a todos."
                    )

        if no_req:
            cleaned["expiry_date"] = None
            return cleaned

        if not expiry:
            raise ValidationError(
                "Debes ingresar la fecha de caducidad o marcar 'No requiere vencimiento'."
            )

        if issue and expiry and expiry < issue:
            raise ValidationError(
                "La fecha de caducidad no puede ser menor a la fecha de creación."
            )

        return cleaned


class PrevencionNotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = PrevencionNotificationSettings
        fields = ["enabled", "include_worker", "extra_to", "extra_cc"]
        widgets = {
            "extra_to": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "extra_cc": forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
        }
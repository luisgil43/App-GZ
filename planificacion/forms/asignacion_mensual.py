from django import forms


class ImportarAsignacionMensualForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo de asignación mensual",
        help_text=(
            "Puedes subir la planilla completa entregada por el cliente. "
            "El sistema detectará automáticamente la columna ID o ID Claro."
        ),
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.xls",
                "class": (
                    "block w-full text-sm text-slate-700 "
                    "border border-slate-300 rounded-xl "
                    "bg-white px-3 py-2"
                ),
            }
        ),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]

        nombre = (archivo.name or "").lower()

        if not nombre.endswith((".xlsx", ".xls")):
            raise forms.ValidationError("Debes subir un archivo Excel (.xlsx o .xls).")

        return archivo

from django import forms


class ImportarContactosForm(forms.Form):
    archivo = forms.FileField(
        label="Archivo Excel",
        help_text=("Sube la base de contactos enviada por el cliente."),
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

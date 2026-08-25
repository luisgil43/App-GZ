from django import forms

from planificacion.models import ContactoSitio


class EditarContactoInlineForm(forms.ModelForm):
    """
    Edición manual inline de la información de contacto.

    IMPORTANTE:
    - No modifica SitioMovil.
    - No modifica el sitio vinculado.
    - No modifica el ID de origen.
    - Permite modificar solamente información propia
      de ContactoSitio.
    """

    class Meta:
        model = ContactoSitio

        fields = [
            "propietario",
            "telefono",
            "correo",
            "responsable",
            "observaciones",
            "accion",
        ]

        widgets = {
            "propietario": forms.Textarea(
                attrs={
                    "rows": 2,
                }
            ),
            "telefono": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "correo": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "responsable": forms.Textarea(
                attrs={
                    "rows": 2,
                }
            ),
            "observaciones": forms.Textarea(
                attrs={
                    "rows": 5,
                }
            ),
            "accion": forms.Textarea(
                attrs={
                    "rows": 5,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Ninguno de estos datos es obligatorio.
        # La información histórica puede venir incompleta.
        for field in self.fields.values():
            field.required = False

    def clean_propietario(self):
        return self._limpiar_texto(self.cleaned_data.get("propietario"))

    def clean_responsable(self):
        return self._limpiar_texto(self.cleaned_data.get("responsable"))

    def clean_observaciones(self):
        return self._limpiar_texto(self.cleaned_data.get("observaciones"))

    def clean_accion(self):
        return self._limpiar_texto(self.cleaned_data.get("accion"))

    def clean_telefono(self):
        """
        Puede contener varios teléfonos.

        Aceptamos:
        934070237
        956438846

        También:
        934070237; 956438846

        Internamente quedan uno por línea.
        """

        return self._normalizar_multivalor(self.cleaned_data.get("telefono"))

    def clean_correo(self):
        """
        Puede contener varios correos.

        Cada correo queda almacenado en una línea.
        """

        return self._normalizar_multivalor(self.cleaned_data.get("correo"))

    @staticmethod
    def _limpiar_texto(value):
        value = str(value or "").strip()

        return value

    @staticmethod
    def _normalizar_multivalor(value):
        value = str(value or "").strip()

        if not value:
            return ""

        # Permitimos que el usuario separe datos
        # con salto de línea, ; o |
        value = value.replace(";", "\n").replace("|", "\n")

        resultado = []

        vistos = set()

        for linea in value.splitlines():
            linea = linea.strip()

            if not linea:
                continue

            # Evitar duplicados exactos.
            clave = linea.casefold()

            if clave in vistos:
                continue

            vistos.add(clave)

            resultado.append(linea)

        return "\n".join(resultado)

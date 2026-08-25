import io

import pandas as pd
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from usuarios.decoradores import rol_requerido


@login_required
@rol_requerido(
    "admin",
    "pm",
    "supervisor",
)
def descargar_formato_contactos(request):
    """
    Descarga el formato oficial de importación para la
    Base de Contactos de Planificación.
    """

    columnas = [
        "REGIÓN",
        "ID",
        "NOMBRE SITIO",
        "Propietario",
        "Teléfono",
        "Correo",
        "Fecha",
        "Responsable",
        "Observaciones",
        "ACCIÓN",
    ]

    ejemplo = [
        "08",
        "08_502",
        "San Pedro - Laguna Grande",
        "Yanhira Angélica Hermosilla Toro",
        "961555445",
        "yhermosillapdr@gmail.com",
        "06-05-2024",
        "CI",
        "Opción 1 Propietaria",
        "Avisar con 1 día de anticipación y coordinar horario de ingreso.",
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
            sheet_name="Contactos",
        )

        workbook = writer.book
        worksheet = writer.sheets["Contactos"]

        # Congelar encabezado
        worksheet.freeze_panes = "A2"

        # Autofiltro
        worksheet.auto_filter.ref = worksheet.dimensions

        # Altura encabezado
        worksheet.row_dimensions[1].height = 24

        # Anchos
        anchos = {
            "A": 12,  # Región
            "B": 16,  # ID
            "C": 32,  # Nombre
            "D": 34,  # Propietario
            "E": 20,  # Teléfono
            "F": 38,  # Correo
            "G": 16,  # Fecha
            "H": 20,  # Responsable
            "I": 55,  # Observaciones
            "J": 65,  # Acción
        }

        for letra, ancho in anchos.items():
            worksheet.column_dimensions[letra].width = ancho

        # Formato encabezados
        for cell in worksheet[1]:
            try:
                cell.font = cell.font.copy(
                    bold=True,
                    color="FFFFFF",
                )

                cell.fill = cell.fill.copy(
                    fill_type="solid",
                    fgColor="0F766E",
                )

                cell.alignment = cell.alignment.copy(
                    horizontal="center",
                    vertical="center",
                )

            except Exception:
                pass

        # Texto multilínea
        for row in worksheet.iter_rows(
            min_row=2,
        ):
            for cell in row:
                try:
                    cell.alignment = cell.alignment.copy(
                        vertical="top",
                        wrap_text=True,
                    )
                except Exception:
                    pass

    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-" "officedocument.spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        'attachment; filename="' 'formato_base_contactos_planificacion.xlsx"'
    )

    return response

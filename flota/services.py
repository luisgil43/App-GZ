# flota/services.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.utils import timezone

from .models import Vehicle, VehicleService, VehicleServiceType


def create_vehicle_service_from_source(
    *,
    vehicle: Vehicle,
    service_type_obj: VehicleServiceType | None,
    service_date,
    service_time=None,
    kilometraje_declarado=None,
    monto=Decimal("0.00"),
    notes="",
    title=None,
    source_label="manual",
):
    """
    Crea un VehicleService usando la misma lógica base del módulo flota.
    Devuelve: (service, messages_list)
    """

    # retrocompatibilidad con campo legacy service_type (choices)
    # si no coincide, cae en 'otro'
    legacy_map = {
        "Combustible": "combustible",
        "Cambio de aceite": "aceite",
        "Cambio de neumáticos": "neumaticos",
        "Revisión técnica": "revision_tecnica",
        "Permiso de circulación": "permiso_circulacion",
    }

    legacy_service_type = "otro"
    if service_type_obj and service_type_obj.name:
        legacy_service_type = legacy_map.get(service_type_obj.name.strip(), "otro")

    svc = VehicleService.objects.create(
        vehicle=vehicle,
        service_type=legacy_service_type,   # campo legacy obligatorio
        service_type_obj=service_type_obj,  # nuevo tipo configurable
        title=title or (service_type_obj.name if service_type_obj else "Servicio"),
        service_date=service_date,
        service_time=service_time,
        kilometraje_declarado=kilometraje_declarado,
        monto=monto or Decimal("0.00"),
        notes=notes or "",
    )

    # mensajes tipo "manual" para mostrar en UI
    messages_out = []
    type_name = service_type_obj.name if service_type_obj else svc.get_service_type_display()

    messages_out.append(
        f"✅ Servicio #{svc.service_code} ({type_name}) registrado para {vehicle.patente}."
    )

    if svc.kilometraje_declarado is not None:
        messages_out.append(f"📍 Kilometraje registrado: {svc.kilometraje_declarado:,} km".replace(",", "."))

    if svc.next_due_km:
        messages_out.append(f"🔔 Próximo vencimiento por KM: {svc.next_due_km:,} km".replace(",", "."))

    if svc.next_due_date:
        messages_out.append(f"📅 Próximo vencimiento por fecha: {svc.next_due_date.strftime('%d-%m-%Y')}")

    messages_out.append(f"🧾 Origen: {source_label}")

    return svc, messages_out
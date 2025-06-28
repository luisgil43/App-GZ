from django.core.management.base import BaseCommand
from django.utils import timezone
from rrhh.models import SolicitudAdelanto


class Command(BaseCommand):
    help = "Desactiva las solicitudes aprobadas del mes anterior para que los trabajadores puedan volver a solicitar adelantos este mes."

    def handle(self, *args, **kwargs):
        hoy = timezone.now()
        mes_actual = hoy.month
        año_actual = hoy.year

        # Marcar como "ya usado este mes" las solicitudes del mes anterior
        solicitudes = SolicitudAdelanto.objects.filter(
            estado='aprobada',
            fecha_solicitud__month=mes_actual,
            fecha_solicitud__year=año_actual,
        )

        total = solicitudes.count()
        self.stdout.write(
            f"✔ {total} solicitudes están activas para el mes actual. No se requiere reinicio.")

        # En realidad no hace nada porque el sistema permite volver a solicitar automáticamente
        # Solo con lógica en la vista limitamos si ya tienen una aprobada ese mes

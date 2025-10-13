from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from rrhh.models import DiasVacacionesTomadosManualmente

User = get_user_model()


class Command(BaseCommand):
    help = 'Registrar manualmente días de vacaciones ya tomados usando el RUT del usuario (sin guion ni puntos)'

    def add_arguments(self, parser):
        parser.add_argument(
            'rut', type=str, help='RUT del usuario (sin puntos ni guion, ej: 123456789)')
        parser.add_argument('dias', type=float,
                            help='Cantidad de días tomados manualmente')

    def handle(self, *args, **options):
        rut = options['rut']
        try:
            usuario = User.objects.get(identidad__replace='.', '').identidad.replace('-', '') == rut
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(
                f'❌ Usuario con RUT {rut} no encontrado.'))
            return

        usuario = User.objects.annotate(
            rut_sin_format=models.functions.Replace(
                models.functions.Replace(
                    'identidad', models.Value('.'), models.Value('')),
                models.Value('-'), models.Value('')
            )
        ).filter(rut_sin_format=rut).first()

        if not usuario:
            self.stdout.write(self.style.ERROR(
                f'❌ Usuario con RUT {rut} no encontrado.'))
            return

        registro, creado = DiasVacacionesTomadosManualmente.objects.get_or_create(
            usuario=usuario)
        registro.cantidad_dias = options['dias']
        registro.save()

        if creado:
            self.stdout.write(self.style.SUCCESS(
                f'✅ Registro creado para {usuario.get_full_name()} con {options["dias"]} días.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✅ Registro actualizado para {usuario.get_full_name()} con {options["dias"]} días.'))

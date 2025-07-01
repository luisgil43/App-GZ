from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.core.exceptions import ImproperlyConfigured
from datetime import timedelta, date
from django.db.models import Sum
# from decimal import Decimal
# from rrhh.models import Feriado

# ✅ Firma en Cloudinary


def ruta_firma_usuario(instance, filename):
    # Asegúrate de que `identidad` existe en tu modelo de usuario
    identidad = instance.identidad
    return f"media/firmas/{identidad}/{identidad}_firma.png"


class LazyCloudinaryStorage(LazyObject):
    def _setup(self):
        storage_path = getattr(settings, 'DEFAULT_FILE_STORAGE', '')
        if not storage_path:
            raise ImproperlyConfigured(
                "DEFAULT_FILE_STORAGE no está definido en settings.")
        self._wrapped = import_string(storage_path)()


cloudinary_storage = LazyCloudinaryStorage()


class Rol(models.Model):
    nombre = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.nombre


class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)
    roles = models.ManyToManyField("usuarios.Rol", blank=True)

    firma_digital = models.ImageField(
        upload_to=ruta_firma_usuario,
        storage=cloudinary_storage,
        blank=True,
        null=True
    )

    dias_vacaciones_consumidos = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text="Días de vacaciones que ya ha consumido fuera del sistema"
    )

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def tiene_rol(self, nombre_rol):
        return self.roles.filter(nombre=nombre_rol).exists()

    @property
    def es_usuario(self): return self.tiene_rol('usuario') or self.is_superuser

    @property
    def es_supervisor(self): return self.tiene_rol(
        'supervisor') or self.is_superuser

    @property
    def es_pm(self): return self.tiene_rol('pm') or self.is_superuser
    @property
    def es_rrhh(self): return self.tiene_rol('rrhh') or self.is_superuser

    @property
    def es_prevencion(self): return self.tiene_rol(
        'prevencion') or self.is_superuser

    @property
    def es_logistica(self): return self.tiene_rol(
        'logistica') or self.is_superuser

    @property
    def es_flota(self): return self.tiene_rol('flota') or self.is_superuser

    @property
    def es_subcontrato(self): return self.tiene_rol(
        'subcontrato') or self.is_superuser

    @property
    def es_facturacion(self): return self.tiene_rol(
        'facturacion') or self.is_superuser

    @property
    def es_admin_general(self): return self.tiene_rol(
        'admin') or self.is_superuser

    @property
    def rol(self):
        primer_rol = self.roles.first()
        return primer_rol.nombre if primer_rol else None

    def obtener_dias_vacaciones_disponibles(self):
        from rrhh.models import SolicitudVacaciones, ContratoTrabajo

        contrato = ContratoTrabajo.objects.filter(
            tecnico=self).order_by('fecha_inicio').first()

        if not contrato or not contrato.fecha_inicio:
            return 0

        dias_trabajados = (date.today() - contrato.fecha_inicio).days
        dias_generados = dias_trabajados * 0.04166

        dias_consumidos_manualmente = float(
            self.dias_vacaciones_consumidos or 0)

        dias_aprobados = SolicitudVacaciones.objects.filter(
            usuario=self,
            estatus='aprobada'
        ).aggregate(total=Sum('dias_solicitados'))['total'] or 0

        total_disponible = dias_generados - \
            dias_consumidos_manualmente - float(dias_aprobados)
        return round(total_disponible, 2)

    def __str__(self):
        nombre = self.get_full_name() or self.username
        return f"{self.identidad or 'Sin RUT'} - {nombre}"


"""
    def calcular_dias_habiles(self, inicio, fin):

        if not inicio or not fin:
            return 0

        feriados = set(Feriado.objects.values_list('fecha', flat=True))
        dias_habiles = 0
        dia_actual = inicio

        while dia_actual <= fin:
            if dia_actual.weekday() < 5 and dia_actual not in feriados:  # 0 = lunes, 6 = domingo
                dias_habiles += 1
            dia_actual += timedelta(days=1)

        return dias_habiles

    def calcular_dias_habiles(self, inicio, fin):
        from rrhh.models import Feriado
        feriados = set(Feriado.objects.values_list('fecha', flat=True))

        dias = 0
        actual = inicio
        while actual <= fin:
            if actual.weekday() < 5 and actual not in feriados:
                dias += 1
            actual += timedelta(days=1)
        return dias"""

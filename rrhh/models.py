from django import forms
from django.db import models
from django.contrib.auth import get_user_model
from liquidaciones.models import cloudinary_storage
from django.conf import settings
from datetime import date
from django.utils import timezone
from datetime import timedelta
from usuarios.models import CustomUser
from django.core.files.storage import default_storage


def ruta_contrato_trabajo(instance, filename):
    rut = instance.tecnico.identidad or f"usuario_{instance.tecnico.id}"
    rut_limpio = rut.replace('.', '').replace('-', '')
    return f"media/Contratos_trabajo/{rut_limpio}/{filename}"


class ContratoTrabajo(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    fecha_inicio = models.DateField()
    fecha_termino = models.DateField(null=True, blank=True)
    archivo = models.FileField(
        upload_to=ruta_contrato_trabajo,
        storage=cloudinary_storage,
        verbose_name="Archivo del contrato"
    )

    def __str__(self):
        return f"Contrato de {self.tecnico.get_full_name()}"

    def save(self, *args, **kwargs):
        try:
            old = ContratoTrabajo.objects.get(pk=self.pk)
        except ContratoTrabajo.DoesNotExist:
            old = None

        if (
            old and
            old.archivo and self.archivo and
            old.archivo.name != self.archivo.name
        ):
            if old.archivo.storage.exists(old.archivo.name):
                old.archivo.delete(save=False)

        super().save(*args, **kwargs)


def ruta_ficha_ingreso(instance, filename):
    # Evita duplicar la ruta si ya viene con fichas_de_ingreso
    if filename.startswith("fichas_de_ingreso/"):
        return filename

    identidad = instance.usuario.identidad if instance.usuario and instance.usuario.identidad else instance.rut or f"usuario_desconocido_{instance.id or 'nuevo'}"
    identidad_limpia = identidad.replace('.', '').replace('-', '')
    return f"fichas_de_ingreso/{identidad_limpia}/{filename}"


class FichaIngreso(models.Model):
    # Relacionales
    usuario = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, null=True, blank=True, related_name="fichas")
    # usuario = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='fichaingreso')
    creado_por = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="fichas_creadas")
    pm = models.ForeignKey(CustomUser, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name="fichas_pm")

    # Datos personales
    nombres = models.CharField(max_length=100, null=True, blank=True)
    apellidos = models.CharField(max_length=100, null=True, blank=True)
    rut = models.CharField(max_length=15, null=True, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    edad = models.PositiveIntegerField(null=True, blank=True)
    sexo = models.CharField(max_length=20, null=True, blank=True)
    estado_civil = models.CharField(max_length=50, null=True, blank=True)
    nacionalidad = models.CharField(max_length=50, null=True, blank=True)

    # Contacto
    telefono = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    comuna = models.CharField(max_length=100, null=True, blank=True)
    region = models.CharField(max_length=100, null=True, blank=True)

    # Estudios y familia
    hijos = models.IntegerField(null=True, blank=True)
    nivel_estudios = models.CharField(max_length=100, null=True, blank=True)
    profesion_u_oficio = models.CharField(
        max_length=100, null=True, blank=True)

    # Contacto de emergencia
    nombre_contacto_emergencia = models.CharField(
        max_length=100, null=True, blank=True)
    parentesco_emergencia = models.CharField(
        max_length=50, null=True, blank=True)
    telefono_emergencia = models.CharField(
        max_length=20, null=True, blank=True)
    direccion_emergencia = models.CharField(
        max_length=255, null=True, blank=True)

    # Salud y previsión
    afp = models.CharField(max_length=100, null=True, blank=True)
    salud = models.CharField(max_length=100, null=True, blank=True)

    # Datos bancarios
    banco = models.CharField(max_length=100, null=True, blank=True)
    tipo_cuenta = models.CharField(max_length=50, null=True, blank=True)
    numero_cuenta = models.CharField(max_length=50, null=True, blank=True)
    banco_2 = models.CharField(max_length=100, null=True, blank=True)
    tipo_cuenta_2 = models.CharField(max_length=50, null=True, blank=True)
    numero_cuenta_2 = models.CharField(max_length=50, null=True, blank=True)

    # Información laboral
    cargo = models.CharField(max_length=100, null=True, blank=True)
    jefe_directo = models.CharField(max_length=100, null=True, blank=True)
    proyecto = models.CharField(max_length=100, null=True, blank=True)
    fecha_inicio = models.DateField(null=True, blank=True)
    tipo_contrato = models.CharField(max_length=50, null=True, blank=True)
    jornada = models.CharField(max_length=100, null=True, blank=True)
    horario_trabajo = models.CharField(max_length=100, null=True, blank=True)
    sueldo_base = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    bono = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    colacion = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    movilizacion = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)

    # Tallas
    talla_polera = models.CharField(max_length=10, null=True, blank=True)
    talla_pantalon = models.CharField(max_length=10, null=True, blank=True)
    talla_zapato = models.CharField(max_length=10, null=True, blank=True)

    # Estado
    ESTADOS_FICHA = [
        ('pendiente_pm', 'Pendiente revisión del PM'),
        ('rechazada_pm', 'Rechazada por el PM'),
        ('pendiente_usuario', 'Pendiente aprobación del trabajador'),
        ('rechazada_usuario', 'Rechazada por el trabajador'),
        ('aprobada', 'Aprobada con firmas'),
    ]
    estado = models.CharField(
        max_length=30, choices=ESTADOS_FICHA, default='pendiente_pm')
    motivo_rechazo_pm = models.TextField(null=True, blank=True)
    motivo_rechazo_usuario = models.TextField(null=True, blank=True)

    # Firmas
    firma_trabajador = models.ImageField(
        upload_to='firmas/', null=True, blank=True)
    firma_pm = models.ImageField(upload_to='firmas/', null=True, blank=True)
    firma_rrhh = models.ImageField(upload_to='firmas/', null=True, blank=True)

    # PDF generado
    archivo = models.FileField(
        upload_to=ruta_ficha_ingreso,
        storage=cloudinary_storage,
        null=True,
        blank=True,
        verbose_name="Comprobante firmado"
    )

    def __str__(self):
        return f"Ficha {self.rut} - {self.nombres} {self.apellidos}"


class Feriado(models.Model):
    fecha = models.DateField(unique=True)
    nombre = models.CharField(max_length=100)  # 🔵 agrega esta línea

    def __str__(self):
        return f"{self.nombre} ({self.fecha})"


class DiasVacacionesTomadosManualmente(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vacaciones_manuales'
    )
    cantidad_dias = models.FloatField(default=0)

    def __str__(self):
        return f"{self.usuario.get_full_name()} - {self.cantidad_dias} días"


def ruta_solicitud_vacaciones(instance, filename):
    identidad = instance.usuario.identidad or f"usuario_{instance.usuario.id}"
    identidad_limpia = identidad.replace('.', '').replace('-', '')
    return f"media/solicitudes de vacaciones/{identidad_limpia}/{filename}"


class SolicitudVacaciones(models.Model):
    ESTADOS = [
        ('pendiente_supervisor', 'Pendiente de Supervisor'),
        ('rechazada_supervisor', 'Rechazada por Supervisor'),
        ('pendiente_pm', 'Pendiente de PM'),
        ('rechazada_pm', 'Rechazada por PM'),
        ('pendiente_rrhh', 'Pendiente de RRHH'),
        ('rechazada_rrhh', 'Rechazada por RRHH'),
        ('rechazada_admin', 'Rechazada por Admin'),
        ('aprobada', 'Aprobada'),
    ]

    TIPO_CHOICES = [
        ('total', 'Total'),
        ('parcial', 'Parcial'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='solicitudes_vacaciones'
    )
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    dias_solicitados = models.DecimalField(max_digits=5, decimal_places=2)

    tipo_solicitud = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        default='total'
    )

    estatus = models.CharField(
        max_length=30,
        choices=ESTADOS,
        default='pendiente_supervisor'
    )
    observacion = models.TextField(blank=True, null=True)
    fecha_solicitud = models.DateField(default=date.today)

    aprobado_por_supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        related_name='vacaciones_aprobadas_supervisor',
        on_delete=models.SET_NULL
    )
    aprobado_por_pm = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        related_name='vacaciones_aprobadas_pm',
        on_delete=models.SET_NULL
    )
    aprobado_por_rrhh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        related_name='vacaciones_aprobadas_rrhh',
        on_delete=models.SET_NULL
    )

    archivo_pdf = models.FileField(
        upload_to=ruta_solicitud_vacaciones,
        storage=cloudinary_storage,
        null=True,
        blank=True,
        verbose_name="Comprobante firmado"
    )

    def __str__(self):
        return f"{self.usuario.get_full_name()} ({self.fecha_inicio} - {self.fecha_fin})"

    def get_estado_actual_display(self):
        estado_map = {
            'pendiente_supervisor': '🟡 Pendiente de Supervisor',
            'rechazada_supervisor': '🔴 Rechazada por Supervisor',
            'pendiente_pm': '🟡 Pendiente de PM',
            'rechazada_pm': '🔴 Rechazada por PM',
            'pendiente_rrhh': '🟡 Pendiente de RRHH',
            'rechazada_rrhh': '🔴 Rechazada por RRHH',
            'rechazada_admin': '🔴 Rechazada por Admin',
            'aprobada': '🟢 Aprobada ✅',
        }
        return estado_map.get(self.estatus, self.estatus)


class TipoDocumento(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    obligatorio = models.BooleanField(default=True)
    vigencia_meses = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.nombre


def ruta_documento_trabajador(instance, filename):
    identidad = instance.trabajador.identidad or f"usuario_{instance.trabajador.id}"
    identidad_limpia = identidad.replace('.', '').replace('-', '')
    return f"media/Documentos de los trabajadores/{identidad_limpia}/{filename}"


class DocumentoTrabajador(models.Model):
    trabajador = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    tipo_documento = models.ForeignKey(TipoDocumento, on_delete=models.CASCADE)
    fecha_emision = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    archivo = models.FileField(
        upload_to=ruta_documento_trabajador,
        storage=cloudinary_storage,
        verbose_name="Archivo del documento"
    )

    subido_en = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.trabajador.get_full_name()} - {self.tipo_documento.nombre}"

    def save(self, *args, **kwargs):
        try:
            old = DocumentoTrabajador.objects.get(pk=self.pk)
        except DocumentoTrabajador.DoesNotExist:
            old = None

        if old and old.archivo and self.archivo and old.archivo.name != self.archivo.name:
            if old.archivo.storage.exists(old.archivo.name):
                old.archivo.delete(save=False)

        super().save(*args, **kwargs)

    def estado(self):
        if not self.fecha_vencimiento:
            return 'Faltante'

        hoy = date.today()
        dias_restantes = (self.fecha_vencimiento - hoy).days

        if dias_restantes < 0:
            return 'Vencido'
        elif dias_restantes <= 30:
            return 'Por vencer'
        else:
            return 'Vigente'


class CronogramaPago(models.Model):
    enero_texto = models.CharField(max_length=100, blank=True, null=True)
    enero_fecha = models.DateField(blank=True, null=True)

    febrero_texto = models.CharField(max_length=100, blank=True, null=True)
    febrero_fecha = models.DateField(blank=True, null=True)

    marzo_texto = models.CharField(max_length=100, blank=True, null=True)
    marzo_fecha = models.DateField(blank=True, null=True)

    abril_texto = models.CharField(max_length=100, blank=True, null=True)
    abril_fecha = models.DateField(blank=True, null=True)

    mayo_texto = models.CharField(max_length=100, blank=True, null=True)
    mayo_fecha = models.DateField(blank=True, null=True)

    junio_texto = models.CharField(max_length=100, blank=True, null=True)
    junio_fecha = models.DateField(blank=True, null=True)

    julio_texto = models.CharField(max_length=100, blank=True, null=True)
    julio_fecha = models.DateField(blank=True, null=True)

    agosto_texto = models.CharField(max_length=100, blank=True, null=True)
    agosto_fecha = models.DateField(blank=True, null=True)

    septiembre_texto = models.CharField(max_length=100, blank=True, null=True)
    septiembre_fecha = models.DateField(blank=True, null=True)

    octubre_texto = models.CharField(max_length=100, blank=True, null=True)
    octubre_fecha = models.DateField(blank=True, null=True)

    noviembre_texto = models.CharField(max_length=100, blank=True, null=True)
    noviembre_fecha = models.DateField(blank=True, null=True)

    diciembre_texto = models.CharField(max_length=100, blank=True, null=True)
    diciembre_fecha = models.DateField(blank=True, null=True)

    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Cronograma General de Pagos"


ESTADOS_SOLICITUD = [
    ('pendiente_pm', 'Pendiente aprobación PM'),
    ('rechazada_pm', 'Rechazada por PM'),
    ('pendiente_rrhh', 'Pendiente aprobación RRHH'),
    ('rechazada_rrhh', 'Rechazada por RRHH'),
    ('aprobada', 'Aprobada'),
]


def ruta_archivos_adelanto(instance, filename):
    return instance.ruta_archivo_completo(filename)


class SolicitudAdelanto(models.Model):
    trabajador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='solicitudes_adelanto'
    )
    monto_solicitado = models.PositiveIntegerField()
    monto_aprobado = models.PositiveIntegerField(null=True, blank=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS_SOLICITUD,
        default='pendiente_pm'
    )
    motivo_rechazo = models.TextField(blank=True, null=True)

    aprobado_por_pm = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adelantos_aprobados_como_pm'
    )
    aprobado_por_rrhh = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adelantos_aprobados_como_rrhh'
    )

    fecha_solicitud = models.DateTimeField(auto_now_add=True)

    comprobante_transferencia = models.FileField(
        upload_to=ruta_archivos_adelanto,
        storage=cloudinary_storage,
        null=True, blank=True
    )
    planilla_pdf = models.FileField(
        upload_to=ruta_archivos_adelanto,
        storage=cloudinary_storage,
        null=True, blank=True
    )

    puede_editar_rrhh = models.BooleanField(default=False)  # ✅ NUEVO CAMPO

    def __str__(self):
        return f"{self.trabajador.get_full_name()} - {self.estado}"

    def ruta_base(self):
        mes = self.fecha_solicitud.strftime('%B')
        identidad = self.trabajador.identidad or f"usuario_{self.trabajador.id}"
        identidad_limpia = identidad.replace('.', '').replace('-', '')
        return f"media/Solicitud de adelanto de sueldo/{mes}/{identidad_limpia}"

    def numero_consecutivo(self):
        return SolicitudAdelanto.objects.filter(
            trabajador=self.trabajador,
            fecha_solicitud__month=self.fecha_solicitud.month,
            fecha_solicitud__year=self.fecha_solicitud.year
        ).exclude(id=self.id).count() + 1

    def carpeta_consecutiva(self):
        return f"{self.ruta_base()}/solicitud_{self.numero_consecutivo()}/"

    def ruta_archivo_completo(self, filename):
        return f"{self.carpeta_consecutiva()}{filename}"

    def ruta_cloudinary(self):
        return self.carpeta_consecutiva()

# usuarios/signals.py

from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from rrhh.models import FichaIngreso
from usuarios.models import CustomUser, Rol


@receiver(post_save, sender=CustomUser)
def asociar_ficha_al_crear_usuario(sender, instance, created, **kwargs):
    if instance.identidad:
        ficha = FichaIngreso.objects.filter(
            usuario__isnull=True,
            rut=instance.identidad,
        ).first()

        if ficha:
            ficha.usuario = instance
            ficha.save()


@receiver(post_migrate)
def crear_roles(sender, **kwargs):
    """
    Crea únicamente los roles base del sistema.

    No se crea ningún superusuario automáticamente.
    """

    for nombre in [
        "admin",
        "rrhh",
        "pm",
        "usuario",
        "supervisor",
        "flota",
        "prevencion",
        "subcontrato",
        "logistica",
        "facturacion",
    ]:
        Rol.objects.get_or_create(nombre=nombre)

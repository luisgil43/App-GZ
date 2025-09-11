# usuarios/signals.py
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.conf import settings


import os

from usuarios.models import CustomUser, Rol
from rrhh.models import FichaIngreso

User = get_user_model()


@receiver(post_save, sender=CustomUser)
def asociar_ficha_al_crear_usuario(sender, instance, created, **kwargs):
    if instance.identidad:
        ficha = FichaIngreso.objects.filter(
            usuario__isnull=True, rut=instance.identidad).first()
        if ficha:
            ficha.usuario = instance
            ficha.save()


@receiver(post_migrate)
def crear_roles_y_admin(sender, **kwargs):
    # 1) Crear roles (idempotente, seguro)
    for nombre in ['admin', 'rrhh', 'pm', 'usuario', 'supervisor', 'flota', 'prevencion', 'subcontrato', 'logistica', 'facturacion']:
        Rol.objects.get_or_create(nombre=nombre)

    # 2) (Opcional) Crear superusuario SOLO en desarrollo y con env vars presentes
    if not getattr(settings, "DEBUG", False):
        return  # Nunca crear admin automático en producción

    username = os.getenv("DJANGO_SUPERUSER_USERNAME")
    email = os.getenv("DJANGO_SUPERUSER_EMAIL")
    password = os.getenv("DJANGO_SUPERUSER_PASSWORD")

    if username and email and password:
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username, email=email, password=password)

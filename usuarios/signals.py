from django.db.models.signals import post_migrate
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from usuarios.models import CustomUser
from rrhh.models import FichaIngreso


@receiver(post_save, sender=CustomUser)
def asociar_ficha_al_crear_usuario(sender, instance, created, **kwargs):
    if instance.identidad:
        print(f"🟡 Buscando ficha con rut = {instance.identidad}")
        ficha = FichaIngreso.objects.filter(
            usuario__isnull=True, rut=instance.identidad).first()
        if ficha:
            ficha.usuario = instance
            ficha.save()
            print(f"🟢 Ficha asociada al usuario {instance.username}")
        else:
            print("🔴 No se encontró ficha con ese rut.")


User = get_user_model()


@receiver(post_migrate)
def crear_admin_por_defecto(sender, **kwargs):
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='admin123'  # cámbiala luego en producción
        )
        print("✅ Usuario admin creado automáticamente")

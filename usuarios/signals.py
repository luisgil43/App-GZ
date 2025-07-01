from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from usuarios.models import CustomUser, Rol
from rrhh.models import FichaIngreso

User = get_user_model()


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


@receiver(post_migrate)
def crear_roles_y_admin(sender, **kwargs):
    print("🔄 Ejecutando señal post_migrate...")

    # Crear roles
    roles = ['admin', 'rrhh', 'pm', 'usuario', 'supervisor',
             'flota', 'prevencion', 'subcontrato', 'logistica', 'facturacion']
    for nombre in roles:
        rol, creado = Rol.objects.get_or_create(nombre=nombre)
        if creado:
            print(f"✅ Rol creado: {nombre}")

    # Crear usuario admin si no existe
    if not User.objects.filter(username='admin').exists():
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='admin123',
            first_name='Admin',
            last_name='General',
            is_active=True,
            is_staff=True,
            is_superuser=True,
            identidad='99999999-9'
        )
        rol_admin = Rol.objects.get(nombre='admin')
        admin.roles.add(rol_admin)
        admin.save()
        print("✅ Usuario admin creado automáticamente y rol asignado.")

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import DetalleIngresoMaterial


@receiver(post_save, sender=DetalleIngresoMaterial)
def actualizar_stock_al_guardar(sender, instance, created, **kwargs):
    material = instance.material
    if created:
        material.stock_actual += instance.cantidad
        material.save()


@receiver(post_delete, sender=DetalleIngresoMaterial)
def actualizar_stock_al_eliminar(sender, instance, **kwargs):
    material = instance.material
    material.stock_actual -= instance.cantidad
    if material.stock_actual < 0:
        material.stock_actual = 0
    material.save()

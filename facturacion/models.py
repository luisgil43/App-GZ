from django.db import models
from decimal import Decimal
from operaciones.models import ServicioCotizado


class OrdenCompraFacturacion(models.Model):
    du = models.ForeignKey(ServicioCotizado, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='ordenes_compra')

    orden_compra = models.CharField(
        "Orden de Compra", max_length=30, blank=True, null=True)
    pos = models.CharField("POS", max_length=10, blank=True, null=True)
    cantidad = models.DecimalField(
        "Cantidad", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    unidad_medida = models.CharField(
        "UM", max_length=10, blank=True, null=True)
    material_servicio = models.CharField(
        "Material/Servicio", max_length=100, blank=True, null=True)
    descripcion_sitio = models.TextField(
        "Descripción / Sitio", blank=True, null=True)
    fecha_entrega = models.DateField("Fecha de Entrega", blank=True, null=True)
    precio_unitario = models.DecimalField(
        "Precio Unitario", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monto = models.DecimalField(
        "Monto", max_digits=12, decimal_places=2, default=Decimal('0.00'))

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Orden de Compra Facturación"
        verbose_name_plural = "Órdenes de Compra Facturación"

    def __str__(self):
        return f"OC {self.orden_compra} - DU: {self.du.du if self.du else 'Sin DU'}"


class FacturaOC(models.Model):
    orden_compra = models.OneToOneField(
        OrdenCompraFacturacion,
        on_delete=models.CASCADE,
        related_name='factura',
        verbose_name="Orden de Compra"
    )

    hes = models.CharField("HES", max_length=50, blank=True, null=True)
    valor_en_clp = models.DecimalField(
        "Valor en CLP", max_digits=15, decimal_places=2, blank=True, null=True)
    conformidad = models.CharField(
        "Conformidad", max_length=50, blank=True, null=True)
    num_factura = models.CharField(
        "Número de Factura", max_length=50, blank=True, null=True)
    fecha_facturacion = models.DateField(
        "Fecha de Facturación", blank=True, null=True)
    mes_produccion = models.CharField(
        "Mes de Producción", max_length=20, blank=True, null=True)
    factorizado = models.BooleanField("¿Factorizado?", default=False)
    fecha_factoring = models.DateField(
        "Fecha de Factoring", blank=True, null=True)
    cobrado = models.BooleanField("¿Cobrado?", default=False)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Factura"
        verbose_name_plural = "Facturas"

    def __str__(self):
        return f"Factura {self.num_factura or 'Sin número'} - OC {self.orden_compra.orden_compra}"

    # Estado dinámico
    def get_status_factura(self):
        if not self.conformidad:
            return "Pendiente por Conformidad"
        if not self.num_factura:
            return "Pendiente por Facturación"
        if self.num_factura:
            status = "Facturado"
            if self.factorizado:
                status = "En proceso de Factoring"
            if self.cobrado:
                status = "Cobrado"
            return status
        return "Pendiente"

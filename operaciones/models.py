# operaciones/models.py

from django.db import models


class SitioMovil(models.Model):
    id_sites = models.CharField(max_length=100, unique=True)
    id_claro = models.CharField(max_length=100, blank=True, null=True)
    id_sites_new = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=100, blank=True, null=True)
    nombre = models.CharField(max_length=255, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)

    # Convertidos a FloatField
    latitud = models.FloatField(blank=True, null=True)
    longitud = models.FloatField(blank=True, null=True)

    comuna = models.CharField(max_length=100, blank=True, null=True)
    tipo_construccion = models.CharField(max_length=100, blank=True, null=True)
    altura = models.CharField(max_length=100, blank=True, null=True)
    candado_bt = models.CharField(max_length=100, blank=True, null=True)
    condiciones_acceso = models.TextField(blank=True, null=True)
    claves = models.TextField(blank=True, null=True)
    llaves = models.TextField(blank=True, null=True)
    cantidad_llaves = models.CharField(max_length=255, blank=True, null=True)
    observaciones_generales = models.TextField(blank=True, null=True)
    zonas_conflictivas = models.TextField(blank=True, null=True)
    alarmas = models.TextField(blank=True, null=True)
    guardias = models.TextField(blank=True, null=True)
    nivel = models.IntegerField(blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre or self.id_sites

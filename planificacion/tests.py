from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from operaciones.models import ServicioCotizado, SitioMovil
from planificacion.modelos import (
    DisponibilidadCuadrillaSemana,
    SalidaPlanificacionDiaria,
    SitioSalidaPlanificacionDiaria,
)
from planificacion.models import (
    BatchPlanificacionSemanal,
    ConfiguracionSemana,
    PlanificacionMensual,
    SitioBatchSemanal,
    SitioPlanificado,
)
from planificacion.services.planificacion_diaria import (
    obtener_servicios_operacionales_por_sitio,
    obtener_sitios_pendientes_planificacion_diaria,
)


class CargaPlanificacionDiariaTests(TestCase):
    def setUp(self):
        self.mensual = PlanificacionMensual.objects.create(anio=2026, mes=11)
        self.batch = BatchPlanificacionSemanal.objects.create(
            planificacion=self.mensual, fecha_inicio=date(2026, 11, 23),
        )

    def crear_sitio(self, codigo, estado):
        return SitioPlanificado.objects.create(
            planificacion=self.mensual,
            sitio=SitioMovil.objects.create(id_claro=codigo, id_sites=codigo),
            estado=estado,
            estado_permiso="aprobado",
            activo_en_mes=True,
        )

    def crear_servicio(self, sitio, estado):
        return ServicioCotizado.objects.create(
            sitio_planificado=sitio,
            mes_produccion="11/2026",
            detalle_tarea="Mantenimiento",
            monto_cotizado=0,
            estado=estado,
        )

    def test_historial_se_muestra_por_paginas(self):
        for semanas in range(1, 13):
            BatchPlanificacionSemanal.objects.create(
                planificacion=self.mensual,
                fecha_inicio=self.batch.fecha_inicio - timedelta(weeks=semanas),
            )

        admin = get_user_model().objects.create_superuser(username="admin_prueba")
        self.client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("planificacion:planificacion_diaria")

        primera = self.client.get(url)
        self.assertEqual(primera.status_code, 200)
        self.assertEqual(len(primera.context["filas"]), 12)
        self.assertEqual(primera.context["filas"][0]["batch"], self.batch)
        self.assertContains(primera, "?pagina=2")

        segunda = self.client.get(url, {"pagina": 2})
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(len(segunda.context["filas"]), 1)
        self.assertContains(segunda, "Semanas más recientes")

    def test_servicios_y_tecnicos_se_cargan_juntos_por_ejecucion(self):
        primero = self.crear_sitio("01_001", "planificado")
        segundo = self.crear_sitio("01_002", "planificado")
        self.crear_servicio(primero, "asignado")
        vigente = self.crear_servicio(primero, "en_progreso")
        otro = self.crear_servicio(segundo, "asignado")
        tecnico = get_user_model().objects.create_user(username="tecnico_prueba")
        vigente.trabajadores_asignados.add(tecnico)

        with self.assertNumQueries(2):
            servicios = obtener_servicios_operacionales_por_sitio([primero.pk, segundo.pk])
            self.assertEqual(servicios, {primero.pk: vigente, segundo.pk: otro})
            self.assertEqual(list(servicios[primero.pk].trabajadores_asignados.all()), [tecnico])

    def test_pendientes_recupera_sitios_reabiertos_y_omite_finalizados(self):
        finalizado = self.crear_sitio("01_003", "completado")
        reabierto = self.crear_sitio("01_004", "completado")
        for sitio in (finalizado, reabierto):
            SitioBatchSemanal.objects.create(
                batch=self.batch, sitio_planificado=sitio, estado="confirmado",
            )
            self.crear_servicio(sitio, "aprobado_supervisor")
        self.crear_servicio(reabierto, "aprobado_pendiente")

        pendientes = obtener_sitios_pendientes_planificacion_diaria(self.batch)

        self.assertEqual([item.sitio_planificado_id for item in pendientes], [reabierto.pk])
        finalizado.refresh_from_db()
        reabierto.refresh_from_db()
        self.assertEqual(finalizado.estado, "completado")
        self.assertEqual(reabierto.estado, "planificado")

    def test_detalle_conserva_resumen_y_servicios_de_la_semana(self):
        configuracion = ConfiguracionSemana.objects.create(fecha_inicio=self.batch.fecha_inicio)
        disponibilidad = DisponibilidadCuadrillaSemana.objects.create(
            configuracion_semana=configuracion, cuadrilla="cuadrilla_1",
        )
        salida = SalidaPlanificacionDiaria.objects.create(
            batch=self.batch, disponibilidad_cuadrilla=disponibilidad,
            fecha=self.batch.fecha_inicio, estado="lista_asignar",
        )
        programado = self.crear_sitio("01_005", "planificado")
        pendiente = self.crear_sitio("01_006", "listo_planificar")
        self.crear_servicio(programado, "en_progreso")
        participacion = SitioBatchSemanal.objects.create(
            batch=self.batch, sitio_planificado=programado, estado="confirmado",
        )
        SitioBatchSemanal.objects.create(
            batch=self.batch, sitio_planificado=pendiente, estado="confirmado",
        )
        SitioSalidaPlanificacionDiaria.objects.create(salida=salida, sitio_batch=participacion)

        admin = get_user_model().objects.create_superuser(username="admin_detalle")
        self.client.force_login(admin, backend="django.contrib.auth.backends.ModelBackend")
        detalle = self.client.get(reverse("planificacion:detalle_planificacion_diaria", args=[self.batch.pk]))

        self.assertEqual(detalle.status_code, 200)
        self.assertEqual(detalle.context["resumen"]["total_salidas"], 1)
        self.assertEqual(detalle.context["resumen"]["total_sitios"], 1)
        self.assertEqual(detalle.context["resumen"]["disponibles_sin_planificar"], 1)
        self.assertEqual(detalle.context["resumen_operaciones"]["en_ejecucion"], 1)
        self.assertContains(detalle, "01_006")

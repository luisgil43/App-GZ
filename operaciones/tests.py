import json
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import EvidenciaFoto, RequisitoFoto, ServicioCotizado, SesionFotos, SesionFotoTecnico


@override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
class ConfigurarRequerimientosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        usuarios = get_user_model().objects
        cls.admin = usuarios.create_superuser(username="admin_requerimientos")
        tecnicos = [usuarios.create_user(username=f"tecnico_{numero}") for numero in range(2)]
        cls.servicio = ServicioCotizado.objects.create(
            mes_produccion="11/2026", detalle_tarea="Mantenimiento", monto_cotizado=0,
        )
        cls.servicio.trabajadores_asignados.add(*tecnicos)
        sesion = SesionFotos.objects.create(servicio=cls.servicio)
        cls.asignaciones = [
            SesionFotoTecnico.objects.create(sesion=sesion, tecnico=tecnico)
            for tecnico in tecnicos
        ]

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.token = "a" * 32
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.token
        self.url = reverse("operaciones:fotos_configurar_requisitos", args=[self.servicio.pk])
        self.destino = reverse("operaciones:listar_servicios_supervisor") + "?page=2"

    def guardar(self, **campos):
        return self.client.post(
            self.url,
            urlencode({"csrfmiddlewaretoken": self.token, "next": self.destino, **campos}, doseq=True),
            content_type="application/x-www-form-urlencoded",
        )

    def test_agregar_fila_a_una_lista_de_200_requerimientos(self):
        for asignacion in self.asignaciones:
            RequisitoFoto.objects.bulk_create([
                RequisitoFoto(
                    tecnico_sesion=asignacion,
                    titulo=f"Foto {orden}",
                    titulo_norm=f"foto {orden}",
                    orden=orden,
                )
                for orden in range(200)
            ])

        originales = list(self.asignaciones[0].requisitos.order_by("orden"))
        filas = [
            [str(requisito.pk), requisito.titulo, str(requisito.orden), "1", ""]
            for requisito in originales
        ]
        filas.append(["", "Medición después de la revisión", "200", "0", "despues"])

        respuesta = self.guardar(requerimientos=json.dumps(filas), proyecto_especial="on")

        self.assertRedirects(respuesta, self.destino, fetch_redirect_response=False)
        for asignacion in self.asignaciones:
            self.assertEqual(asignacion.requisitos.filter(activo=True).count(), 201)
            nuevo = asignacion.requisitos.get(orden=200)
            self.assertEqual(nuevo.titulo, "Medición después de la revisión")
            self.assertEqual(nuevo.etapa, "despues")
            self.assertFalse(nuevo.obligatorio)
        self.assertTrue(self.asignaciones[0].requisitos.filter(pk=originales[0].pk, activo=True).exists())
        self.assertTrue(SesionFotos.objects.get(servicio=self.servicio).proyecto_especial)

    def test_carga_invalida_conserva_los_requerimientos(self):
        requisito = RequisitoFoto.objects.create(
            tecnico_sesion=self.asignaciones[0], titulo="Foto existente", orden=0,
        )
        respuesta = self.guardar(requerimientos='[["campo incompleto"]]')
        self.assertContains(respuesta, "La lista de requerimientos tiene un formato inválido.")
        requisito.refresh_from_db()
        self.assertTrue(requisito.activo)

    def test_formulario_anterior_sigue_guardando(self):
        respuesta = self.guardar(**{
            "id[]": "", "name[]": "Foto inicial", "order[]": "0",
            "mandatory[]": "1", "etapa[]": "antes",
        })
        self.assertRedirects(respuesta, self.destino, fetch_redirect_response=False)
        for asignacion in self.asignaciones:
            self.assertTrue(asignacion.requisitos.filter(titulo="Foto inicial", activo=True).exists())

    def test_papelera_desactiva_equivalentes_y_conserva_evidencia(self):
        primero, segundo = self.asignaciones
        origen = RequisitoFoto.objects.create(tecnico_sesion=primero, titulo="Medición")
        equivalente = RequisitoFoto.objects.create(tecnico_sesion=segundo, titulo="Medicion")
        otro = RequisitoFoto.objects.create(tecnico_sesion=segundo, titulo="Otra foto", orden=4)
        evidencia = EvidenciaFoto.objects.create(
            tecnico_sesion=segundo, requisito=equivalente, imagen="test/existente.jpg",
        )
        url = reverse("operaciones:fotos_quitar_requerimiento", args=[self.servicio.pk, origen.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        for _ in range(2):
            respuesta = self.client.post(url, HTTP_X_CSRFTOKEN=self.token, HTTP_ACCEPT="application/json")
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json(), {"ok": True})

        origen.refresh_from_db()
        equivalente.refresh_from_db()
        otro.refresh_from_db()
        evidencia.refresh_from_db()
        self.assertFalse(origen.activo)
        self.assertFalse(equivalente.activo)
        self.assertTrue(otro.activo)
        self.assertEqual(otro.orden, 4)
        self.assertEqual(evidencia.requisito_id, equivalente.pk)

    def test_papelera_rechaza_un_requerimiento_de_otro_servicio(self):
        otro_servicio = ServicioCotizado.objects.create(
            mes_produccion="11/2026", detalle_tarea="Otro mantenimiento", monto_cotizado=0,
        )
        SesionFotos.objects.create(servicio=otro_servicio)
        requisito = RequisitoFoto.objects.create(
            tecnico_sesion=self.asignaciones[0], titulo="Solo en el servicio original",
        )
        url = reverse("operaciones:fotos_quitar_requerimiento", args=[otro_servicio.pk, requisito.pk])
        respuesta = self.client.post(url, HTTP_X_CSRFTOKEN=self.token, HTTP_ACCEPT="application/json")
        self.assertEqual(respuesta.status_code, 404)
        requisito.refresh_from_db()
        self.assertTrue(requisito.activo)

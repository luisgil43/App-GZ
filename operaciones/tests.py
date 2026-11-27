import json
from io import BytesIO
from urllib.parse import urlencode
from unittest.mock import patch
from tempfile import TemporaryDirectory

from botocore.response import StreamingBody
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from PIL import Image
from usuarios.models import Rol

from .models import (
    EntregableServicio, EvidenciaFoto, FormatoEntregable, RequisitoFoto, ServicioCotizado,
    SesionFotos, SesionFotoTecnico,
)


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


class EntregableServicioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(username="admin_entregables")
        cls.tecnico = get_user_model().objects.create_user(username="tecnico_entregables")
        cls.servicio = ServicioCotizado.objects.create(
            mes_produccion="11/2026", detalle_tarea="Mantenimiento", monto_cotizado=0,
            estado="en_progreso",
        )
        cls.otro_servicio = ServicioCotizado.objects.create(
            mes_produccion="11/2026", detalle_tarea="Otro mantenimiento", monto_cotizado=0,
        )

    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        campo = EntregableServicio._meta.get_field("archivo")
        self.assertIs(campo.storage, EvidenciaFoto._meta.get_field("imagen").storage)
        self.almacen = FileSystemStorage(location=carpeta.name)
        parche = patch.object(campo, "storage", self.almacen)
        parche.start()
        self.addCleanup(parche.stop)

        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.token = "a" * 32
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.token
        self.adjuntar = reverse("operaciones:adjuntar_entregable", args=[self.servicio.pk])
        self.revision = reverse("operaciones:fotos_revisar_sesion", args=[self.servicio.pk])

    def subir(self, nombre, contenido, titulo="Informe de cálculo"):
        return self.client.post(self.adjuntar, {
            "titulo": titulo, "archivo": SimpleUploadedFile(nombre, contenido),
            "csrfmiddlewaretoken": self.token,
        }, HTTP_ACCEPT="application/json")

    def asignar_tecnico_en_proceso(self):
        self.servicio.trabajadores_asignados.add(self.tecnico)
        sesion, _ = SesionFotos.objects.get_or_create(servicio=self.servicio)
        return SesionFotoTecnico.objects.create(
            sesion=sesion, tecnico=self.tecnico, estado="en_proceso",
        )

    def test_guarda_entregables_grandes_con_el_mismo_storage_de_fotos(self):
        tamano = 80 * 1024 * 1024
        with patch.object(self.almacen, "_save", wraps=self.almacen._save) as guardar:
            respuesta = self.subir("informe.xlsx", b"0" * tamano)
        self.assertEqual(respuesta.status_code, 201)
        self.assertIn("informe.xlsx", respuesta.json()["fila"])
        self.assertIsInstance(guardar.call_args.args[1], TemporaryUploadedFile)

        entregable = self.servicio.entregables.get()
        self.assertEqual(entregable.adjuntado_por, self.admin)
        self.assertEqual(entregable.archivo.size, tamano)
        self.assertContains(self.client.get(self.revision), "Informe de cálculo")
        self.servicio.refresh_from_db()
        self.assertEqual(self.servicio.estado, "en_progreso")
        self.assertFalse(self.servicio.reporte_fotografico)

        descarga = reverse("operaciones:descargar_entregable", args=[self.servicio.pk, entregable.pk])
        with patch.object(self.almacen, "url", return_value="https://almacen.example/get") as url:
            self.assertEqual(self.client.get(descarga).json(), {"url": "https://almacen.example/get"})
            self.assertEqual(
                url.call_args.kwargs["parameters"]["ResponseContentDisposition"],
                'attachment; filename="informe.xlsx"',
            )
        otra_descarga = reverse(
            "operaciones:descargar_entregable", args=[self.otro_servicio.pk, entregable.pk],
        )
        self.assertEqual(self.client.get(otra_descarga).status_code, 404)
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(descarga, HTTP_ACCEPT="application/json").status_code, 403)
        self.assertEqual(self.subir("informe.xlsx", b"datos").status_code, 403)

    def test_rechaza_extensiones_invalidas(self):
        self.assertEqual(self.subir("archivo.exe", b"datos").status_code, 400)
        self.assertFalse(self.servicio.entregables.exists())

    def test_elimina_el_entregable_y_su_archivo_solo_desde_el_du_correcto(self):
        self.assertEqual(self.subir("informe.pdf", b"datos").status_code, 201)
        entregable = self.servicio.entregables.get()
        ruta = entregable.archivo.name
        url = reverse("operaciones:eliminar_entregable", args=[self.servicio.pk, entregable.pk])
        otro_du = reverse("operaciones:eliminar_entregable", args=[self.otro_servicio.pk, entregable.pk])
        headers = {"HTTP_X_CSRFTOKEN": self.token, "HTTP_ACCEPT": "application/json"}

        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(otro_du, **headers).status_code, 404)
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, **headers).status_code, 403)
        self.assertTrue(self.almacen.exists(ruta))
        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.post(url, **headers).json(), {"ok": True})
        self.assertFalse(self.almacen.exists(ruta))
        self.assertFalse(self.servicio.entregables.exists())

    def test_si_falla_wasabi_conserva_el_entregable_en_la_lista(self):
        self.assertEqual(self.subir("informe.pdf", b"datos").status_code, 201)
        entregable = self.servicio.entregables.get()
        url = reverse("operaciones:eliminar_entregable", args=[self.servicio.pk, entregable.pk])
        with patch.object(self.almacen, "delete", side_effect=OSError("Wasabi no disponible")):
            respuesta = self.client.post(url, HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(respuesta.status_code, 503)
        self.assertTrue(self.servicio.entregables.filter(pk=entregable.pk).exists())

    def test_admite_archivos_de_ingenieria_en_selector_y_subida(self):
        revision = self.client.get(self.revision)
        self.assertContains(revision, f'accept="{EntregableServicio.extensiones_selector()}"')
        self.assertContains(revision, ", ".join(EntregableServicio.EXTENSIONES_ADMITIDAS).upper())
        for extension in ("DWG", "dxf", "ifc", "rvt", "step", "kmz"):
            with self.subTest(extension=extension):
                respuesta = self.subir(f"Plano.{extension}", b"datos")
                self.assertEqual(respuesta.status_code, 201)
                self.assertIn("Descargar", respuesta.json()["fila"])

    def test_fallo_del_almacen_no_deja_un_entregable_registrado(self):
        with patch.object(self.almacen, "_save", side_effect=OSError("Wasabi no disponible")):
            self.assertEqual(self.subir("informe.xlsx", b"datos").status_code, 503)
        self.assertFalse(self.servicio.entregables.exists())

    def test_visualiza_pdf_con_titulo_amplio_y_enlace_privado(self):
        titulo = "I" * EntregableServicio.TITULO_MAX_LENGTH
        respuesta = self.subir("Informe.PDF", b"%PDF-1.4", titulo=titulo)
        self.assertEqual(respuesta.status_code, 201)
        self.assertIn("Visualizar", respuesta.json()["fila"])
        entregable = self.servicio.entregables.get()
        self.assertEqual(entregable.titulo, titulo)
        self.assertContains(self.client.get(self.revision), 'maxlength="500"')

        visualizar = reverse("operaciones:visualizar_entregable", args=[self.servicio.pk, entregable.pk])
        with patch.object(self.almacen, "url", return_value="https://almacen.example/pdf") as url:
            respuesta = self.client.get(visualizar)
            self.assertRedirects(respuesta, "https://almacen.example/pdf", fetch_redirect_response=False)
            self.assertIn("no-store", respuesta["Cache-Control"])
            parametros = url.call_args.kwargs["parameters"]
            self.assertEqual(parametros["ResponseContentType"], "application/pdf")
            self.assertEqual(parametros["ResponseContentDisposition"], 'inline; filename="Informe.PDF"')

        otro_du = reverse("operaciones:visualizar_entregable", args=[self.otro_servicio.pk, entregable.pk])
        self.assertEqual(self.client.get(otro_du).status_code, 404)
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(visualizar, HTTP_ACCEPT="application/json").status_code, 403)

    def test_planilla_permite_visualizar_una_hoja_y_conserva_descarga(self):
        libro = Workbook()
        libro.active.title = "Resumen"
        libro.active["A1"] = "Primera hoja"
        hoja = libro.create_sheet("Mediciones")
        hoja["A1"] = "Velocidad del viento"
        hoja["A101"] = "Fuera de la vista previa"
        original = BytesIO()
        libro.save(original)
        libro.close()
        datos = original.getvalue()

        respuesta = self.subir("informe.xlsx", datos)
        self.assertEqual(respuesta.status_code, 201)
        self.assertIn("Visualizar", respuesta.json()["fila"])
        self.assertIn("Descargar", respuesta.json()["fila"])
        entregable = self.servicio.entregables.get()
        visualizar = reverse("operaciones:visualizar_entregable", args=[self.servicio.pk, entregable.pk])
        with patch("operaciones.views_entregables._almacen") as almacen:
            almacen.return_value.connection.meta.client.get_object.return_value = {
                "ContentLength": len(datos),
                "Body": StreamingBody(BytesIO(datos), len(datos)),
            }
            respuesta = self.client.get(visualizar, {"hoja": "Mediciones"})
            almacen.return_value.connection.meta.client.get_object.assert_called_once_with(
                Bucket=almacen.return_value.bucket_name, Key=entregable.archivo.name,
            )

        self.assertContains(respuesta, "Velocidad del viento")
        self.assertNotContains(respuesta, "Primera hoja")
        self.assertNotContains(respuesta, "Fuera de la vista previa")
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_rechaza_titulos_de_mas_de_500_caracteres(self):
        respuesta = self.subir("informe.pdf", b"datos", titulo="I" * 501)
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(self.servicio.entregables.exists())

    def test_formato_del_catalogo_disponible_en_selector_y_perfil(self):
        catalogo = reverse("operaciones:formatos_entregables")
        titulo = "Informe de verticalidad"
        campo = FormatoEntregable._meta.get_field("ejemplo")
        with patch.object(campo, "storage", self.almacen):
            respuesta = self.client.post(catalogo, {
                "csrfmiddlewaretoken": self.token, "nombre": titulo,
                "archivo": SimpleUploadedFile("verticalidad.xlsx", b"plantilla"),
            })
            self.assertRedirects(respuesta, catalogo)
            formato = FormatoEntregable.objects.get(nombre=titulo)
            self.assertTrue(self.almacen.exists(formato.ejemplo.name))
            descarga = reverse("operaciones:descargar_formato_entregable", args=[formato.pk])
            self.assertContains(self.client.get(catalogo), descarga)
            with patch.object(self.almacen, "url", return_value="https://almacen.example/formato"):
                self.assertRedirects(
                    self.client.get(descarga), "https://almacen.example/formato",
                    fetch_redirect_response=False,
                )

        configuracion = reverse("operaciones:fotos_configurar_requisitos", args=[self.servicio.pk])
        self.assertContains(self.client.get(configuracion), f'<option value="{formato.pk}">{titulo}</option>')
        solicitar = reverse("operaciones:solicitar_entregable", args=[self.servicio.pk])
        self.assertEqual(self.client.post(solicitar, {
            "csrfmiddlewaretoken": self.token, "titulo": titulo,
            "instrucciones": "Incluye todas las mediciones.",
            "extensiones": ["pdf", "xlsx"], "obligatorio": "on", "formato": formato.pk,
        }).status_code, 302)
        self.assertEqual(self.servicio.requerimientos_entregables.get().formato, formato)
        sugerencias = reverse("operaciones:sugerir_requerimientos_entregables")
        self.assertEqual(self.client.get(sugerencias, {"q": "verticalidad"}).json()["resultados"], [{
            "titulo": titulo, "instrucciones": "Incluye todas las mediciones.",
            "extensiones": ["pdf", "xlsx"], "obligatorio": True, "formato": formato.pk,
        }])

    def test_ver_y_eliminar_formato_sin_romper_requerimientos(self):
        formato = FormatoEntregable(nombre="Certificado", nombre_original="certificado.pdf")
        campo = FormatoEntregable._meta.get_field("ejemplo")
        with patch.object(campo, "storage", self.almacen):
            formato.ejemplo.save("certificado.pdf", SimpleUploadedFile("certificado.pdf", b"pdf"))
            ruta = formato.ejemplo.name
            ver = reverse("operaciones:ver_formato_entregable", args=[formato.pk])
            eliminar = reverse("operaciones:eliminar_formato_entregable", args=[formato.pk])
            with patch.object(self.almacen, "url", return_value="https://almacen.example/vista") as url:
                self.assertRedirects(self.client.get(ver), "https://almacen.example/vista", fetch_redirect_response=False)
                self.assertTrue(url.call_args.kwargs["parameters"]["ResponseContentDisposition"].startswith("inline"))
            self.assertEqual(self.client.get(eliminar).status_code, 405)
            requisito = self.servicio.requerimientos_entregables.create(titulo="Certificado", formato=formato)
            self.client.post(eliminar, {"csrfmiddlewaretoken": self.token})
            self.assertTrue(FormatoEntregable.objects.filter(pk=formato.pk).exists())
            self.assertTrue(self.almacen.exists(ruta))
            requisito.delete()
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(eliminar, {"csrfmiddlewaretoken": self.token})
            self.assertFalse(FormatoEntregable.objects.filter(pk=formato.pk).exists())
            self.assertFalse(self.almacen.exists(ruta))

    def test_solicitud_documental_y_ejemplo_para_el_tecnico_asignado(self):
        campo = FormatoEntregable._meta.get_field("ejemplo")
        with patch.object(campo, "storage", self.almacen):
            self._verificar_solicitud_documental()

    def _verificar_solicitud_documental(self):
        url = reverse("operaciones:solicitar_entregable", args=[self.servicio.pk])
        respuesta = self.client.post(url, {
            "csrfmiddlewaretoken": self.token, "titulo": "Certificado de montaje",
            "extension": "pdf", "instrucciones": "Incluye la firma del responsable.",
            "ejemplo": SimpleUploadedFile("plantilla.pdf", b"ejemplo"),
        })
        self.assertEqual(respuesta.status_code, 302)
        requisito = self.servicio.requerimientos_entregables.get()
        self.assertEqual(requisito.formato.nombre_original, "plantilla.pdf")
        configuracion = reverse("operaciones:fotos_configurar_requisitos", args=[self.servicio.pk])
        self.assertContains(self.client.get(configuracion), "Certificado de montaje")

        tecnico_rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(tecnico_rol)
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        listado = reverse("operaciones:entregables_del_tecnico", args=[self.servicio.pk])
        subir = reverse("operaciones:entregar_requerimiento", args=[self.servicio.pk, requisito.pk])
        ejemplo = reverse("operaciones:ejemplo_entregable", args=[self.servicio.pk, requisito.pk])
        self.assertEqual(self.client.get(listado).status_code, 404)
        self.assertEqual(self.client.get(ejemplo).status_code, 404)
        self.assertEqual(self.client.post(subir, HTTP_X_CSRFTOKEN=self.token).status_code, 404)
        asignacion = self.asignar_tecnico_en_proceso()
        pantalla = reverse("operaciones:fotos_upload", args=[asignacion.pk])
        self.assertContains(self.client.get(pantalla), "Incluye la firma del responsable.")
        self.assertContains(self.client.get(pantalla), 'accept=".pdf"')
        with patch.object(self.almacen, "url", return_value="https://almacen.example/ejemplo"):
            self.assertRedirects(
                self.client.get(ejemplo), "https://almacen.example/ejemplo",
                fetch_redirect_response=False,
            )

        for nombre, estado in (("foto.jpg", 400), ("informe.xlsx", 400), ("certificado.pdf", 201)):
            respuesta = self.client.post(
                subir, {"archivo": SimpleUploadedFile(nombre, b"contenido")},
                HTTP_X_CSRFTOKEN=self.token, HTTP_ACCEPT="application/json",
            )
            self.assertEqual(respuesta.status_code, estado)

        repetida = self.client.post(
            subir, {"archivo": SimpleUploadedFile("duplicado.pdf", b"contenido")},
            HTTP_X_CSRFTOKEN=self.token,
        )
        self.assertEqual(repetida.status_code, 409)
        entregable = self.servicio.entregables.get()
        self.assertEqual(entregable.requerimiento, requisito)
        self.assertEqual(entregable.adjuntado_por, self.tecnico)
        self.assertFalse(RequisitoFoto.objects.exists())
        self.servicio.refresh_from_db()
        self.assertFalse(self.servicio.permitir_archivos_evidencias)

        self.client.force_login(self.admin, backend="django.contrib.auth.backends.ModelBackend")
        self.assertContains(self.client.get(self.revision), "certificado.pdf")
        otro = reverse("operaciones:solicitar_entregable", args=[self.otro_servicio.pk])
        respuesta = self.client.post(otro, {
            "csrfmiddlewaretoken": self.token, "titulo": "Certificado de otro servicio",
            "extension": "pdf", "formato": requisito.formato_id,
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.otro_servicio.requerimientos_entregables.get().formato_id, requisito.formato_id)

    def test_administracion_completa_una_solicitud_y_adjunta_un_archivo_privado(self):
        requisito = self.servicio.requerimientos_entregables.create(titulo="Certificado", extension="pdf")
        sesion = SesionFotos.objects.create(servicio=self.servicio, estado="en_revision_supervisor")
        self.assertContains(self.client.get(self.revision), "1 obligatorios pendientes")
        with patch("operaciones.views_fotos._xlsx_path_reporte_fotografico") as generar:
            respuesta = self.client.post(self.revision, {
                "accion": "aprobar", "csrfmiddlewaretoken": self.token,
            })
        self.assertEqual(respuesta.status_code, 302)
        generar.assert_not_called()
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, "en_revision_supervisor")

        respuesta = self.client.post(self.adjuntar, {
            "requerimiento": requisito.pk, "archivo": SimpleUploadedFile("certificado.pdf", b"documento"),
            "csrfmiddlewaretoken": self.token,
        })
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.json()["requerimiento"], requisito.pk)
        self.assertEqual(requisito.entrega.adjuntado_por, self.admin)
        self.assertEqual(self.subir("carta.pdf", b"privado", titulo="Carta interna").status_code, 201)

        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        asignacion = self.asignar_tecnico_en_proceso()
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        listado = self.client.get(reverse("operaciones:fotos_upload", args=[asignacion.pk]))
        self.assertContains(listado, "certificado.pdf")
        self.assertNotContains(listado, "Carta interna")

    def test_importacion_mixta_conserva_archivos_anteriores(self):
        self.servicio.trabajadores_asignados.add(self.tecnico)
        importar = reverse("operaciones:fotos_importar_requisitos", args=[self.servicio.pk])
        csv = (
            "name,order,mandatory,etapa,tipo,formato,instrucciones\n"
            "Foto inicial,0,1,antes,foto,,\n"
            "Plano final,1,0,,entregable,dwg,Versión conforme a obra\n"
        ).encode()
        libro = Workbook()
        libro.active.append(["name", "order", "mandatory", "etapa", "tipo", "formato", "instrucciones"])
        libro.active.append(["Certificado", 0, 1, "", "entregable", "pdf", "Firmado"])
        archivo_xlsx = BytesIO()
        libro.save(archivo_xlsx)
        libro.close()
        archivos = (
            SimpleUploadedFile("mixto.csv", csv),
            SimpleUploadedFile("documentos.xlsx", archivo_xlsx.getvalue()),
            SimpleUploadedFile("anterior.csv", b"name,order,mandatory,etapa\nOtra foto,1,1,despues\n"),
        )
        for archivo in archivos:
            vista = self.client.post(importar, {"file": archivo, "csrfmiddlewaretoken": self.token})
            self.assertEqual(vista.status_code, 200)
            respuesta = self.client.post(importar, {
                "accion": "confirmar", "preview_token": vista.context["preview_token"],
                "csrfmiddlewaretoken": self.token,
            })
            self.assertRedirects(
                respuesta,
                reverse("operaciones:fotos_configurar_requisitos", args=[self.servicio.pk]),
                fetch_redirect_response=False,
            )
        plano = self.servicio.requerimientos_entregables.get(titulo="Plano final")
        self.assertEqual(plano.extensiones, ["dwg"])
        self.assertFalse(plano.obligatorio)
        self.assertEqual(plano.instrucciones, "Versión conforme a obra")
        self.assertTrue(self.servicio.requerimientos_entregables.get(titulo="Certificado").obligatorio)
        self.assertEqual(set(RequisitoFoto.objects.values_list("titulo", flat=True)), {"Foto inicial", "Otra foto"})

    def test_sugerencias_y_formatos_multiples_actualizan_la_solicitud_sin_duplicarla(self):
        solicitar = reverse("operaciones:solicitar_entregable", args=[self.servicio.pk])
        catalogo = reverse("operaciones:sugerir_requerimientos_entregables")
        campos = {
            "csrfmiddlewaretoken": self.token, "titulo": "Cálculo estructural",
            "instrucciones": "Adjunta la memoria firmada.", "extensiones": ["pdf", "xlsx"],
            "obligatorio": "on",
        }
        self.assertEqual(self.client.post(solicitar, campos).status_code, 302)
        original = self.servicio.requerimientos_entregables.get()
        sugerencia, = self.client.get(catalogo, {"q": "calculo"}).json()["resultados"]
        self.assertEqual(sugerencia["instrucciones"], "Adjunta la memoria firmada.")
        self.assertEqual(sugerencia["extensiones"], ["pdf", "xlsx"])

        otra_solicitud = reverse("operaciones:solicitar_entregable", args=[self.otro_servicio.pk])
        self.assertEqual(self.client.post(otra_solicitud, campos).status_code, 302)
        campos.update(instrucciones="Adjunta la versión aprobada.", extensiones=["pdf", "dwg"])
        self.assertEqual(self.client.post(solicitar, campos).status_code, 302)
        actualizado = self.servicio.requerimientos_entregables.get()
        self.assertEqual(actualizado.pk, original.pk)
        self.assertEqual(actualizado.extensiones, ["pdf", "dwg"])
        self.assertEqual(self.otro_servicio.requerimientos_entregables.get().extensiones, ["pdf", "xlsx"])
        sugerencia, = self.client.get(catalogo, {"q": "estructural"}).json()["resultados"]
        self.assertEqual(sugerencia["instrucciones"], "Adjunta la versión aprobada.")

        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        self.asignar_tecnico_en_proceso()
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        url = reverse("operaciones:entregar_requerimiento", args=[self.servicio.pk, actualizado.pk])
        for extension, codigo in (("xlsx", 400), ("dwg", 201)):
            respuesta = self.client.post(
                url, {"archivo": SimpleUploadedFile(f"calculo.{extension}", b"datos")},
                HTTP_X_CSRFTOKEN=self.token,
            )
            self.assertEqual(respuesta.status_code, codigo)

    def test_quitar_requerimiento_conserva_el_archivo_y_el_nombre_guardado(self):
        solicitud = reverse("operaciones:solicitar_entregable", args=[self.servicio.pk])
        self.client.post(solicitud, {
            "titulo": "Certificado", "extensiones": ["pdf"], "csrfmiddlewaretoken": self.token,
        })
        requisito = self.servicio.requerimientos_entregables.get()
        respuesta = self.client.post(self.adjuntar, {
            "requerimiento": requisito.pk, "archivo": SimpleUploadedFile("certificado.pdf", b"documento"),
            "csrfmiddlewaretoken": self.token,
        })
        self.assertEqual(respuesta.status_code, 201)
        entregable = requisito.entrega
        quitar = reverse("operaciones:quitar_requerimiento_entregable", args=[self.servicio.pk, requisito.pk])
        otro_du = reverse("operaciones:quitar_requerimiento_entregable", args=[self.otro_servicio.pk, requisito.pk])
        self.assertEqual(self.client.get(quitar).status_code, 405)
        self.assertEqual(self.client.post(otro_du, HTTP_X_CSRFTOKEN=self.token).status_code, 404)
        self.assertEqual(self.client.post(quitar, HTTP_X_CSRFTOKEN=self.token).json(), {"ok": True})
        self.assertFalse(self.servicio.requerimientos_entregables.exists())
        entregable.refresh_from_db()
        self.assertIsNone(entregable.requerimiento_id)
        self.assertTrue(self.almacen.exists(entregable.archivo.name))
        catalogo = self.client.get(reverse("operaciones:sugerir_requerimientos_entregables"), {"q": "cert"})
        self.assertEqual(catalogo.json()["resultados"][0]["titulo"], "Certificado")

    def test_tecnico_puede_ver_descargar_eliminar_y_reemplazar_su_entregable(self):
        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        asignacion = self.asignar_tecnico_en_proceso()
        requisito = self.servicio.requerimientos_entregables.create(titulo="Certificado", extension="pdf")
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        subir = reverse("operaciones:entregar_requerimiento", args=[self.servicio.pk, requisito.pk])
        cabeceras = {"HTTP_X_CSRFTOKEN": self.token, "HTTP_ACCEPT": "application/json"}
        respuesta = self.client.post(subir, {"archivo": SimpleUploadedFile("uno.pdf", b"uno")}, **cabeceras)
        self.assertEqual(respuesta.status_code, 201)
        self.assertContains(respuesta, "Eliminar", status_code=201)
        entrega = self.servicio.entregables.get()
        ids = [self.servicio.pk, entrega.pk]
        with patch.object(self.almacen, "url", return_value="https://almacen.example/archivo") as url:
            for accion in ("visualizar", "descargar"):
                destino = reverse("operaciones:abrir_entrega_tecnico", args=[*ids, accion])
                self.assertRedirects(
                    self.client.get(destino), "https://almacen.example/archivo", fetch_redirect_response=False,
                )
            self.assertTrue(url.call_args.kwargs["parameters"]["ResponseContentDisposition"].startswith("attachment"))

        quitar = reverse("operaciones:quitar_entrega_tecnico", args=ids)
        asignacion.estado = "en_revision_supervisor"
        asignacion.save(update_fields=["estado"])
        self.assertEqual(self.client.post(quitar, **cabeceras).status_code, 409)
        asignacion.estado = "en_proceso"
        asignacion.save(update_fields=["estado"])
        respuesta = self.client.post(quitar, **cabeceras)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "data-form-entrega")
        self.assertFalse(self.almacen.exists(entrega.archivo.name))
        reemplazo = self.client.post(subir, {"archivo": SimpleUploadedFile("dos.pdf", b"dos")}, **cabeceras)
        self.assertEqual(reemplazo.status_code, 201)
        self.assertEqual(self.servicio.entregables.get(requerimiento=requisito).nombre_original, "dos.pdf")

    def test_tecnico_no_puede_borrar_archivo_administrativo_ni_abrir_adjuntos_libres(self):
        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        self.asignar_tecnico_en_proceso()
        requisito = self.servicio.requerimientos_entregables.create(titulo="Certificado")
        entrega_vinculada = EntregableServicio.objects.create(
            servicio=self.servicio, titulo="Certificado", archivo="certificado.pdf",
            nombre_original="certificado.pdf", requerimiento=requisito, adjuntado_por=self.admin,
        )
        libre = EntregableServicio.objects.create(
            servicio=self.servicio, titulo="Carta interna", archivo="carta.pdf",
            nombre_original="carta.pdf", adjuntado_por=self.admin,
        )
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        quitar = reverse("operaciones:quitar_entrega_tecnico", args=[self.servicio.pk, entrega_vinculada.pk])
        self.assertEqual(self.client.post(quitar, HTTP_X_CSRFTOKEN=self.token).status_code, 404)
        ver = reverse("operaciones:abrir_entrega_tecnico", args=[self.servicio.pk, libre.pk, "visualizar"])
        self.assertEqual(self.client.get(ver).status_code, 404)

    def test_fotos_extra_dependen_de_proyecto_especial_sin_bloquear_las_etapas(self):
        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        asignacion = self.asignar_tecnico_en_proceso()
        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        pantalla = reverse("operaciones:fotos_upload", args=[asignacion.pk])
        camara = reverse("operaciones:fotos_camera_take", args=[asignacion.pk])
        ajax = reverse("operaciones:fotos_upload_ajax", args=[asignacion.pk])
        presign = reverse("operaciones:fotos_presign", args=[asignacion.pk])
        finalizar = reverse("operaciones:fotos_finalize", args=[asignacion.pk])
        cabeceras = {"HTTP_X_CSRFTOKEN": self.token, "HTTP_ACCEPT": "application/json"}
        self.servicio.permitir_archivos_evidencias = True
        self.servicio.save(update_fields=["permitir_archivos_evidencias"])

        self.assertNotContains(self.client.get(pantalla), "Fotos Extra")
        self.assertContains(self.client.get(camara), "const especial = false")
        datos = {"lat": "-33", "lng": "-70", "acc": "5", "client_taken_at": timezone.now().isoformat()}
        for ruta, campos in (
            (camara, datos), (ajax, {}),
            (presign, {"filename": "extra.jpg", "size_bytes": "200"}),
            (finalizar, {"key": settings.DIRECT_UPLOADS_SAFE_PREFIX + "extra.jpg"}),
        ):
            respuesta = self.client.post(ruta, campos, **cabeceras)
            self.assertEqual(respuesta.status_code, 400)
            self.assertIn("proyectos especiales", respuesta.json()["error"])
        respuesta = self.client.post(pantalla, {"csrfmiddlewaretoken": self.token})
        self.assertRedirects(respuesta, pantalla, fetch_redirect_response=False)

        imagen = BytesIO()
        Image.new("RGB", (12, 12), "white").save(imagen, format="JPEG")
        campo = EvidenciaFoto._meta.get_field("imagen")
        with patch.object(campo, "storage", self.almacen):
            for etapa in ("antes", "durante", "despues"):
                requisito = RequisitoFoto.objects.create(
                    tecnico_sesion=asignacion, titulo=f"Foto {etapa}", etapa=etapa,
                )
                respuesta = self.client.post(camara, {
                    **datos, "req_id": requisito.pk,
                    "imagen": SimpleUploadedFile("foto.jpg", imagen.getvalue(), content_type="image/jpeg"),
                }, **cabeceras)
                self.assertEqual(respuesta.status_code, 200)
            asignacion.sesion.proyecto_especial = True
            asignacion.sesion.save(update_fields=["proyecto_especial"])
            self.assertContains(self.client.get(pantalla), "Fotos Extra")
            self.assertContains(self.client.get(camara), "const especial = true")
            respuesta = self.client.post(camara, {
                **datos, "titulo_manual": "Foto adicional",
                "imagen": SimpleUploadedFile("extra.jpg", imagen.getvalue(), content_type="image/jpeg"),
            }, **cabeceras)
            self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(asignacion.evidencias.filter(requisito__isnull=True, titulo_manual="Foto adicional").exists())

    def test_miniatura_de_requerimiento_compartida_solo_con_el_equipo_asignado(self):
        rol, _ = Rol.objects.get_or_create(nombre="usuario")
        self.tecnico.roles.add(rol)
        asignacion = self.asignar_tecnico_en_proceso()
        companero = get_user_model().objects.create_user(username="companero_miniatura")
        companero.roles.add(rol)
        self.servicio.trabajadores_asignados.add(companero)
        otra_asignacion = SesionFotoTecnico.objects.create(
            sesion=asignacion.sesion, tecnico=companero, estado="en_proceso",
        )
        propio = RequisitoFoto.objects.create(tecnico_sesion=asignacion, titulo="Mállá", etapa="despues")
        equivalente = RequisitoFoto.objects.create(tecnico_sesion=otra_asignacion, titulo="Malla", etapa="despues")
        evidencia = EvidenciaFoto.objects.create(
            tecnico_sesion=otra_asignacion, requisito=equivalente, imagen="fotos/compartida.jpg",
        )
        pantalla = reverse("operaciones:fotos_upload", args=[asignacion.pk])
        estado = reverse("operaciones:fotos_status_json", args=[asignacion.pk])
        foto = reverse("operaciones:foto_de_requerimiento", args=[evidencia.pk])

        self.client.force_login(self.tecnico, backend="django.contrib.auth.backends.ModelBackend")
        respuesta = self.client.get(pantalla)
        self.assertContains(respuesta, "Buscar requerimiento")
        self.assertContains(respuesta, f'src="{foto}"')
        self.assertIn(
            {"id": propio.pk, "global_done": True, "foto_id": evidencia.pk},
            self.client.get(estado).json()["requisitos"],
        )
        with patch.object(EvidenciaFoto._meta.get_field("imagen"), "storage", self.almacen):
            self.assertRedirects(self.client.get(foto), "/media/fotos/compartida.jpg", fetch_redirect_response=False)

        evidencia.ubicacion_referencia = {
            "latitud": "-26.327583", "longitud": "-70.620211", "direccion": "Chañaral",
        }
        evidencia.save(update_fields=["ubicacion_referencia"])
        with patch("operaciones.views_fotos._respuesta_foto_con_sello") as generar_sello:
            generar_sello.return_value = HttpResponse(b"foto sellada", content_type="image/jpeg")
            self.assertEqual(self.client.get(foto).content, b"foto sellada")
            generar_sello.assert_called_once_with(evidencia)

        self.servicio.trabajadores_asignados.remove(self.tecnico)
        self.assertEqual(self.client.get(foto).status_code, 404)
        self.assertEqual(self.client.get(estado).status_code, 404)

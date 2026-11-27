from django.test import SimpleTestCase

from .views import _es_referencia_vial, _indice_direccion_preferida


class DireccionPreferidaTests(SimpleTestCase):
    def test_prioriza_calle_y_ruta_sobre_plus_code(self):
        codigo = {"types": ["plus_code"], "formatted_address": "M9CH+XW Chañaral, Chile"}
        ruta = {"types": ["route"], "formatted_address": "C-120, Chañaral, Atacama, Chile"}
        calle = {"types": ["street_address"], "formatted_address": "Calle de prueba 15"}
        self.assertEqual(_indice_direccion_preferida([codigo, ruta]), 1)
        self.assertEqual(_indice_direccion_preferida([codigo, ruta, calle]), 2)
        self.assertEqual(_indice_direccion_preferida([codigo]), 0)
        self.assertIsNone(_indice_direccion_preferida([]))
        self.assertTrue(_es_referencia_vial(ruta))
        self.assertFalse(_es_referencia_vial(calle))
        self.assertFalse(_es_referencia_vial(codigo))

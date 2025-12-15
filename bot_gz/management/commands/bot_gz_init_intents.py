# bot_gz/management/commands/bot_gz_init_intents.py

from django.core.management.base import BaseCommand

from bot_gz.models import BotIntent, BotTrainingExample

INTENTS_BASE = [
    {
        "slug": "mis_liquidaciones",
        "nombre": "Mis liquidaciones de sueldo",
        "descripcion": "Permite al técnico consultar sus liquidaciones por mes y año.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "pásame mi liquidación de este mes",
            "quiero ver mi liquidación de enero",
            "muéstrame mis liquidaciones",
            "enviame la liquidación del mes pasado",
        ],
    },
    {
        "slug": "mi_contrato_vigente",
        "nombre": "Mi contrato vigente",
        "descripcion": "Muestra el contrato de trabajo vigente del técnico.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "pásame mi contrato de trabajo",
            "quiero ver mi contrato vigente",
            "envíame mi contrato",
            "muéstrame mis contratos antiguos y el actual",
        ],
    },
    {
        "slug": "mi_produccion_hasta_hoy",
        "nombre": "Mi producción hasta hoy",
        "descripcion": "Calcula la producción del técnico hasta la fecha o para un rango de fechas.",
        "scope": "tecnico",
        "requiere_revision_humana": True,
        "training": [
            "quiero saber cuánto llevo de producción hasta hoy",
            "cuál es mi producción acumulada este mes",
            "dime mi producción de este mes",
            "cuánto llevo producido al día de hoy",
        ],
    },
    {
        "slug": "mis_proyectos_rechazados",
        "nombre": "Mis proyectos rechazados",
        "descripcion": "Lista los servicios/proyectos rechazados para el técnico.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "qué proyectos tengo rechazados",
            "muéstrame mis servicios rechazados",
            "tengo proyectos rechazados este mes",
        ],
    },
    {
        "slug": "mis_proyectos_pendientes",
        "nombre": "Mis proyectos pendientes",
        "descripcion": "Lista los servicios asignados al técnico que aún no están finalizados.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "qué proyectos tengo pendientes",
            "qué servicios me faltan por terminar",
            "muéstrame mis servicios asignados",
        ],
    },
    {
        "slug": "mis_rendiciones_pendientes",
        "nombre": "Mis rendiciones pendientes",
        "descripcion": "Muestra las rendiciones de gastos del técnico pendientes por aprobación o por completar.",
        "scope": "tecnico",
        "requiere_revision_humana": True,
        "training": [
            "cuántas declaraciones tengo pendientes por aprobación",
            "qué rendiciones tengo pendientes",
            "mis gastos aún no aprobados",
        ],
    },
    {
        "slug": "info_sitio_id_claro",
        "nombre": "Información de sitio por ID Claro",
        "descripcion": "Entrega información de un sitio (dirección, accesos, Google Maps) a partir del ID Claro.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "necesito información del sitio id claro MA5694",
            "dame la dirección del sitio con id claro tal",
            "cuál es la ubicación del sitio xxxx",
        ],
    },
    {
        "slug": "cronograma_produccion_corte",
        "nombre": "Corte de producción",
        "descripcion": "Informa cuándo es el corte de producción y fechas relevantes de pago.",
        "scope": "tecnico",
        "requiere_revision_humana": False,
        "training": [
            "cuándo es el corte de la producción",
            "qué día cierran la producción",
            "hasta qué fecha cuenta la producción de este mes",
        ],
    },
    {
        "slug": "ayuda_rendicion_gastos",
        "nombre": "Asistente de rendición de gastos",
        "descripcion": "Guía paso a paso para crear una rendición de gastos por el bot.",
        "scope": "tecnico",
        "requiere_revision_humana": True,
        "training": [
            "quiero rendir un gasto",
            "ayúdame a hacer una rendición",
            "quiero declarar un gasto nuevo",
        ],
    },
]


class Command(BaseCommand):
    help = "Inicializa intents base y ejemplos de entrenamiento para el bot de GZ Services."

    def handle(self, *args, **options):
        self.stdout.write("Inicializando intents del bot GZ...")

        for item in INTENTS_BASE:
            slug = item["slug"]
            defaults = {
                "nombre": item["nombre"],
                "descripcion": item.get("descripcion", ""),
                "scope": item.get("scope", "tecnico"),
                "activo": True,
                "requiere_revision_humana": item.get(
                    "requiere_revision_humana", False
                ),
            }

            obj, created = BotIntent.objects.get_or_create(
                slug=slug,
                defaults=defaults,
            )

            if created:
                self.stdout.write(self.style.SUCCESS(f"  + Intent '{slug}' creado."))
            else:
                # Actualizamos campos clave por si modificamos descripción o scope
                changed = False
                for field, value in defaults.items():
                    if getattr(obj, field) != value:
                        setattr(obj, field, value)
                        changed = True
                if changed:
                    obj.save()
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ~ Intent '{slug}' ya existía, datos actualizados."
                        )
                    )
                else:
                    self.stdout.write(f"  = Intent '{slug}' ya existía (sin cambios).")

            # ===== Crear ejemplos de entrenamiento =====
            for txt in item.get("training", []):
                ex, ex_created = BotTrainingExample.objects.get_or_create(
                    intent=obj,
                    texto=txt,
                    defaults={
                        "locale": "es",
                        "activo": True,
                    },
                )
                if ex_created:
                    self.stdout.write(f"      + Ejemplo añadido: “{txt}”")

        self.stdout.write(self.style.SUCCESS("Intents base inicializados."))
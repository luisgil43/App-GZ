# bot_gz/management/commands/enviar_alertas_clima_diarias.py

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from bot_gz.services_clima import procesar_alertas_clima_diarias


class Command(BaseCommand):
    help = "Envía alertas diarias de clima, UV y radiación por Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fecha",
            type=str,
            default=None,
            help="Fecha en formato YYYY-MM-DD. Si no se indica, usa hoy.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Enviar/probar solo para un usuario específico.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="No envía Telegram, solo imprime resultado.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignora si ya fue enviada una alerta hoy.",
        )

    def handle(self, *args, **options):
        fecha_raw = options.get("fecha")
        fecha = None

        if fecha_raw:
            fecha = parse_date(fecha_raw)
            if not fecha:
                raise ValueError("Fecha inválida. Usa formato YYYY-MM-DD.")

        result = procesar_alertas_clima_diarias(
            fecha=fecha,
            user_id=options.get("user_id"),
            dry_run=options.get("dry_run"),
            force=options.get("force"),
        )

        self.stdout.write(self.style.SUCCESS("Resultado alertas clima:"))
        self.stdout.write(str(result))

        if result.get("dry_run") and result.get("dry_messages"):
            self.stdout.write("\nMensajes dry-run:")
            for item in result["dry_messages"]:
                self.stdout.write("=" * 80)
                self.stdout.write(f"Usuario: {item['usuario']}")
                self.stdout.write(f"Chat ID: {item['chat_id']}")
                self.stdout.write(item["mensaje"])

# operaciones/migrations/0016_requisito_norm_cleanup.py
import re
import unicodedata

from django.db import migrations, models
from django.db.models import Count, Q


def _norm_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def forwards(apps, schema_editor):
    Requisito = apps.get_model("operaciones", "RequisitoFoto")
    Evidencia = apps.get_model("operaciones", "EvidenciaFoto")

    # 1) Backfill de titulo_norm para todos
    batch = []
    for r in Requisito.objects.all().only("id", "titulo", "titulo_norm"):
        norm = _norm_title(r.titulo)
        if r.titulo_norm != norm:
            r.titulo_norm = norm
            batch.append(r)
        if len(batch) >= 1000:
            Requisito.objects.bulk_update(batch, ["titulo_norm"])
            batch = []
    if batch:
        Requisito.objects.bulk_update(batch, ["titulo_norm"])

    # 2) Deduplicación de activos por (tecnico_sesion, titulo_norm)
    dup_groups = (
        Requisito.objects
        .filter(activo=True)
        .values("tecnico_sesion_id", "titulo_norm")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
    )
    for g in dup_groups:
        items = list(
            Requisito.objects
            .filter(
                tecnico_sesion_id=g["tecnico_sesion_id"],
                titulo_norm=g["titulo_norm"],
                activo=True,
            )
            .order_by("id")
        )
        if not items:
            continue
        canonical = items[0]
        for extra in items[1:]:
            Evidencia.objects.filter(requisito_id=extra.id).update(requisito_id=canonical.id)
            extra.activo = False
            extra.save(update_fields=["activo"])

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('operaciones', '0014_alter_serviciocotizado_estado'),  # <-- AJUSTA ESTE NOMBRE
    ]
    operations = [
        migrations.AddField(
            model_name="requisitofoto",
            name="titulo_norm",
            field=models.CharField(max_length=220, default="", editable=False, db_index=True),
        ),
        migrations.RunPython(forwards, noop),
        migrations.AddConstraint(
            model_name="requisitofoto",
            constraint=models.UniqueConstraint(
                fields=["tecnico_sesion", "titulo_norm"],
                name="uq_req_norm_asig_activo",
                condition=Q(activo=True),
            ),
        ),
    ]
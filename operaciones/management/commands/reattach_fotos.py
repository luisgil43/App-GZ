# operaciones/management/commands/reattach_evidencias.py
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Prefetch, Q
from difflib import get_close_matches
import os
import re
import unicodedata

# 👇 Ajustado a TUS modelos y related_names actuales
from operaciones.models import (
    ServicioCotizado,
    SesionFotos,
    SesionFotoTecnico,
    RequisitoFoto,
    EvidenciaFoto,
)


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[_\-\.]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _candidatos(f: EvidenciaFoto) -> list[str]:
    """
    Saca posibles textos que apunten al requisito de la foto:
    - titulo_manual (si el técnico lo escribió)
    - nota
    - stem del nombre de archivo
    """
    cands = []
    posibles = [
        getattr(f, "titulo_manual", None),
        getattr(f, "nota", None),
    ]

    # stem de imagen
    file_name = None
    img = getattr(f, "imagen", None)
    if img:
        try:
            file_name = os.path.basename(getattr(img, "name", "") or str(img))
        except Exception:
            file_name = None
    if file_name:
        stem = os.path.splitext(file_name)[0]
        posibles.append(stem)

    # normaliza y dedup
    seen = set()
    for x in posibles:
        if not x:
            continue
        n = _norm(str(x))
        if n and n not in seen:
            seen.add(n)
            cands.append(n)
    return cands


class Command(BaseCommand):
    help = "Re-engancha evidencias huérfanas (marcadas como 'Extra') con sus RequisitoFoto."

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument("--servicio", type=int, help="ID de ServicioCotizado")
        g.add_argument("--sesion", type=int, help="ID de SesionFotos")

        parser.add_argument("--apply", action="store_true",
                            help="Aplica cambios (default: dry-run).")
        parser.add_argument("--threshold", type=float, default=0.85,
                            help="Umbral fuzzy 0-1 (default 0.85).")
        parser.add_argument("--asignacion", type=int, default=None,
                            help="Limitar a una SesionFotoTecnico.id")
        parser.add_argument("--verbose", action="store_true")

    def handle(self, *args, **opts):
        servicio_id = opts.get("servicio")
        sesion_id = opts.get("sesion")
        asignacion_id = opts.get("asignacion")
        apply_changes = opts["apply"]
        threshold = float(opts["threshold"])
        verbose = opts["verbose"]

        # 1) Resolver sesión
        if servicio_id:
            try:
                servicio = ServicioCotizado.objects.get(pk=servicio_id)
            except ServicioCotizado.DoesNotExist:
                raise CommandError(f"Servicio {servicio_id} no existe")
            sesion = getattr(servicio, "sesion_fotos", None)
            if not sesion:
                raise CommandError("El servicio no tiene SesionFotos asociada")
        else:
            try:
                sesion = SesionFotos.objects.get(pk=sesion_id)
            except SesionFotos.DoesNotExist:
                raise CommandError(f"Sesión {sesion_id} no existe")

        asign_qs = sesion.asignaciones.all()
        if asignacion_id:
            asign_qs = asign_qs.filter(id=asignacion_id)

        asign_qs = asign_qs.prefetch_related(
            Prefetch(
                "requisitos",
                queryset=RequisitoFoto.objects.all().order_by("orden", "id"),
            ),

            Prefetch(
                "evidencias",
                queryset=EvidenciaFoto.objects.all().order_by("tomada_en", "id"),
            ),
        )

        moved = ambiguous = 0
        total_orphans = 0

        tx = transaction.atomic() if apply_changes else None
        if tx:
            tx.__enter__()
        try:
            for asign in asign_qs:
                reqs = list(asign.requisitos.all())
                if not reqs:
                    if verbose:
                        self.stdout.write(
                            f"[asign {asign.id}] sin requisitos activos.")
                    continue

                req_by_norm = {_norm(r.titulo): r for r in reqs}
                req_norm_titles = list(req_by_norm.keys())

                evids = list(asign.evidencias.all())
                orphan = [e for e in evids if not getattr(
                    e, "requisito_id", None)]
                total_orphans += len(orphan)

                if verbose:
                    self.stdout.write(
                        f"[asign {asign.id}] huérfanas={len(orphan)} / evidencias={len(evids)} / reqs={len(reqs)}"
                    )

                # 1) exacto
                unresolved = []
                for ev in orphan:
                    linked = False
                    for cand in _candidatos(ev):
                        r = req_by_norm.get(cand)
                        if r:
                            if apply_changes:
                                ev.requisito = r
                                ev.save(update_fields=["requisito"])
                            moved += 1
                            linked = True
                            if verbose:
                                self.stdout.write(
                                    f"  ✓ exacto: ev#{ev.id} -> req#{r.id} ({r.titulo})")
                            break
                    if not linked:
                        unresolved.append(ev)

                # 2) fuzzy
                still = []
                for ev in unresolved:
                    chosen = None
                    for cand in _candidatos(ev):
                        m = get_close_matches(
                            cand, req_norm_titles, n=1, cutoff=threshold)
                        if m:
                            chosen = req_by_norm[m[0]]
                            break
                    if chosen:
                        if apply_changes:
                            ev.requisito = chosen
                            ev.save(update_fields=["requisito"])
                        moved += 1
                        if verbose:
                            self.stdout.write(
                                f"  ≈ fuzzy: ev#{ev.id} -> req#{chosen.id} ({chosen.titulo})")
                    else:
                        still.append(ev)

                # 3) por orden, solo si todas están huérfanas y # coincide
                if still and len(still) == len(orphan) and len(orphan) == len(reqs):
                    if verbose:
                        self.stdout.write(
                            "  ↔ 1:1 por orden (tomada_en vs orden)")
                    for ev, rq in zip(still, reqs):
                        if apply_changes:
                            ev.requisito = rq
                            ev.save(update_fields=["requisito"])
                        moved += 1
                    still = []

                ambiguous += len(still)
                if verbose and still:
                    ids = ", ".join(str(x.id) for x in still)
                    self.stdout.write(self.style.WARNING(
                        f"  ? sin resolver: {ids}"))

            self.stdout.write(
                self.style.SUCCESS(
                    f"[{'APPLY' if apply_changes else 'DRY'}] moved={moved}, ambiguous={ambiguous}, total_orphans_seen={total_orphans}"
                )
            )
            if not apply_changes and moved > 0:
                self.stdout.write(
                    "Sugerencia: vuelve a correr con --apply si el resumen te convence.")
        finally:
            if tx:
                tx.__exit__(None, None, None)

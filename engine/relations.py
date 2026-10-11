"""Détection des relations de clés (liens non orientés, docs/05 § 2).

Niveaux : N1 exact (0.90), N2 normalisé (0.75), N3 partiel (0.55), déclaré (1.0),
conventions Data Vault (0.95). Ajustements : clé +0.10, types compatibles +0.05,
types incompatibles −0.30, nom générique −0.40, colonne trop fréquente −0.30.

Blocking : on ne compare que les colonnes partageant un nom normalisé ou un token ;
les groupes plus grands que `max_bucket_size` sont ignorés (trop peu discriminants).
Une relation exige qu'au moins une des deux colonnes ressemble à une clé (`is_key` ou
motif de `key_patterns` : `id_`, `code_`, `num_`, `_ref`…), sinon `montant` ↔ `montant`
deviendrait une « relation ». Chaque objet garde au plus `max_relations_per_object`
relations suggérées (les meilleures).

Point d'entrée : `detect_relations(catalog, md, flow_edges, config)`.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from .catalog import Catalog, Column
from .inference import name_match_weight
from .normalize import types_compatible

LEVELS = {"EXACT": ("N1", 0.90), "NORMALIZED": ("N2", 0.75), "PARTIAL": ("N3", 0.55)}
ORIGIN_RANK = {"MANUAL": 5, "DECLARED": 4, "DATAVAULT": 3, "EXACT": 2, "NORMALIZED": 1, "PARTIAL": 0}


def score_columns(a: Column, b: Column, level: str, catalog: Catalog) -> tuple[float, list[str]]:
    """Score d'une paire de colonnes et liste des ajustements appliqués."""
    label, score = LEVELS[level]
    notes = [f"{label} — nom {'identique' if level == 'EXACT' else 'normalisé identique' if level == 'NORMALIZED' else 'partiel'}"]
    if a.is_key or b.is_key:
        score += 0.10
        notes.append("clé +0.10")
    if types_compatible(a.data_type, b.data_type):
        score += 0.05
        notes.append("types compatibles +0.05")
    else:
        score -= 0.30
        notes.append("types incompatibles −0.30")
    if a.generic or b.generic:
        score -= 0.40
        notes.append("nom générique −0.40")
    if a.norm in catalog.frequent or b.norm in catalog.frequent:
        score -= 0.30
        notes.append("colonne très fréquente −0.30")
    return round(max(0.0, min(1.0, score)), 3), notes


def _buckets(catalog: Catalog) -> tuple[dict, dict]:
    """Index nom normalisé → colonnes, et token → colonnes (hors colonnes ignorées)."""
    by_norm: dict[str, list] = defaultdict(list)
    by_token: dict[str, list] = defaultdict(list)
    for obj in catalog.objects.values():
        for col in obj.columns:
            if col.ignored:
                continue
            by_norm[col.norm].append((obj.id, col))
            if not col.generic:
                for tok in set(col.tokens):
                    if len(tok) >= 4:
                        by_token[tok].append((obj.id, col))
    return by_norm, by_token


def _keep_best(best: dict, a_id: str, a: Column, b_id: str, b: Column, level: str,
               catalog: Catalog, min_score: float, blocked: set) -> None:
    """Conserve le meilleur lien de colonnes par couple d'objets."""
    if a_id == b_id or not (a.key_like or b.key_like):
        return
    if a_id > b_id:
        a_id, a, b_id, b = b_id, b, a_id, a
    if (a_id, b_id) in blocked:
        return
    score, notes = score_columns(a, b, level, catalog)
    if score < min_score:
        return
    current = best.get((a_id, b_id))
    if current is None or score > current["score"]:
        best[(a_id, b_id)] = {
            "kind": "RELATION", "source_object": a_id, "target_object": b_id,
            "source_column": a.name, "target_column": b.name, "origin": level, "score": score,
            "explanation": f"Relation suggérée (score {score:.2f}) : `{a.name}` ↔ `{b.name}` — "
                           + ", ".join(notes) + ".",
            "job": None, "transformation": None, "details": {"level": LEVELS[level][0]}, "columns": [],
        }


def detect_relations(catalog: Catalog, md: dict, flow_edges: list[dict], config: dict) -> tuple[list[dict], dict]:
    """Détecte les relations et renvoie (arêtes, statistiques)."""
    cfg = config["relations"]
    blocked = {tuple(sorted((e["source_object"], e["target_object"]))) for e in flow_edges}
    best: dict[tuple[str, str], dict] = {}
    by_norm, by_token = _buckets(catalog)
    skipped_buckets = 0

    for items in by_norm.values():
        if len(items) > cfg["max_bucket_size"]:
            skipped_buckets += 1
            continue
        for (a_id, a), (b_id, b) in combinations(items, 2):
            level = "EXACT" if a.name == b.name else "NORMALIZED"
            _keep_best(best, a_id, a, b_id, b, level, catalog, cfg["min_score"], blocked)

    for items in by_token.values():
        if len(items) > cfg["max_bucket_size"]:
            skipped_buckets += 1
            continue
        for (a_id, a), (b_id, b) in combinations(items, 2):
            if a.norm == b.norm or (a_id, b_id) in best or (b_id, a_id) in best:
                continue
            if name_match_weight(a.norm, b.norm) > 0:
                _keep_best(best, a_id, a, b_id, b, "PARTIAL", catalog, cfg["min_score"], blocked)

    for edge in datavault_relations(catalog) + declared_relations(md, catalog):
        key = (edge["source_object"], edge["target_object"])
        current = best.get(key)
        if current is None or ORIGIN_RANK[edge["origin"]] > ORIGIN_RANK[current["origin"]]:
            best[key] = edge

    edges, pruned = prune_per_object(list(best.values()), cfg["max_relations_per_object"])
    stats = defaultdict(int)
    for edge in edges:
        stats[edge["details"].get("level", edge["origin"])] += 1
    stats["groupes_ignores_trop_frequents"] = skipped_buckets
    stats["relations_ecartees_plafond"] = pruned
    return edges, dict(stats)


def prune_per_object(edges: list[dict], limit: int) -> tuple[list[dict], int]:
    """Garde une relation suggérée seulement si elle est dans le top `limit` de ses deux objets.

    Les relations déclarées et Data Vault sont toujours conservées.
    """
    ranked: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        ranked[edge["source_object"]].append(edge)
        ranked[edge["target_object"]].append(edge)
    top: dict[str, set[int]] = {}
    for obj_id, items in ranked.items():
        items.sort(key=lambda e: (-e["score"], e["source_object"], e["target_object"]))
        top[obj_id] = {id(e) for e in items[:limit]}
    kept = [e for e in edges if e["origin"] in ("DECLARED", "DATAVAULT")
            or (id(e) in top[e["source_object"]] and id(e) in top[e["target_object"]])]
    return kept, len(edges) - len(kept)


def declared_relations(md: dict, catalog: Catalog) -> list[dict]:
    """Relations `relations[]` du JSON (origine DECLARED, score 1.0)."""
    edges = []
    for rel in md["relations"]:
        if rel["a"] not in catalog.objects or rel["b"] not in catalog.objects:
            continue
        a, ac, b, bc = rel["a"], rel["a_column"], rel["b"], rel["b_column"]
        if a > b:
            a, ac, b, bc = b, bc, a, ac
        edges.append({
            "kind": "RELATION", "source_object": a, "target_object": b,
            "source_column": ac, "target_column": bc, "origin": "DECLARED", "score": 1.0,
            "explanation": "Relation déclarée dans le JSON" + (f" : {rel['comment']}" if rel["comment"] else "."),
            "job": None, "transformation": None, "details": {"level": "DECLARED"}, "columns": [],
        })
    return edges


def datavault_relations(catalog: Catalog) -> list[dict]:
    """Conventions Data Vault : hub_<e> ↔ sat_<e>_* et hub_<e> ↔ lnk_* via `hk_<e>`."""
    hubs = {}
    for obj in catalog.objects.values():
        if obj.name.startswith("hub_"):
            hubs[obj.name[4:]] = obj
    edges = []
    for obj in catalog.objects.values():
        is_sat, is_lnk = obj.name.startswith("sat_"), obj.name.startswith("lnk_")
        if not (is_sat or is_lnk):
            continue
        for col in obj.columns:
            if not col.name.startswith("hk_"):
                continue
            entity = col.name[3:]
            hub = hubs.get(entity)
            if hub is None or hub.column(col.name) is None:
                continue
            if is_sat and not obj.name.startswith(f"sat_{entity}"):
                continue
            a, b = sorted((hub.id, obj.id))
            edges.append({
                "kind": "RELATION", "source_object": a, "target_object": b,
                "source_column": col.name, "target_column": col.name, "origin": "DATAVAULT",
                "score": 0.95, "job": None, "transformation": None, "columns": [],
                "explanation": f"Convention Data Vault : `{col.name}` relie {'hub ↔ satellite' if is_sat else 'hub ↔ link'}.",
                "details": {"level": "DATAVAULT"},
            })
    return edges

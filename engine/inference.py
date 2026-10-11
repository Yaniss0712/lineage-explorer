"""⭐ Inférence du lineage à partir de la structure seule (docs/05 § 1.2).

Algorithme, pour chaque objet cible C (BV ou MART) :
1. Sources candidates dans les couches en amont (le sens vient des couches).
2. Blocking : on ne garde que les objets partageant au moins `min_shared_columns`
   colonnes rares (index inversé des noms normalisés).
3. Correspondance colonne à colonne : nom normalisé identique (1.0), inclusion
   de tokens (0.6), similarité difflib ≥ 0.85 (0.5), types vérifiés.
4. Score : couverture pondérée par l'IDF × 0.7 + bonus (clé, nom d'objet, domaine)
   − pénalité de saut de couche.
5. Sélection gloutonne des sources (gain marginal minimal, `max_sources`).
6. Explication en français et lineage colonne inféré.

Point d'entrée : `infer_flows(catalog, config)` → liste d'arêtes (dict).
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from functools import lru_cache

from .catalog import Catalog, Column, Obj
from .layers import layer_rank

MATCH_EXACT = 1.0
MATCH_TOKENS = 0.6
MATCH_FUZZY = 0.5
FUZZY_THRESHOLD = 0.85

MATCH_LABELS = {MATCH_EXACT: "identique", MATCH_TOKENS: "partielle", MATCH_FUZZY: "proche"}


def allowed_source_layers(target_layer: str) -> dict[str, bool]:
    """Couches sources autorisées pour une cible → {couche: saut_de_couche}."""
    if target_layer == "BV":
        return {"LAC": False, "BV": False}
    if target_layer == "MART":
        return {"BV": False, "LAC": True}
    return {}


@lru_cache(maxsize=1_000_000)
def name_match_weight(a: str, b: str) -> float:
    """Poids de correspondance entre deux noms normalisés (mis en cache : les noms se répètent)."""
    if a == b:
        return MATCH_EXACT
    if _tokens_included(tuple(a.split("_")), tuple(b.split("_"))):
        return MATCH_TOKENS
    if 2 * min(len(a), len(b)) / (len(a) + len(b)) < FUZZY_THRESHOLD:
        return 0.0                                   # ratio difflib forcément trop faible
    matcher = difflib.SequenceMatcher(None, a, b)
    if matcher.real_quick_ratio() < FUZZY_THRESHOLD or matcher.quick_ratio() < FUZZY_THRESHOLD:
        return 0.0
    return MATCH_FUZZY if matcher.ratio() >= FUZZY_THRESHOLD else 0.0


def match_column(target: Column, source_obj: Obj) -> tuple[float, Column | None]:
    """Meilleure correspondance d'une colonne cible dans un objet source."""
    best_weight, best_col = 0.0, None
    for col in source_obj.columns:
        if col.ignored:
            continue
        weight = name_match_weight(target.norm, col.norm)
        if not weight:
            continue
        if not _families_ok(target.family, col.family):
            continue
        if weight > best_weight or (weight == best_weight and col.name == target.name):
            best_weight, best_col = weight, col
            if weight == MATCH_EXACT and col.name == target.name:
                break
    return best_weight, best_col


def _tokens_included(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Vrai si les tokens de l'un sont inclus dans ceux de l'autre (tokens de ≥ 3 lettres)."""
    sa = {t for t in a if len(t) >= 3}
    sb = {t for t in b if len(t) >= 3}
    if not sa or not sb:
        return False
    return sa <= sb or sb <= sa


def _families_ok(a: str, b: str) -> bool:
    return a == b or "other" in (a, b)


def names_similar(a: Obj, b: Obj) -> bool:
    """Noms d'objets proches après nettoyage (`contrats_raw` → `contrats`)."""
    x, y = a.clean_name, b.clean_name
    if not x or not y:
        return False
    if x == y or x in y or y in x:
        return True
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.8


def candidate_sources(target: Obj, catalog: Catalog, min_shared: int, max_candidates: int = 50) -> dict[str, int]:
    """Blocking : objets des couches amont partageant ≥ `min_shared` colonnes rares
    (au plus `max_candidates`, ceux qui en partagent le plus)."""
    allowed = allowed_source_layers(target.layer)
    if not allowed:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for norm in {c.norm for c in target.columns if not c.ignored and not c.generic}:
        for other_id in catalog.index.get(norm, ()):
            if other_id != target.id and catalog.objects[other_id].layer in allowed:
                counts[other_id] += 1
    kept = sorted(((n, oid) for oid, n in counts.items() if n >= min_shared), reverse=True)
    return {oid: n for n, oid in kept[:max_candidates]}


def score_pair(source: Obj, target: Obj, catalog: Catalog, config: dict) -> dict:
    """Calcule le score d'un couple (source, cible) avec le détail des correspondances."""
    weights = config["inference"]["weights"]
    skip = allowed_source_layers(target.layer).get(source.layer, False)
    total_idf, covered_idf = 0.0, 0.0
    matches, unexplained = [], []
    key_found = False
    for col in target.columns:
        if col.ignored:
            continue
        idf = catalog.idf(col.norm)
        total_idf += idf
        weight, src_col = match_column(col, source)
        if src_col is None:
            unexplained.append(col.name)
            continue
        covered_idf += idf * weight
        key_found = key_found or (col.is_key and weight == MATCH_EXACT)
        matches.append({"source": src_col.name, "target": col.name, "weight": weight,
                        "idf": round(idf, 3), "df": catalog.df.get(col.norm, 1),
                        "is_key": col.is_key or src_col.is_key})
    coverage = covered_idf / total_idf if total_idf else 0.0
    same_name = names_similar(source, target)
    same_domain = bool(source.domain) and source.domain == target.domain
    score = coverage * weights["coverage"]
    score += weights["key_found"] if key_found else 0.0
    score += weights["object_name_similar"] if same_name else 0.0
    score += weights["same_domain"] if same_domain else 0.0
    score += weights["layer_skip_penalty"] if skip else 0.0
    return {
        "source": source.id, "target": target.id,
        "score": round(max(0.0, min(1.0, score)), 3),
        "coverage": round(coverage, 3), "total_idf": total_idf,
        "matches": matches, "unexplained": unexplained,
        "key_found": key_found, "same_name": same_name,
        "same_domain": source.domain if same_domain else None, "layer_skip": skip,
    }


def explain(pair: dict, nb_columns: int) -> str:
    """Texte affiché dans l'interface : pourquoi ce flux est proposé."""
    exact = [m for m in pair["matches"] if m["weight"] == MATCH_EXACT]
    partial = [m for m in pair["matches"] if m["weight"] < MATCH_EXACT]
    parts = [f"Flux proposé (score {pair['score']:.2f}) : {len(pair['matches'])} colonne(s) "
             f"sur {nb_columns} retrouvée(s)"]
    keys = sorted((m for m in exact if m["is_key"]), key=lambda m: m["df"])
    if keys:
        k = keys[0]
        parts[0] += f", dont la clé `{k['target']}` (présente dans {k['df']} objet(s))"
    parts[0] += "."
    if partial:
        desc = ", ".join(f"`{m['source']}` ↔ `{m['target']}`" for m in partial[:4])
        parts.append(f"Correspondance partielle {desc}.")
    if pair["same_name"]:
        parts.append("Noms d'objets proches.")
    if pair["same_domain"]:
        parts.append(f"Domaine commun `{pair['same_domain']}`.")
    if pair["layer_skip"]:
        parts.append("Saut de couche (LAC → MART), score pénalisé.")
    if pair["unexplained"]:
        parts.append("Non expliquées : " + ", ".join(f"`{c}`" for c in pair["unexplained"][:6])
                     + ("…" if len(pair["unexplained"]) > 6 else "") + ".")
    return " ".join(parts)


def select_sources(target: Obj, pairs: list[dict], config: dict) -> list[dict]:
    """Sélection gloutonne : on ne garde une source que si elle explique de nouvelles colonnes."""
    inf = config["inference"]
    chosen: list[dict] = []
    covered: dict[str, float] = {}
    for pair in sorted(pairs, key=lambda p: (-p["score"], p["source"])):
        if pair["score"] < inf["store_min_score"] or len(chosen) >= inf["max_sources"]:
            break
        if not chosen:
            gain = 1.0
        else:
            new = sum(m["idf"] * max(0.0, m["weight"] - covered.get(m["target"], 0.0))
                      for m in pair["matches"])
            gain = new / pair["total_idf"] if pair["total_idf"] else 0.0
        if gain < inf["min_marginal_gain"]:
            continue
        pair["marginal_gain"] = round(gain, 3)
        chosen.append(pair)
        for m in pair["matches"]:
            covered[m["target"]] = max(covered.get(m["target"], 0.0), m["weight"])
    return chosen


def _bv_to_bv_ok(source: Obj, target: Obj, pair: dict, config: dict) -> bool:
    """BV → BV autorisé si le score est élevé et que la source est plus petite que la cible."""
    if not (source.layer == "BV" and target.layer == "BV"):
        return True
    return (pair["score"] >= config["inference"]["bv_to_bv_min_score"]
            and len(source.columns) < len(target.columns))


def infer_flows(catalog: Catalog, config: dict) -> list[dict]:
    """Infère les flux de lineage pour tout le catalogue → arêtes objet + colonnes."""
    inf = config["inference"]
    edges: list[dict] = []
    for target in catalog.objects.values():
        candidates = candidate_sources(target, catalog, inf["min_shared_columns"], inf["max_candidates"])
        pairs = []
        for source_id in candidates:
            source = catalog.objects[source_id]
            pair = score_pair(source, target, catalog, config)
            if _bv_to_bv_ok(source, target, pair, config):
                pairs.append(pair)
        nb_cols = len([c for c in target.columns if not c.ignored])
        for pair in select_sources(target, pairs, config):
            edges.append(_flow_edge(pair, explain(pair, nb_cols)))
    return edges


def _flow_edge(pair: dict, explanation: str) -> dict:
    """Construit l'arête objet (avec ses correspondances colonne) d'un flux inféré."""
    return {
        "kind": "FLOW", "source_object": pair["source"], "target_object": pair["target"],
        "origin": "INFERRED", "score": pair["score"], "explanation": explanation,
        "job": None, "transformation": None,
        "details": {"coverage": pair["coverage"], "unexplained": pair["unexplained"],
                    "marginal_gain": pair.get("marginal_gain")},
        "columns": [
            {"source": m["source"], "target": m["target"],
             "transformation": None if m["weight"] == MATCH_EXACT and m["source"] == m["target"]
             else f"correspondance {MATCH_LABELS[m['weight']]}",
             "score": round(pair["score"] * m["weight"], 3)}
            for m in pair["matches"]
        ],
    }

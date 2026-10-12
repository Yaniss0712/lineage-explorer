"""Suggestions de liens entre objets (aucun lien n'est créé automatiquement).

Une suggestion dit « ces deux tables ont l'air liées, voici pourquoi ». C'est l'équipe qui
crée le lien (dans le sens qu'elle choisit) ou qui écarte la suggestion.

Sources de suggestions :
1. **Structure** (toujours) : colonnes communes (même nom normalisé), pondérées par leur rareté.
   score = 0,70 × recouvrement + 0,15 × noms d'objets proches + 0,15 × min(1, nb_communes / 4)
   où recouvrement = Σ rareté(colonnes communes) / min(Σ rareté(colonnes de A), Σ rareté(colonnes de B)).
   Les colonnes techniques (« ignorées ») et génériques (id, code, url…) ne comptent pas.
2. **Indications** du fichier importé : `flows[]` (flux connus ou lus dans le SQL, avec sens et
   colonnes) et `relations[]` (jointures connues). Score 0,95 / 0,90.

Point d'entrée : `compute_suggestions(catalog, md, settings)` → liste de dicts.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from itertools import combinations

from .catalog import Catalog, Obj
from .normalize import types_compatible

ORIGIN_LABEL = {"STRUCTURE": "colonnes communes", "SQL": "requête SQL", "DECLARE": "fichier importé"}


def pair_key(a: str, b: str) -> str:
    """Clé d'une paire d'objets, indépendante de l'ordre."""
    return "|".join(sorted((a, b)))


def names_similar(a: Obj, b: Obj) -> bool:
    x, y = a.clean_name, b.clean_name
    if not x or not y:
        return False
    return x == y or x in y or y in x or difflib.SequenceMatcher(None, x, y).ratio() >= 0.8


def _weights(catalog: Catalog) -> dict[str, float]:
    """Poids d'un objet : somme des raretés de ses colonnes significatives."""
    result = {}
    for obj in catalog.objects.values():
        norms = {c.norm for c in obj.columns if not c.ignored and not c.generic}
        result[obj.id] = sum(catalog.idf(n) for n in norms) or 1.0
    return result


def structural_suggestions(catalog: Catalog, settings: dict) -> dict[str, dict]:
    """Suggestions issues des colonnes communes."""
    buckets: dict[str, list] = defaultdict(list)
    for obj in catalog.objects.values():
        seen = set()
        for col in obj.columns:
            if col.ignored or col.generic or col.norm in seen:
                continue
            seen.add(col.norm)
            buckets[col.norm].append((obj.id, col))
    shared: dict[tuple[str, str], list] = defaultdict(list)
    for norm, items in buckets.items():
        if len(items) > settings["max_bucket_size"]:
            continue
        for (a_id, a_col), (b_id, b_col) in combinations(items, 2):
            if a_id > b_id:
                a_id, a_col, b_id, b_col = b_id, b_col, a_id, a_col
            shared[(a_id, b_id)].append((norm, a_col, b_col))
    weights = _weights(catalog)
    result = {}
    for (a_id, b_id), cols in shared.items():
        a, b = catalog.objects[a_id], catalog.objects[b_id]
        overlap = sum(catalog.idf(n) for n, _, _ in cols) / min(weights[a_id], weights[b_id])
        similar = names_similar(a, b)
        score = 0.70 * min(1.0, overlap) + (0.15 if similar else 0.0) + 0.15 * min(1.0, len(cols) / 4)
        columns = [{"a": ac.name, "b": bc.name, "type_a": ac.data_type, "type_b": bc.data_type,
                    "mismatch": not types_compatible(ac.data_type, bc.data_type), "df": catalog.df.get(n, 1)}
                   for n, ac, bc in sorted(cols, key=lambda c: catalog.df.get(c[0], 1))]
        result[pair_key(a_id, b_id)] = {
            "key": pair_key(a_id, b_id), "a": a_id, "b": b_id, "score": round(min(1.0, score), 3),
            "origin": "STRUCTURE", "kind": None, "direction": None, "columns": columns, "mapping": [],
            "similar_names": similar,
        }
    return result


def _top_per_object(items: dict[str, dict], settings: dict) -> dict[str, dict]:
    """Garde une suggestion si elle est dans les meilleures de l'un des deux objets."""
    ranked: dict[str, list] = defaultdict(list)
    for sug in items.values():
        if sug["score"] >= settings["min_score"] or sug["origin"] != "STRUCTURE":
            ranked[sug["a"]].append(sug)
            ranked[sug["b"]].append(sug)
    kept = {}
    for sugs in ranked.values():
        sugs.sort(key=lambda s: (-s["score"], s["key"]))
        for sug in sugs[:settings["max_per_object"]]:
            kept[sug["key"]] = sug
    for sug in items.values():                              # les indications sont toujours gardées
        if sug["origin"] != "STRUCTURE":
            kept[sug["key"]] = sug
    return kept


def hint_suggestions(md: dict, catalog: Catalog) -> list[dict]:
    """Indications du fichier importé (flux connus, jointures connues)."""
    hints = []
    for flow in md["flows"]:
        if flow["source"] in catalog.objects and flow["target"] in catalog.objects and flow["source"] != flow["target"]:
            hints.append({"a": flow["source"], "b": flow["target"], "score": 0.95, "origin": flow["origin"],
                          "kind": "FLOW", "direction": flow["source"],
                          "mapping": [{"source": m["source"], "target": m["target"], "transformation": m["transformation"]}
                                      for m in flow["column_mapping"]],
                          "job": flow.get("job")})
    for rel in md["relations"]:
        if rel["a"] in catalog.objects and rel["b"] in catalog.objects and rel["a"] != rel["b"]:
            mapping = [{"source": rel["a_column"], "target": rel["b_column"], "transformation": None}] \
                if rel["a_column"] and rel["b_column"] else []
            hints.append({"a": rel["a"], "b": rel["b"], "score": 0.90, "origin": "DECLARE", "kind": "JOIN",
                          "direction": rel["a"], "mapping": mapping, "comment": rel.get("comment")})
    return hints


def explain(sug: dict) -> str:
    """Texte « pourquoi ? » affiché dans l'interface."""
    parts = []
    if sug["origin"] != "STRUCTURE":
        what = "Flux" if sug["kind"] == "FLOW" else "Jointure"
        parts.append(f"{what} indiqué par {ORIGIN_LABEL[sug['origin']]}"
                     + (f" ({sug['job']})" if sug.get("job") else "") + ".")
    if sug["columns"]:
        names = [c["a"] if c["a"] == c["b"] else f"{c['a']} = {c['b']}" for c in sug["columns"][:6]]
        parts.append(f"{len(sug['columns'])} colonne(s) commune(s) : " + ", ".join(names)
                     + ("…" if len(sug["columns"]) > 6 else "") + ".")
        mismatch = [c["a"] for c in sug["columns"] if c["mismatch"]]
        if mismatch:
            parts.append("Types différents pour : " + ", ".join(mismatch) + ".")
    if sug.get("similar_names"):
        parts.append("Noms de tables proches.")
    return " ".join(parts)


def compute_suggestions(catalog: Catalog, md: dict, settings: dict) -> list[dict]:
    """Toutes les suggestions (structure + indications), triées par score décroissant."""
    items = structural_suggestions(catalog, settings)
    for hint in hint_suggestions(md, catalog):
        key = pair_key(hint["a"], hint["b"])
        base = items.get(key, {"columns": [], "similar_names": False})
        a, b = sorted((hint["a"], hint["b"]))
        items[key] = {**hint, "key": key, "a": a, "b": b, "columns": base["columns"],
                      "similar_names": base["similar_names"]}
    kept = _top_per_object(items, settings)
    result = sorted(kept.values(), key=lambda s: (-s["score"], s["key"]))
    for sug in result:
        sug["explanation"] = explain(sug)
    return result

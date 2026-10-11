"""Attribution des couches (LAC / BV / MART) selon les règles de `config.json`.

- `assign_layer(obj, rules)` : couche explicite de l'objet, sinon premier motif
  `database_pattern` qui correspond, sinon `AUTRE`.
- `LAYER_ORDER` : ordre des couches, qui donne le sens des flux inférés.
- `domain(database, rules)` : domaine métier de la base (`lac_pret` → `pret`).
"""

from __future__ import annotations

import re

LAYER_ORDER = {"LAC": 0, "BV": 1, "MART": 2}
OTHER = "AUTRE"


def compile_rules(rules: list[dict]) -> list[tuple[str, re.Pattern]]:
    """Compile les motifs de couches."""
    return [(rule["code"].upper(), re.compile(rule["database_pattern"], re.IGNORECASE)) for rule in rules]


def assign_layer(obj: dict, compiled: list[tuple[str, re.Pattern]]) -> str:
    """Renvoie la couche d'un objet."""
    explicit = (obj.get("layer") or "").upper()
    if explicit:
        return explicit
    database = obj.get("database") or ""
    for code, pattern in compiled:
        if pattern.search(database):
            return code
    return OTHER


def layer_rank(layer: str) -> int:
    """Rang de la couche (AUTRE = -1, non ordonnée)."""
    return LAYER_ORDER.get(layer, -1)


def domain(database: str, compiled: list[tuple[str, re.Pattern]]) -> str:
    """Domaine d'une base : nom de base sans le préfixe de couche."""
    database = (database or "").lower()
    for _code, pattern in compiled:
        match = pattern.search(database)
        if match and match.start() == 0:
            return database[match.end():].strip("_")
    return database

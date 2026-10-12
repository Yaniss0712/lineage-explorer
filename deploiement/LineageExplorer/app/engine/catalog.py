"""Catalogue préparé : objets enrichis (noms normalisés, familles de types) et fréquences des colonnes.

Construit une fois par build et utilisé par le calcul des suggestions (`suggestions.py`)
et par l'index de recherche (`store.py`).

Point d'entrée : `build_catalog(metadata, settings)` où `settings` contient les réglages
`suggestions` de la configuration, éventuellement complétés par l'équipe (colonnes ignorées).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .normalize import clean_object_name, normalize_column, simplify, tokens, type_family


@dataclass
class Column:
    name: str
    data_type: str | None
    description: str
    position: int
    norm: str                 # nom normalisé (sans accents, préfixes/suffixes retirés)
    tokens: tuple[str, ...]
    family: str               # string | number | date | bool | other
    ignored: bool             # colonne technique (réglage « colonnes ignorées »)
    generic: bool             # nom trop générique pour suggérer un lien (id, code, url…)


@dataclass
class Obj:
    id: str
    database: str
    name: str
    type: str
    clean_name: str
    description: str
    owner: str
    sql: str | None
    columns: list[Column] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


@dataclass
class Catalog:
    objects: dict[str, Obj]
    df: dict[str, int]                      # nom normalisé → nombre d'objets qui le portent

    @property
    def size(self) -> int:
        return len(self.objects)

    def idf(self, norm: str) -> float:
        """Rareté d'un nom de colonne : log(1 + N / nb_objets_portant)."""
        return math.log(1 + self.size / max(1, self.df.get(norm, 1)))


def build_catalog(md: dict, settings: dict) -> Catalog:
    """Prépare le catalogue à partir des métadonnées normalisées par `ingest`."""
    prefixes, suffixes = settings["strip_prefixes"], settings["strip_suffixes"]
    ignored = {simplify(n) for n in settings["ignore_columns"]}
    generic = {simplify(n) for n in settings["generic_names"]}
    objects: dict[str, Obj] = {}
    df: dict[str, int] = defaultdict(int)
    for raw in md["objects"]:
        if not raw["id"] or raw["id"] in objects:
            continue
        obj = Obj(id=raw["id"], database=raw["database"], name=raw["name"], type=raw["type"],
                  clean_name=clean_object_name(raw["name"], settings["object_name_noise"]),
                  description=raw["description"], owner=raw["owner"], sql=raw["sql"])
        seen = set()
        for col in raw["columns"]:
            simple = simplify(col["name"])
            norm = normalize_column(col["name"], prefixes, suffixes)
            obj.columns.append(Column(
                name=col["name"], data_type=col["type"], description=col["description"], position=col["position"],
                norm=norm, tokens=tuple(tokens(norm)), family=type_family(col["type"]),
                ignored=simple in ignored, generic=simple in generic or norm in generic))
            if norm not in seen:
                seen.add(norm)
                df[norm] += 1
        objects[obj.id] = obj
    return Catalog(objects=objects, df=dict(df))

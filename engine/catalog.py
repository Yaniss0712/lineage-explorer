"""Catalogue préparé : objets enrichis (couche, domaine, noms normalisés) et statistiques.

Le catalogue est construit une seule fois au build et partagé par l'inférence
du lineage (`inference.py`) et la détection des relations (`relations.py`).

Point d'entrée : `build_catalog(metadata, config)`.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import layers as layers_mod
from .normalize import clean_object_name, normalize_column, simplify, tokens, type_family


@dataclass
class Column:
    """Colonne enrichie."""
    name: str
    data_type: str | None
    is_key: bool
    description: str
    position: int
    norm: str
    tokens: tuple[str, ...]
    family: str
    ignored: bool
    generic: bool
    key_like: bool = False             # clé connue ou nom de clé (id_, code_, num_, _ref…)


@dataclass
class Obj:
    """Objet (table / vue) enrichi."""
    id: str
    database: str
    name: str
    type: str
    layer: str
    domain: str
    clean_name: str
    description: str
    owner: str
    sql: str | None
    columns: list[Column] = field(default_factory=list)

    def column(self, name: str) -> Column | None:
        """Colonne par nom brut."""
        for col in self.columns:
            if col.name == name:
                return col
        return None


@dataclass
class Catalog:
    """Ensemble des objets + index inversé des noms normalisés."""
    objects: dict[str, Obj]
    df: dict[str, int]                 # nom normalisé → nb d'objets qui le portent
    index: dict[str, set[str]]         # nom normalisé (non générique, non ignoré) → objets
    frequent: set[str]                 # noms normalisés jugés trop fréquents

    @property
    def size(self) -> int:
        return len(self.objects)

    def idf(self, norm: str) -> float:
        """Rareté d'un nom de colonne : log(1 + N / nb_objets_portant)."""
        return math.log(1 + self.size / max(1, self.df.get(norm, 1)))


def build_catalog(md: dict, config: dict) -> Catalog:
    """Prépare le catalogue à partir des métadonnées normalisées par `ingest`."""
    rel_cfg = config["relations"]
    inf_cfg = config["inference"]
    prefixes = rel_cfg["strip_prefixes"]
    suffixes = rel_cfg["strip_suffixes"]
    ignored = {simplify(name) for name in rel_cfg["ignore_columns"]}
    generic = {simplify(name) for name in rel_cfg["generic_names"]}
    compiled = layers_mod.compile_rules(config["layers"])
    key_patterns = [re.compile(p) for p in rel_cfg.get("key_patterns", [])]

    objects: dict[str, Obj] = {}
    df: dict[str, int] = defaultdict(int)
    for raw in md["objects"]:
        if not raw["id"] or raw["id"] in objects:
            continue
        obj = Obj(
            id=raw["id"], database=raw["database"], name=raw["name"], type=raw["type"],
            layer=layers_mod.assign_layer(raw, compiled),
            domain=layers_mod.domain(raw["database"], compiled),
            clean_name=clean_object_name(raw["name"], inf_cfg["object_name_noise"]),
            description=raw["description"], owner=raw["owner"], sql=raw["sql"],
        )
        seen_norms = set()
        for col in raw["columns"]:
            simple = simplify(col["name"])
            norm = normalize_column(col["name"], prefixes, suffixes)
            obj.columns.append(Column(
                name=col["name"], data_type=col["type"], is_key=col["is_key"],
                description=col["description"], position=col["position"],
                norm=norm, tokens=tuple(tokens(norm)), family=type_family(col["type"]),
                ignored=simple in ignored, generic=simple in generic or norm in generic,
                key_like=col["is_key"] or any(p.search(simple) for p in key_patterns),
            ))
            if norm not in seen_norms:
                seen_norms.add(norm)
                df[norm] += 1
        objects[obj.id] = obj

    size = len(objects)
    limit = max(rel_cfg["max_column_frequency"] * size, rel_cfg["frequency_floor"])
    frequent = {norm for norm, count in df.items() if count > limit}

    index: dict[str, set[str]] = defaultdict(set)
    for obj in objects.values():
        for col in obj.columns:
            if col.ignored or col.generic or col.norm in frequent:
                continue
            index[col.norm].add(obj.id)
    return Catalog(objects=objects, df=dict(df), index=dict(index), frequent=frequent)

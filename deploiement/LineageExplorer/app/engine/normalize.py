"""Normalisation des noms (colonnes, objets) et compatibilité des types.

Fonctions principales :
- `simplify(text)`        : minuscules, sans accents, séparateurs unifiés en `_` ;
- `normalize_column(name, prefixes, suffixes)` : nom normalisé utilisé pour comparer ;
- `tokens(name)`          : découpage en mots ;
- `type_family(type)`     : famille de type (string, number, date, bool, other) ;
- `types_compatible(a, b)`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_SEP_RE = re.compile(r"[^a-z0-9]+")


def strip_accents(text: str) -> str:
    """Supprime les accents (é → e)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def simplify(text: str) -> str:
    """Minuscules, sans accents, camelCase et séparateurs convertis en `_`."""
    if not text:
        return ""
    text = _CAMEL_RE.sub(r"\1_\2", text)
    text = strip_accents(text).lower()
    return _SEP_RE.sub("_", text).strip("_")


def normalize_column(name: str, prefixes: Iterable[str] = (), suffixes: Iterable[str] = ()) -> str:
    """Nom normalisé : simplifié, puis préfixe et suffixe connus retirés (une fois chacun).

    Exemple : `code_tiers_ref` → `tiers` avec les préfixes/suffixes par défaut.
    On ne retire jamais un affixe si le nom deviendrait vide.
    """
    norm = simplify(name)
    for prefix in sorted(prefixes, key=len, reverse=True):
        if norm.startswith(prefix) and len(norm) > len(prefix):
            norm = norm[len(prefix):]
            break
    for suffix in sorted(suffixes, key=len, reverse=True):
        if norm.endswith(suffix) and len(norm) > len(suffix):
            norm = norm[: -len(suffix)]
            break
    return norm.strip("_") or simplify(name)


def tokens(name: str) -> list[str]:
    """Découpe un nom simplifié en mots (`num_contrat` → ['num', 'contrat'])."""
    return [tok for tok in simplify(name).split("_") if tok]


def clean_object_name(name: str, noise: Iterable[str] = ()) -> str:
    """Nom d'objet sans suffixes de bruit (`contrats_raw` → `contrats`)."""
    norm = simplify(name)
    changed = True
    while changed:
        changed = False
        for suffix in noise:
            if norm.endswith(suffix) and len(norm) > len(suffix):
                norm = norm[: -len(suffix)]
                changed = True
    return norm


_TYPE_FAMILIES = [
    ("string", ("string", "varchar", "char", "text", "clob")),
    ("number", ("int", "bigint", "smallint", "tinyint", "decimal", "numeric",
                "double", "float", "real", "number")),
    ("date", ("date", "timestamp", "datetime", "time")),
    ("bool", ("boolean", "bool")),
]


def type_family(data_type: str | None) -> str:
    """Famille d'un type Hive / SQL (`decimal(18,2)` → `number`)."""
    if not data_type:
        return "other"
    base = data_type.strip().lower().split("(")[0].strip()
    for family, names in _TYPE_FAMILIES:
        if base in names:
            return family
    return "other"


def types_compatible(a: str | None, b: str | None) -> bool:
    """Deux types sont compatibles s'ils ont la même famille (ou une famille inconnue)."""
    fa, fb = type_family(a), type_family(b)
    return fa == fb or "other" in (fa, fb)

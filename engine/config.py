"""Lecture de la configuration (`config.json`) avec valeurs par défaut.

Point d'entrée : `load_config(path)`.
Si `path` est absent, on cherche `config.json` puis `config.example.json`
à la racine du projet. Toute clé absente du fichier prend la valeur par défaut.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS: dict[str, Any] = {
    "paths": {
        "input": "examples/metadata.sample.json",
        "database": "data/lineage.db",
        "annotations_dir": "data/annotations",
    },
    "layers": [
        {"code": "LAC", "database_pattern": "^lac_"},
        {"code": "BV", "database_pattern": "^bv_"},
        {"code": "MART", "database_pattern": "^(dm|mart)_"},
    ],
    "relations": {
        "min_score": 0.6,
        "max_column_frequency": 0.05,
        "frequency_floor": 50,
        "max_bucket_size": 300,
        "max_relations_per_object": 30,
        "key_patterns": ["^id_", "^cd_", "^code_", "^hk_", "^num_", "^no_", "^ref_",
                         "_id$", "_ref$", "_reference$", "_cd$", "_hk$", "_key$", "_code$"],
        "ignore_columns": ["date_chargement", "dt_maj", "date_maj", "id_traitement", "flag_actif"],
        "generic_names": ["id", "code", "libelle", "type", "date", "statut"],
        "strip_prefixes": ["id_", "cd_", "code_", "hk_"],
        "strip_suffixes": ["_ref", "_reference", "_id", "_cd", "_hk"],
    },
    "inference": {
        "min_score": 0.4,
        "store_min_score": 0.2,
        "min_shared_columns": 2,
        "max_candidates": 50,
        "max_sources": 5,
        "min_marginal_gain": 0.10,
        "bv_to_bv_min_score": 0.6,
        "weights": {
            "coverage": 0.7,
            "key_found": 0.10,
            "object_name_similar": 0.10,
            "same_domain": 0.05,
            "layer_skip_penalty": -0.15,
        },
        "object_name_noise": ["_raw", "_hist", "_tmp", "_v"],
    },
    "ui": {"default_depth": 2, "max_nodes": 150, "page_size": 50},
    "server": {"host": "127.0.0.1", "port": 8765},
    "user": {"name": "anonyme"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusionne récursivement `override` dans une copie de `base`."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def find_config_file() -> Optional[str]:
    """Renvoie le chemin du fichier de configuration par défaut, s'il existe."""
    for name in ("config.json", "config.example.json"):
        candidate = os.path.join(PROJECT_ROOT, name)
        if os.path.exists(candidate):
            return candidate
    return None


def resolve_path(path: str) -> str:
    """Rend un chemin relatif à la racine du projet."""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def load_config(path: Optional[str] = None) -> dict:
    """Charge la configuration et complète avec les valeurs par défaut."""
    path = path or find_config_file()
    data: dict = {}
    if path:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    config = _deep_merge(DEFAULTS, data)
    config["_source"] = path
    return config

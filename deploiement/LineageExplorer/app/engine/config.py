"""Configuration : valeurs par défaut, fichier `config.json` facultatif, emplacements des dossiers.

Deux emplacements distincts :
- **l'espace de travail** (`workspace`) : partagé par l'équipe (dossier Teams / SharePoint
  synchronisé, lecteur réseau…). Il contient les métadonnées importées, le journal des
  modifications et les versions enregistrées. Rien n'y est jamais modifié en place ;
- **le cache** (`cache_dir`) : local à chaque poste (index SQLite reconstruit à partir de
  l'espace de travail). Il ne doit pas être dans un dossier synchronisé.

Avec l'exécutable, l'espace de travail par défaut est le dossier où se trouve l'exe.

Point d'entrée : `load_config(path)`.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Optional

FROZEN = getattr(sys, "frozen", False)                     # vrai dans l'exécutable PyInstaller
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else PROJECT_ROOT
RESOURCE_DIR = getattr(sys, "_MEIPASS", PROJECT_ROOT)      # web/ et examples/ embarqués

DEFAULTS: dict[str, Any] = {
    "paths": {
        "workspace": None,        # None → dossier de l'exe, ou data/espace en développement
        "cache_dir": None,        # None → %LOCALAPPDATA%/LineageExplorer/cache (ou data/cache)
    },
    "suggestions": {
        "min_score": 0.30,
        "max_per_object": 12,
        "max_bucket_size": 400,
        "ignore_columns": ["dt_chargement", "date_chargement", "date_maj", "dt_maj", "xx_date_insertion",
                           "id_tech", "id_traitement", "annee_mois_jour"],
        "generic_names": ["id", "code", "libelle", "type", "date", "statut", "flag", "url", "actions", "event"],
        "strip_prefixes": ["id_", "cd_", "code_", "r_"],
        "strip_suffixes": ["_ref", "_reference", "_id", "_cd"],
        "object_name_noise": ["_raw", "_hist", "_histo", "_tmp", "_v", "_visioncourante", "_vision_courante"],
    },
    "ui": {"default_depth": 2, "max_nodes": 150, "max_suggestions_in_graph": 8, "refresh_seconds": 15},
    "server": {"host": "127.0.0.1", "port": 8765},
    "user": {"name": None},       # None → nom de la session Windows
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


def resolve_path(path: str, base: str = APP_DIR) -> str:
    """Rend un chemin absolu : variables d'environnement développées (%USERPROFILE%, ~),
    chemin relatif interprété par rapport au dossier de l'application."""
    path = os.path.expanduser(os.path.expandvars(path))
    return path if os.path.isabs(path) else os.path.join(base, path)


PORTABLE_WORKSPACE = os.environ.get("LINEAGE_WORKSPACE")    # défini par LineageExplorer.cmd (version portable)


def default_workspace() -> str:
    if PORTABLE_WORKSPACE:
        return os.path.abspath(PORTABLE_WORKSPACE)
    return APP_DIR if FROZEN else os.path.join(PROJECT_ROOT, "data", "espace")


def default_cache_dir() -> str:
    if FROZEN or PORTABLE_WORKSPACE:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "LineageExplorer", "cache")
    return os.path.join(PROJECT_ROOT, "data", "cache")


def current_user() -> str:
    """Nom de l'utilisateur de la session (Windows : %USERNAME%)."""
    return (os.environ.get("USERNAME") or os.environ.get("USER") or "anonyme").strip().lower()


def find_config_file() -> Optional[str]:
    folders = ([os.path.abspath(PORTABLE_WORKSPACE)] if PORTABLE_WORKSPACE else []) + [APP_DIR, PROJECT_ROOT]
    for folder in folders:
        candidate = os.path.join(folder, "config.json")
        if os.path.exists(candidate):
            return candidate
    return None


def load_config(path: Optional[str] = None) -> dict:
    """Charge la configuration et complète avec les valeurs par défaut."""
    path = path or find_config_file()
    data: dict = {}
    if path:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    config = _deep_merge(DEFAULTS, data)
    paths = config["paths"]
    paths["workspace"] = resolve_path(paths["workspace"]) if paths.get("workspace") else default_workspace()
    paths["cache_dir"] = resolve_path(paths["cache_dir"]) if paths.get("cache_dir") else default_cache_dir()
    config["user"]["name"] = config["user"].get("name") or current_user()
    config["_source"] = path
    return config

"""Outils communs aux tests : exemples fictifs et configuration isolée dans un dossier temporaire."""

from __future__ import annotations

import os
import tempfile

from engine.config import PROJECT_ROOT

EXAMPLES = os.path.join(PROJECT_ROOT, "examples")
ARCHI = os.path.join(PROJECT_ROOT, "architecture test")
DEMO = os.path.join(EXAMPLES, "metadata.demo_une_base.json")
SAMPLE = os.path.join(EXAMPLES, "metadata.sample.json")
DB = "db_demo_dw"


def temp_config() -> tuple[dict, tempfile.TemporaryDirectory]:
    """Configuration par défaut avec espace partagé et cache dans un dossier temporaire."""
    tmp = tempfile.TemporaryDirectory()
    config = load_config_default()
    config["paths"]["workspace"] = os.path.join(tmp.name, "espace")
    config["paths"]["cache_dir"] = os.path.join(tmp.name, "cache")
    config["user"]["name"] = "testeur"
    return config, tmp


def load_config_default() -> dict:
    """Valeurs par défaut uniquement (ignore un éventuel config.json local)."""
    from engine.config import DEFAULTS, _deep_merge
    config = _deep_merge(DEFAULTS, {})
    config["paths"] = {"workspace": None, "cache_dir": None}
    return config


def demo_api():
    """API prête à l'emploi avec le jeu de démonstration importé."""
    from engine.server import LineageApi
    config, tmp = temp_config()
    api = LineageApi(config)
    with open(DEMO, "rb") as handle:
        result = api.import_metadata(handle.read(), {"format": "json", "name": "demo"}, "testeur")
    assert result["ok"], result
    return api, config, tmp

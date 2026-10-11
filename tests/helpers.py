"""Outils communs aux tests : chemins des exemples, configuration isolée dans un dossier temporaire."""

from __future__ import annotations

import os
import tempfile

from engine.config import PROJECT_ROOT, load_config

EXAMPLES = os.path.join(PROJECT_ROOT, "examples")
SAMPLE = os.path.join(EXAMPLES, "metadata.sample.json")
STRUCTURE_ONLY = os.path.join(EXAMPLES, "metadata.structure_only.json")


def temp_config() -> tuple[dict, tempfile.TemporaryDirectory]:
    """Configuration par défaut avec base et annotations dans un dossier temporaire."""
    tmp = tempfile.TemporaryDirectory()
    config = load_config(os.path.join(PROJECT_ROOT, "config.example.json"))
    config["paths"]["database"] = os.path.join(tmp.name, "lineage.db")
    config["paths"]["annotations_dir"] = os.path.join(tmp.name, "annotations")
    return config, tmp

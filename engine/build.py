"""Construction du cache local : métadonnées → catalogue → suggestions → SQLite + rapport.

Point d'entrée : `build_cache(metadata, settings, cache_path, extra_meta)` → rapport (dict).
`settings` = réglages `suggestions` de la configuration, complétés par les colonnes ignorées
définies par l'équipe dans l'interface.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone

from . import store
from .catalog import build_catalog
from .suggestions import compute_suggestions


def effective_settings(base: dict, team_settings: dict | None = None) -> dict:
    """Réglages de suggestion : configuration + colonnes ignorées ajoutées par l'équipe."""
    settings = dict(base)
    extra = (team_settings or {}).get("ignore_columns") or []
    settings["ignore_columns"] = sorted(set(base["ignore_columns"]) | set(extra))
    return settings


def build_cache(md: dict, settings: dict, cache_path: str, extra_meta: dict | None = None) -> dict:
    start = time.perf_counter()
    catalog = build_catalog(md, settings)
    suggestions = compute_suggestions(catalog, md, settings)
    shared = Counter()
    for obj in catalog.objects.values():
        for col in {c.name for c in obj.columns}:
            shared[col] += 1
    report = {
        "nb_objects": len(catalog.objects),
        "nb_columns": sum(len(o.columns) for o in catalog.objects.values()),
        "objects_by_database": dict(Counter(o.database for o in catalog.objects.values()).most_common()),
        "objects_by_type": dict(Counter(o.type for o in catalog.objects.values())),
        "nb_suggestions": len(suggestions),
        "suggestions_by_origin": dict(Counter(s["origin"] for s in suggestions)),
        "most_shared_columns": [{"name": n, "nb_objects": c} for n, c in shared.most_common(15) if c > 1],
        "duration_s": round(time.perf_counter() - start, 2),
    }
    meta = {**(extra_meta or {}), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "extracted_at": md["meta"].get("extracted_at"), "source": md["meta"].get("source"), "report": report}
    store.write_cache(cache_path, catalog, suggestions, meta)
    report["duration_s"] = round(time.perf_counter() - start, 2)
    return report

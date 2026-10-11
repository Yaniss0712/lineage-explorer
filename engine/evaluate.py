"""Mesure de la qualité de l'inférence sur une vérité terrain (docs/05 § 4).

Vérité terrain : fichier JSON au format `{"flows": [{"source": ..., "target": ...}, ...]}`
(ou directement une liste de flux). Les flux validés dans l'interface s'y ajoutent.

Point d'entrée : `evaluate(con, truth_flows, min_score, validated)` → précision, rappel, erreurs.
"""

from __future__ import annotations

import json

from . import store


def load_truth(path: str) -> list[dict]:
    """Charge la vérité terrain."""
    with open(path, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    flows = data.get("flows", []) if isinstance(data, dict) else data
    return [{"source": f["source"].lower(), "target": f["target"].lower()} for f in flows]


def evaluate(con, truth: list[dict], min_score: float, validated: list[tuple[str, str]] = ()) -> dict:
    """Compare les flux INFERRED (score ≥ min_score) à la vérité terrain."""
    truth_pairs = {(f["source"], f["target"]) for f in truth} | set(validated)
    inferred = {(e["source_object"], e["target_object"]): e["score"]
                for e in store.edges_by_origin(con, "FLOW", "INFERRED")}
    # Les flux déclarés / SQL ont écrasé l'inféré correspondant lors de l'arbitrage :
    # on récupère le score inféré conservé dans les détails.
    for origin in ("DECLARED", "SQL_PARSED"):
        for e in store.edges_by_origin(con, "FLOW", origin):
            if "inferred_score" in e["details"]:
                inferred[(e["source_object"], e["target_object"])] = e["details"]["inferred_score"]
    proposed = {pair for pair, score in inferred.items() if score >= min_score}
    true_pos = proposed & truth_pairs
    false_pos = sorted(proposed - truth_pairs)
    false_neg = sorted(truth_pairs - proposed)
    precision = len(true_pos) / len(proposed) if proposed else None
    recall = len(true_pos) / len(truth_pairs) if truth_pairs else None
    return {
        "min_score": min_score,
        "nb_truth": len(truth_pairs),
        "nb_proposed": len(proposed),
        "nb_correct": len(true_pos),
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "false_positives": [{"source": s, "target": t, "score": inferred[(s, t)]} for s, t in false_pos],
        "false_negatives": [{"source": s, "target": t, "score": inferred.get((s, t))} for s, t in false_neg],
    }

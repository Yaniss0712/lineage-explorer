"""Orchestration du build : JSON → catalogue → lineage + relations → SQLite + rapport.

Point d'entrée : `run_build(metadata, config, db_path, input_label)` → rapport (dict).
Le rapport est aussi écrit dans `build_report.json` à côté de la base (docs/05 § 5).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import inference, lineage, relations, store
from .catalog import Catalog, build_catalog


def compute_edges(md: dict, config: dict) -> tuple[Catalog, list[dict], dict]:
    """Calcule toutes les arêtes (flux arbitrés + relations) et les statistiques de relations."""
    catalog = build_catalog(md, config)
    inferred = inference.infer_flows(catalog, config)
    flows = lineage.arbitrate(lineage.declared_flows(md, catalog) + lineage.sql_flows(catalog) + inferred)
    rels, rel_stats = relations.detect_relations(catalog, md, flows, config)
    return catalog, flows + rels, rel_stats


def _score_bucket(score: float) -> str:
    if score >= 0.9:
        return "0.9-1.0"
    if score >= 0.75:
        return "0.75-0.9"
    if score >= 0.5:
        return "0.5-0.75"
    if score >= 0.4:
        return "0.4-0.5"
    return "<0.4"


def _mart_columns_explained(catalog: Catalog, flows: list[dict], min_score: float) -> dict:
    """Part des colonnes de marts dont l'origine remonte jusqu'à une colonne du lac."""
    ups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for edge in flows:
        if edge["score"] < min_score:
            continue
        for col in edge.get("columns") or []:
            ups[(edge["target_object"], col["target"])].append((edge["source_object"], col["source"]))
    memo: dict[tuple[str, str], bool] = {}

    def reaches_lake(node: tuple[str, str], stack: frozenset) -> bool:
        if node in memo:
            return memo[node]
        obj = catalog.objects.get(node[0])
        if obj is not None and obj.layer == "LAC":
            return True
        result = any(reaches_lake(up, stack | {node}) for up in ups.get(node, []) if up not in stack)
        memo[node] = result
        return result

    total = explained = 0
    for obj in catalog.objects.values():
        if obj.layer != "MART":
            continue
        for col in obj.columns:
            if col.ignored:
                continue
            total += 1
            explained += reaches_lake((obj.id, col.name), frozenset())
    return {"total": total, "explained": explained,
            "rate": round(explained / total, 3) if total else None}


def make_report(catalog: Catalog, edges: list[dict], rel_stats: dict, config: dict) -> dict:
    """Indicateurs qualité du build."""
    min_score = config["inference"]["min_score"]
    flows = [e for e in edges if e["kind"] == "FLOW"]
    objects_by_layer = Counter(o.layer for o in catalog.objects.values())
    columns_by_layer: Counter = Counter()
    for obj in catalog.objects.values():
        columns_by_layer[obj.layer] += len(obj.columns)
    with_source = {e["target_object"] for e in flows if e["score"] >= min_score}
    orphans = sorted(o.id for o in catalog.objects.values()
                     if o.layer in ("BV", "MART") and o.id not in with_source)
    return {
        "objects_by_layer": dict(objects_by_layer),
        "columns_by_layer": dict(columns_by_layer),
        "nb_objects": len(catalog.objects),
        "nb_columns": sum(columns_by_layer.values()),
        "flows_by_origin": dict(Counter(e["origin"] for e in flows)),
        "inferred_by_score": dict(Counter(_score_bucket(e["score"]) for e in flows if e["origin"] == "INFERRED")),
        "relations_by_level": rel_stats,
        "orphans": orphans[:200],
        "nb_orphans": len(orphans),
        "mart_columns_explained": _mart_columns_explained(catalog, flows, min_score),
        "frequent_columns": sorted(catalog.frequent)[:50],
    }


def run_build(md: dict, config: dict, db_path: str, input_label: str = "") -> dict:
    """Build complet : calcule, écrit la base et le rapport, renvoie le rapport."""
    start = time.perf_counter()
    catalog, edges, rel_stats = compute_edges(md, config)
    report = make_report(catalog, edges, rel_stats, config)
    report["duration_s"] = round(time.perf_counter() - start, 2)
    meta = {
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "input": input_label,
        "extracted_at": md["meta"].get("extracted_at"),
        "source": md["meta"].get("source"),
        "format_version": md["meta"].get("format_version"),
        "report": report,
    }
    store.write_database(db_path, catalog, edges, meta)
    report["duration_s"] = round(time.perf_counter() - start, 2)
    report_path = os.path.join(os.path.dirname(os.path.abspath(db_path)), "build_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump({**meta, "report": report}, handle, ensure_ascii=False, indent=2)
    return report

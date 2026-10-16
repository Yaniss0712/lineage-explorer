"""Ligne de commande : `python -m engine <commande>`.

- check    : valide un fichier de métadonnées (JSON, JSONL, SQL) ;
- import   : importe un fichier dans l'espace de travail (comme le bouton « Importer ») ;
- serve    : lance l'interface web ;
- report   : indicateurs de l'espace de travail ;
- evaluate : compare les suggestions à une liste de liens connus (précision / rappel).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import ingest
from .config import load_config


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _config(args) -> dict:
    config = load_config(args.config)
    if getattr(args, "workspace", None):
        config["paths"]["workspace"] = os.path.abspath(args.workspace)
    return config


def cmd_check(args) -> int:
    report = ingest.check_metadata(ingest.load_metadata(args.input))
    _print(report)
    return 0 if report["ok"] else 1


def cmd_import(args) -> int:
    from .server import LineageApi
    api = LineageApi(_config(args))
    fmt = "sql" if os.path.isdir(args.input) or args.input.lower().endswith(".sql") else \
        "jsonl" if args.input.lower().endswith(".jsonl") else "json"
    raw = ingest.read_sql(args.input).encode("utf-8") if fmt == "sql" else open(args.input, "rb").read()
    result = api.import_metadata(raw, {"format": fmt, "name": os.path.basename(args.input.rstrip("/\\"))},
                                 args.user or api.config["user"]["name"])
    _print({k: result.get(k) for k in ("ok", "report", "diff")} if result["ok"] else result["check"])
    return 0 if result["ok"] else 1


def cmd_serve(args) -> int:
    from .server import serve
    serve(_config(args), host=args.host, port=args.port)
    return 0


def cmd_report(args) -> int:
    from .server import LineageApi
    _print(LineageApi(_config(args)).report())
    return 0


def cmd_evaluate(args) -> int:
    from .server import LineageApi
    api = LineageApi(_config(args))
    with open(args.truth, encoding="utf-8-sig") as handle:
        truth_data = json.load(handle)
    truth = {"|".join(sorted((f["source"].lower(), f["target"].lower())))
             for f in (truth_data.get("flows", []) if isinstance(truth_data, dict) else truth_data)}
    proposed = {s["key"] for s in api.suggestions({"limit": 100000, "min_score": args.min_score})["items"]}
    good = proposed & truth
    _print({"nb_truth": len(truth), "nb_suggestions": len(proposed), "nb_correct": len(good),
            "precision": round(len(good) / len(proposed), 3) if proposed else None,
            "recall": round(len(good) / len(truth), 3) if truth else None,
            "missed": sorted(truth - proposed)[:50]})
    return 0


def cmd_compact(args) -> int:
    """Regroupe l'ancien journal (un fichier par modification) en un seul fichier."""
    from .workspace import Workspace
    root = _config(args)["paths"]["workspace"]
    ws = Workspace(root, compact=False)
    plan = ws.compact_legacy(dry_run=True)
    print(f"Espace : {root}")
    print(f"Anciens fichiers à regrouper : {plan['files']} ({plan['events']} modification(s))")
    if plan["unreadable"]:
        print(f"Fichiers illisibles (en cours de synchronisation ?), laissés en place : {len(plan['unreadable'])}")
    if args.dry_run or not plan["files"]:
        return 0
    removed = ws.compact_legacy(user=args.user or "manuel")
    print(f"Regroupés : {removed} fichier(s) dans journal/archive__….jsonl ; originaux sauvegardés dans journal/sauvegardes/.")
    return 0 if removed == plan["files"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine", description="Lineage Explorer")
    parser.add_argument("--config", help="fichier de configuration (défaut : config.json s'il existe)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="valider un fichier de métadonnées")
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("import", help="importer des métadonnées dans l'espace de travail")
    p.add_argument("--input", required=True, help="fichier .json / .jsonl / .sql ou dossier de .sql")
    p.add_argument("--workspace")
    p.add_argument("--user")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("serve", help="lancer l'interface web")
    p.add_argument("--workspace")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("report", help="indicateurs de l'espace de travail")
    p.add_argument("--workspace")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("evaluate", help="précision / rappel des suggestions")
    p.add_argument("--truth", required=True)
    p.add_argument("--min-score", default="0")
    p.add_argument("--workspace")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("compact", help="regrouper l'ancien journal (un fichier par modification) en un seul fichier")
    p.add_argument("--workspace")
    p.add_argument("--user")
    p.add_argument("--dry-run", action="store_true", help="afficher le bilan sans rien modifier")
    p.set_defaults(func=cmd_compact)

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

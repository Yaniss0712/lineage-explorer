"""Ligne de commande : `python -m engine <commande>`.

Commandes :
- check    : valide le JSON d'entrée et affiche la volumétrie ;
- build    : construit la base SQLite (lineage, relations, index de recherche) ;
- serve    : lance l'interface web locale ;
- stats    : affiche le rapport du dernier build ;
- search   : recherche en ligne de commande ;
- evaluate : précision / rappel de l'inférence sur une vérité terrain.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import build, evaluate, ingest, store
from .annotations import AnnotationStore
from .config import load_config, resolve_path


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _paths(args, config: dict) -> tuple[str, str]:
    input_path = resolve_path(args.input or config["paths"]["input"]) if hasattr(args, "input") else ""
    db_path = resolve_path(args.db or config["paths"]["database"])
    return input_path, db_path


def cmd_check(args, config) -> int:
    input_path, _ = _paths(args, config)
    report = ingest.check_metadata(ingest.load_metadata(input_path), config)
    _print(report)
    return 0 if report["ok"] else 1


def cmd_build(args, config) -> int:
    input_path, db_path = _paths(args, config)
    md = ingest.load_metadata(input_path)
    check = ingest.check_metadata(md, config)
    if not check["ok"]:
        _print({"erreur": "JSON invalide, build annulé", "details": check["errors"][:50]})
        return 1
    report = build.run_build(md, config, db_path, input_label=input_path)
    _print(report)
    print(f"\nBase écrite : {db_path}")
    return 0


def cmd_serve(args, config) -> int:
    from .server import serve
    if args.db:
        config["paths"]["database"] = args.db
    if args.annotations:
        config["paths"]["annotations_dir"] = args.annotations
    serve(config, host=args.host, port=args.port)
    return 0


def cmd_stats(args, config) -> int:
    _, db_path = _paths(args, config)
    con = store.connect(db_path)
    _print(store.get_meta(con))
    return 0


def cmd_search(args, config) -> int:
    _, db_path = _paths(args, config)
    con = store.connect(db_path)
    result = store.search(con, args.query, layer=args.layer, in_desc=True, page_size=args.limit)
    print(f"{result['total']} résultat(s)")
    for row in result["results"]:
        print(f"  [{row['layer']:<5}] {row['kind']:<6} {row['ref_id']}" + (f"  ({row['data_type']})" if row["data_type"] else ""))
    return 0


def cmd_evaluate(args, config) -> int:
    _, db_path = _paths(args, config)
    con = store.connect(db_path)
    validated = []
    if args.with_validated:
        notes = AnnotationStore(resolve_path(config["paths"]["annotations_dir"]))
        validated = [(l["source_object"], l["target"]) for l in notes.all_links().values()
                     if l["kind"] == "FLOW" and l["status"] in ("VALIDATED", "MANUAL") and not l.get("source_column")]
    min_score = args.min_score if args.min_score is not None else config["inference"]["min_score"]
    _print(evaluate.evaluate(con, evaluate.load_truth(resolve_path(args.truth)), min_score, validated))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine", description="Lineage Explorer — moteur")
    parser.add_argument("--config", help="fichier de configuration (défaut : config.json ou config.example.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="valider le JSON d'entrée")
    p.add_argument("--input")
    p.add_argument("--db")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("build", help="construire la base SQLite")
    p.add_argument("--input")
    p.add_argument("--db")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("serve", help="lancer l'interface web")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--db")
    p.add_argument("--annotations")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("stats", help="rapport du dernier build")
    p.add_argument("--db")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("search", help="rechercher une table ou une colonne")
    p.add_argument("query")
    p.add_argument("--layer")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--db")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("evaluate", help="précision / rappel sur une vérité terrain")
    p.add_argument("--truth", default="data/ground_truth.json")
    p.add_argument("--min-score", type=float)
    p.add_argument("--with-validated", action="store_true", help="ajouter les flux validés dans l'interface")
    p.add_argument("--db")
    p.set_defaults(func=cmd_evaluate)

    args = parser.parse_args(argv)
    config = load_config(args.config)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        return args.func(args, config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

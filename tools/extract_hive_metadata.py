"""Extraction des métadonnées Hive (ODBC) pour Lineage Explorer.

Produit, en un seul lancement :
- un JSON de structure (tables, vues, colonnes, types, commentaires, colonnes de partition) ;
- un fichier SQL avec la définition de chaque vue (`SHOW CREATE TABLE`) : à importer ensuite
  en mode « Compléter » pour obtenir automatiquement liens, jointures et règles de gestion ;
- un fichier d'erreurs (bases ou objets illisibles), si besoin.

Le nom du DSN et la liste des bases ne sont PAS dans ce fichier (le dépôt est public) : ils sont
lus dans `extraction.local.json` (ignoré par git), à créer à partir de `extraction.example.json`.

    python tools/extract_hive_metadata.py                      # extraction.local.json à côté du script
    python tools/extract_hive_metadata.py --config C:\\chemin\\extraction.local.json

Identifiants : variables d'environnement HIVE_ODBC_USERNAME / HIVE_ODBC_PASSWORD (facultatives,
selon la configuration du DSN). Nécessite `pyodbc` sur le poste qui lance l'extraction.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "extraction.local.json")
DEFAULTS = {"dsn": "", "databases": [], "output": "metadata_databases.json", "sql_output": "vues_databases.sql",
            "errors": "metadata_errors.json", "show_create_views": True, "timeout": 30}


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit(f"Configuration introuvable : {path}\n"
                         f"Copiez tools/extraction.example.json en extraction.local.json et complétez-le.")
    with open(path, encoding="utf-8") as handle:
        config = {**DEFAULTS, **json.load(handle)}
    if not config["dsn"] or not config["databases"]:
        raise SystemExit("La configuration doit indiquer 'dsn' et 'databases'.")
    base = os.path.dirname(os.path.abspath(path))
    for key in ("output", "sql_output", "errors"):
        if config[key] and not os.path.isabs(config[key]):
            config[key] = os.path.join(base, config[key])
    return config


def quote_identifier(identifier: str) -> str:
    """Protège un identifiant Hive avec des backticks."""
    return "`" + identifier.replace("`", "``") + "`"


def normalize_column_name(column_name: str) -> str:
    """Retire les préfixes éventuellement ajoutés par le pilote ODBC."""
    value = str(column_name).strip()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value.strip("` ")


def create_connection(config: dict):
    import pyodbc  # noqa: PLC0415 — seulement sur le poste qui extrait
    parts = [f"DSN={config['dsn']}"]
    if os.getenv("HIVE_ODBC_USERNAME"):
        parts.append(f"UID={os.getenv('HIVE_ODBC_USERNAME')}")
    if os.getenv("HIVE_ODBC_PASSWORD"):
        parts.append(f"PWD={os.getenv('HIVE_ODBC_PASSWORD')}")
    return pyodbc.connect(";".join(parts) + ";", autocommit=True, timeout=config.get("timeout", 30))


def get_tables(cursor, database: str) -> list[str]:
    """Exécute SHOW TABLES IN et retourne les noms trouvés."""
    cursor.execute(f"SHOW TABLES IN {quote_identifier(database)}")
    tables: list[str] = []
    for row in cursor.fetchall():
        values = [str(v).strip() for v in row if v is not None and str(v).strip()]
        if not values:
            continue
        if len(values) == 1:
            table_name = values[0]
        else:
            candidates = [v for v in values if v.lower() not in {database.lower(), "true", "false"}]
            table_name = candidates[0] if candidates else values[0]
        table_name = table_name.strip("` ")
        if table_name:
            tables.append(table_name)
    return list(dict.fromkeys(tables))


_IGNORED_COMMENTS = {"", "none", "null", "from deserializer"}


def parse_describe(rows) -> dict[str, Any]:
    """Lit la sortie de DESCRIBE FORMATTED : colonnes, partitions, type, commentaire de la table."""
    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    object_type, comment, section = "TABLE", "", "columns"
    for row in rows:
        values = list(row) + [None] * (3 - len(row))
        name = str(values[0] or "").strip()
        data_type = str(values[1] or "").strip()
        extra = str(values[2] or "").strip()
        key = name.lower().rstrip(":").strip()
        if key == "table type" and "VIEW" in data_type.upper():
            object_type = "VIEW"
        if key in {"view original text", "view expanded text"}:
            object_type = "VIEW"
        if not name and data_type.lower().strip() == "comment" and extra.lower() not in _IGNORED_COMMENTS:
            comment = extra                                       # ligne « Table Parameters » : comment
        if name.startswith("#"):
            low = name.lower()
            if "partition information" in low:
                section = "partitions"
            elif not low.startswith("# col_name"):              # # Detailed Table Information, # Storage…
                section = "other"
            continue
        if section == "other" or not name or not data_type or key in {"col_name", "column_name"}:
            continue
        clean = normalize_column_name(name)
        if not clean:
            continue
        if clean in seen:
            if section == "partitions":
                next(c for c in columns if c["name"] == clean)["partition"] = True
            continue
        seen.add(clean)
        column: dict[str, Any] = {"name": clean, "type": data_type}
        if extra.lower() not in _IGNORED_COMMENTS:
            column["description"] = extra
        if section == "partitions":
            column["partition"] = True
        columns.append(column)
    return {"columns": columns, "type": object_type, "description": comment}


def describe_object(cursor, database: str, table_name: str) -> dict[str, Any]:
    cursor.execute(f"DESCRIBE FORMATTED {quote_identifier(database)}.{quote_identifier(table_name)}")
    return parse_describe(cursor.fetchall())


def show_create(cursor, database: str, table_name: str) -> str:
    """Définition SQL d'une vue (SHOW CREATE TABLE), nom de base garanti."""
    cursor.execute(f"SHOW CREATE TABLE {quote_identifier(database)}.{quote_identifier(table_name)}")
    text = "\n".join(str(r[0]) for r in cursor.fetchall() if r and r[0] is not None).strip().rstrip(";")
    return text


def write_json(data: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def export_metadata(cursor, config: dict, log=print) -> dict[str, Any]:
    """Exporte toutes les bases : structure JSON + SQL des vues + erreurs."""
    result: dict[str, Any] = {"meta": {"extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "source": "hive", "extractor": "tools/extract_hive_metadata.py"},
                              "objects": []}
    views_sql: list[str] = []
    errors: list[dict[str, str]] = []
    databases = config["databases"]
    for index, database in enumerate(databases, start=1):
        log(f"Base [{index}/{len(databases)}] : {database}")
        try:
            tables = get_tables(cursor, database)
        except Exception as error:  # noqa: BLE001
            errors.append({"database": database, "object": database, "error": str(error)})
            log(f"  ERREUR : {error}")
            continue
        for position, table_name in enumerate(tables, start=1):
            object_id = f"{database}.{table_name}"
            try:
                info = describe_object(cursor, database, table_name)
                obj = {"id": object_id, "database": database, "name": table_name, "type": info["type"],
                       "columns": info["columns"]}
                if info["description"]:
                    obj["description"] = info["description"]
                result["objects"].append(obj)
                if info["type"] == "VIEW" and config.get("show_create_views", True):
                    try:
                        views_sql.append(f"-- {object_id}\n{show_create(cursor, database, table_name)};")
                    except Exception as error:  # noqa: BLE001
                        errors.append({"database": database, "object": object_id, "error": f"SHOW CREATE : {error}"})
                log(f"  [{position}/{len(tables)}] {object_id} : {info['type']}, {len(info['columns'])} colonne(s)")
            except Exception as error:  # noqa: BLE001
                errors.append({"database": database, "object": object_id, "error": str(error)})
                log(f"  [{position}/{len(tables)}] {object_id} : ERREUR {error}")
    result["objects"].sort(key=lambda o: (o["database"].lower(), o["name"].lower()))
    write_json(result, config["output"])
    if views_sql and config.get("sql_output"):
        with open(config["sql_output"], "w", encoding="utf-8") as handle:
            handle.write("-- Définitions des vues extraites par SHOW CREATE TABLE\n\n" + "\n\n".join(views_sql) + "\n")
    if errors and config.get("errors"):
        write_json({"errors": errors}, config["errors"])
    return {"objects": len(result["objects"]), "views": len(views_sql), "errors": len(errors)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Extraction des métadonnées Hive pour Lineage Explorer")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    connection = None
    try:
        print(f"Connexion au DSN : {config['dsn']}")
        connection = create_connection(config)
        cursor = connection.cursor()
        try:
            stats = export_metadata(cursor, config)
        finally:
            cursor.close()
        print(f"\nExport terminé : {stats['objects']} objet(s), {stats['views']} vue(s) en SQL, {stats['errors']} erreur(s)")
        print(f"Structure : {config['output']}")
        if stats["views"]:
            print(f"SQL des vues : {config['sql_output']} (à importer ensuite en mode « Compléter »)")
    except Exception as error:  # noqa: BLE001
        print(f"Erreur : {error}")
        sys.exit(1)
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()

"""Lecture et validation des métadonnées d'entrée (contrat : docs/04_format_json.md).

Formats acceptés :
- JSON : `{"meta": {...}, "objects": [...], "flows": [...], "relations": [...]}` ;
- JSON Lines (`.jsonl`) : une ligne = un enregistrement avec un champ `record`
  (`meta`, `object`, `flow`, `relation`) ;
- SQL (`.sql` ou dossier de `.sql`) : traduit par `sql_ingest`.

Seuls `objects[]` (tables / vues et leurs colonnes) sont obligatoires. `flows[]` et
`relations[]` sont facultatifs : ils ne créent pas de liens, ils deviennent des
**suggestions** fiables que l'équipe transforme (ou non) en liens.
Les anciens champs `layer` et `is_key` sont acceptés mais ignorés.

Points d'entrée : `load_metadata(path)`, `parse_metadata(data)`, `parse_text(text, fmt)`,
`check_metadata(md)`.
"""

from __future__ import annotations

import json
import os
from collections import Counter

from . import sql_ingest


def read_sql(path: str) -> str:
    """Texte d'un fichier `.sql`, ou de tous les `.sql` d'un dossier (ordre alphabétique)."""
    files = [path] if not os.path.isdir(path) else [
        os.path.join(root, name) for root, _dirs, names in sorted(os.walk(path))
        for name in sorted(names) if name.lower().endswith(".sql")]
    parts = []
    for file in files:
        with open(file, encoding="utf-8-sig") as handle:
            parts.append(handle.read())
    return ";\n".join(parts)


def _from_jsonl_lines(lines) -> dict:
    """Reconstitue la structure standard à partir de lignes JSON Lines."""
    data: dict = {"meta": {}, "objects": [], "flows": [], "relations": []}
    targets = {"object": "objects", "flow": "flows", "relation": "relations"}
    for line_no, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ligne {line_no} invalide : {exc}") from exc
        kind = record.pop("record", "object")
        if kind == "meta":
            data["meta"].update(record)
        elif kind in targets:
            data[targets[kind]].append(record)
    return data


def load_metadata(path: str) -> dict:
    """Charge un fichier JSON, JSON Lines, un script SQL ou un dossier de scripts `.sql`."""
    if os.path.isdir(path) or path.lower().endswith(".sql"):
        return parse_metadata(sql_ingest.parse_sql_script(read_sql(path), source=os.path.basename(path)))
    with open(path, encoding="utf-8-sig") as handle:
        if path.lower().endswith(".jsonl"):
            return parse_metadata(_from_jsonl_lines(handle))
        return parse_metadata(json.load(handle))


def parse_text(text: str, fmt: str = "json", name: str = "import") -> dict:
    """Analyse un contenu texte (`json`, `jsonl` ou `sql`) et renvoie les métadonnées normalisées."""
    if fmt == "sql":
        return parse_metadata(sql_ingest.parse_sql_script(text, source=name))
    if fmt == "jsonl":
        return parse_metadata(_from_jsonl_lines(text.splitlines()))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide : {exc}") from exc
    return parse_metadata(data)


def _clean_column(col: dict, position: int) -> dict:
    """Normalise une colonne (nom en minuscules, champs facultatifs)."""
    return {
        "name": str(col.get("name") or "").strip().lower(),
        "type": (str(col.get("type") or "")).strip().lower() or None,
        "description": col.get("description") or "",
        "position": col.get("position") or position,
    }


def _clean_object(obj: dict) -> dict:
    """Normalise un objet : identifiant `base.nom` en minuscules, colonnes nettoyées."""
    database = str(obj.get("database") or "").strip().lower()
    name = str(obj.get("name") or "").strip().lower()
    obj_id = str(obj.get("id") or "").strip().lower()
    if not obj_id and database and name:
        obj_id = f"{database}.{name}"
    if obj_id and (not database or not name) and "." in obj_id:
        database, name = obj_id.split(".", 1)
    return {
        "id": obj_id,
        "database": database,
        "name": name,
        "type": (obj.get("type") or "TABLE").upper(),
        "description": obj.get("description") or "",
        "owner": obj.get("owner") or "",
        "sql": obj.get("sql") or None,
        "columns": [_clean_column(col, i) for i, col in enumerate(obj.get("columns") or [], 1)],
    }


def parse_metadata(data: dict) -> dict:
    """Normalise un dict de métadonnées au format du contrat."""
    if not isinstance(data, dict) or "objects" not in data:
        raise ValueError("Le JSON doit être un objet contenant la clé 'objects'.")
    flows = [{
        "source": str(flow.get("source") or "").lower(),
        "target": str(flow.get("target") or "").lower(),
        "job": flow.get("job") or None,
        "origin": "SQL" if str(flow.get("origin", "")).upper() in ("SQL", "SQL_PARSED") else "DECLARE",
        "column_mapping": [
            {"source": str(m.get("source") or "").lower(), "target": str(m.get("target") or "").lower(),
             "transformation": m.get("transformation") or None}
            for m in (flow.get("column_mapping") or []) if m.get("source") and m.get("target")],
    } for flow in data.get("flows") or []]
    relations = [{
        "a": str(rel.get("a") or "").lower(), "a_column": str(rel.get("a_column") or "").lower() or None,
        "b": str(rel.get("b") or "").lower(), "b_column": str(rel.get("b_column") or "").lower() or None,
        "comment": rel.get("comment") or "",
    } for rel in data.get("relations") or []]
    return {
        "meta": data.get("meta") or {},
        "objects": [_clean_object(obj) for obj in data.get("objects") or []],
        "flows": flows,
        "relations": relations,
    }


def check_metadata(md: dict) -> dict:
    """Contrôle les métadonnées avant le build : erreurs bloquantes, avertissements, volumétrie."""
    errors: list[str] = []
    warnings: list[str] = []
    ids = [obj["id"] for obj in md["objects"]]
    known = set(ids)
    for obj_id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"Identifiant dupliqué : {obj_id} ({count} fois)")
    per_database: Counter = Counter()
    nb_columns = 0
    for obj in md["objects"]:
        if not obj["id"]:
            errors.append("Objet sans identifiant (ni 'id', ni 'database' + 'name').")
            continue
        if not obj["columns"]:
            warnings.append(f"Objet sans colonne : {obj['id']}")
        per_database[obj["database"]] += 1
        names = Counter()
        for col in obj["columns"]:
            nb_columns += 1
            if not col["name"]:
                errors.append(f"Colonne sans nom dans {obj['id']}")
            if not col["type"]:
                warnings.append(f"Colonne sans type : {obj['id']}.{col['name']}")
            names[col["name"]] += 1
        for name, count in names.items():
            if count > 1:
                errors.append(f"Colonne dupliquée : {obj['id']}.{name}")
    for flow in md["flows"]:
        for end in ("source", "target"):
            if flow[end] not in known:
                warnings.append(f"Flux ignoré, objet inexistant : {flow[end]}")
    for rel in md["relations"]:
        for end in ("a", "b"):
            if rel[end] not in known:
                warnings.append(f"Relation ignorée, objet inexistant : {rel[end]}")
    return {
        "ok": not errors, "errors": errors, "warnings": warnings,
        "nb_objects": len(md["objects"]), "nb_columns": nb_columns,
        "nb_flows": len(md["flows"]), "nb_relations": len(md["relations"]),
        "objects_by_database": dict(per_database.most_common()),
    }

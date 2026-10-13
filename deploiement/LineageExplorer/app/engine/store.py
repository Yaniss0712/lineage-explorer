"""Cache SQLite local : métadonnées indexées, suggestions, dictionnaire des colonnes, recherche.

Ce fichier est **local à chaque poste** (jamais dans le dossier partagé : une base SQLite
synchronisée par Teams / OneDrive se corromprait). Il est reconstruit automatiquement à partir
des métadonnées de l'espace de travail ; le supprimer n'a aucune conséquence.

Recherche : FTS5 `trigram` (sous-chaînes) si disponible, sinon `LIKE` sur les noms normalisés.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Iterable, Optional

from .catalog import Catalog
from .normalize import strip_accents

SCHEMA = """
CREATE TABLE objects (id TEXT PRIMARY KEY, database TEXT, name TEXT, type TEXT, description TEXT,
                      owner TEXT, nb_columns INTEGER);
CREATE TABLE columns (id TEXT PRIMARY KEY, object_id TEXT, name TEXT, name_norm TEXT, data_type TEXT,
                      description TEXT, position INTEGER);
CREATE TABLE suggestions (key TEXT PRIMARY KEY, a TEXT, b TEXT, score REAL, origin TEXT, kind TEXT,
                          direction TEXT, data TEXT);
CREATE TABLE rules (rowid INTEGER PRIMARY KEY, target TEXT, data TEXT);
CREATE TABLE rule_objects (rule_id INTEGER, object_id TEXT, role TEXT);
CREATE TABLE column_stats (name TEXT PRIMARY KEY, name_norm TEXT, nb_objects INTEGER, types TEXT, conflict INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE search (rowid INTEGER PRIMARY KEY, kind TEXT, object_id TEXT, column_name TEXT, database TEXT,
                     name_norm TEXT, desc_norm TEXT, data_type TEXT);
"""
INDEXES = """
CREATE INDEX idx_columns_obj ON columns(object_id);
CREATE INDEX idx_columns_name ON columns(name);
CREATE INDEX idx_columns_norm ON columns(name_norm);
CREATE INDEX idx_sugg_a ON suggestions(a);
CREATE INDEX idx_sugg_b ON suggestions(b);
CREATE INDEX idx_search_name ON search(name_norm);
CREATE INDEX idx_rule_objects ON rule_objects(object_id);
"""


def norm_text(text: str) -> str:
    return strip_accents(text or "").lower()


def has_trigram() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        con.close()
        return True
    except sqlite3.Error:
        return False


# --------------------------------------------------------------------------- écriture


def write_cache(path: str, catalog: Catalog, suggestions: list[dict], meta: dict, rules: list[dict] | None = None) -> None:
    """Écrit le cache complet dans un fichier temporaire puis remplace l'ancien."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
        con.executescript(SCHEMA)
        obj_rows, col_rows, search_rows = [], [], []
        types_by_name: dict[str, Counter] = defaultdict(Counter)
        norm_by_name, count_by_name = {}, Counter()
        for obj in catalog.objects.values():
            obj_rows.append((obj.id, obj.database, obj.name, obj.type, obj.description, obj.owner, len(obj.columns)))
            search_rows.append(("OBJECT", obj.id, None, obj.database, norm_text(obj.name), norm_text(obj.description), None))
            for col in obj.columns:
                col_rows.append((f"{obj.id}.{col.name}", obj.id, col.name, col.norm, col.data_type, col.description,
                                 col.position))
                search_rows.append(("COLUMN", obj.id, col.name, obj.database, norm_text(col.name),
                                    norm_text(col.description), col.data_type))
                types_by_name[col.name][(col.data_type or "?").split("(")[0]] += 1
                norm_by_name[col.name] = col.norm
                count_by_name[col.name] += 1
        con.executemany("INSERT INTO objects VALUES (?,?,?,?,?,?,?)", obj_rows)
        con.executemany("INSERT INTO columns VALUES (?,?,?,?,?,?,?)", col_rows)
        con.executemany("INSERT INTO search(kind, object_id, column_name, database, name_norm, desc_norm, data_type) "
                        "VALUES (?,?,?,?,?,?,?)", search_rows)
        con.executemany("INSERT INTO column_stats VALUES (?,?,?,?,?)",
                        [(name, norm_by_name[name], count_by_name[name], json.dumps(dict(types)),
                          1 if len(types) > 1 else 0) for name, types in types_by_name.items()])
        con.executemany("INSERT INTO suggestions VALUES (?,?,?,?,?,?,?,?)",
                        [(s["key"], s["a"], s["b"], s["score"], s["origin"], s["kind"], s["direction"],
                          json.dumps(s, ensure_ascii=False)) for s in suggestions])
        for rule in rules or []:
            cur = con.execute("INSERT INTO rules(target, data) VALUES (?, ?)",
                              (rule.get("target"), json.dumps(rule, ensure_ascii=False)))
            roles = {}
            for src in rule.get("sources") or []:
                roles.setdefault(src, "source")
            for join in rule.get("joins") or []:
                roles.setdefault(join["table"], "source")
            if rule.get("target"):
                roles[rule["target"]] = "target"
            con.executemany("INSERT INTO rule_objects VALUES (?,?,?)", [(cur.lastrowid, o, r) for o, r in roles.items() if o])
        fts = has_trigram()
        if fts:
            con.execute("CREATE VIRTUAL TABLE search_fts USING fts5(name_norm, desc_norm, content='search', "
                        "content_rowid='rowid', tokenize='trigram')")
            con.execute("INSERT INTO search_fts(rowid, name_norm, desc_norm) SELECT rowid, name_norm, desc_norm FROM search")
        meta = {**meta, "fts": "trigram" if fts else "like"}
        con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        con.executescript(INDEXES)
        con.commit()
    finally:
        con.close()
    for attempt in range(20):                      # sous Windows, un lecteur peut garder le fichier un instant
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


# --------------------------------------------------------------------------- lecture


def connect(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise FileNotFoundError("Aucune donnée importée pour l'instant.")
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


def get_meta(con) -> dict:
    return {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}


def all_objects(con) -> list[dict]:
    return _rows(con.execute("SELECT id, database, name, type, nb_columns FROM objects ORDER BY database, name"))


def get_object(con, obj_id: str) -> Optional[dict]:
    row = con.execute("SELECT * FROM objects WHERE id = ?", (obj_id,)).fetchone()
    return dict(row) if row else None


def get_objects(con, ids: Iterable[str]) -> dict[str, dict]:
    ids, result = list(ids), {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for row in con.execute(f"SELECT * FROM objects WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            result[row["id"]] = dict(row)
    return result


def get_columns(con, obj_id: str) -> list[dict]:
    return _rows(con.execute("SELECT name, data_type, description, position FROM columns WHERE object_id = ? "
                             "ORDER BY position, name", (obj_id,)))


def column_types(con, obj_id: str) -> dict[str, str]:
    return {r["name"]: r["data_type"] for r in con.execute("SELECT name, data_type FROM columns WHERE object_id = ?",
                                                             (obj_id,))}


def column_occurrences(con, name: str, similar: bool = False) -> list[dict]:
    """Tables qui portent une colonne (nom exact, ou aussi noms proches : même nom normalisé / contenu)."""
    name = name.lower()
    if not similar:
        return _rows(con.execute("SELECT object_id, name, data_type FROM columns WHERE name = ? ORDER BY object_id",
                                 (name,)))
    row = con.execute("SELECT name_norm FROM column_stats WHERE name = ?", (name,)).fetchone()
    norm = row["name_norm"] if row else name
    return _rows(con.execute(
        "SELECT object_id, name, data_type FROM columns WHERE name = :n OR name_norm = :norm OR name LIKE :like "
        "ORDER BY object_id, name", {"n": name, "norm": norm, "like": f"%{name}%"}))


def column_dictionary(con, query: str = "", limit: int = 200, offset: int = 0, conflicts_only: bool = False) -> dict:
    where, params = [], {"limit": limit, "offset": offset}
    if query:
        where.append("name LIKE :q")
        params["q"] = f"%{norm_text(query)}%"
    if conflicts_only:
        where.append("conflict = 1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT count(*) FROM column_stats{clause}", params).fetchone()[0]
    rows = _rows(con.execute(f"SELECT * FROM column_stats{clause} ORDER BY nb_objects DESC, name "
                             "LIMIT :limit OFFSET :offset", params))
    for row in rows:
        row["types"] = json.loads(row["types"])
    return {"total": total, "items": rows}


def search_hits(con, query: str, in_desc: bool = True, kinds: tuple = ("OBJECT", "COLUMN"),
                limit: int = 5000) -> list[dict]:
    """Toutes les correspondances (objets et colonnes) d'un texte, sur l'ensemble du catalogue."""
    q = norm_text(query).strip()
    if not q:
        return []
    params = {"like": f"%{q}%", "limit": limit}
    if len(q) >= 3 and get_meta(con).get("fts") == "trigram":
        params["match"] = ("{name_norm desc_norm} : " if in_desc else "name_norm : ") + '"' + q.replace('"', '""') + '"'
        cond = "s.rowid IN (SELECT rowid FROM search_fts WHERE search_fts MATCH :match)"
    else:
        cond = "(s.name_norm LIKE :like" + (" OR s.desc_norm LIKE :like)" if in_desc else ")")
    kind_clause = " AND s.kind IN (" + ",".join(f"'{k}'" for k in kinds) + ")"
    return _rows(con.execute(f"SELECT s.kind, s.object_id, s.column_name, s.data_type FROM search s WHERE {cond}"
                             f"{kind_clause} LIMIT :limit", params))


def rules_for(con, obj_id: str) -> list[dict]:
    """Règles de gestion (SQL) où l'objet est la cible ou une source, avec son rôle."""
    rows = con.execute("SELECT r.data, o.role FROM rule_objects o JOIN rules r ON r.rowid = o.rule_id "
                       "WHERE o.object_id = ? ORDER BY o.role DESC, r.rowid", (obj_id,))
    return [{**json.loads(r["data"]), "role": r["role"]} for r in rows]


def suggestions_all(con) -> list[dict]:
    return [json.loads(r["data"]) for r in con.execute("SELECT data FROM suggestions ORDER BY score DESC, key")]


def suggestions_for(con, obj_id: str) -> list[dict]:
    return [json.loads(r["data"]) for r in con.execute(
        "SELECT data FROM suggestions WHERE a = ? OR b = ? ORDER BY score DESC", (obj_id, obj_id))]


def suggestion(con, key: str) -> Optional[dict]:
    row = con.execute("SELECT data FROM suggestions WHERE key = ?", (key,)).fetchone()
    return json.loads(row["data"]) if row else None

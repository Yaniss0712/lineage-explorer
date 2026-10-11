"""Base SQLite (`data/lineage.db`) : schéma, écriture du build et requêtes de lecture.

- `write_database(path, catalog, edges, meta)` : écrit une base complète (fichier temporaire
  puis remplacement, pour ne pas gêner un serveur en cours d'exécution) ;
- `connect(path)` : connexion en lecture (une par requête HTTP) ;
- fonctions `get_*`, `search`, `flow_neighbours`… utilisées par l'API.

Recherche : table FTS5 avec tokenizer `trigram` (recherche de sous-chaîne rapide) si
disponible, sinon repli `LIKE` sur les noms normalisés.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Iterable, Optional

from .catalog import Catalog
from .layers import layer_rank
from .normalize import strip_accents

SCHEMA = """
CREATE TABLE objects (
  id TEXT PRIMARY KEY, layer TEXT, layer_rank INTEGER, database TEXT, name TEXT, type TEXT,
  description TEXT, owner TEXT, nb_columns INTEGER, has_sql INTEGER, nb_edges INTEGER DEFAULT 0
);
CREATE TABLE columns (
  id TEXT PRIMARY KEY, object_id TEXT, name TEXT, name_norm TEXT,
  data_type TEXT, is_key INTEGER, description TEXT, position INTEGER
);
CREATE TABLE edges (
  id INTEGER PRIMARY KEY, kind TEXT, source_object TEXT, target_object TEXT,
  source_column TEXT, target_column TEXT, origin TEXT, score REAL, explanation TEXT,
  transformation TEXT, job TEXT, details TEXT, parent_id INTEGER
);
CREATE TABLE column_stats (name_norm TEXT PRIMARY KEY, nb_objects INTEGER, idf REAL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE search (
  rowid INTEGER PRIMARY KEY, kind TEXT, ref_id TEXT, object_id TEXT, layer TEXT, layer_rank INTEGER,
  database TEXT, name TEXT, name_norm TEXT, desc_norm TEXT, data_type TEXT, short_norm TEXT
);
"""

INDEXES = """
CREATE INDEX idx_edges_src ON edges(source_object, kind);
CREATE INDEX idx_edges_tgt ON edges(target_object, kind);
CREATE INDEX idx_edges_src_col ON edges(source_object, source_column);
CREATE INDEX idx_edges_tgt_col ON edges(target_object, target_column);
CREATE INDEX idx_edges_parent ON edges(parent_id);
CREATE INDEX idx_columns_obj ON columns(object_id);
CREATE INDEX idx_columns_norm ON columns(name_norm);
CREATE INDEX idx_search_name ON search(name_norm);
CREATE INDEX idx_objects_layer ON objects(layer);
"""


def norm_text(text: str) -> str:
    """Texte normalisé pour la recherche (minuscules, sans accents)."""
    return strip_accents(text or "").lower()


def has_trigram() -> bool:
    """Vrai si SQLite dispose de FTS5 avec le tokenizer trigram."""
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        con.close()
        return True
    except sqlite3.Error:
        return False


# --------------------------------------------------------------------------- écriture


def write_database(path: str, catalog: Catalog, edges: list[dict], meta: dict) -> None:
    """Écrit la base complète dans un fichier temporaire puis remplace l'ancienne."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
        con.executescript(SCHEMA)
        _write_objects(con, catalog, edges)
        _write_edges(con, edges)
        _write_stats(con, catalog)
        fts = has_trigram()
        if fts:
            con.execute("CREATE VIRTUAL TABLE search_fts USING fts5(name_norm, desc_norm, "
                        "content='search', content_rowid='rowid', tokenize='trigram')")
            con.execute("INSERT INTO search_fts(rowid, name_norm, desc_norm) "
                        "SELECT rowid, name_norm, desc_norm FROM search")
        meta = {**meta, "fts": "trigram" if fts else "like"}
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        con.executescript(INDEXES)
        con.commit()
    finally:
        con.close()
    for attempt in range(20):  # sous Windows, un lecteur peut garder le fichier ouvert un instant
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def _write_objects(con: sqlite3.Connection, catalog: Catalog, edges: list[dict]) -> None:
    degree: dict[str, int] = {}
    for edge in edges:
        for end in (edge["source_object"], edge["target_object"]):
            degree[end] = degree.get(end, 0) + 1
    obj_rows, col_rows, search_rows = [], [], []
    for obj in catalog.objects.values():
        rank = layer_rank(obj.layer)
        obj_rows.append((obj.id, obj.layer, rank, obj.database, obj.name, obj.type, obj.description,
                         obj.owner, len(obj.columns), 1 if obj.sql else 0, degree.get(obj.id, 0)))
        search_rows.append(("OBJECT", obj.id, obj.id, obj.layer, rank, obj.database, obj.id,
                            norm_text(obj.id), norm_text(obj.description), None, norm_text(obj.name)))
        for col in obj.columns:
            col_id = f"{obj.id}.{col.name}"
            col_rows.append((col_id, obj.id, col.name, col.norm, col.data_type, 1 if col.is_key else 0,
                             col.description, col.position))
            search_rows.append(("COLUMN", col_id, obj.id, obj.layer, rank, obj.database, col.name,
                                norm_text(col.name), norm_text(col.description), col.data_type, norm_text(col.name)))
    con.executemany("INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)", obj_rows)
    con.executemany("INSERT INTO columns VALUES (?,?,?,?,?,?,?,?)", col_rows)
    con.executemany("INSERT INTO search(kind, ref_id, object_id, layer, layer_rank, database, name, "
                    "name_norm, desc_norm, data_type, short_norm) VALUES (?,?,?,?,?,?,?,?,?,?,?)", search_rows)


def _write_edges(con: sqlite3.Connection, edges: list[dict]) -> None:
    for edge in edges:
        cur = con.execute(
            "INSERT INTO edges(kind, source_object, target_object, source_column, target_column, origin, "
            "score, explanation, transformation, job, details, parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (edge["kind"], edge["source_object"], edge["target_object"], edge.get("source_column"),
             edge.get("target_column"), edge["origin"], edge["score"], edge["explanation"],
             edge.get("transformation"), edge.get("job"), json.dumps(edge.get("details") or {}, ensure_ascii=False)))
        parent = cur.lastrowid
        con.executemany(
            "INSERT INTO edges(kind, source_object, target_object, source_column, target_column, origin, "
            "score, explanation, transformation, job, details, parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [("FLOW", edge["source_object"], edge["target_object"], c["source"], c["target"], edge["origin"],
              c.get("score", edge["score"]), None, c.get("transformation"), edge.get("job"), None, parent)
             for c in edge.get("columns") or []])


def _write_stats(con: sqlite3.Connection, catalog: Catalog) -> None:
    con.executemany("INSERT INTO column_stats VALUES (?,?,?)",
                    [(norm, count, round(catalog.idf(norm), 4)) for norm, count in catalog.df.items()])


# --------------------------------------------------------------------------- lecture


def connect(path: str) -> sqlite3.Connection:
    """Ouvre la base en lecture."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Base introuvable : {path}. Lancez d'abord `python -m engine build`.")
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _rows(cur: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in cur]


def _edge_row(row: sqlite3.Row) -> dict:
    edge = dict(row)
    edge["details"] = json.loads(edge["details"]) if edge.get("details") else {}
    return edge


def get_meta(con: sqlite3.Connection) -> dict:
    return {row["key"]: json.loads(row["value"]) for row in con.execute("SELECT key, value FROM meta")}


def get_object(con: sqlite3.Connection, obj_id: str) -> Optional[dict]:
    row = con.execute("SELECT * FROM objects WHERE id = ?", (obj_id,)).fetchone()
    return dict(row) if row else None


def get_objects(con: sqlite3.Connection, ids: Iterable[str]) -> dict[str, dict]:
    ids = list(ids)
    result: dict[str, dict] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for row in con.execute(f"SELECT * FROM objects WHERE id IN ({marks})", chunk):
            result[row["id"]] = dict(row)
    return result


def get_columns(con: sqlite3.Connection, obj_id: str) -> list[dict]:
    return _rows(con.execute("SELECT * FROM columns WHERE object_id = ? ORDER BY position, name", (obj_id,)))


def object_edges(con: sqlite3.Connection, obj_id: str, kind: Optional[str] = None,
                 direction: str = "both") -> list[dict]:
    """Arêtes de niveau objet touchant `obj_id` (direction : up, down, both)."""
    clauses = []
    if direction in ("up", "both"):
        clauses.append("target_object = :id")
    if direction in ("down", "both"):
        clauses.append("source_object = :id")
    sql = f"SELECT * FROM edges WHERE parent_id IS NULL AND ({' OR '.join(clauses)})"
    params: dict[str, Any] = {"id": obj_id}
    if kind:
        sql += " AND kind = :kind"
        params["kind"] = kind
    return [_edge_row(r) for r in con.execute(sql, params)]


def column_edges(con: sqlite3.Connection, parent_id: int) -> list[dict]:
    return _rows(con.execute("SELECT source_column, target_column, transformation, score FROM edges "
                             "WHERE parent_id = ? ORDER BY target_column", (parent_id,)))


def column_neighbours(con: sqlite3.Connection, obj_id: str, column: str, direction: str) -> list[dict]:
    """Arêtes colonne amont (`up`) ou aval (`down`) d'une colonne."""
    if direction == "up":
        sql = "SELECT * FROM edges WHERE parent_id IS NOT NULL AND target_object = ? AND target_column = ?"
    else:
        sql = "SELECT * FROM edges WHERE parent_id IS NOT NULL AND source_object = ? AND source_column = ?"
    return [_edge_row(r) for r in con.execute(sql, (obj_id, column))]


def edges_by_origin(con: sqlite3.Connection, kind: str, origin: str) -> list[dict]:
    return [_edge_row(r) for r in con.execute(
        "SELECT * FROM edges WHERE parent_id IS NULL AND kind = ? AND origin = ?", (kind, origin))]


def search(con: sqlite3.Connection, query: str, layer: Optional[str] = None, database: Optional[str] = None,
           kind: Optional[str] = None, dtype: Optional[str] = None, in_desc: bool = False,
           page: int = 1, page_size: int = 50) -> dict:
    """Recherche objets + colonnes (partielle, insensible à la casse et aux accents)."""
    q = norm_text(query).strip()
    where, params = [], {}
    use_fts = q and len(q) >= 3 and get_meta(con).get("fts") == "trigram"
    if q:
        params["q"] = q
        params["like"] = f"%{q}%"
        if use_fts:
            phrase = '"' + q.replace('"', '""') + '"'
            params["match"] = ("{name_norm desc_norm} : " if in_desc else "name_norm : ") + phrase
            where.append("s.rowid IN (SELECT rowid FROM search_fts WHERE search_fts MATCH :match)")
        else:
            where.append("(s.name_norm LIKE :like" + (" OR s.desc_norm LIKE :like)" if in_desc else ")"))
    if layer:
        layers = [x for x in layer.upper().split(",") if x]
        where.append("s.layer IN (" + ",".join(f":l{i}" for i in range(len(layers))) + ")")
        params.update({f"l{i}": x for i, x in enumerate(layers)})
    if database:
        where.append("s.database = :db")
        params["db"] = database.lower()
    if kind:
        where.append("s.kind = :kind")
        params["kind"] = kind.upper()
    if dtype:
        where.append("lower(s.data_type) LIKE :dtype")
        params["dtype"] = f"{dtype.lower()}%"
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT count(*) FROM search s{clause}", params).fetchone()[0]
    params.update({"limit": page_size, "offset": (max(1, page) - 1) * page_size, "q": q or ""})
    rows = con.execute(
        f"SELECT s.kind, s.ref_id, s.object_id, s.layer, s.database, s.name, s.data_type FROM search s{clause} "
        "ORDER BY CASE WHEN s.short_norm = :q THEN 0 WHEN s.short_norm LIKE :q || '%' THEN 1 ELSE 2 END, "
        "CASE s.layer_rank WHEN -1 THEN 9 ELSE s.layer_rank END, s.kind DESC, s.ref_id "
        "LIMIT :limit OFFSET :offset", params)
    return {"total": total, "page": page, "page_size": page_size, "results": _rows(rows)}


def review_candidates(con: sqlite3.Connection, limit: int) -> list[dict]:
    """Flux inférés triés pour la revue : marts d'abord, scores intermédiaires, objets connectés."""
    rows = con.execute(
        "SELECT e.*, o.layer AS target_layer, o.nb_edges AS target_degree FROM edges e "
        "JOIN objects o ON o.id = e.target_object "
        "WHERE e.parent_id IS NULL AND e.kind = 'FLOW' AND e.origin = 'INFERRED' "
        "ORDER BY (o.layer = 'MART') DESC, (e.score BETWEEN 0.5 AND 0.75) DESC, o.nb_edges DESC, e.score DESC "
        "LIMIT ?", (limit,))
    return [_edge_row(r) for r in rows]

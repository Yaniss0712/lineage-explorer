"""Serveur HTTP local (stdlib) : API JSON + fichiers statiques de `web/` (docs/03 § 5).

Routes principales :
  GET  /api/search?q=&layer=&db=&kind=&dtype=&desc=1&page=
  GET  /api/object/{id}
  GET  /api/edge/{id}
  GET  /api/graph/{id}?depth=2&dir=up|down|both&rel=1&min_score=0.4&rejected=0
  GET  /api/lineage/column?object=&column=&dir=up|down&depth=
  GET  /api/path?from=&to=&min_score=
  GET  /api/review?limit=
  GET  /api/stats
  GET  /api/impact/{id}.csv
  GET  /api/examples
  POST /api/annotations/{object_id}   {author, spec_url | note | delete_note | link}
  POST /api/import                     corps = JSON de métadonnées (ou ?example=nom)

Le serveur écoute sur 127.0.0.1 par défaut et ne fait aucun appel sortant.
"""

from __future__ import annotations

import csv
import functools
import io
import json
import mimetypes
import os
import threading
import traceback
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import build, ingest, lineage, sql_ingest, store
from .annotations import AnnotationStore, link_key, manual_edges, merge_edge
from .config import PROJECT_ROOT, resolve_path

WEB_DIR = os.path.join(PROJECT_ROOT, "web")
EXAMPLES_DIR = os.path.join(PROJECT_ROOT, "examples")
EXAMPLE_DIRS = [os.path.join(PROJECT_ROOT, "architecture test"), EXAMPLES_DIR]
MAX_BODY = 512 * 1024 * 1024


class ApiError(Exception):
    """Erreur renvoyée au client avec un code HTTP."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _closing(method):
    """Décorateur : ferme les connexions SQLite ouvertes pendant l'appel."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            self.release()
    return wrapper


# --------------------------------------------------------------------------- logique API


class LineageApi:
    """Logique de l'API, indépendante du transport HTTP (testable directement)."""

    def __init__(self, config: dict):
        self.config = config
        self.db_path = resolve_path(config["paths"]["database"])
        self.notes = AnnotationStore(resolve_path(config["paths"]["annotations_dir"]))
        self.build_lock = threading.Lock()
        self._local = threading.local()

    # ---- outils

    def con(self):
        """Connexion du thread courant, fermée à la fin de la requête (cf. `_closing`)."""
        try:
            con = store.connect(self.db_path)
        except FileNotFoundError as exc:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        self._local.__dict__.setdefault("cons", []).append(con)
        return con

    def release(self) -> None:
        """Ferme les connexions ouvertes par le thread courant (indispensable sous Windows
        pour que le build puisse remplacer le fichier de base)."""
        for con in self._local.__dict__.pop("cons", []):
            con.close()

    def _manual_for(self, links: dict, node: str, kind: str, direction: str) -> list[dict]:
        """Liens manuels touchant `node` dans la direction demandée."""
        result = []
        for edge in manual_edges(links, set()):
            if edge["kind"] != kind:
                continue
            up = edge["target_object"] == node
            down = edge["source_object"] == node
            if kind == "RELATION" and (up or down):
                result.append(edge)
            elif (direction in ("up", "both") and up) or (direction in ("down", "both") and down):
                result.append(edge)
        return result

    def merged_edges(self, con, links: dict, node: str, kind: str, direction: str = "both",
                     include_rejected: bool = False) -> list[dict]:
        """Arêtes de niveau objet fusionnées avec les annotations (+ liens manuels)."""
        edges = [merge_edge(e, links) for e in store.object_edges(con, node, kind, direction)]
        pairs = {(e["source_object"], e["target_object"]) for e in edges}
        if kind == "RELATION":
            pairs |= {(t, s) for s, t in pairs}
        for manual in self._manual_for(links, node, kind, direction):
            if (manual["source_object"], manual["target_object"]) not in pairs:
                edges.append(manual)
                pairs.add((manual["source_object"], manual["target_object"]))
        if not include_rejected:
            edges = [e for e in edges if e["status"] != "REJECTED"]
        return edges

    def _node(self, obj: dict, specs: dict, depth: int = 0) -> dict:
        return {"id": obj["id"], "layer": obj["layer"], "database": obj["database"], "name": obj["name"],
                "type": obj["type"], "nb_columns": obj["nb_columns"], "has_spec": obj["id"] in specs,
                "depth": depth}

    @staticmethod
    def _edge_out(edge: dict) -> dict:
        keys = ("id", "kind", "source_object", "target_object", "source_column", "target_column", "origin",
                "status", "score", "base_score", "explanation", "job", "annotation", "details")
        return {k: edge.get(k) for k in keys}

    # ---- lecture

    @_closing
    def search(self, params: dict) -> dict:
        con = self.con()
        page_size = self.config["ui"]["page_size"]
        return store.search(con, params.get("q", ""), layer=params.get("layer"), database=params.get("db"),
                            kind=params.get("kind"), dtype=params.get("dtype"),
                            in_desc=params.get("desc") == "1", page=int(params.get("page", 1)),
                            page_size=page_size)

    @_closing
    def object_detail(self, obj_id: str) -> dict:
        con = self.con()
        obj = store.get_object(con, obj_id)
        if obj is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {obj_id}")
        links = self.notes.all_links()
        columns = store.get_columns(con, obj_id)
        flows = self.merged_edges(con, links, obj_id, "FLOW", "both", include_rejected=True)
        rels = self.merged_edges(con, links, obj_id, "RELATION", "both", include_rejected=True)
        explained = set()
        for edge in flows:
            if edge["target_object"] != obj_id or edge["status"] == "REJECTED":
                continue
            if isinstance(edge.get("id"), int):
                explained |= {c["target_column"] for c in store.column_edges(con, edge["id"])}
            elif edge.get("target_column"):
                explained.add(edge["target_column"])
        for link in links.values():
            if link["kind"] == "FLOW" and link["status"] == "MANUAL" and link["target"] == obj_id and link.get("target_column"):
                explained.add(link["target_column"])
        unexplained = [] if obj["layer"] == "LAC" else [c["name"] for c in columns if c["name"] not in explained]
        return {
            "object": obj, "columns": columns,
            "upstream": [self._edge_out(e) for e in flows if e["target_object"] == obj_id],
            "downstream": [self._edge_out(e) for e in flows if e["source_object"] == obj_id],
            "relations": [self._edge_out(e) for e in rels],
            "unexplained_columns": unexplained,
            "annotations": self.notes.load(obj_id),
        }

    @_closing
    def edge_detail(self, edge_id: str) -> dict:
        con = self.con()
        links = self.notes.all_links()
        if edge_id.startswith("m:"):
            for edge in manual_edges(links, set()):
                if edge["id"] == edge_id:
                    return {"edge": edge, "columns": []}
            raise ApiError(HTTPStatus.NOT_FOUND, "Lien manuel introuvable")
        row = con.execute("SELECT * FROM edges WHERE id = ?", (int(edge_id),)).fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Lien introuvable")
        edge = merge_edge(store._edge_row(row), links)
        columns = store.column_edges(con, edge["id"])
        for col in columns:
            key = link_key("FLOW", edge["source_object"], col["source_column"], edge["target_object"], col["target_column"])
            col["status"] = links[key]["status"] if key in links else None
        manual_cols = [{"source_column": l["source_column"], "target_column": l["target_column"],
                        "transformation": l.get("comment") or None, "score": 1.0, "status": "MANUAL"}
                       for l in links.values()
                       if l["kind"] == "FLOW" and l["status"] == "MANUAL" and l.get("source_column")
                       and l["source_object"] == edge["source_object"] and l["target"] == edge["target_object"]]
        return {"edge": self._edge_out(edge), "columns": columns + manual_cols}

    @_closing
    def graph(self, obj_id: str, params: dict) -> dict:
        con = self.con()
        center = store.get_object(con, obj_id)
        if center is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {obj_id}")
        ui = self.config["ui"]
        depth = max(1, min(int(params.get("depth", ui["default_depth"])), 20))
        direction = params.get("dir", "both")
        with_rel = params.get("rel", "1") == "1"
        rejected = params.get("rejected") == "1"
        min_score = float(params.get("min_score", self.config["inference"]["min_score"]))
        max_nodes = int(params.get("max_nodes", ui["max_nodes"]))
        links = self.notes.all_links()

        def neighbours(node, way):
            return [e for e in self.merged_edges(con, links, node, "FLOW", way, rejected)
                    if e["status"] != "SUGGESTED" or e["score"] >= min_score]

        depths = {obj_id: 0}
        edges: dict = {}
        truncated = 0
        for way, sign in (("up", -1), ("down", 1)):
            if direction not in (way, "both"):
                continue
            steps, cut = lineage.traverse(obj_id, way, neighbours, depth, 0.0, max_nodes)
            truncated += cut
            for step in steps:
                if step["node"] not in depths and len(depths) >= max_nodes:
                    continue
                depths.setdefault(step["node"], sign * step["depth"])
                edge = dict(step["edge"])
                edge["path_score"] = step["path_score"]
                edges[str(edge["id"])] = edge
        if with_rel:
            rels = [e for e in self.merged_edges(con, links, obj_id, "RELATION", "both", rejected)
                    if e["status"] != "SUGGESTED" or e["score"] >= self.config["relations"]["min_score"]]
            rels.sort(key=lambda e: (e["status"] == "SUGGESTED", -e["score"]))
            max_rel = int(params.get("max_rel", 12))
            truncated += max(0, len(rels) - max_rel)
            for edge in rels[:max_rel]:
                other = edge["target_object"] if edge["source_object"] == obj_id else edge["source_object"]
                if other not in depths:
                    if len(depths) >= max_nodes:
                        truncated += 1
                        continue
                    depths[other] = 0
                edges[str(edge["id"])] = edge
        objects = store.get_objects(con, depths)
        specs = self.notes.spec_urls()
        nodes = [self._node(objects[n], specs, d) for n, d in depths.items() if n in objects]
        visible = {n["id"] for n in nodes}
        out_edges = [{**self._edge_out(e), "path_score": e.get("path_score")} for e in edges.values()
                     if e["source_object"] in visible and e["target_object"] in visible]
        return {"center": obj_id, "nodes": nodes, "edges": out_edges, "truncated": truncated,
                "params": {"depth": depth, "dir": direction, "rel": with_rel, "min_score": min_score}}

    @_closing
    def column_lineage(self, params: dict) -> dict:
        con = self.con()
        obj_id, column = params.get("object", "").lower(), params.get("column", "").lower()
        direction = params.get("dir", "up")
        depth = max(1, min(int(params.get("depth", 10)), 30))
        min_score = float(params.get("min_score", 0.0))
        links = self.notes.all_links()

        def edge_allowed(src, tgt, src_col, tgt_col):
            for key in (link_key("FLOW", src, None, tgt, None), link_key("FLOW", src, src_col, tgt, tgt_col)):
                if key in links and links[key]["status"] == "REJECTED":
                    return False
            return True

        start = (obj_id, column)
        seen = {start: 1.0}
        frontier = [start]
        steps = []
        for level in range(1, depth + 1):
            nxt = []
            for node_obj, node_col in frontier:
                candidates = store.column_neighbours(con, node_obj, node_col, direction)
                for link in links.values():
                    if link["kind"] != "FLOW" or link["status"] != "MANUAL" or not link.get("source_column"):
                        continue
                    if direction == "up" and link["target"] == node_obj and link["target_column"] == node_col:
                        candidates.append({"source_object": link["source_object"], "source_column": link["source_column"],
                                           "target_object": node_obj, "target_column": node_col, "origin": "MANUAL",
                                           "score": 1.0, "transformation": link.get("comment") or None})
                    if direction == "down" and link["source_object"] == node_obj and link["source_column"] == node_col:
                        candidates.append({"source_object": node_obj, "source_column": node_col,
                                           "target_object": link["target"], "target_column": link["target_column"],
                                           "origin": "MANUAL", "score": 1.0, "transformation": link.get("comment") or None})
                for edge in candidates:
                    if not edge_allowed(edge["source_object"], edge["target_object"], edge["source_column"], edge["target_column"]):
                        continue
                    other = ((edge["source_object"], edge["source_column"]) if direction == "up"
                             else (edge["target_object"], edge["target_column"]))
                    score = 1.0 if link_key("FLOW", edge["source_object"], None, edge["target_object"], None) in links \
                        and links[link_key("FLOW", edge["source_object"], None, edge["target_object"], None)]["status"] == "VALIDATED" \
                        else edge["score"]
                    if edge["origin"] == "INFERRED" and score < min_score:
                        continue
                    path_score = round(seen[(node_obj, node_col)] * score, 3)
                    steps.append({"depth": level, "from": f"{node_obj}.{node_col}", "object": other[0], "column": other[1],
                                  "origin": edge["origin"], "score": score, "path_score": path_score,
                                  "transformation": edge.get("transformation")})
                    if other not in seen:
                        seen[other] = path_score
                        nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        return {"object": obj_id, "column": column, "dir": direction, "steps": steps,
                "objects": sorted({s["object"] for s in steps} | {obj_id})}

    @_closing
    def path(self, params: dict) -> dict:
        con = self.con()
        source, target = params.get("from", "").lower(), params.get("to", "").lower()
        min_score = float(params.get("min_score", self.config["inference"]["min_score"]))
        for oid in (source, target):
            if store.get_object(con, oid) is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {oid}")
        links = self.notes.all_links()
        previous = {source: None}
        queue = deque([(source, 0)])
        while queue and target not in previous and len(previous) < 20000:
            node, dist = queue.popleft()
            if dist >= 8:
                continue
            for kind in ("FLOW", "RELATION"):
                for edge in self.merged_edges(con, links, node, kind, "both"):
                    if edge["status"] == "SUGGESTED" and edge["score"] < min_score:
                        continue
                    other = edge["target_object"] if edge["source_object"] == node else edge["source_object"]
                    if other not in previous:
                        previous[other] = (node, edge)
                        queue.append((other, dist + 1))
        if target not in previous:
            return {"found": False, "nodes": [], "edges": []}
        chain, node = [], target
        while previous[node] is not None:
            parent, edge = previous[node]
            chain.append(edge)
            node = parent
        chain.reverse()
        ids, current = [source], source
        for edge in chain:
            current = edge["target_object"] if edge["source_object"] == current else edge["source_object"]
            ids.append(current)
        objects = store.get_objects(con, ids)
        specs = self.notes.spec_urls()
        return {"found": True, "nodes": [self._node(objects[i], specs, k) for k, i in enumerate(ids)],
                "edges": [self._edge_out(e) for e in chain]}

    @_closing
    def review(self, params: dict) -> dict:
        con = self.con()
        limit = int(params.get("limit", 50))
        links = self.notes.all_links()
        items = []
        for edge in store.review_candidates(con, limit * 5 + 100):
            merged = merge_edge(edge, links)
            if merged["status"] == "SUGGESTED":
                items.append({**self._edge_out(merged), "target_layer": edge["target_layer"]})
            if len(items) >= limit:
                break
        total = con.execute("SELECT count(*) FROM edges WHERE parent_id IS NULL AND kind='FLOW' AND origin='INFERRED'").fetchone()[0]
        reviewed = sum(1 for k, l in links.items() if l["kind"] == "FLOW" and l["status"] in ("VALIDATED", "REJECTED"))
        return {"items": items, "total_inferred": total, "reviewed": reviewed}

    @_closing
    def stats(self) -> dict:
        con = self.con()
        meta = store.get_meta(con)
        nb_objects = con.execute("SELECT count(*) FROM objects").fetchone()[0]
        links = self.notes.all_links().values()
        specs = self.notes.spec_urls()
        return {
            **meta,
            "annotations": {
                "spec_urls": len(specs),
                "spec_coverage": round(len(specs) / nb_objects, 3) if nb_objects else None,
                "validated": sum(1 for l in links if l["status"] == "VALIDATED"),
                "rejected": sum(1 for l in links if l["status"] == "REJECTED"),
                "manual": sum(1 for l in links if l["status"] == "MANUAL"),
            },
            "ui": self.config["ui"],
            "user": self.config["user"]["name"],
            "min_score": self.config["inference"]["min_score"],
        }

    @_closing
    def impact_csv(self, obj_id: str) -> str:
        con = self.con()
        if store.get_object(con, obj_id) is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {obj_id}")
        links = self.notes.all_links()
        min_score = self.config["inference"]["min_score"]
        steps, _ = lineage.traverse(
            obj_id, "down",
            lambda n, w: [e for e in self.merged_edges(con, links, n, "FLOW", w)
                          if e["status"] != "SUGGESTED" or e["score"] >= min_score],
            50)
        objects = store.get_objects(con, {s["node"] for s in steps})
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["profondeur", "objet_impacte", "couche", "via", "origine", "statut", "score_chemin"])
        done = set()
        for step in steps:
            if step["node"] in done:
                continue
            done.add(step["node"])
            edge = step["edge"]
            writer.writerow([step["depth"], step["node"], objects.get(step["node"], {}).get("layer", ""),
                             edge["source_object"], edge["origin"], edge["status"], step["path_score"]])
        return buffer.getvalue()

    def _example_files(self) -> dict[str, str]:
        """Fichiers de métadonnées fictifs proposés à l'import : nom affiché → chemin."""
        files = {}
        for folder in EXAMPLE_DIRS:
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            prefix = "" if folder == EXAMPLES_DIR else os.path.basename(folder) + "/"
            for name in names:
                if (name.startswith("metadata") and name.endswith((".json", ".jsonl"))) or name.endswith(".sql"):
                    files[prefix + name] = os.path.join(folder, name)
        return files

    def examples(self) -> list[str]:
        return list(self._example_files())

    # ---- écriture

    def annotate(self, obj_id: str, body: dict) -> dict:
        author = (body.pop("author", None) or self.config["user"]["name"]).strip()[:80]
        try:
            return self.notes.update(obj_id.lower(), body, author)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def import_metadata(self, raw: bytes, params: dict) -> dict:
        """Importe un JSON (corps de requête ou exemple) et relance le build."""
        if params.get("example"):
            name = params["example"]
            path = self._example_files().get(name)
            if path is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"Exemple introuvable : {name}")
            md, label = ingest.load_metadata(path), name
        else:
            try:
                text = raw.decode("utf-8-sig")
                if params.get("format") == "sql":
                    data = sql_ingest.parse_sql_script(text, params.get("default_db") or "default",
                                                       source=params.get("name") or "sql")
                elif params.get("format") == "jsonl":
                    data = {"meta": {}, "objects": [], "flows": [], "relations": []}
                    targets = {"object": "objects", "flow": "flows", "relation": "relations"}
                    for line in text.splitlines():
                        if line.strip():
                            rec = json.loads(line)
                            kind = rec.pop("record", "object")
                            if kind == "meta":
                                data["meta"].update(rec)
                            elif kind in targets:
                                data[targets[kind]].append(rec)
                else:
                    data = json.loads(text)
                md = ingest.parse_metadata(data)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"JSON invalide : {exc}") from exc
            label = params.get("name") or "import via l'interface"
        check = ingest.check_metadata(md, self.config)
        if not check["ok"]:
            return {"ok": False, "check": check}
        with self.build_lock:
            report = build.run_build(md, self.config, self.db_path, input_label=label)
        return {"ok": True, "check": check, "report": report}


# --------------------------------------------------------------------------- transport HTTP


def make_handler(api: LineageApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LineageExplorer/0.1"

        def log_message(self, fmt, *args):  # journal discret
            if self.path.startswith("/api/"):
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _handle(self, method: str) -> None:
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = unquote(url.path)
            try:
                if path.startswith("/api/"):
                    self._route(method, path[5:], params)
                elif method == "GET":
                    self._static(path)
                else:
                    raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "Méthode non autorisée")
            except ApiError as exc:
                self._json({"error": str(exc)}, exc.status)
            except (ValueError, KeyError) as exc:
                self._json({"error": f"Requête invalide : {exc}"}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # noqa: BLE001 — on renvoie l'erreur au client
                traceback.print_exc()
                self._json({"error": f"Erreur interne : {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Fichier trop gros pour l'import web ; utilisez la ligne de commande.")
            return self.rfile.read(length) if length else b""

        def _route(self, method: str, route: str, params: dict) -> None:
            if method == "GET":
                if route == "search":
                    return self._json(api.search(params))
                if route.startswith("object/"):
                    return self._json(api.object_detail(route[7:].lower()))
                if route.startswith("edge/"):
                    return self._json(api.edge_detail(route[5:]))
                if route.startswith("graph/"):
                    return self._json(api.graph(route[6:].lower(), params))
                if route == "lineage/column":
                    return self._json(api.column_lineage(params))
                if route == "path":
                    return self._json(api.path(params))
                if route == "review":
                    return self._json(api.review(params))
                if route == "stats":
                    return self._json(api.stats())
                if route == "examples":
                    return self._json(api.examples())
                if route.startswith("impact/") and route.endswith(".csv"):
                    obj_id = route[7:-4].lower()
                    body = ("﻿" + api.impact_csv(obj_id)).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="impact_{obj_id}.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return None
            if method == "POST":
                if route.startswith("annotations/"):
                    body = json.loads(self._body() or b"{}")
                    return self._json(api.annotate(route[12:], body))
                if route == "import":
                    return self._json(api.import_metadata(self._body(), params))
            raise ApiError(HTTPStatus.NOT_FOUND, f"Route inconnue : {route}")

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            full = os.path.realpath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.realpath(WEB_DIR)) or not os.path.isfile(full):
                raise ApiError(HTTPStatus.NOT_FOUND, "Fichier introuvable")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as handle:
                self._send(200, handle.read(), ctype)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    return Handler


def serve(config: dict, host: str | None = None, port: int | None = None) -> None:
    """Lance le serveur (bloquant)."""
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    host = host or config["server"]["host"]
    port = port or config["server"]["port"]
    api = LineageApi(config)
    httpd = ThreadingHTTPServer((host, port), make_handler(api))
    print(f"Lineage Explorer : http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    if not os.path.exists(api.db_path):
        print("Aucune base trouvée : importez un JSON depuis l'interface ou lancez `python -m engine build`.")
    print("Ctrl+C pour arrêter.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        httpd.server_close()

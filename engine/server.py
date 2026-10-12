"""Serveur HTTP local (stdlib) : API JSON + interface web.

Données :
- métadonnées (tables, colonnes) et suggestions : cache SQLite local, reconstruit depuis l'espace ;
- tags, liens, specs, notes, réglages, versions : état de l'équipe rejoué depuis le journal partagé.

Toutes les routes de lecture commencent par `sync()` : relecture du journal (nouvelles
modifications arrivées par la synchronisation Teams) et reconstruction du cache si les
métadonnées ou les colonnes ignorées ont changé.

Le serveur écoute sur 127.0.0.1 et ne fait aucun appel sortant.
"""

from __future__ import annotations

import csv
import fnmatch
import functools
import hashlib
import io
import json
import mimetypes
import os
import re
import socket
import threading
import traceback
from collections import Counter, defaultdict, deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import build, ingest, lineage, store
from .config import PROJECT_ROOT, RESOURCE_DIR
from .normalize import strip_accents
from .suggestions import pair_key
from .workspace import LINK_KINDS, Workspace, diff_metadata, diff_states, effective_tag, link_signature, new_id
from .xlsx import write_xlsx

WEB_DIR = os.path.join(RESOURCE_DIR, "web")
EXAMPLE_DIRS = [os.path.join(RESOURCE_DIR, "examples"), os.path.join(RESOURCE_DIR, "architecture test")]
MAX_BODY = 512 * 1024 * 1024
PALETTE = ["#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#ef4444", "#14b8a6", "#ec4899", "#84cc16", "#6366f1", "#f97316"]
UNTAGGED = "__none__"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _closing(method):
    """Ferme les connexions SQLite ouvertes pendant l'appel (indispensable sous Windows)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        finally:
            self.release()
    return wrapper


def norm(text: str) -> str:
    return strip_accents(text or "").lower().strip()


EVENT_LABELS = {
    "tag.save": "a enregistré un tag", "tag.delete": "a supprimé un tag", "object.tag": "a tagué des objets",
    "rule.save": "a créé une règle de tag", "rule.delete": "a supprimé une règle de tag",
    "link.create": "a créé un lien", "link.update": "a modifié un lien", "link.validate": "a validé un lien",
    "link.delete": "a supprimé un lien", "suggestion.dismiss": "a écarté une suggestion",
    "suggestion.restore": "a rétabli une suggestion", "object.update": "a documenté un objet",
    "note.add": "a ajouté une note", "note.delete": "a supprimé une note", "settings.update": "a modifié les réglages",
    "metadata.import": "a importé des métadonnées", "version.save": "a enregistré une version",
    "version.restore": "a restauré une version",
}


class LineageApi:
    """Logique de l'API, indépendante du transport HTTP (testable directement)."""

    def __init__(self, config: dict):
        self.config = config
        self.workspace = Workspace(config["paths"]["workspace"])
        key = hashlib.sha1(os.path.abspath(config["paths"]["workspace"]).lower().encode()).hexdigest()[:12]
        self.cache_path = os.path.join(config["paths"]["cache_dir"], f"lineage_{key}.db")
        self.build_lock = threading.Lock()
        self._local = threading.local()
        self._objects: dict[str, dict] = {}
        self._cache_key = None
        self.last_report = None
        self.sync(force=True)

    # ================================================================== outils

    def con(self):
        try:
            con = store.connect(self.cache_path)
        except FileNotFoundError as exc:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        self._local.__dict__.setdefault("cons", []).append(con)
        return con

    def release(self) -> None:
        for con in self._local.__dict__.pop("cons", []):
            con.close()

    @property
    def state(self) -> dict:
        return self.workspace.state

    def _settings(self) -> dict:
        return build.effective_settings(self.config["suggestions"], self.state["settings"])

    def _wanted_key(self):
        meta = self.state.get("metadata")
        return (meta["file"], tuple(self._settings()["ignore_columns"])) if meta else None

    def sync(self, force: bool = False) -> None:
        """Relit le journal partagé et reconstruit le cache local si nécessaire."""
        self.workspace.refresh(force=force)
        wanted = self._wanted_key()
        if wanted is None:
            self._objects, self._cache_key = {}, None
            return
        if wanted == self._cache_key and os.path.exists(self.cache_path):
            return
        with self.build_lock:
            current = None
            if os.path.exists(self.cache_path):
                try:
                    con = store.connect(self.cache_path)
                    meta = store.get_meta(con)
                    con.close()
                    current = (meta.get("metadata_file"), tuple(meta.get("ignore_columns", [])))
                except Exception:  # noqa: BLE001 — cache illisible : reconstruit
                    current = None
            if current != wanted:
                path = self.workspace.metadata_path()
                if path is None:
                    return                                   # fichier pas encore synchronisé
                md = ingest.load_metadata(path)
                self.last_report = build.build_cache(md, self._settings(), self.cache_path,
                                                     {"metadata_file": wanted[0], "ignore_columns": list(wanted[1])})
            con = store.connect(self.cache_path)
            try:
                self._objects = {o["id"]: o for o in store.all_objects(con)}
            finally:
                con.close()
            self._cache_key = wanted

    def tag_of(self, obj_id: str):
        obj = self._objects.get(obj_id)
        tag_id = effective_tag(self.state, obj_id, obj["name"] if obj else obj_id.split(".")[-1])
        return self.state["tags"].get(tag_id) if tag_id else None

    def hidden(self) -> set[str]:
        return set(self.state["settings"].get("hidden_objects", []))

    def node(self, obj_id: str, depth: int = 0) -> dict:
        obj = self._objects.get(obj_id) or {"id": obj_id, "database": obj_id.split(".")[0],
                                              "name": obj_id.split(".")[-1], "type": "?", "nb_columns": 0}
        tag = self.tag_of(obj_id)
        doc = self.state["objects"].get(obj_id, {})
        return {"id": obj_id, "database": obj["database"], "name": obj["name"], "type": obj["type"],
                "nb_columns": obj["nb_columns"], "tag_id": tag["id"] if tag else None,
                "tag": tag["name"] if tag else None, "color": tag["color"] if tag else None,
                "has_spec": bool(doc.get("spec_url")), "depth": depth, "missing": obj_id not in self._objects}

    def links(self, include_pending: bool = True) -> list[dict]:
        return [l for l in self.state["links"].values() if include_pending or l["status"] == "VALIDE"]

    def link_out(self, link: dict, types: dict | None = None) -> dict:
        """Lien enrichi : libellés et contrôle « à revoir » (objet ou colonne disparus)."""
        problems = [f"objet absent : {end}" for end in (link["source"], link["target"]) if end not in self._objects]
        if types is not None and not problems:
            for col in link.get("columns") or []:
                if col.get("source") and col["source"] not in types.get(link["source"], {}):
                    problems.append(f"colonne absente : {link['source']}.{col['source']}")
                if col.get("target") and col["target"] not in types.get(link["target"], {}):
                    problems.append(f"colonne absente : {link['target']}.{col['target']}")
        return {**link, "source_object": link["source"], "target_object": link["target"],
                "problems": problems, "edge_id": "l:" + link["id"]}

    def _types_for(self, con, ids) -> dict:
        return {i: store.column_types(con, i) for i in set(ids)}

    def _linked_pairs(self) -> set[str]:
        return {pair_key(l["source"], l["target"]) for l in self.state["links"].values()}

    def proposed_source(self, sug: dict):
        """Sens proposé : indication du fichier, sinon ordre des tags, sinon à choisir (None)."""
        if sug.get("direction"):
            return sug["direction"]
        ta, tb = self.tag_of(sug["a"]), self.tag_of(sug["b"])
        oa, ob = (ta or {}).get("order"), (tb or {}).get("order")
        if isinstance(oa, (int, float)) and isinstance(ob, (int, float)) and oa != ob:
            return sug["a"] if oa < ob else sug["b"]
        return None

    def sug_out(self, sug: dict) -> dict:
        return {**sug, "proposed_source": self.proposed_source(sug), "edge_id": "s:" + sug["key"],
                "source_object": sug["a"], "target_object": sug["b"], "status": "SUGGESTION",
                "kind": sug.get("kind") or "FLOW"}

    def visible_suggestions(self, sugs: list[dict]) -> list[dict]:
        hidden, linked, dismissed = self.hidden(), self._linked_pairs(), self.state["dismissed"]
        return [s for s in sugs if s["key"] not in dismissed and s["key"] not in linked
                and s["a"] not in hidden and s["b"] not in hidden]

    # ================================================================== état général

    def state_info(self, user: str) -> dict:
        self.sync()
        st = self.state
        counts = Counter(effective_tag(st, o["id"], o["name"]) or UNTAGGED for o in self._objects.values())
        tags = sorted(({**t, "count": counts.get(t["id"], 0)} for t in st["tags"].values()),
                      key=lambda t: (t.get("order") if isinstance(t.get("order"), (int, float)) else 9999,
                                     norm(t["name"])))
        links = list(st["links"].values())
        return {
            "revision": self.workspace.revision, "user": user, "has_data": bool(self._objects),
            "workspace": self.workspace.root, "tags": tags, "rules": list(st["rules"].values()),
            "settings": st["settings"], "metadata": st.get("metadata"),
            "counts": {"objects": len(self._objects), "untagged": counts.get(UNTAGGED, 0),
                       "links_pending": sum(1 for l in links if l["status"] == "A_VALIDER"),
                       "links_validated": sum(1 for l in links if l["status"] == "VALIDE")},
            "ui": self.config["ui"], "palette": PALETTE,
        }

    def describe(self, event: dict) -> dict:
        data, label = event.get("data") or {}, EVENT_LABELS.get(event["type"], event["type"])
        detail = ""
        if event["type"].startswith("link.") and data.get("id"):
            link = self.state["links"].get(data["id"]) or self.state["deleted_links"].get(data["id"]) or data
            if link.get("source"):
                detail = f"{link['source']} {'→' if link.get('kind') == 'FLOW' else '='} {link['target']}"
        elif event["type"] == "tag.save":
            detail = data.get("name", "")
        elif event["type"] == "object.tag":
            tag = self.state["tags"].get(data.get("tag_id") or "", {})
            detail = f"{len(data.get('object_ids', []))} objet(s) → {tag.get('name', 'sans tag')}"
        elif event["type"] in ("object.update", "note.add"):
            detail = data.get("id") if event["type"] == "object.update" else data.get("object_id", "")
        elif event["type"] in ("version.save", "version.restore"):
            detail = data.get("label", "")
        elif event["type"] == "metadata.import":
            detail = data.get("name", "")
        return {"id": event["id"], "at": event["ts"], "user": event.get("user"), "type": event["type"],
                "label": label, "detail": detail}

    def revision(self, since: str) -> dict:
        self.sync()
        changes = [self.describe(e) for e in self.workspace.events_since(since)] if since != self.workspace.revision else []
        return {"revision": self.workspace.revision, "changes": changes[-50:], "nb_changes": len(changes)}

    def activity(self, params: dict) -> list[dict]:
        self.sync()
        limit = int(params.get("limit", 200))
        return [self.describe(e) for e in reversed(self.workspace.events[-limit:])]

    # ================================================================== catalogue / recherche

    def tree(self, params: dict) -> list[dict]:
        self.sync()
        hidden = self.hidden() if params.get("hidden") != "1" else set()
        groups: dict[str, Counter] = defaultdict(Counter)
        for obj in self._objects.values():
            if obj["id"] in hidden:
                continue
            groups[effective_tag(self.state, obj["id"], obj["name"]) or UNTAGGED][obj["database"]] += 1
        result = []
        for tag_id, dbs in groups.items():
            tag = self.state["tags"].get(tag_id)
            result.append({"tag_id": tag_id, "name": tag["name"] if tag else "Sans tag", "color": tag["color"] if tag else None,
                           "order": tag.get("order") if tag else None, "count": sum(dbs.values()),
                           "databases": [{"database": d, "count": c} for d, c in sorted(dbs.items())]})
        result.sort(key=lambda g: (g["tag_id"] == UNTAGGED,
                                   g["order"] if isinstance(g["order"], (int, float)) else 9999, norm(g["name"])))
        return result

    def tree_objects(self, params: dict) -> list[dict]:
        self.sync()
        tag_id, database = params.get("tag"), params.get("db")
        hidden = self.hidden() if params.get("hidden") != "1" else set()
        items = [self.node(o["id"]) for o in self._objects.values()
                 if o["id"] not in hidden and (not database or o["database"] == database)
                 and (effective_tag(self.state, o["id"], o["name"]) or UNTAGGED) == tag_id]
        return sorted(items, key=lambda n: n["name"])

    _PREFIX_RE = re.compile(r'\b(tag|base|db|col|colonne|table):("[^"]*"|\S+)', re.IGNORECASE)

    @_closing
    def search(self, params: dict) -> dict:
        """Recherche sur tout le catalogue : tags, bases, tables, colonnes (préfixes possibles)."""
        self.sync()
        raw = params.get("q", "")
        filters = defaultdict(list)
        for key, value in self._PREFIX_RE.findall(raw):
            key = {"db": "base", "colonne": "col"}.get(key.lower(), key.lower())
            filters[key].append(norm(value.strip('"')))
        free = norm(self._PREFIX_RE.sub(" ", raw))
        if not free and not filters:
            return {"objects": [], "total": 0, "tags": [], "databases": []}
        con = self.con()
        hidden = self.hidden()
        objects = {o["id"]: o for o in self._objects.values() if o["id"] not in hidden}
        matched_cols: dict[str, list] = defaultdict(list)
        candidates = set(objects)
        for value in filters["tag"]:
            ok = {t for t, tag in self.state["tags"].items() if value in norm(tag["name"])}
            candidates &= {o for o in candidates if effective_tag(self.state, o, objects[o]["name"]) in ok}
        for value in filters["base"]:
            candidates &= {o for o in candidates if value in norm(objects[o]["database"])}
        for value in filters["table"]:
            candidates &= {o for o in candidates if value in norm(objects[o]["name"])}
        for value in filters["col"]:
            hits = store.search_hits(con, value, in_desc=False, kinds=("COLUMN",))
            ids = set()
            for h in hits:
                if h["object_id"] in candidates:
                    ids.add(h["object_id"])
                    matched_cols[h["object_id"]].append({"name": h["column_name"], "type": h["data_type"]})
            candidates &= ids
        tags_found, dbs_found = [], []
        if free:
            selected = set()
            tags_found = [t for t in self.state["tags"].values() if free in norm(t["name"])]
            tag_ids = {t["id"] for t in tags_found}
            dbs_found = sorted({o["database"] for o in objects.values() if free in norm(o["database"])})
            for oid in candidates:
                obj = objects[oid]
                if free in norm(obj["name"]) or obj["database"] in dbs_found \
                        or effective_tag(self.state, oid, obj["name"]) in tag_ids:
                    selected.add(oid)
            for h in store.search_hits(con, free, in_desc=params.get("desc", "1") == "1"):
                if h["object_id"] in candidates:
                    selected.add(h["object_id"])
                    if h["kind"] == "COLUMN" and not any(c["name"] == h["column_name"] for c in matched_cols[h["object_id"]]):
                        matched_cols[h["object_id"]].append({"name": h["column_name"], "type": h["data_type"]})
            candidates = selected
        limit = int(params.get("limit", 2000))
        ordered = sorted(candidates, key=lambda o: (objects[o]["database"], objects[o]["name"]))
        return {"objects": [{**self.node(o), "matched_columns": matched_cols.get(o, [])} for o in ordered[:limit]],
                "total": len(ordered), "truncated": len(ordered) > limit,
                "tags": [{"id": t["id"], "name": t["name"], "color": t["color"]} for t in tags_found],
                "databases": dbs_found}

    @_closing
    def columns(self, params: dict) -> dict:
        self.sync()
        return store.column_dictionary(self.con(), params.get("q", ""), int(params.get("limit", 200)),
                                       int(params.get("offset", 0)), params.get("conflicts") == "1")

    # ================================================================== objet, graphe, colonne

    @_closing
    def object_detail(self, obj_id: str) -> dict:
        self.sync()
        con = self.con()
        obj = store.get_object(con, obj_id)
        if obj is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {obj_id}")
        columns = store.get_columns(con, obj_id)
        related = [l for l in self.state["links"].values() if obj_id in (l["source"], l["target"])]
        types = self._types_for(con, [x for l in related for x in (l["source"], l["target"]) if x in self._objects])
        links = [self.link_out(l, types) for l in related]
        col_links = defaultdict(int)
        for l in related:
            for c in l.get("columns") or []:
                col_links[c.get("source") if l["source"] == obj_id else c.get("target")] += 1
        for col in columns:
            col["nb_links"] = col_links.get(col["name"], 0)
        sugs = [self.sug_out(s) for s in self.visible_suggestions(store.suggestions_for(con, obj_id))]
        return {
            "object": {**obj, **self.node(obj_id)}, "columns": columns,
            "upstream": [l for l in links if l["kind"] == "FLOW" and l["target"] == obj_id],
            "downstream": [l for l in links if l["kind"] == "FLOW" and l["source"] == obj_id],
            "joins": [l for l in links if l["kind"] == "JOIN"],
            "suggestions": sugs[:30],
            "doc": self.state["objects"].get(obj_id, {}), "notes": self.state["notes"].get(obj_id, []),
            "hidden": obj_id in self.hidden(),
        }

    def _flow_neighbours(self, include_pending: bool):
        up, down = defaultdict(list), defaultdict(list)
        for link in self.links(include_pending):
            if link["kind"] == "FLOW":
                edge = {**link, "source_object": link["source"], "target_object": link["target"], "score": 1.0}
                down[link["source"]].append(edge)
                up[link["target"]].append(edge)
        return lambda node, way: (up if way == "up" else down).get(node, [])

    @_closing
    def graph(self, obj_id: str, params: dict) -> dict:
        self.sync()
        if obj_id not in self._objects:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Objet inconnu : {obj_id}")
        ui = self.config["ui"]
        depth = max(1, min(int(params.get("depth", ui["default_depth"])), 20))
        direction = params.get("dir", "both")
        pending = params.get("pending", "1") == "1"
        max_nodes = int(params.get("max_nodes", ui["max_nodes"]))
        neighbours = self._flow_neighbours(pending)
        depths, edges, truncated = {obj_id: 0}, {}, 0
        for way, sign in (("up", -1), ("down", 1)):
            if direction not in (way, "both"):
                continue
            steps, cut = lineage.traverse(obj_id, way, neighbours, depth, 0.0, max_nodes)
            truncated += cut
            for step in steps:
                if step["node"] not in depths and len(depths) >= max_nodes:
                    truncated += 1
                    continue
                depths.setdefault(step["node"], sign * step["depth"])
                edges["l:" + step["edge"]["id"]] = self.link_out(step["edge"])
        if params.get("join", "1") == "1":
            for link in self.links(pending):
                if link["kind"] != "JOIN":
                    continue
                ends = (link["source"], link["target"])
                if obj_id in ends or (ends[0] in depths and ends[1] in depths):
                    for end in ends:
                        if end not in depths and len(depths) < max_nodes:
                            depths[end] = 0
                    if ends[0] in depths and ends[1] in depths:
                        edges["l:" + link["id"]] = self.link_out(link)
        if params.get("sugg", "1") == "1":
            con = self.con()
            sugs = self.visible_suggestions(store.suggestions_for(con, obj_id))[:ui["max_suggestions_in_graph"]]
            for sug in sugs:
                other = sug["b"] if sug["a"] == obj_id else sug["a"]
                if other not in depths:
                    if len(depths) >= max_nodes:
                        truncated += 1
                        continue
                    depths[other] = 0
                edges["s:" + sug["key"]] = self.sug_out(sug)
        nodes = [self.node(n, d) for n, d in depths.items()]
        return {"center": obj_id, "nodes": nodes, "edges": list(edges.values()), "truncated": truncated}

    @_closing
    def column_view(self, params: dict) -> dict:
        """Toutes les tables qui portent une colonne, leurs tags, types et liens entre elles."""
        self.sync()
        name = (params.get("name") or "").strip().lower()
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Nom de colonne manquant")
        con = self.con()
        hidden = self.hidden()
        occ = [o for o in store.column_occurrences(con, name, params.get("similar") == "1") if o["object_id"] not in hidden]
        by_obj = defaultdict(list)
        for o in occ:
            by_obj[o["object_id"]].append({"name": o["name"], "type": o["data_type"]})
        types = Counter((c["type"] or "?").split("(")[0] for cols in by_obj.values() for c in cols if c["name"] == name)
        ids = set(by_obj)
        links = [self.link_out(l) for l in self.state["links"].values() if l["source"] in ids and l["target"] in ids]
        tables = []
        for oid in sorted(by_obj, key=lambda o: ((self.tag_of(o) or {}).get("name") or "~", o)):
            main_type = next((c["type"] for c in by_obj[oid] if c["name"] == name), None)
            tables.append({**self.node(oid), "columns": by_obj[oid], "column_type": main_type,
                           "type_differs": len(types) > 1 and main_type is not None
                           and (main_type or "?").split("(")[0] != types.most_common(1)[0][0]})
        similar_names = sorted({c["name"] for cols in by_obj.values() for c in cols if c["name"] != name})
        return {"name": name, "tables": tables, "links": links, "types": dict(types),
                "type_conflict": len(types) > 1, "similar_names": similar_names}

    def column_lineage(self, params: dict) -> dict:
        """Lineage d'une colonne à travers les correspondances colonne → colonne des liens."""
        self.sync()
        obj_id, column = params.get("object", "").lower(), params.get("column", "").lower()
        direction = params.get("dir", "up")
        seen, frontier, steps = {(obj_id, column)}, [(obj_id, column)], []
        flows = [l for l in self.links(True) if l["kind"] == "FLOW"]
        for level in range(1, int(params.get("depth", 15)) + 1):
            nxt = []
            for node_obj, node_col in frontier:
                for link in flows:
                    for col in link.get("columns") or []:
                        if direction == "up" and link["target"] == node_obj and col.get("target") == node_col:
                            other = (link["source"], col.get("source"))
                        elif direction == "down" and link["source"] == node_obj and col.get("source") == node_col:
                            other = (link["target"], col.get("target"))
                        else:
                            continue
                        steps.append({"depth": level, "from": f"{node_obj}.{node_col}", "object": other[0],
                                      "column": other[1], "transformation": col.get("transformation"),
                                      "status": link["status"], "link_id": link["id"]})
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        return {"object": obj_id, "column": column, "dir": direction, "steps": steps,
                "objects": sorted({s["object"] for s in steps} | {obj_id})}

    def path(self, params: dict) -> dict:
        self.sync()
        source, target = params.get("from", "").lower(), params.get("to", "").lower()
        adjacency = defaultdict(list)
        for link in self.links(True):
            adjacency[link["source"]].append((link["target"], link))
            adjacency[link["target"]].append((link["source"], link))
        previous, queue = {source: None}, deque([source])
        while queue and target not in previous:
            node = queue.popleft()
            for other, link in adjacency[node]:
                if other not in previous:
                    previous[other] = (node, link)
                    queue.append(other)
        if target not in previous:
            return {"found": False, "nodes": [], "edges": []}
        chain, node = [], target
        while previous[node] is not None:
            parent, link = previous[node]
            chain.append(link)
            node = parent
        chain.reverse()
        ids, current = [source], source
        for link in chain:
            current = link["target"] if link["source"] == current else link["source"]
            ids.append(current)
        return {"found": True, "nodes": [self.node(i, k) for k, i in enumerate(ids)],
                "edges": [self.link_out(l) for l in chain]}

    @_closing
    def review(self, params: dict) -> dict:
        self.sync()
        con = self.con()
        pending = sorted((l for l in self.state["links"].values() if l["status"] == "A_VALIDER"),
                         key=lambda l: l["created_at"] or "", reverse=True)
        types = self._types_for(con, [x for l in pending for x in (l["source"], l["target"]) if x in self._objects])
        return {"items": [self.link_out(l, types) for l in pending],
                "recently_deleted": sorted(self.state["deleted_links"].values(),
                                           key=lambda l: l["deleted_at"] or "", reverse=True)[:20]}

    @_closing
    def suggestions(self, params: dict) -> dict:
        self.sync()
        sugs = self.visible_suggestions(store.suggestions_all(self.con()))
        if params.get("min_score"):
            sugs = [s for s in sugs if s["score"] >= float(params["min_score"])]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
        return {"total": len(sugs), "items": [self.sug_out(s) for s in sugs[offset:offset + limit]],
                "dismissed": len(self.state["dismissed"])}

    # ================================================================== exports, rapport, versions

    def impact_csv(self, obj_id: str) -> str:
        self.sync()
        steps, _ = lineage.traverse(obj_id, "down", self._flow_neighbours(True), 50)
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["profondeur", "objet_impacte", "tag", "via", "statut_du_lien"])
        done = set()
        for step in steps:
            if step["node"] in done:
                continue
            done.add(step["node"])
            writer.writerow([step["depth"], step["node"], (self.tag_of(step["node"]) or {}).get("name", ""),
                             step["edge"]["source"], step["edge"]["status"]])
        return buffer.getvalue()

    def export_xlsx(self) -> bytes:
        self.sync()
        st = self.state
        objects = [[o["id"], o["database"], o["name"], o["type"], (self.tag_of(o["id"]) or {}).get("name", ""),
                    st["objects"].get(o["id"], {}).get("description", ""), st["objects"].get(o["id"], {}).get("owner", ""),
                    st["objects"].get(o["id"], {}).get("spec_url", ""), o["nb_columns"]]
                   for o in sorted(self._objects.values(), key=lambda o: o["id"])]
        links = [[l["id"], "Flux" if l["kind"] == "FLOW" else "Jointure", l["source"], l["target"],
                  "validé" if l["status"] == "VALIDE" else "à valider", len(l.get("columns") or []), l.get("comment", ""),
                  l["created_by"], l["created_at"], l.get("validated_by") or "", l.get("validated_at") or ""]
                 for l in st["links"].values()]
        mapping = [[l["id"], l["source"], c.get("source"), l["target"], c.get("target"), c.get("transformation") or ""]
                   for l in st["links"].values() for c in l.get("columns") or []]
        counts = Counter(effective_tag(st, o["id"], o["name"]) for o in self._objects.values())
        tags = [[t["name"], t.get("color"), t.get("description", ""), t.get("order"), counts.get(t["id"], 0)]
                for t in st["tags"].values()]
        rules = [[r["pattern"], st["tags"].get(r["tag_id"], {}).get("name", "")] for r in st["rules"].values()]
        return write_xlsx([
            ("Objets", ["id", "base", "nom", "type", "tag", "description", "propriétaire", "spec", "nb colonnes"], objects),
            ("Liens", ["id", "type", "source", "cible", "statut", "nb colonnes", "commentaire", "créé par", "créé le",
                       "validé par", "validé le"], links),
            ("Colonnes liées", ["lien", "table source", "colonne source", "table cible", "colonne cible",
                                "transformation"], mapping),
            ("Tags", ["tag", "couleur", "description", "ordre", "nb objets"], tags),
            ("Règles de tag", ["motif", "tag"], rules),
        ])

    @_closing
    def report(self) -> dict:
        self.sync()
        meta = store.get_meta(self.con()) if self._objects else {}
        st, total = self.state, len(self._objects) or 1
        tagged = sum(1 for o in self._objects.values() if effective_tag(st, o["id"], o["name"]))
        linked = {x for l in st["links"].values() for x in (l["source"], l["target"])}
        return {"metadata": st.get("metadata"), "build": meta.get("report"), "built_at": meta.get("built_at"),
                "fts": meta.get("fts"), "indicators": {
                    "objects": len(self._objects), "tagged_rate": round(tagged / total, 3),
                    "linked_rate": round(len(linked & set(self._objects)) / total, 3),
                    "documented": sum(1 for d in st["objects"].values() if d.get("description") or d.get("spec_url")),
                    "links_validated": sum(1 for l in st["links"].values() if l["status"] == "VALIDE"),
                    "links_pending": sum(1 for l in st["links"].values() if l["status"] == "A_VALIDER"),
                    "links_deleted": len(st["deleted_links"]), "events": len(self.workspace.events)}}

    def versions(self) -> list[dict]:
        self.sync()
        return list(reversed(self.state["versions"]))

    def version_detail(self, params: dict) -> dict:
        self.sync()
        snapshot = self.workspace.read_version(params.get("file", ""))
        if snapshot is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Version introuvable (pas encore synchronisée ?)")
        return {"label": snapshot.get("label"), "comment": snapshot.get("comment"), "by": snapshot.get("by"),
                "at": snapshot.get("at"), "diff": diff_states(snapshot["state"], self.state)}

    # ================================================================== écriture

    def _require_object(self, obj_id: str) -> None:
        if obj_id not in self._objects:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"Objet inconnu : {obj_id}")

    def write(self, action: str, body: dict, user: str) -> dict:
        """Toutes les écritures passent par le journal partagé."""
        if action == "refresh":                       # bouton « Actualiser »
            self.workspace.refresh(force=True)
            self._cache_key = None
            self.sync(force=True)
            return {"ok": True, "revision": self.workspace.revision, "objects": len(self._objects)}
        self.sync()
        ws = self.workspace
        if action == "tags/save":
            name = (body.get("name") or "").strip()
            if not name:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Nom de tag obligatoire")
            if any(norm(t["name"]) == norm(name) and t["id"] != body.get("id") for t in self.state["tags"].values()):
                raise ApiError(HTTPStatus.CONFLICT, f"Le tag « {name} » existe déjà")
            order = body.get("order")
            order = float(order) if order not in (None, "") else None
            data = {"id": body.get("id") or new_id(), "name": name[:60],
                    "color": body.get("color") or PALETTE[len(self.state["tags"]) % len(PALETTE)],
                    "description": (body.get("description") or "")[:500], "order": order}
            ws.append("tag.save", data, user)
            return data
        if action == "tags/delete":
            ws.append("tag.delete", {"id": body["id"]}, user)
            return {"ok": True}
        if action == "tags/assign":
            tag_id = body.get("tag_id") or None
            if tag_id and tag_id not in self.state["tags"]:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Tag inconnu")
            ids = [i for i in body.get("object_ids", []) if i in self._objects]
            if not ids:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Aucun objet sélectionné")
            ws.append("object.tag", {"object_ids": ids, "tag_id": tag_id}, user)
            return {"ok": True, "count": len(ids)}
        if action == "rules/save":
            pattern = (body.get("pattern") or "").strip().lower()
            if not pattern or body.get("tag_id") not in self.state["tags"]:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Motif et tag obligatoires")
            data = {"id": body.get("id") or new_id(), "pattern": pattern, "tag_id": body["tag_id"]}
            ws.append("rule.save", data, user)
            matched = sum(1 for o in self._objects.values() if fnmatch.fnmatchcase(o["name"], pattern)
                          or fnmatch.fnmatchcase(o["id"], pattern))
            return {**data, "matched": matched}
        if action == "rules/delete":
            ws.append("rule.delete", {"id": body["id"]}, user)
            return {"ok": True}
        if action in ("links/create", "links/update"):
            return self._save_link(action, body, user)
        if action == "links/validate":
            if body.get("id") not in self.state["links"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "Lien introuvable (déjà supprimé ?)")
            ws.append("link.validate", {"id": body["id"]}, user)
            return {"ok": True}
        if action == "links/delete":
            if body.get("id") not in self.state["links"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "Lien introuvable (déjà supprimé ?)")
            ws.append("link.delete", {"id": body["id"], "reason": (body.get("reason") or "")[:300]}, user)
            return {"ok": True}
        if action == "suggestions/dismiss":
            ws.append("suggestion.dismiss", {"key": body["key"]}, user)
            return {"ok": True}
        if action == "suggestions/restore":
            ws.append("suggestion.restore", {"key": body["key"]}, user)
            return {"ok": True}
        if action == "object/update":
            self._require_object(body.get("id", ""))
            data = {"id": body["id"]}
            for key in ("description", "owner", "spec_url"):
                if key in body:
                    value = (body[key] or "").strip()
                    if key == "spec_url" and value and not re.match(r"^https?://", value, re.IGNORECASE):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "L'URL de spec doit commencer par http:// ou https://")
                    data[key] = value[:2000]
            ws.append("object.update", data, user)
            return {"ok": True}
        if action == "notes/add":
            self._require_object(body.get("object_id", ""))
            text = (body.get("text") or "").strip()
            if not text:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Note vide")
            ws.append("note.add", {"object_id": body["object_id"], "id": new_id(), "text": text[:4000]}, user)
            return {"ok": True}
        if action == "notes/delete":
            ws.append("note.delete", {"object_id": body["object_id"], "id": body["id"]}, user)
            return {"ok": True}
        if action == "settings/save":
            data = {k: body[k] for k in ("ignore_columns", "hidden_objects") if isinstance(body.get(k), list)}
            ws.append("settings.update", data, user)
            self.sync()
            return {"ok": True}
        if action == "versions/save":
            label = (body.get("label") or "").strip() or datetime.now().strftime("Version du %d/%m/%Y %H:%M")
            event = ws.save_version(label[:80], (body.get("comment") or "")[:1000], user)
            return {"ok": True, "file": event["data"]["file"], "label": label}
        if action == "versions/restore":
            try:
                ws.restore_version(body.get("file", ""), user)
            except ValueError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            return {"ok": True}
        raise ApiError(HTTPStatus.NOT_FOUND, f"Action inconnue : {action}")

    def _save_link(self, action: str, body: dict, user: str) -> dict:
        kind = str(body.get("kind", "FLOW")).upper()
        source, target = str(body.get("source", "")).lower(), str(body.get("target", "")).lower()
        if kind not in LINK_KINDS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Type de lien : FLOW (flux) ou JOIN (jointure)")
        self._require_object(source)
        self._require_object(target)
        if source == target:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Un lien relie deux objets différents")
        con = store.connect(self.cache_path)
        try:
            src_cols, tgt_cols = set(store.column_types(con, source)), set(store.column_types(con, target))
        finally:
            con.close()
        columns = []
        for col in body.get("columns") or []:
            s, t = (col.get("source") or "").lower(), (col.get("target") or "").lower()
            if not s and not t:
                continue
            if (s and s not in src_cols) or (t and t not in tgt_cols):
                raise ApiError(HTTPStatus.BAD_REQUEST, f"Colonne inconnue : {s or '?'} → {t or '?'}")
            columns.append({"source": s or None, "target": t or None, "transformation": (col.get("transformation") or "")[:500] or None})
        data = {"kind": kind, "source": source, "target": target, "columns": columns,
                "comment": (body.get("comment") or "")[:1000]}
        signature = link_signature(data)
        for link in self.state["links"].values():
            if link["id"] != body.get("id") and link_signature(link) == signature:
                raise ApiError(HTTPStatus.CONFLICT, "Ce lien existe déjà")
        if action == "links/update":
            if body.get("id") not in self.state["links"]:
                raise ApiError(HTTPStatus.NOT_FOUND, "Lien introuvable")
            self.workspace.append("link.update", {"id": body["id"], **data}, user)
            return {"ok": True, "id": body["id"]}
        data["id"] = new_id()
        data["suggestion_key"] = body.get("suggestion_key")
        self.workspace.append("link.create", data, user)
        if body.get("validate"):
            self.workspace.append("link.validate", {"id": data["id"]}, user)
        return {"ok": True, "id": data["id"]}

    # ================================================================== import

    def examples(self) -> dict[str, str]:
        files = {}
        for folder in EXAMPLE_DIRS:
            if not os.path.isdir(folder):
                continue
            prefix = "" if os.path.basename(folder) == "examples" else os.path.basename(folder) + "/"
            for name in sorted(os.listdir(folder)):
                if (name.startswith("metadata") and name.endswith((".json", ".jsonl"))) or name.endswith(".sql"):
                    files[prefix + name] = os.path.join(folder, name)
        return files

    def import_metadata(self, raw: bytes, params: dict, user: str) -> dict:
        """Importe des métadonnées (JSON, JSONL, SQL) dans l'espace partagé et reconstruit le cache."""
        self.sync()
        if params.get("example"):
            path = self.examples().get(params["example"])
            if path is None:
                raise ApiError(HTTPStatus.NOT_FOUND, f"Exemple introuvable : {params['example']}")
            md, name = ingest.load_metadata(path), params["example"]
        else:
            try:
                md = ingest.parse_text(raw.decode("utf-8-sig"), params.get("format", "json"), params.get("name") or "import")
            except (UnicodeDecodeError, ValueError) as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            name = params.get("name") or "import"
        check = ingest.check_metadata(md)
        if not check["ok"]:
            return {"ok": False, "check": check}
        previous_path = self.workspace.metadata_path()
        previous = ingest.load_metadata(previous_path) if previous_path else None
        file = self.workspace.save_metadata(md, name, user)
        diff = diff_metadata(previous, md)
        stats = {"nb_objects": check["nb_objects"], "nb_columns": check["nb_columns"]}
        self.workspace.append("metadata.import", {"file": file, "name": name, "stats": stats, "diff": diff}, user)
        self._cache_key = None
        self.sync(force=True)
        return {"ok": True, "check": check, "report": self.last_report, "diff": diff}


# ====================================================================== transport HTTP


def make_handler(api: LineageApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LineageExplorer/0.2"

        def log_message(self, fmt, *args):
            if self.command == "POST":
                super().log_message(fmt, *args)

        def _user(self, body: dict | None = None) -> str:
            name = (body or {}).pop("author", None) or unquote(self.headers.get("X-User") or "")
            name = re.sub(r"[^\w .@-]", "", name).strip()[:40]
            return (name or api.config["user"]["name"]).lower()

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Fichier trop gros pour l'import web")
            return self.rfile.read(length) if length else b""

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
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._json({"error": f"Erreur interne : {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                api.release()

        def _route(self, method: str, route: str, params: dict) -> None:
            if method == "GET":
                simple = {
                    "state": lambda: api.state_info(self._user()), "revision": lambda: api.revision(params.get("since", "")),
                    "tree": lambda: api.tree(params), "tree/objects": lambda: api.tree_objects(params),
                    "search": lambda: api.search(params), "columns": lambda: api.columns(params),
                    "column": lambda: api.column_view(params), "lineage/column": lambda: api.column_lineage(params),
                    "path": lambda: api.path(params), "review": lambda: api.review(params),
                    "suggestions": lambda: api.suggestions(params), "report": api.report, "versions": api.versions,
                    "version": lambda: api.version_detail(params), "activity": lambda: api.activity(params),
                    "examples": lambda: list(api.examples()),
                }
                if route in simple:
                    return self._json(simple[route]())
                if route.startswith("object/"):
                    return self._json(api.object_detail(route[7:].lower()))
                if route.startswith("graph/"):
                    return self._json(api.graph(route[6:].lower(), params))
                if route.startswith("impact/") and route.endswith(".csv"):
                    body = ("﻿" + api.impact_csv(route[7:-4].lower())).encode("utf-8")
                    return self._send(200, body, "text/csv; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="impact_{route[7:-4]}.csv"'})
                if route == "export.xlsx":
                    name = f"lineage_documentation_{datetime.now():%Y%m%d}.xlsx"
                    return self._send(200, api.export_xlsx(),
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            if method == "POST":
                if route == "import":
                    return self._json(api.import_metadata(self._body(), params, self._user()))
                body = json.loads(self._body() or b"{}")
                return self._json(api.write(route, body, self._user(body)))
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


class LocalServer(ThreadingHTTPServer):
    """Serveur qui refuse un port déjà utilisé. Sous Windows, SO_REUSEADDR permettrait à deux
    instances d'écouter le même port, avec des requêtes envoyées au hasard à l'une ou l'autre."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(config: dict, host: str | None = None, port: int | None = None) -> tuple[ThreadingHTTPServer, LineageApi]:
    """Crée le serveur ; si le port est pris, essaie les 20 suivants puis un port libre quelconque."""
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    host = host or config["server"]["host"]
    port = config["server"]["port"] if port is None else port
    api = LineageApi(config)
    handler = make_handler(api)
    for candidate in list(range(port, port + 20)) + [0]:
        try:
            return LocalServer((host, candidate), handler), api
        except OSError:
            continue
    raise OSError("Aucun port disponible")


def serve(config: dict, host: str | None = None, port: int | None = None) -> None:
    httpd, api = create_server(config, host, port)
    url = f"http://localhost:{httpd.server_address[1]}"
    print(f"Lineage Explorer : {url}")
    print(f"Espace de travail : {api.workspace.root}")
    print("Ctrl+C pour arrêter.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
    finally:
        httpd.server_close()

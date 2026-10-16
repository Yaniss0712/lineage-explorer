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

from . import build, ingest, lineage, sql_glot, store
from .config import PROJECT_ROOT, RESOURCE_DIR
from .normalize import strip_accents
from .suggestions import pair_key
from .workspace import (LINK_KINDS, OBJECT_TYPES, TICKET_KINDS, TICKET_STATUSES, Workspace, diff_metadata,
                        diff_states, effective_tag, link_ends, link_signature, new_id, safe_name)
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


def short_id(obj_id: str) -> str:
    return str(obj_id or "").split(".")[-1]


EVENT_LABELS = {
    "tag.save": "a enregistré un tag", "tag.delete": "a supprimé un tag", "object.tag": "a tagué des objets",
    "rule.save": "a créé une règle de tag", "rule.delete": "a supprimé une règle de tag",
    "link.create": "a créé un lien", "link.update": "a modifié un lien", "link.validate": "a validé un lien",
    "link.delete": "a supprimé un lien", "suggestion.dismiss": "a écarté une suggestion",
    "suggestion.restore": "a rétabli une suggestion", "object.update": "a documenté un objet",
    "note.add": "a ajouté une note", "note.delete": "a supprimé une note", "settings.update": "a modifié les réglages",
    "metadata.import": "a importé des métadonnées", "version.save": "a enregistré une version",
    "version.restore": "a restauré une version", "links.import": "a importé des liens lus dans le SQL",
    "object.create": "a créé un objet", "object.edit": "a modifié un objet créé à la main",
    "object.confirm": "a validé un objet New", "object.delete": "a supprimé un objet créé à la main",
    "object.merge": "a fusionné une table New avec sa définition", "objects.rename": "a renommé des objets",
    "ticket.save": "a mis à jour le suivi", "ticket.comment": "a commenté un sujet du suivi",
    "ticket.delete": "a supprimé un sujet du suivi", "event.revert": "a annulé", "event.restore": "a rétabli",
}
OBJECT_TYPE_LABELS = {"OBJET_METIER": "Objet métier", "SOURCE": "Source", "TABLE": "Table", "VIEW": "Vue",
                      "VIZ": "Viz", "AUTRE": "Autre"}


def empty_metadata() -> dict:
    return {"meta": {"source": "objets créés à la main"}, "objects": [], "flows": [], "relations": [], "rules": []}


def sql_layer(md: dict) -> dict:
    """Ce que les imports SQL ont apporté (règles, flux, jointures et tables citées) : reporté sur
    une nouvelle structure JSON pour ne rien perdre lors de sa mise à jour."""
    flows = [f for f in md.get("flows") or [] if f.get("origin") == "SQL"]
    rules = list(md.get("rules") or [])
    relations = list(md.get("relations") or [])
    used = {x for f in flows for x in (f["source"], f["target"])} | {x for r in relations for x in (r["a"], r["b"])}
    for rule in rules:
        used |= {rule.get("target"), rule.get("from"), *(rule.get("sources") or []),
                 *(j.get("table") for j in rule.get("joins") or [])}
    return {"meta": {}, "objects": [o for o in md["objects"] if o["id"] in used], "flows": flows,
            "relations": relations, "rules": rules}


def sql_links(md: dict, remap, source_file: str) -> list[dict]:
    """Liens lus dans le SQL : flux (INSERT, CTAS, vues) et jointures (ON, USING, clés étrangères)."""
    out, joins = [], {}
    for flow in md.get("flows") or []:
        if flow.get("origin") != "SQL":
            continue
        src, tgt = remap(flow["source"]), remap(flow["target"])
        if not src or not tgt or src == tgt:
            continue
        out.append({"id": new_id(), "kind": "FLOW", "source": src, "target": tgt, "origin": "SQL",
                    "columns": [{"source": m["source"], "target": m["target"], "transformation": m.get("transformation")}
                                for m in flow.get("column_mapping") or []],
                    "comment": f"Lu dans le SQL ({flow.get('job') or 'requête'})"})
    for rel in md.get("relations") or []:
        a, b = remap(rel["a"]), remap(rel["b"])
        if not a or not b or a == b:
            continue
        key = tuple(sorted((a, b)))
        link = joins.setdefault(key, {"id": new_id(), "kind": "JOIN", "source": a, "target": b, "origin": "SQL",
                                      "status": "VALIDE", "columns": [], "comment": rel.get("comment") or ""})
        col = {"source": rel["a_column"], "target": rel["b_column"]} if link["source"] == a else \
            {"source": rel["b_column"], "target": rel["a_column"]}
        if col not in link["columns"]:
            link["columns"].append(col)
    return out + list(joins.values())


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

    def _custom_key(self) -> str:
        custom = [(o["id"], o["type"], o.get("description"), [(c.get("name"), c.get("type")) for c in o["columns"]])
                  for o in sorted(self.state["custom"].values(), key=lambda o: o["id"])]
        return hashlib.sha1(json.dumps(custom, ensure_ascii=False).encode()).hexdigest()[:16] if custom else ""

    def _wanted_key(self):
        meta = self.state.get("metadata")
        custom = self._custom_key()
        if not meta and not custom:
            return None
        return (meta["file"] if meta else "", tuple(self._settings()["ignore_columns"]), custom)

    def _compose(self, md: dict) -> dict:
        """Métadonnées importées + objets créés à la main (ceux dont l'identifiant n'existe pas déjà)."""
        known = {o["id"] for o in md["objects"]}
        extra = [ingest.parse_metadata({"objects": [{
            "id": o["id"], "database": o["database"], "name": o["name"], "type": o["type"],
            "description": o.get("description") or "", "columns": o.get("columns") or []}]})["objects"][0]
            for o in self.state["custom"].values() if o["id"] not in known]
        return {**md, "objects": md["objects"] + extra}

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
                    current = (meta.get("metadata_file"), tuple(meta.get("ignore_columns", [])), meta.get("custom_key", ""))
                except Exception:  # noqa: BLE001 — cache illisible : reconstruit
                    current = None
            if current != wanted:
                path = self.workspace.metadata_path()
                if path is None and wanted[0]:
                    return                                   # fichier pas encore synchronisé
                md = ingest.load_metadata(path) if path else empty_metadata()
                self.last_report = build.build_cache(self._compose(md), self._settings(), self.cache_path,
                                                     {"metadata_file": wanted[0], "ignore_columns": list(wanted[1]),
                                                      "custom_key": wanted[2]})
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

    def _open_tickets(self) -> Counter:
        key = self.workspace.revision
        if getattr(self, "_tickets_key", None) != key:
            self._tickets_cache = Counter(t.get("object_id") for t in self.state["tickets"].values()
                                          if t.get("object_id") and t.get("status") in ("OUVERT", "EN_COURS"))
            self._tickets_key = key
        return self._tickets_cache

    def node(self, obj_id: str, depth: int = 0) -> dict:
        obj = self._objects.get(obj_id) or {"id": obj_id, "database": obj_id.split(".")[0],
                                              "name": obj_id.split(".")[-1], "type": "?", "nb_columns": 0}
        tag = self.tag_of(obj_id)
        doc = self.state["objects"].get(obj_id, {})
        custom = self.state["custom"].get(obj_id)
        order = (tag or {}).get("order")
        return {"id": obj_id, "database": obj["database"], "name": obj["name"], "type": obj["type"],
                "nb_columns": obj["nb_columns"], "tag_id": tag["id"] if tag else None,
                "tag": tag["name"] if tag else None, "color": tag["color"] if tag else None,
                "tag_order": order if isinstance(order, (int, float)) else None,
                "custom": bool(custom), "is_new": bool(custom and custom["status"] == "NEW"),
                "tickets": self._open_tickets().get(obj_id, 0),
                "has_spec": bool(doc.get("spec_url")), "depth": depth, "missing": obj_id not in self._objects}

    def tag_rank(self, obj_id: str):
        """Ordre du tag (sens d'alimentation) ; None si pas de tag ou tag sans ordre."""
        order = (self.tag_of(obj_id) or {}).get("order")
        return order if isinstance(order, (int, float)) else None

    def lanes(self, ids) -> list[dict]:
        """Tags présents, rangés par ordre : une colonne du graphe par tag, de gauche à droite."""
        tags = {}
        for obj_id in ids:
            tag = self.tag_of(obj_id)
            if tag:
                tags[tag["id"]] = tag
        return [{"id": t["id"], "name": t["name"], "color": t["color"], "order": t.get("order")}
                for t in sorted(tags.values(), key=lambda t: (t.get("order") if isinstance(t.get("order"), (int, float))
                                                                else 9999, norm(t["name"])))]

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
        ra, rb = self.tag_rank(link["source"]), self.tag_rank(link["target"])
        against = link["kind"] == "FLOW" and ra is not None and rb is not None and ra > rb
        return {**link, "source_object": link["source"], "target_object": link["target"],
                "problems": problems, "edge_id": "l:" + link["id"],
                "orphan": any(end not in self._objects for end in (link["source"], link["target"])),
                "against_order": against}

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
                       "links_validated": sum(1 for l in links if l["status"] == "VALIDE"),
                       "new_objects": sum(1 for o in st["custom"].values() if o["status"] == "NEW"),
                       "tickets_open": sum(1 for t in st["tickets"].values() if t.get("status") in ("OUVERT", "EN_COURS"))},
            "new_objects": sorted(({"id": o["id"], "name": o["name"], "type": o["type"]} for o in st["custom"].values()
                                   if o["status"] == "NEW"), key=lambda o: o["name"]),
            "undo": self._undo_info(user), "object_types": OBJECT_TYPE_LABELS,
            "sqlglot": sql_glot.AVAILABLE,
            "ui": self.config["ui"], "palette": PALETTE,
        }

    def _undo_info(self, user: str) -> dict:
        undo = self.workspace.undo_candidate(user)
        redo = self.workspace.redo_candidate(user)
        return {"undo": self.describe(undo) if undo else None, "redo": len(redo) if redo else 0}

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
        elif event["type"] == "links.import":
            detail = f"{len(data.get('links') or [])} lien(s) — {data.get('source_file') or ''}"
        elif event["type"] in ("object.create", "object.edit", "object.confirm", "object.delete"):
            detail = data.get("id", "")
        elif event["type"] == "object.merge":
            detail = f"{data.get('id')} → {data.get('into')}"
        elif event["type"].startswith("ticket."):
            ticket = self.state["tickets"].get(data.get("id") or "", {})
            detail = data.get("title") or ticket.get("title", "")
        elif event["type"] in ("event.revert", "event.restore"):
            n = len(data.get("ids") or [])
            label = {"undo": "a annulé (Ctrl+Z)", "redo": "a rétabli (Ctrl+Y)", "point": "est revenu à un point du journal"}.get(
                data.get("mode"), label)
            detail = f"{n} modification(s)"
        return {"id": event["id"], "at": event["ts"], "user": event.get("user"), "type": event["type"],
                "label": label, "detail": detail, "reverted": event["id"] in self.workspace.reverted,
                "control": event["type"] in ("event.revert", "event.restore")}

    def revision(self, since: str) -> dict:
        self.sync()
        changes = [self.describe(e) for e in self.workspace.events_since(since)] if since != self.workspace.revision else []
        return {"revision": self.workspace.revision, "changes": changes[-50:], "nb_changes": len(changes)}

    def activity(self, params: dict) -> list[dict]:
        self.sync()
        limit = int(params.get("limit", 200))
        events = self.workspace.events
        if params.get("object"):
            oid = params["object"].lower()
            events = [e for e in events if oid in json.dumps(e.get("data") or {}, ensure_ascii=False)]
        return [self.describe(e) for e in reversed(events[-limit:])]

    def history_at(self, params: dict) -> dict:
        """Retour dans le temps : état de l'équipe juste après une modification, comparé à aujourd'hui."""
        self.sync()
        try:
            past = self.workspace.state_at(params.get("event", ""))
        except ValueError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        event = next(e for e in self.workspace.events if e["id"] == params["event"])
        later = [e for e in self.workspace.events if (e["ts"], e["id"]) > (event["ts"], event["id"])
                 and e["type"] not in ("event.revert", "event.restore") and e["id"] not in self.workspace.reverted]
        return {"event": self.describe(event), "diff": diff_states(past, self.state), "nb_later": len(later),
                "counts": {"links": len(past["links"]), "tags": len(past["tags"]), "custom": len(past["custom"]),
                           "tickets": len(past["tickets"])}}

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
            variants = {free, free.replace(" ", "_")}                 # « tableau de bord » = tableau_de_bord
            for oid in candidates:
                obj = objects[oid]
                if any(v in norm(obj["name"]) for v in variants) or obj["database"] in dbs_found \
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
        rules = store.rules_for(con, obj_id)
        tickets = sorted((t for t in self.state["tickets"].values() if t.get("object_id") == obj_id),
                         key=lambda t: t.get("updated_at") or "", reverse=True)
        return {
            "rules": rules, "custom": self.state["custom"].get(obj_id), "tickets": tickets,
            "object": {**obj, **self.node(obj_id)}, "columns": columns,
            "upstream": [l for l in links if l["kind"] == "FLOW" and l["target"] == obj_id],
            "downstream": [l for l in links if l["kind"] == "FLOW" and l["source"] == obj_id],
            "joins": [l for l in links if l["kind"] == "JOIN"],
            "suggestions": sugs[:30],
            "doc": self.state["objects"].get(obj_id, {}), "notes": self.state["notes"].get(obj_id, []),
            "hidden": obj_id in self.hidden(),
        }

    @_closing
    def link_detail(self, link_id: str) -> dict:
        """Lien + règles de gestion des requêtes SQL qui le justifient (jointure, filtres, formules)."""
        self.sync()
        link = self.state["links"].get(link_id)
        if link is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "Lien introuvable (supprimé ?)")
        con = self.con()
        rules = []
        if link["target"] in self._objects or link["source"] in self._objects:
            for rule in store.rules_for(con, link["target"]) + store.rules_for(con, link["source"]):
                tables = {rule.get("target"), rule.get("from"), *(rule.get("sources") or []),
                          *(j.get("table") for j in rule.get("joins") or [])}
                if link["source"] in tables and link["target"] in tables and rule not in rules:
                    rules.append(rule)
        types = self._types_for(con, [x for x in (link["source"], link["target"]) if x in self._objects])
        return {"link": self.link_out(link, types), "rules": rules,
                "source": self.node(link["source"]), "target": self.node(link["target"])}

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
        return {"center": obj_id, "nodes": nodes, "edges": list(edges.values()), "truncated": truncated,
                "lanes": self.lanes(depths)}

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
        # Chaîne d'existence : en partant des tables les plus en aval, on remonte les liens (colonne,
        # puis même nom, puis niveau table) jusqu'à l'origine (objet métier, source…).
        chains, extra, chain_edges = [], {}, {}
        if len(ids) <= 60:
            flows_out = {l["source"] for l in self.state["links"].values() if l["kind"] == "FLOW" and l["target"] in ids}
            terminal = [t for t in ids if t not in flows_out] or sorted(ids)
            for oid in sorted(terminal, key=lambda o: -(self.tag_rank(o) or 0))[:6]:
                lin = self.column_lineage({"object": oid, "column": name, "dir": "up", "tables": "1"})
                if lin["steps"]:
                    chains.append({"object": oid, "steps": lin["steps"]})
                for step in lin["steps"]:
                    if step["object"] not in ids and step["object"] not in extra:
                        extra[step["object"]] = {**self.node(step["object"]), "columns": [], "column_type": None,
                                                 "type_differs": False, "without_column": True}
                    if step["link_id"] in self.state["links"] and "l:" + step["link_id"] not in chain_edges:
                        chain_edges["l:" + step["link_id"]] = self.link_out(self.state["links"][step["link_id"]])
                    elif str(step["link_id"]).startswith("s:"):
                        src = step["object"]
                        tgt = step["from"].rsplit(".", 1)[0]
                        chain_edges.setdefault(step["link_id"], {
                            "id": step["link_id"], "edge_id": step["link_id"], "kind": "FLOW", "status": "SUGGESTION",
                            "source": src, "target": tgt, "source_object": src, "target_object": tgt,
                            "proposed_source": src, "columns": [{"source": step["column"], "target": name}],
                            "explanation": "lu dans le SQL", "score": 0.95})
        all_links = {l["edge_id"]: l for l in links}
        all_links.update({k: v for k, v in chain_edges.items() if k not in all_links})
        return {"name": name, "tables": tables + list(extra.values()), "links": list(all_links.values()),
                "types": dict(types), "type_conflict": len(types) > 1, "similar_names": similar_names,
                "chains": chains, "lanes": self.lanes(list(ids) + list(extra))}

    def column_lineage(self, params: dict) -> dict:
        """Lineage d'une colonne à travers les correspondances colonne → colonne des liens."""
        self.sync()
        obj_id, column = params.get("object", "").lower(), params.get("column", "").lower()
        direction = params.get("dir", "up")
        seen, frontier, steps = {(obj_id, column)}, [(obj_id, column)], []
        flows = [l for l in self.links(True) if l["kind"] == "FLOW"]
        if params.get("sql", "1") == "1" and self._objects:
            con = store.connect(self.cache_path)
            try:
                hints = [s for s in store.suggestions_all(con) if s.get("mapping") and s.get("kind") == "FLOW"
                         and s["key"] not in self.state["dismissed"]]
            finally:
                con.close()
            linked = {(l["source"], c.get("source"), l["target"], c.get("target")) for l in flows for c in l.get("columns") or []}
            for s in hints:
                src = s["direction"] or s["a"]
                tgt = s["b"] if src == s["a"] else s["a"]
                cols = [c for c in s["mapping"] if (src, c["source"], tgt, c["target"]) not in linked]
                if cols:
                    flows.append({"id": "s:" + s["key"], "kind": "FLOW", "source": src, "target": tgt,
                                  "columns": cols, "status": "SUGGESTION"})
        table_level = params.get("tables") == "1"
        cols_of = {}

        def has_col(obj, col):
            if obj not in cols_of:
                cols_of[obj] = {c["name"] for c in self._columns_of(obj)}
            return col in cols_of[obj]

        for level in range(1, int(params.get("depth", 15)) + 1):
            nxt = []
            for node_obj, node_col in frontier:
                found = False
                for link in flows:
                    for col in link.get("columns") or []:
                        if node_col is None:
                            continue
                        if direction == "up" and link["target"] == node_obj and col.get("target") == node_col:
                            other = (link["source"], col.get("source"))
                        elif direction == "down" and link["source"] == node_obj and col.get("source") == node_col:
                            other = (link["target"], col.get("target"))
                        else:
                            continue
                        found = True
                        steps.append({"depth": level, "from": f"{node_obj}.{node_col}", "object": other[0],
                                      "column": other[1], "transformation": col.get("transformation"),
                                      "status": link["status"], "link_id": link["id"], "level": "column"})
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
                if found or not table_level:
                    continue
                # Pas de correspondance de colonne : on suit les liens au niveau table.
                for link in flows:
                    if str(link["id"]).startswith("s:"):
                        continue
                    if direction == "up" and link["target"] == node_obj:
                        other_obj = link["source"]
                    elif direction == "down" and link["source"] == node_obj:
                        other_obj = link["target"]
                    else:
                        continue
                    same = node_col is not None and has_col(other_obj, node_col)
                    other = (other_obj, node_col if same else None)
                    steps.append({"depth": level, "from": f"{node_obj}.{node_col or '*'}", "object": other_obj,
                                  "column": other[1], "transformation": None, "status": link["status"],
                                  "link_id": link["id"], "level": "name" if same else "table"})
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        for step in steps:
            node = self.node(step["object"])
            step.update(tag=node["tag"], color=node["color"], tag_order=node["tag_order"], name=node["name"])
        return {"object": obj_id, "column": column, "dir": direction, "steps": steps,
                "objects": sorted({s["object"] for s in steps} | {obj_id})}

    def _columns_of(self, obj_id: str) -> list[dict]:
        con = store.connect(self.cache_path)
        try:
            return store.get_columns(con, obj_id)
        finally:
            con.close()

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
            ("Objets créés", ["id", "type", "statut", "description", "colonnes", "créé par", "créé le"],
             [[o["id"], OBJECT_TYPE_LABELS.get(o["type"], o["type"]), "New" if o["status"] == "NEW" else "validé",
               o.get("description", ""), ", ".join(c["name"] for c in o["columns"]), o["created_by"], o["created_at"]]
              for o in st["custom"].values()]),
            ("Suivi", ["id", "type", "titre", "statut", "priorité", "objet", "colonne", "responsable", "créé par",
                       "créé le", "commentaires"],
             [[t["id"], t.get("kind"), t.get("title"), t.get("status"), t.get("priority"), t.get("object_id") or "",
               t.get("column") or "", t.get("assignee") or "", t.get("created_by"), t.get("created_at"),
               len(t.get("comments") or [])] for t in st["tickets"].values()]),
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
        if action.startswith("objects/"):
            return self._write_object(action, body, user)
        if action.startswith("tickets/"):
            return self._write_ticket(action, body, user)
        if action.startswith("history/"):
            return self._write_history(action, body, user)
        raise ApiError(HTTPStatus.NOT_FOUND, f"Action inconnue : {action}")

    # ---- objets créés à la main (objet métier, source, viz, table prévue…)

    @staticmethod
    def _clean_columns(raw) -> list[dict]:
        if isinstance(raw, str):
            raw = [line for line in re.split(r"[\n,;]+", raw) if line.strip()]
        out, seen = [], set()
        for item in raw or []:
            if isinstance(item, str):
                parts = item.strip().split()
                item = {"name": parts[0] if parts else "", "type": " ".join(parts[1:]) or None}
            name = re.sub(r"[^\w$]", "", str(item.get("name") or "")).lower()
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "type": (str(item.get("type") or "").strip().lower() or None)})
        return out

    def _write_object(self, action: str, body: dict, user: str) -> dict:
        ws, custom = self.workspace, self.state["custom"]
        if action == "objects/create":
            name = re.sub(r"[^\w$]", "_", (body.get("name") or "").strip().lower()).strip("_")
            database = re.sub(r"[^\w$]", "_", (body.get("database") or "manuel").strip().lower()).strip("_") or "manuel"
            if not name:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Nom obligatoire")
            obj_id = f"{database}.{name}"
            if obj_id in self._objects:
                raise ApiError(HTTPStatus.CONFLICT, f"L'objet {obj_id} existe déjà")
            otype = str(body.get("type") or "TABLE").upper()
            if otype not in OBJECT_TYPES:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"Type inconnu : {otype}")
            data = {"id": obj_id, "database": database, "name": name, "type": otype,
                    "description": (body.get("description") or "")[:2000], "columns": self._clean_columns(body.get("columns"))}
            ws.append("object.create", data, user)
            if body.get("tag_id") in self.state["tags"]:
                ws.append("object.tag", {"object_ids": [obj_id], "tag_id": body["tag_id"]}, user)
            self.sync()
            return {"ok": True, "id": obj_id}
        obj_id = str(body.get("id") or "").lower()
        if obj_id not in custom:
            raise ApiError(HTTPStatus.NOT_FOUND, "Objet créé à la main introuvable")
        if action == "objects/edit":
            data = {"id": obj_id}
            if "type" in body:
                data["type"] = str(body["type"]).upper() if str(body["type"]).upper() in OBJECT_TYPES else "TABLE"
            if "description" in body:
                data["description"] = (body.get("description") or "")[:2000]
            if "columns" in body:
                data["columns"] = self._clean_columns(body["columns"])
            ws.append("object.edit", data, user)
        elif action == "objects/confirm":
            ws.append("object.confirm", {"id": obj_id}, user)
        elif action == "objects/delete":
            ws.append("object.delete", {"id": obj_id}, user)
        elif action == "objects/merge":
            into = str(body.get("into") or "").lower()
            if into not in self._objects or (into in custom and into != obj_id):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Choisissez une table importée (JSON ou SQL) existante")
            ws.append("object.merge", {"id": obj_id, "into": into}, user)
        else:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Action inconnue : {action}")
        self.sync()
        return {"ok": True, "id": obj_id}

    # ---- suivi des anomalies, modifications, améliorations

    def _write_ticket(self, action: str, body: dict, user: str) -> dict:
        ws, tickets = self.workspace, self.state["tickets"]
        if action == "tickets/save":
            ticket_id = body.get("id") or new_id()
            data = {"id": ticket_id}
            if body.get("id") is None or "title" in body:
                title = (body.get("title") or "").strip()
                if not title:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Titre obligatoire")
                data["title"] = title[:200]
            if body.get("id") is None:
                data.update(kind="ANOMALIE", status="OUVERT", priority="NORMALE")
            for key in ("description", "assignee", "column", "link_id"):
                if key in body:
                    data[key] = (body.get(key) or "")[:4000] or None
            if "object_id" in body:
                data["object_id"] = (body.get("object_id") or "").lower() or None
            if "kind" in body:
                kind = str(body["kind"]).upper()
                data["kind"] = kind if kind in TICKET_KINDS else "ANOMALIE"
            if "status" in body:
                status = str(body["status"]).upper()
                if status not in TICKET_STATUSES:
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"Statut inconnu : {status}")
                data["status"] = status
            if "priority" in body:
                data["priority"] = str(body["priority"]).upper() if str(body["priority"]).upper() in ("BASSE", "NORMALE", "HAUTE") else "NORMALE"
            if body.get("id") and body["id"] not in tickets:
                raise ApiError(HTTPStatus.NOT_FOUND, "Sujet introuvable")
            ws.append("ticket.save", data, user)
            return {"ok": True, "id": ticket_id}
        ticket_id = body.get("id")
        if ticket_id not in tickets:
            raise ApiError(HTTPStatus.NOT_FOUND, "Sujet introuvable")
        if action == "tickets/comment":
            text = (body.get("text") or "").strip()
            if not text:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Commentaire vide")
            ws.append("ticket.comment", {"id": ticket_id, "comment_id": new_id(), "text": text[:4000]}, user)
        elif action == "tickets/delete":
            ws.append("ticket.delete", {"id": ticket_id}, user)
        else:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Action inconnue : {action}")
        return {"ok": True, "id": ticket_id}

    def tickets(self, params: dict) -> dict:
        self.sync()
        items = list(self.state["tickets"].values())
        for key in ("status", "kind", "object_id"):
            if params.get(key):
                wanted = set(params[key].upper().split(",")) if key != "object_id" else {params[key].lower()}
                items = [t for t in items if (t.get(key) or "").upper() in wanted or (key == "object_id" and t.get(key) in wanted)]
        order = {"OUVERT": 0, "EN_COURS": 1, "RESOLU": 2, "REJETE": 3}
        items.sort(key=lambda t: (order.get(t.get("status"), 9), {"HAUTE": 0, "NORMALE": 1, "BASSE": 2}.get(t.get("priority"), 1),
                                  t.get("updated_at") or ""))
        return {"items": [{**t, "object": self.node(t["object_id"]) if t.get("object_id") else None} for t in items],
                "counts": dict(Counter(t.get("status") for t in self.state["tickets"].values()))}

    # ---- annuler / rétablir / revenir à un point

    def _write_history(self, action: str, body: dict, user: str) -> dict:
        ws = self.workspace
        if action == "history/undo":
            event = ws.undo(user)
            if event is None:
                raise ApiError(HTTPStatus.CONFLICT, "Rien à annuler")
            done = self.describe(event)
        elif action == "history/redo":
            ids = ws.redo(user)
            if not ids:
                raise ApiError(HTTPStatus.CONFLICT, "Rien à rétablir")
            done = {"label": "rétabli", "detail": f"{len(ids)} modification(s)"}
        elif action == "history/revert":
            ids = ws.revert(list(body.get("ids") or [body.get("id")]), user)
            if not ids:
                raise ApiError(HTTPStatus.CONFLICT, "Modification déjà annulée ou introuvable")
            done = {"label": "annulé", "detail": f"{len(ids)} modification(s)"}
        elif action == "history/restore":
            ids = [i for i in body.get("ids") or [body.get("id")] if i in ws.reverted]
            if not ids:
                raise ApiError(HTTPStatus.CONFLICT, "Cette modification n'est pas annulée")
            ws.append("event.restore", {"ids": ids, "mode": "history"}, user)
            done = {"label": "rétabli", "detail": f"{len(ids)} modification(s)"}
        elif action == "history/revert_to":
            try:
                ids = ws.revert_to(body.get("event", ""), user)
            except ValueError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            done = {"label": "retour au point choisi", "detail": f"{len(ids)} modification(s) annulée(s)"}
        else:
            raise ApiError(HTTPStatus.NOT_FOUND, f"Action inconnue : {action}")
        self._cache_key = None
        self.sync(force=True)
        return {"ok": True, "done": done, "undo": self._undo_info(user)}

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
        swapped = False
        ra, rb = self.tag_rank(source), self.tag_rank(target)
        if kind == "FLOW" and not body.get("keep_direction") and ra is not None and rb is not None and ra > rb:
            source, target, swapped = target, source, True        # le tag le plus à gauche alimente l'autre
            columns = [{"source": c["target"], "target": c["source"], "transformation": c["transformation"]} for c in columns]
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
            if body.get("validate") or kind == "JOIN":
                self.workspace.append("link.validate", {"id": body["id"]}, user)
            return {"ok": True, "id": body["id"], "swapped": swapped}
        data["id"] = new_id()
        data["suggestion_key"] = body.get("suggestion_key")
        if body.get("validate") or kind == "JOIN":                # une jointure n'a pas besoin d'être validée
            data["status"] = "VALIDE"
        self.workspace.append("link.create", data, user)
        return {"ok": True, "id": data["id"], "swapped": swapped, "status": data.get("status", "A_VALIDER")}

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
        return self._import(md, name, params, user)

    def _import(self, incoming: dict, name: str, params: dict, user: str) -> dict:
        """Modes : merge (le fichier complète la structure : défaut pour le SQL), refresh (le JSON
        remplace la structure, ce que le SQL avait apporté est conservé : défaut pour un JSON),
        replace (repartir de ce seul fichier). Tags, liens, objets créés à la main, documentation
        et suivi ne sont jamais touchés par un import."""
        previous_path = self.workspace.metadata_path()
        previous = ingest.load_metadata(previous_path) if previous_path else None
        is_sql = params.get("format") == "sql" or bool(incoming.get("rules"))
        mode = params.get("mode") or ("merge" if is_sql else "refresh")
        if not previous:
            mode = "replace"
        md, recognition, renames = incoming, None, {}
        identity = {o["id"]: o["id"] for o in incoming["objects"]}
        mapping = identity
        if mode == "merge":
            md, recognition = ingest.merge_metadata(previous, incoming, name)
            mapping = recognition["mapping"]
        elif mode == "refresh":
            layer = sql_layer(previous)
            if layer["rules"] or layer["flows"] or layer["relations"]:
                md, rec = ingest.merge_metadata(incoming, layer, "SQL déjà importé")
                new_ids = {o["id"] for o in incoming["objects"]}
                renames = {old: new for old, new in rec["mapping"].items() if old != new and new in new_ids}
                md["meta"]["sources"] = [{"name": name, "kind": "json"}] + [
                    x for x in (previous.get("meta") or {}).get("sources") or [] if x.get("kind") == "sql"]
                recognition = {**rec, "kept_from_sql": True}
        check = ingest.check_metadata(md)
        if not check["ok"]:
            return {"ok": False, "check": check}
        file = self.workspace.save_metadata(md, name, user)
        diff = diff_metadata(previous, md)
        stats = {"nb_objects": check["nb_objects"], "nb_columns": check["nb_columns"]}
        rec_small = {k: v for k, v in (recognition or {}).items() if k != "mapping"} or None
        self.workspace.append("metadata.import", {"file": file, "name": name, "stats": stats, "diff": diff,
                                                  "mode": mode, "recognition": rec_small}, user)
        if renames:                                # tables passées de « default.x » (SQL) à « base.x » (JSON)
            self.workspace.append("objects.rename", {"map": renames}, user)
        links, skipped = [], 0
        if is_sql and params.get("links", "1") == "1":
            links = sql_links(incoming, lambda i: mapping.get(i, i) if i else i, name)
            taken = {link_ends(l) for l in self.state["links"].values()} |                 {link_ends(l) for l in self.state["deleted_links"].values()}
            fresh, seen = [], set()
            for link in links:                       # déjà présent, ou supprimé par l'équipe : pas recréé
                if link_ends(link) not in taken and link_ends(link) not in seen:
                    seen.add(link_ends(link))
                    fresh.append(link)
            skipped = len(links) - len(fresh)
            links = fresh
            if links:
                self.workspace.append("links.import", {"links": links, "source_file": name}, user)
        self._cache_key = None
        self.sync(force=True)
        created = [l for l in links if l["id"] in self.state["links"]]
        new_names = {o["name"]: o["id"] for o in self.state["custom"].values() if o["status"] == "NEW"}
        new_matches = sorted({(new_names[o["name"]], mapping.get(o["id"], o["id"])) for o in incoming["objects"]
                              if o["name"] in new_names and mapping.get(o["id"], o["id"]) != new_names[o["name"]]})
        return {"ok": True, "check": check, "report": self.last_report, "diff": diff, "mode": mode,
                "recognition": rec_small, "parsers": (incoming.get("meta") or {}).get("parsers"),
                "links": {"flows": sum(1 for l in created if l["kind"] == "FLOW"),
                          "joins": sum(1 for l in created if l["kind"] == "JOIN"),
                          "skipped": skipped + len(links) - len(created)},
                "renamed": renames,
                "new_matches": [{"new": a, "object": b} for a, b in new_matches]}

    # ---- fusionner une table New avec sa vraie définition (SQL ou JSON)

    def _parse_fragment(self, raw: bytes, params: dict) -> dict:
        try:
            return ingest.parse_text(raw.decode("utf-8-sig"), params.get("format", "sql"), params.get("name") or "fusion")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def merge_preview(self, raw: bytes, params: dict) -> dict:
        """Tables décrites par le fragment et table New proposée pour chacune (même nom, sinon colonnes)."""
        self.sync()
        md = self._parse_fragment(raw, params)
        targets = {r.get("target") for r in md.get("rules") or [] if r.get("target")}
        objects = [o for o in md["objects"] if o["id"] in targets] or md["objects"]
        news = [o for o in self.state["custom"].values() if o["status"] == "NEW"]
        proposals = []
        for obj in objects:
            cols = {c["name"] for c in obj["columns"]}
            best, score = None, 0.0
            for new in news:
                ncols = {c["name"] for c in new["columns"]}
                value = 1.0 if new["name"] == obj["name"] else (
                    len(cols & ncols) / len(cols | ncols) if cols and ncols else 0.0)
                if value > score:
                    best, score = new["id"], value
            proposals.append({"object": obj["id"], "name": obj["name"], "type": obj["type"], "nb_columns": len(cols),
                              "proposed": best if score >= 0.3 else None, "score": round(score, 2)})
        return {"objects": proposals, "news": [{"id": n["id"], "name": n["name"], "type": n["type"]} for n in news],
                "nb_rules": len(md.get("rules") or [])}

    def merge_apply(self, raw: bytes, params: dict, user: str) -> dict:
        """Importe le fragment (mode Compléter) puis fusionne la table New choisie dans la table définie."""
        self.sync()
        new_id_ = (params.get("new") or "").lower()
        if new_id_ not in self.state["custom"]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choisissez la table New à fusionner")
        md = self._parse_fragment(raw, params)
        wanted = (params.get("object") or "").lower()
        if wanted not in {o["id"] for o in md["objects"]}:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Table introuvable dans le code fourni")
        new = self.state["custom"][new_id_]
        if not new["columns"] or True:
            pass
        result = self._import(md, params.get("name") or f"fusion {new['name']}", {**params, "mode": "merge" if self.workspace.metadata_path() else ""}, user)
        if not result["ok"]:
            return result
        mapping_target = wanted
        meta = self.state.get("metadata") or {}
        rec = meta.get("recognition") or {}
        if wanted not in self._objects:                 # reconnu sous un autre identifiant (même nom unique)
            same = [o for o in self._objects if o.split(".")[-1] == wanted.split(".")[-1] and o not in self.state["custom"]]
            mapping_target = same[0] if len(same) == 1 else wanted
        if mapping_target not in self._objects:
            raise ApiError(HTTPStatus.CONFLICT, "La table n'a pas pu être reconnue après import")
        self.workspace.append("object.merge", {"id": new_id_, "into": mapping_target}, user)
        self._cache_key = None
        self.sync(force=True)
        return {**result, "merged": {"new": new_id_, "into": mapping_target}, "recognized": bool(rec)}

    # ---- vue d'ensemble d'un périmètre (blocs par tag ou par base)

    def overview(self, params: dict) -> dict:
        self.sync()
        group_by = params.get("by", "tag")
        hidden = self.hidden()
        wanted_tags = set(filter(None, (params.get("tags") or "").split(",")))
        wanted_dbs = set(filter(None, (params.get("dbs") or "").split(",")))

        def group_of(oid):
            obj = self._objects[oid]
            if group_by == "database":
                return obj["database"]
            return effective_tag(self.state, oid, obj["name"]) or UNTAGGED

        members = defaultdict(list)
        for oid, obj in self._objects.items():
            if oid in hidden:
                continue
            tag = effective_tag(self.state, oid, obj["name"]) or UNTAGGED
            if wanted_tags and tag not in wanted_tags:
                continue
            if wanted_dbs and obj["database"] not in wanted_dbs:
                continue
            members[group_of(oid)].append(oid)
        selected = {o for ids in members.values() for o in ids}
        if params.get("detail") == "1":
            limit = int(params.get("max_nodes", 300))
            ids = sorted(selected, key=lambda o: (self.tag_rank(o) or 0, o))[:limit]
            keep = set(ids)
            edges = [self.link_out(l) for l in self.state["links"].values() if l["source"] in keep and l["target"] in keep]
            return {"mode": "objects", "nodes": [self.node(o) for o in ids], "edges": edges, "lanes": self.lanes(ids),
                    "truncated": max(0, len(selected) - limit)}
        groups = []
        for key, ids in members.items():
            tag = self.state["tags"].get(key) if group_by == "tag" else None
            groups.append({"id": f"g:{key}", "key": key, "name": (tag["name"] if tag else ("Sans tag" if key == UNTAGGED else key)),
                           "color": tag["color"] if tag else None, "order": tag.get("order") if tag else None,
                           "count": len(ids), "new": sum(1 for o in ids if o in self.state["custom"]),
                           "databases": dict(Counter(self._objects[o]["database"] for o in ids).most_common(6)),
                           "sample": sorted(self._objects[o]["name"] for o in ids)[:12]})
        groups.sort(key=lambda g: (g["order"] if isinstance(g["order"], (int, float)) else 9999, norm(g["name"])))
        flows = defaultdict(lambda: {"count": 0, "validated": 0, "pending": 0})
        inner = Counter()
        for link in self.state["links"].values():
            if link["source"] not in selected or link["target"] not in selected:
                continue
            ga, gb = group_of(link["source"]), group_of(link["target"])
            if ga == gb:
                inner[ga] += 1
                continue
            if link["kind"] == "JOIN" and ga > gb:
                ga, gb = gb, ga
            item = flows[(ga, gb, link["kind"])]
            item["count"] += 1
            item["validated" if link["status"] == "VALIDE" else "pending"] += 1
        for g in groups:
            g["inner_links"] = inner.get(g["key"], 0)
        edges = [{"edge_id": f"o:{a}|{b}|{k}", "id": f"o:{a}|{b}|{k}", "source_object": f"g:{a}", "target_object": f"g:{b}",
                  "kind": k, "status": "VALIDE" if v["pending"] == 0 else "A_VALIDER", **v, "label": str(v["count"])}
                 for (a, b, k), v in flows.items()]
        return {"mode": "groups", "by": group_by, "groups": groups, "edges": edges,
                "databases": sorted({o["database"] for o in self._objects.values()})}

    # ---- anomalies détectées automatiquement (à transformer en sujets de suivi)

    @_closing
    def anomalies(self) -> dict:
        self.sync()
        out = []
        if self._objects:
            conflicts = store.column_dictionary(self.con(), "", 50, 0, True)
            for col in conflicts["items"]:
                out.append({"kind": "TYPE", "title": f"Types différents pour la colonne {col['name']}",
                            "detail": " / ".join(col["types"]), "column": col["name"], "object_id": None})
        for link in self.state["links"].values():
            lo = self.link_out(link)
            if lo["orphan"]:
                out.append({"kind": "ORPHELIN", "title": f"Lien orphelin {short_id(link['source'])} → {short_id(link['target'])}",
                            "detail": "; ".join(lo["problems"]), "link_id": link["id"],
                            "object_id": link["source"] if link["source"] in self._objects else link["target"]})
            elif lo["against_order"]:
                out.append({"kind": "SENS", "title": f"Sens inhabituel {short_id(link['source'])} → {short_id(link['target'])}",
                            "detail": "La source a un tag placé après celui de la cible", "link_id": link["id"],
                            "object_id": link["source"]})
        rec = (self.state.get("metadata") or {}).get("recognition") or {}
        for col in rec.get("unknown_columns") or []:
            obj = col.rsplit(".", 1)[0]
            out.append({"kind": "COLONNE_SQL", "title": f"Colonne lue dans le SQL mais absente de la structure : {col}",
                        "detail": "Faute de frappe, colonne supprimée ou JSON pas à jour", "object_id": obj,
                        "column": col.rsplit(".", 1)[-1]})
        by_name = defaultdict(list)
        for oid in self._objects:
            by_name[oid.split(".")[-1]].append(oid)
        for oid, obj in self.state["custom"].items():
            twins = [o for o in by_name.get(obj["name"], []) if o != oid and o not in self.state["custom"]]
            if obj["status"] == "NEW" and twins:
                out.append({"kind": "DOUBLON", "title": f"La table New {obj['name']} existe maintenant : {twins[0]}",
                            "detail": "Fusionnez-la pour garder ses liens sans doublon", "object_id": oid, "into": twins[0]})
        untagged = sum(1 for o in self._objects.values() if not effective_tag(self.state, o["id"], o["name"]))
        return {"items": out, "untagged": untagged, "counts": dict(Counter(a["kind"] for a in out))}


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
                    "history/at": lambda: api.history_at(params), "tickets": lambda: api.tickets(params),
                    "overview": lambda: api.overview(params), "anomalies": api.anomalies,
                }
                if route in simple:
                    return self._json(simple[route]())
                if route.startswith("object/"):
                    return self._json(api.object_detail(route[7:].lower()))
                if route.startswith("link/"):
                    return self._json(api.link_detail(route[5:]))
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
                if route == "merge/preview":
                    return self._json(api.merge_preview(self._body(), params))
                if route == "merge/apply":
                    return self._json(api.merge_apply(self._body(), params, self._user()))
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

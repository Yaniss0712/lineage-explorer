"""Annotations partagées : un fichier JSON par objet dans `annotations_dir` (docs/04 § 3).

Contenu d'un fichier : URL de spec, notes, liens (VALIDATED / REJECTED / MANUAL).
Les annotations ne sont jamais stockées dans la base : elles sont fusionnées à la lecture,
donc un nouveau build ne les écrase pas et un lien rejeté ne réapparaît pas.

Écriture concurrente (dossier partagé) : on relit le fichier, on fusionne entrée par entrée
(dernier écrit gagne par entrée), puis on réécrit de façon atomique.

Points d'entrée : `AnnotationStore(directory)`, `link_key(...)`, `merge_edge(...)`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

STATUSES = {"VALIDATED", "REJECTED", "MANUAL"}
CERTAIN_ORIGINS = {"DECLARED", "SQL_PARSED", "DATAVAULT"}
_SAFE_RE = re.compile(r"[^\w.\-]")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def link_key(kind: str, source: str, source_column: Optional[str], target: str,
             target_column: Optional[str]) -> tuple:
    """Clé d'unicité d'un lien. Les relations (non orientées) sont mises dans un ordre canonique.

    Un flux sans colonnes est un flux de niveau objet ; avec colonnes, c'est un lien colonne.
    """
    a, b = (source, source_column or ""), (target, target_column or "")
    if kind == "RELATION" and b < a:
        a, b = b, a
    return (kind, a[0], a[1], b[0], b[1])


class AnnotationStore:
    """Lecture / écriture des fichiers d'annotation, avec cache invalidé par date de modification."""

    def __init__(self, directory: str):
        self.directory = directory
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        os.makedirs(directory, exist_ok=True)

    def _path(self, object_id: str) -> str:
        return os.path.join(self.directory, _SAFE_RE.sub("_", object_id) + ".json")

    def _read(self, path: str) -> Optional[dict]:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        self._cache[path] = (mtime, data)
        return data

    def load(self, object_id: str) -> dict:
        """Annotations d'un objet (structure vide si aucune)."""
        data = self._read(self._path(object_id))
        return data or {"object_id": object_id, "spec_url": None, "notes": [], "links": []}

    def load_all(self) -> list[dict]:
        """Toutes les annotations du dossier."""
        result = []
        try:
            entries = list(os.scandir(self.directory))
        except OSError:
            return result
        for entry in entries:
            if entry.name.endswith(".json"):
                data = self._read(entry.path)
                if data:
                    result.append(data)
        return result

    def all_links(self) -> dict[tuple, dict]:
        """Index clé → lien annoté (avec `source_object`)."""
        index = {}
        for data in self.load_all():
            for link in data.get("links", []):
                full = {**link, "source_object": data["object_id"]}
                key = link_key(link["kind"], data["object_id"], link.get("source_column"),
                               link["target"], link.get("target_column"))
                current = index.get(key)
                if current is None or full.get("updated_at", "") >= current.get("updated_at", ""):
                    index[key] = full
        return index

    def spec_urls(self) -> dict[str, str]:
        return {d["object_id"]: d["spec_url"] for d in self.load_all() if d.get("spec_url")}

    def update(self, object_id: str, payload: dict, author: str) -> dict:
        """Enregistre une modification : `spec_url`, `note`, `delete_note` ou `link`."""
        with self._lock:
            path = self._path(object_id)
            self._cache.pop(path, None)
            data = self.load(object_id)
            data = {"object_id": object_id, "spec_url": data.get("spec_url"),
                    "spec_author": data.get("spec_author"), "spec_updated_at": data.get("spec_updated_at"),
                    "notes": list(data.get("notes", [])), "links": list(data.get("links", []))}
            stamp = {"author": author, "updated_at": now_iso()}
            if "spec_url" in payload:
                url = (payload["spec_url"] or "").strip() or None
                if url and not re.match(r"^https?://", url, re.IGNORECASE):
                    raise ValueError("L'URL de spec doit commencer par http:// ou https://")
                data.update(spec_url=url, spec_author=author, spec_updated_at=stamp["updated_at"])
            if payload.get("note"):
                data["notes"].append({"id": uuid.uuid4().hex[:8], "text": str(payload["note"])[:4000], **stamp})
            if payload.get("delete_note"):
                data["notes"] = [n for n in data["notes"] if n.get("id") != payload["delete_note"]]
            if payload.get("link"):
                self._merge_link(data, object_id, payload["link"], stamp)
            self._write(path, data)
            return data

    @staticmethod
    def _merge_link(data: dict, object_id: str, link: dict, stamp: dict) -> None:
        kind = str(link.get("kind", "")).upper()
        status = str(link.get("status", "")).upper()
        if kind not in ("FLOW", "RELATION"):
            raise ValueError("kind doit valoir FLOW ou RELATION")
        if not link.get("target"):
            raise ValueError("Objet cible manquant")
        entry = {"kind": kind, "target": str(link["target"]).lower(),
                 "source_column": (link.get("source_column") or None),
                 "target_column": (link.get("target_column") or None),
                 "status": status, "comment": link.get("comment") or "", **stamp}
        key = link_key(kind, object_id, entry["source_column"], entry["target"], entry["target_column"])
        kept = [l for l in data["links"]
                if link_key(l["kind"], object_id, l.get("source_column"), l["target"], l.get("target_column")) != key]
        if status in STATUSES:
            kept.append(entry)
        elif status != "DELETE":
            raise ValueError("status doit valoir VALIDATED, REJECTED, MANUAL ou DELETE")
        data["links"] = kept

    @staticmethod
    def _write(path: str, data: dict) -> None:
        tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def merge_edge(edge: dict, links: dict[tuple, dict]) -> dict:
    """Applique les annotations à une arête : statut, score forcé, auteur."""
    key = link_key(edge["kind"], edge["source_object"], edge.get("source_column"),
                   edge["target_object"], edge.get("target_column"))
    link = links.get(key)
    edge = dict(edge)
    edge["base_score"] = edge["score"]
    if link and link["status"] == "REJECTED":
        edge["status"] = "REJECTED"
    elif link and link["status"] in ("VALIDATED", "MANUAL"):
        edge["status"] = "VALIDATED"
        edge["score"] = 1.0
    else:
        edge["status"] = "CERTAIN" if edge["origin"] in CERTAIN_ORIGINS else "SUGGESTED"
    if link:
        edge["annotation"] = {k: link.get(k) for k in ("status", "author", "updated_at", "comment")}
    return edge


def manual_edges(links: dict[tuple, dict], known_edges: set[tuple]) -> list[dict]:
    """Liens MANUAL absents du build, convertis en arêtes."""
    result = []
    for key, link in links.items():
        if link["status"] != "MANUAL" or key in known_edges:
            continue
        result.append({
            "id": "m:" + "|".join(key), "kind": link["kind"],
            "source_object": link["source_object"], "target_object": link["target"],
            "source_column": link.get("source_column"), "target_column": link.get("target_column"),
            "origin": "MANUAL", "score": 1.0, "base_score": 1.0, "status": "MANUAL",
            "explanation": "Lien ajouté à la main" + (f" : {link['comment']}" if link.get("comment") else "."),
            "transformation": None, "job": None, "details": {},
            "annotation": {k: link.get(k) for k in ("status", "author", "updated_at", "comment")},
        })
    return result

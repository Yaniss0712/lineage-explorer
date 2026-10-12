"""Espace de travail partagé : journal des modifications, état de l'équipe, versions.

Organisation du dossier (pensée pour un dossier Teams / SharePoint synchronisé) :

    <espace>/
    ├── journal/        1 fichier JSON par modification (jamais modifié, jamais écrasé)
    ├── versions/       instantanés « Enregistrer la version actuelle »
    └── metadonnees/    métadonnées importées (JSON normalisé), une par import

Pourquoi un journal : plusieurs personnes écrivent en même temps dans un dossier synchronisé.
Avec un fichier par modification (nom unique : date + utilisateur + identifiant), il n'y a
jamais de conflit de synchronisation ; chaque poste relit les fichiers et rejoue les
événements dans l'ordre chronologique pour obtenir le même état. On garde aussi l'historique
complet (qui a fait quoi, quand).

Types d'événements : tag.save, tag.delete, object.tag, rule.save, rule.delete, link.create,
link.update, link.validate, link.delete, suggestion.dismiss, suggestion.restore,
object.update, note.add, note.delete, settings.update, metadata.import, version.save,
version.restore.

Point d'entrée : `Workspace(root)` ; `append(type, data, user)`, `refresh()`, `state`.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

SNAPSHOT_KEYS = ("tags", "assign", "rules", "links", "deleted_links", "dismissed", "objects", "notes", "settings")
LINK_KINDS = ("FLOW", "JOIN")
_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def safe_name(text: str, max_len: int = 40) -> str:
    return _SAFE_RE.sub("-", (text or "").lower()).strip("-")[:max_len] or "x"


def empty_state() -> dict:
    return {"tags": {}, "assign": {}, "rules": {}, "links": {}, "deleted_links": {}, "dismissed": {},
            "objects": {}, "notes": {}, "settings": {"ignore_columns": [], "hidden_objects": []},
            "metadata": None, "versions": []}


# --------------------------------------------------------------------------- règles d'état


def effective_tag(state: dict, obj_id: str, obj_name: str) -> Optional[str]:
    """Tag d'un objet : attribution explicite, sinon première règle de nommage qui correspond."""
    if obj_id in state["assign"]:
        tag = state["assign"][obj_id]
        return tag if tag in state["tags"] else None
    for rule in state["rules"].values():
        pattern = rule["pattern"].lower()
        if (fnmatch.fnmatchcase(obj_name.lower(), pattern) or fnmatch.fnmatchcase(obj_id.lower(), pattern)) \
                and rule["tag_id"] in state["tags"]:
            return rule["tag_id"]
    return None


def link_signature(link: dict) -> tuple:
    """Identité fonctionnelle d'un lien (pour refuser les doublons)."""
    cols = tuple(sorted((c.get("source") or "", c.get("target") or "") for c in link.get("columns") or []))
    ends = (link["source"], link["target"])
    if link["kind"] == "JOIN":
        ends = tuple(sorted(ends))
    return (link["kind"], *ends, cols)


def apply_event(state: dict, event: dict, load_version: Callable[[str], dict]) -> None:
    """Applique un événement à l'état (fonction pure côté données ; les erreurs sont ignorées)."""
    kind, data, user, ts = event["type"], event.get("data") or {}, event.get("user"), event.get("ts")
    stamp = {"by": user, "at": ts}
    if kind == "tag.save":
        current = state["tags"].get(data["id"], {})
        state["tags"][data["id"]] = {**current, **{k: data.get(k, current.get(k)) for k in
                                                   ("name", "color", "description", "order")},
                                     "id": data["id"], **stamp}
    elif kind == "tag.delete":
        state["tags"].pop(data["id"], None)
        state["assign"] = {o: t for o, t in state["assign"].items() if t != data["id"]}
        state["rules"] = {r: v for r, v in state["rules"].items() if v["tag_id"] != data["id"]}
    elif kind == "object.tag":
        for obj_id in data.get("object_ids", []):
            if data.get("tag_id"):
                state["assign"][obj_id] = data["tag_id"]
            else:
                state["assign"].pop(obj_id, None)
    elif kind == "rule.save":
        state["rules"][data["id"]] = {"id": data["id"], "pattern": data["pattern"], "tag_id": data["tag_id"], **stamp}
    elif kind == "rule.delete":
        state["rules"].pop(data["id"], None)
    elif kind == "link.create":
        state["links"][data["id"]] = {
            "id": data["id"], "kind": data["kind"], "source": data["source"], "target": data["target"],
            "columns": data.get("columns") or [], "comment": data.get("comment") or "",
            "suggestion_key": data.get("suggestion_key"), "status": "A_VALIDER",
            "created_by": user, "created_at": ts, "validated_by": None, "validated_at": None}
    elif kind == "link.update" and data["id"] in state["links"]:
        link = state["links"][data["id"]]
        for key in ("kind", "source", "target", "columns", "comment"):
            if key in data:
                link[key] = data[key]
        link.update(status="A_VALIDER", validated_by=None, validated_at=None, updated_by=user, updated_at=ts)
    elif kind == "link.validate" and data["id"] in state["links"]:
        state["links"][data["id"]].update(status="VALIDE", validated_by=user, validated_at=ts)
    elif kind == "link.delete" and data["id"] in state["links"]:
        link = state["links"].pop(data["id"])
        state["deleted_links"][data["id"]] = {**link, "deleted_by": user, "deleted_at": ts,
                                              "reason": data.get("reason") or ""}
    elif kind == "suggestion.dismiss":
        state["dismissed"][data["key"]] = stamp
    elif kind == "suggestion.restore":
        state["dismissed"].pop(data["key"], None)
    elif kind == "object.update":
        current = state["objects"].get(data["id"], {})
        for key in ("description", "owner", "spec_url"):
            if key in data:
                current[key] = data[key]
        state["objects"][data["id"]] = {**current, "updated_by": user, "updated_at": ts}
    elif kind == "note.add":
        state["notes"].setdefault(data["object_id"], []).append(
            {"id": data["id"], "text": data["text"], "author": user, "at": ts})
    elif kind == "note.delete":
        notes = state["notes"].get(data["object_id"], [])
        state["notes"][data["object_id"]] = [n for n in notes if n["id"] != data["id"]]
    elif kind == "settings.update":
        for key in ("ignore_columns", "hidden_objects"):
            if key in data:
                state["settings"][key] = sorted({str(v).strip().lower() for v in data[key] if str(v).strip()})
    elif kind == "metadata.import":
        state["metadata"] = {**data, **stamp}
    elif kind == "version.save":
        state["versions"].append({**data, **stamp})
    elif kind == "version.restore":
        snapshot = load_version(data["file"])
        if snapshot:
            for key in SNAPSHOT_KEYS:
                state[key] = copy.deepcopy(snapshot["state"].get(key, empty_state()[key]))


# --------------------------------------------------------------------------- espace de travail


class Workspace:
    """Lecture / écriture de l'espace partagé. Une instance par serveur (thread-safe)."""

    def __init__(self, root: str):
        self.root = root
        self.journal_dir = os.path.join(root, "journal")
        self.versions_dir = os.path.join(root, "versions")
        self.metadata_dir = os.path.join(root, "metadonnees")
        for folder in (self.journal_dir, self.versions_dir, self.metadata_dir):
            os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self._files: dict[str, dict] = {}
        self._versions_cache: dict[str, dict] = {}
        self._last_scan = 0.0
        self.events: list[dict] = []
        self.state = empty_state()
        self.revision = "0"
        self.refresh(force=True)

    # ---- lecture

    def refresh(self, force: bool = False, min_interval: float = 2.0) -> bool:
        """Relit le journal (fichiers arrivés par synchronisation). Renvoie vrai si l'état a changé."""
        with self._lock:
            if not force and time.monotonic() - self._last_scan < min_interval:
                return False
            self._last_scan = time.monotonic()
            try:
                names = [n for n in os.listdir(self.journal_dir) if n.endswith(".json") and not n.startswith((".", "~"))]
            except OSError:
                return False
            new = False
            for name in names:
                if name in self._files:
                    continue
                event = self._read_json(os.path.join(self.journal_dir, name))
                if event and {"id", "ts", "type"} <= event.keys():   # fichier incomplet : relu au prochain passage
                    self._files[name] = event
                    new = True
            if new or force:
                self._replay()
            return new

    @staticmethod
    def _read_json(path: str) -> Optional[dict]:
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _replay(self) -> None:
        unique = {e["id"]: e for e in self._files.values()}          # copies « conflit » de la synchro
        events = sorted(unique.values(), key=lambda e: (e["ts"], e["id"]))
        state = empty_state()
        for event in events:
            try:
                apply_event(state, event, self.read_version)
            except (KeyError, TypeError, ValueError):
                continue                                              # événement malformé : ignoré
        self.events, self.state = events, state
        self.revision = f"{len(events)}-{events[-1]['id']}" if events else "0"

    def events_since(self, revision: str) -> list[dict]:
        """Événements postérieurs à une révision donnée (pour le rafraîchissement des postes)."""
        try:
            count = int(revision.split("-")[0])
        except ValueError:
            count = 0
        return self.events[count:] if count <= len(self.events) else self.events

    # ---- écriture

    def _write_json(self, folder: str, name: str, data: dict) -> str:
        path = os.path.join(folder, name)
        tmp = os.path.join(folder, f".~{uuid.uuid4().hex[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return path

    def append(self, kind: str, data: dict, user: str) -> dict:
        """Enregistre une modification dans le journal partagé et l'applique."""
        with self._lock:
            event = {"id": new_id(), "ts": now_iso(), "user": user, "type": kind, "data": data}
            stamp = re.sub(r"[^0-9T]", "", event["ts"][:26])
            name = f"{stamp}_{safe_name(user, 20)}_{kind.replace('.', '-')}_{event['id']}.json"
            self._write_json(self.journal_dir, name, event)
            self._files[name] = event
            self._replay()
            return event

    # ---- versions

    def read_version(self, file: str) -> Optional[dict]:
        if file not in self._versions_cache:
            data = self._read_json(os.path.join(self.versions_dir, os.path.basename(file)))
            if data is None:
                return None
            self._versions_cache[file] = data
        return self._versions_cache[file]

    def save_version(self, label: str, comment: str, user: str) -> dict:
        with self._lock:
            ts = now_iso()
            file = f"{re.sub(r'[^0-9T]', '', ts[:19])}_{safe_name(user, 20)}_{safe_name(label, 30)}.json"
            snapshot = {"label": label, "comment": comment, "by": user, "at": ts,
                        "state": {k: copy.deepcopy(self.state[k]) for k in SNAPSHOT_KEYS},
                        "metadata": self.state.get("metadata")}
            self._write_json(self.versions_dir, file, snapshot)
            return self.append("version.save", {"file": file, "label": label, "comment": comment}, user)

    def restore_version(self, file: str, user: str) -> dict:
        snapshot = self.read_version(file)
        if snapshot is None:
            raise ValueError(f"Version introuvable : {file}")
        return self.append("version.restore", {"file": os.path.basename(file), "label": snapshot.get("label")}, user)

    # ---- métadonnées

    def save_metadata(self, md: dict, name: str, user: str) -> str:
        ts = now_iso()
        file = f"{re.sub(r'[^0-9T]', '', ts[:19])}_{safe_name(user, 20)}_{safe_name(name, 40)}.json"
        self._write_json(self.metadata_dir, file, md)
        return file

    def metadata_path(self) -> Optional[str]:
        meta = self.state.get("metadata")
        if not meta:
            return None
        path = os.path.join(self.metadata_dir, os.path.basename(meta["file"]))
        return path if os.path.exists(path) else None


# --------------------------------------------------------------------------- comparaisons


def diff_states(old: dict, new: dict, names: Callable[[str], str] = lambda x: x) -> dict:
    """Différences lisibles entre deux états (version enregistrée → état actuel)."""
    def link_label(link):
        arrow = "→" if link["kind"] == "FLOW" else "="
        return f"{link['source']} {arrow} {link['target']}"
    old_links, new_links = old.get("links", {}), new.get("links", {})
    old_tags, new_tags = old.get("tags", {}), new.get("tags", {})
    return {
        "links_added": [link_label(new_links[i]) for i in new_links if i not in old_links],
        "links_removed": [link_label(old_links[i]) for i in old_links if i not in new_links],
        "links_validated": [link_label(new_links[i]) for i in new_links if i in old_links
                            and new_links[i]["status"] == "VALIDE" and old_links[i]["status"] != "VALIDE"],
        "tags_added": [new_tags[t]["name"] for t in new_tags if t not in old_tags],
        "tags_removed": [old_tags[t]["name"] for t in old_tags if t not in new_tags],
        "assignments_changed": sum(1 for o in set(old.get("assign", {})) | set(new.get("assign", {}))
                                   if old.get("assign", {}).get(o) != new.get("assign", {}).get(o)),
        "rules_changed": len(set(old.get("rules", {})) ^ set(new.get("rules", {}))),
        "notes_changed": sum(1 for o in set(old.get("notes", {})) | set(new.get("notes", {}))
                             if len(old.get("notes", {}).get(o, [])) != len(new.get("notes", {}).get(o, []))),
        "objects_documented": sum(1 for o in set(old.get("objects", {})) | set(new.get("objects", {}))
                                  if old.get("objects", {}).get(o) != new.get("objects", {}).get(o)),
    }


def diff_metadata(old: Optional[dict], new: dict) -> dict:
    """Objets et colonnes apparus / disparus entre deux imports."""
    if not old:
        return {"first_import": True}
    def index(md):
        return {o["id"]: {c["name"] for c in o["columns"]} for o in md["objects"]}
    a, b = index(old), index(new)
    cols_added = [f"{o}.{c}" for o in b if o in a for c in sorted(b[o] - a[o])]
    cols_removed = [f"{o}.{c}" for o in a if o in b for c in sorted(a[o] - b[o])]
    return {"objects_added": sorted(set(b) - set(a)), "objects_removed": sorted(set(a) - set(b)),
            "columns_added": cols_added[:500], "columns_removed": cols_removed[:500],
            "nb_columns_added": len(cols_added), "nb_columns_removed": len(cols_removed)}

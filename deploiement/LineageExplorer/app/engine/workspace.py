"""Espace de travail partagé : journal des modifications, état de l'équipe, versions.

Organisation du dossier (pensée pour un dossier Teams / SharePoint synchronisé) :

    <espace>/
    ├── journal/        les modifications, une ligne JSON chacune, dans des fichiers .jsonl
    ├── versions/       instantanés « Enregistrer la version actuelle »
    └── metadonnees/    métadonnées importées (JSON normalisé), une par import

Journal : un fichier par **version** et par **poste** (`<version>__<utilisateur>__<poste>.jsonl`).
Chaque poste n'écrit que dans son propre fichier, en ajoutant des lignes : jamais deux personnes
dans le même fichier, donc pas de conflit de synchronisation, et quelques fichiers d'1 Mo au
lieu de milliers de fichiers d'1 Ko. « Enregistrer la version » ouvre un nouveau fichier.
Les anciens journaux (un fichier `.json` par modification) sont regroupés automatiquement dans
un fichier `archive__….jsonl`, puis supprimés une fois la copie vérifiée.

Chaque poste relit les fichiers et rejoue les événements dans l'ordre chronologique (date, puis
identifiant) : tout le monde obtient le même état. Les copies « conflit » de la synchronisation
sont dédoublonnées par identifiant d'événement ; une ligne incomplète (fichier en cours de
synchronisation) est ignorée jusqu'au passage suivant.

Annuler : rien n'est effacé. `event.revert` (ids) retire des modifications de l'état,
`event.restore` (ids) les rétablit. Ctrl+Z / Ctrl+Y, « annuler cette modification » et
« revenir à ce point » de l'historique reposent sur ces deux événements.

Types d'événements : tag.save, tag.delete, object.tag, rule.save, rule.delete, link.create,
link.update, link.validate, link.delete, links.import, suggestion.dismiss, suggestion.restore,
object.update, object.create, object.edit, object.confirm, object.delete, object.merge,
note.add, note.delete, ticket.save, ticket.comment, ticket.delete, settings.update,
metadata.import, version.save, version.restore, event.revert, event.restore.

Point d'entrée : `Workspace(root)` ; `append(type, data, user)`, `refresh()`, `state`.
"""

from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
import socket
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Callable, Optional

SNAPSHOT_KEYS = ("tags", "assign", "rules", "links", "deleted_links", "dismissed", "objects", "notes", "settings",
                 "custom", "tickets", "merged")
LINK_KINDS = ("FLOW", "JOIN")
CONTROL_TYPES = ("event.revert", "event.restore")
NOT_UNDOABLE = CONTROL_TYPES + ("version.save", "version.restore", "metadata.import")
OBJECT_TYPES = ("OBJET_METIER", "SOURCE", "TABLE", "VIEW", "VIZ", "AUTRE")
TICKET_KINDS = ("ANOMALIE", "MODIFICATION", "AMELIORATION")
TICKET_STATUSES = ("OUVERT", "EN_COURS", "RESOLU", "REJETE")
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
            "custom": {}, "tickets": {}, "merged": {}, "metadata": None, "versions": []}


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


def link_ends(link: dict) -> tuple:
    """Type + extrémités (jointure : sans ordre) — pour ne pas doubler un lien lu dans le SQL."""
    ends = (link["source"], link["target"])
    return (link["kind"], *(sorted(ends) if link["kind"] == "JOIN" else ends))


def _new_link(data: dict, user: str, ts: str) -> dict:
    validated = data.get("status") == "VALIDE"
    return {"id": data["id"], "kind": data["kind"], "source": data["source"], "target": data["target"],
            "columns": data.get("columns") or [], "comment": data.get("comment") or "",
            "suggestion_key": data.get("suggestion_key"), "status": "VALIDE" if validated else "A_VALIDER",
            "origin": data.get("origin") or "MANUEL", "source_file": data.get("source_file"),
            "created_by": user, "created_at": ts,
            "validated_by": user if validated else None, "validated_at": ts if validated else None}


def _rename_object(state: dict, old: str, new: str) -> None:
    """Fusion d'un objet créé à la main dans sa vraie définition : tout ce qui le concerne suit."""
    for link in state["links"].values():
        if link["source"] == old:
            link["source"] = new
        if link["target"] == old:
            link["target"] = new
    if old in state["assign"]:
        state["assign"][new] = state["assign"].pop(old)
    if old in state["objects"]:
        doc = state["objects"].pop(old)
        current = state["objects"].get(new, {})
        state["objects"][new] = {**doc, **{k: v for k, v in current.items() if v}}
    if old in state["notes"]:
        state["notes"][new] = state["notes"].get(new, []) + state["notes"].pop(old)
    for ticket in state["tickets"].values():
        if ticket.get("object_id") == old:
            ticket["object_id"] = new


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
        state["links"][data["id"]] = _new_link(data, user, ts)
    elif kind == "links.import":
        taken = {link_ends(l) for l in state["links"].values()}
        refused = {link_ends(l) for l in state["deleted_links"].values()}
        for item in data.get("links") or []:
            ends = link_ends(item)
            if item.get("id") and ends not in taken and ends not in refused:
                state["links"][item["id"]] = _new_link({**item, "source_file": data.get("source_file")}, user, ts)
                taken.add(ends)
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
    elif kind == "object.create":
        state["custom"][data["id"]] = {
            "id": data["id"], "database": data["database"], "name": data["name"],
            "type": data.get("type") or "TABLE", "description": data.get("description") or "",
            "columns": data.get("columns") or [], "status": "NEW", "created_by": user, "created_at": ts}
    elif kind == "object.edit" and data["id"] in state["custom"]:
        obj = state["custom"][data["id"]]
        for key in ("type", "description", "columns"):
            if key in data:
                obj[key] = data[key]
        obj.update(updated_by=user, updated_at=ts)
    elif kind == "object.confirm" and data["id"] in state["custom"]:
        state["custom"][data["id"]].update(status="VALIDE", confirmed_by=user, confirmed_at=ts)
    elif kind == "object.delete":
        state["custom"].pop(data["id"], None)
    elif kind == "object.merge" and data["id"] in state["custom"]:
        state["custom"].pop(data["id"])
        if data["into"] != data["id"]:
            _rename_object(state, data["id"], data["into"])
        state["merged"][data["id"]] = {"into": data["into"], **stamp}
    elif kind == "objects.rename":
        for old, new in (data.get("map") or {}).items():
            if old != new:
                _rename_object(state, old, new)
    elif kind == "note.add":
        state["notes"].setdefault(data["object_id"], []).append(
            {"id": data["id"], "text": data["text"], "author": user, "at": ts})
    elif kind == "note.delete":
        notes = state["notes"].get(data["object_id"], [])
        state["notes"][data["object_id"]] = [n for n in notes if n["id"] != data["id"]]
    elif kind == "ticket.save":
        current = state["tickets"].get(data["id"])
        if current is None:
            current = {"id": data["id"], "created_by": user, "created_at": ts, "comments": [], "history": []}
            state["tickets"][data["id"]] = current
        before = current.get("status")
        for key in ("kind", "title", "description", "priority", "status", "assignee", "object_id", "column",
                    "link_id"):
            if key in data:
                current[key] = data[key]
        current.update(updated_by=user, updated_at=ts)
        if current.get("status") != before:
            current["history"].append({"status": current.get("status"), "by": user, "at": ts})
    elif kind == "ticket.comment" and data["id"] in state["tickets"]:
        state["tickets"][data["id"]]["comments"].append({"id": data.get("comment_id"), "text": data["text"],
                                                         "author": user, "at": ts})
    elif kind == "ticket.delete":
        state["tickets"].pop(data["id"], None)
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


def reverted_ids(events: list[dict]) -> set[str]:
    """Modifications annulées (en tenant compte des rétablissements), dans l'ordre du journal."""
    out: set[str] = set()
    for event in events:
        ids = (event.get("data") or {}).get("ids") or []
        if event["type"] == "event.revert":
            out.update(ids)
        elif event["type"] == "event.restore":
            out.difference_update(ids)
    return out


def replay(events: list[dict], load_version: Callable[[str], dict]) -> dict:
    """État obtenu en rejouant une liste d'événements triés (annulations comprises)."""
    skipped = reverted_ids(events)
    state = empty_state()
    for event in events:
        if event["type"] in CONTROL_TYPES or event["id"] in skipped:
            continue
        try:
            apply_event(state, event, load_version)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue                                              # événement malformé : ignoré
    return state


# --------------------------------------------------------------------------- espace de travail


class Workspace:
    """Lecture / écriture de l'espace partagé. Une instance par serveur (thread-safe)."""

    def __init__(self, root: str, compact: bool = True, host: str | None = None):
        self.root = root
        self.journal_dir = os.path.join(root, "journal")
        self.versions_dir = os.path.join(root, "versions")
        self.metadata_dir = os.path.join(root, "metadonnees")
        for folder in (self.journal_dir, self.versions_dir, self.metadata_dir):
            os.makedirs(folder, exist_ok=True)
        self.host = safe_name(host or socket.gethostname() or "poste", 20)
        self._lock = threading.RLock()
        self._files: dict[str, list[dict]] = {}          # fichier → événements lus
        self._stats: dict[str, tuple] = {}               # fichier → (taille, date) au dernier passage
        self._versions_cache: dict[str, dict] = {}
        self._last_scan = 0.0
        self.events: list[dict] = []
        self.reverted: set[str] = set()
        self.state = empty_state()
        self.revision = "0"
        self.refresh(force=True)
        if compact:
            self.compact_legacy()

    # ---- lecture

    def _journal_files(self) -> list[str]:
        try:
            return [n for n in os.listdir(self.journal_dir)
                    if n.endswith((".json", ".jsonl")) and not n.startswith((".", "~"))]
        except OSError:
            return []

    def refresh(self, force: bool = False, min_interval: float = 2.0) -> bool:
        """Relit le journal (fichiers arrivés ou complétés par la synchronisation).
        Renvoie vrai si l'état a changé."""
        with self._lock:
            if not force and time.monotonic() - self._last_scan < min_interval:
                return False
            self._last_scan = time.monotonic()
            names = set(self._journal_files())
            changed = False
            for name in list(self._files):
                if name not in names:                       # fichier regroupé ou retiré
                    self._files.pop(name)
                    self._stats.pop(name, None)
                    changed = True
            for name in names:
                path = os.path.join(self.journal_dir, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                stamp = (st.st_size, st.st_mtime_ns)
                if self._stats.get(name) == stamp:
                    continue
                events = self._read_jsonl(path) if name.endswith(".jsonl") else self._read_event(path)
                if events is None:
                    continue                                 # fichier incomplet : relu au prochain passage
                self._stats[name] = stamp
                if [e["id"] for e in events] != [e["id"] for e in self._files.get(name, [])]:
                    self._files[name] = events
                    changed = True
            if changed or force:
                self._replay()
            return changed

    @staticmethod
    def _read_json(path: str) -> Optional[dict]:
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    @classmethod
    def _read_event(cls, path: str) -> Optional[list[dict]]:
        event = cls._read_json(path)
        return [event] if isinstance(event, dict) and {"id", "ts", "type"} <= event.keys() else None

    @staticmethod
    def _read_jsonl(path: str) -> Optional[list[dict]]:
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        events = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue                                     # ligne en cours d'écriture / synchronisation
            if isinstance(event, dict) and {"id", "ts", "type"} <= event.keys():
                events.append(event)
        return events

    def _replay(self) -> None:
        unique = {e["id"]: e for events in self._files.values() for e in events}   # copies « conflit »
        events = sorted(unique.values(), key=lambda e: (e["ts"], e["id"]))
        self.events, self.reverted = events, reverted_ids(events)
        self.state = replay(events, self.read_version)
        self.revision = f"{len(events)}-{events[-1]['id']}" if events else "0"

    def events_since(self, revision: str) -> list[dict]:
        """Événements postérieurs à une révision donnée (pour le rafraîchissement des postes)."""
        try:
            count = int(revision.split("-")[0])
        except ValueError:
            count = 0
        return self.events[count:] if count <= len(self.events) else self.events

    def state_at(self, event_id: str) -> dict:
        """État de l'équipe juste après une modification donnée (retour dans le temps)."""
        index = next((i for i, e in enumerate(self.events) if e["id"] == event_id), None)
        if index is None:
            raise ValueError("Modification introuvable")
        return replay(self.events[:index + 1], self.read_version)

    # ---- écriture

    def _write_json(self, folder: str, name: str, data: dict) -> str:
        path = os.path.join(folder, name)
        tmp = os.path.join(folder, f".~{uuid.uuid4().hex[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return path

    @property
    def segment(self) -> str:
        """Fichier de journal courant : celui de la dernière version enregistrée."""
        versions = self.state.get("versions") or []
        return safe_name(os.path.splitext(versions[-1]["file"])[0], 60) if versions else "debut"

    def journal_file(self, user: str) -> str:
        return f"{self.segment}__{safe_name(user, 20)}__{self.host}.jsonl"

    def append(self, kind: str, data: dict, user: str) -> dict:
        """Enregistre une modification dans le journal partagé et l'applique."""
        with self._lock:
            event = {"id": new_id(), "ts": now_iso(), "user": user, "type": kind, "data": data}
            name = self.journal_file(user)
            path = os.path.join(self.journal_dir, name)
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._files.setdefault(name, []).append(event)
            try:
                st = os.stat(path)
                self._stats[name] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
            self._replay()
            return event

    def compact_legacy(self, user: str = "auto", dry_run: bool = False) -> int | dict:
        """Regroupe les anciens fichiers d'une modification (.json, format v0.1 à v0.3) dans un seul
        `archive__….jsonl`, une ligne par modification, dans l'ordre chronologique.

        Sécurités : copie relue avant toute suppression ; état de l'équipe identique avant / après
        (sinon rien n'est supprimé) ; originaux gardés dans `journal/sauvegardes/*.zip` (ignoré à la
        lecture). `dry_run` : ne modifie rien, renvoie le bilan. Renvoie le nombre de fichiers regroupés."""
        with self._lock:
            legacy = {n: ev for n, ev in self._files.items() if n.endswith(".json")}
            unreadable = [n for n in self._journal_files() if n.endswith(".json") and n not in legacy]
            if dry_run:
                return {"files": len(legacy), "events": len({e["id"] for ev in legacy.values() for e in ev}),
                        "unreadable": unreadable, "already_jsonl": [n for n in self._files if n.endswith(".jsonl")]}
            if not legacy:
                return 0
            before = json.dumps({k: self.state[k] for k in SNAPSHOT_KEYS}, sort_keys=True, default=str)
            events = sorted({e["id"]: e for ev in legacy.values() for e in ev}.values(), key=lambda e: (e["ts"], e["id"]))
            stamp = re.sub(r"[^0-9T]", "", now_iso()[:19])
            name = f"archive__{safe_name(user, 20)}__{self.host}__{stamp}.jsonl"
            path = os.path.join(self.journal_dir, name)
            tmp = os.path.join(self.journal_dir, f".~{uuid.uuid4().hex[:8]}.tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
            copied = {e["id"] for e in self._read_jsonl(path) or []}
            if copied != {e["id"] for e in events}:
                os.remove(path)                             # copie incomplète : on ne touche à rien
                return 0
            backup_dir = os.path.join(self.journal_dir, "sauvegardes")
            os.makedirs(backup_dir, exist_ok=True)
            with zipfile.ZipFile(os.path.join(backup_dir, f"journal_avant_regroupement_{stamp}_{self.host}.zip"),
                                 "w", zipfile.ZIP_DEFLATED) as archive:
                for legacy_name in legacy:
                    archive.write(os.path.join(self.journal_dir, legacy_name), legacy_name)
            removed = 0
            for legacy_name, evs in legacy.items():
                if all(e["id"] in copied for e in evs):
                    try:
                        os.remove(os.path.join(self.journal_dir, legacy_name))
                        removed += 1
                    except OSError:
                        pass                                 # fichier verrouillé : regroupé au prochain lancement
            self.refresh(force=True)
            after = json.dumps({k: self.state[k] for k in SNAPSHOT_KEYS}, sort_keys=True, default=str)
            if after != before:                             # ne devrait jamais arriver : on restaure les originaux
                with zipfile.ZipFile(os.path.join(backup_dir, f"journal_avant_regroupement_{stamp}_{self.host}.zip")) as archive:
                    archive.extractall(self.journal_dir)
                os.remove(path)
                self.refresh(force=True)
                return 0
            return removed

    # ---- annuler / rétablir

    def _active(self, event: dict) -> bool:
        return event["type"] not in CONTROL_TYPES and event["id"] not in self.reverted

    def undo_candidate(self, user: str) -> Optional[dict]:
        for event in reversed(self.events):
            if event.get("user") == user and event["type"] not in NOT_UNDOABLE and self._active(event):
                return event
        return None

    def redo_candidate(self, user: str) -> Optional[list[str]]:
        """Modifications annulées par Ctrl+Z que Ctrl+Y peut rétablir (aucune nouvelle action depuis)."""
        for event in reversed(self.events):
            if event.get("user") != user:
                continue
            data = event.get("data") or {}
            if event["type"] == "event.revert" and data.get("mode") == "undo":
                ids = [i for i in data.get("ids") or [] if i in self.reverted]
                if ids:
                    return ids
                continue
            if event["type"] == "event.restore":
                continue
            if event["type"] not in NOT_UNDOABLE:
                return None                                  # nouvelle action : plus rien à rétablir
        return None

    def undo(self, user: str) -> Optional[dict]:
        target = self.undo_candidate(user)
        if target is None:
            return None
        self.append("event.revert", {"ids": [target["id"]], "mode": "undo"}, user)
        return target

    def redo(self, user: str) -> Optional[list[str]]:
        ids = self.redo_candidate(user)
        if ids:
            self.append("event.restore", {"ids": ids, "mode": "redo"}, user)
        return ids

    def revert(self, ids: list[str], user: str, mode: str = "history") -> list[str]:
        known = {e["id"]: e for e in self.events}
        ids = [i for i in ids if i in known and self._active(known[i]) and known[i]["type"] != "version.save"]
        if ids:
            self.append("event.revert", {"ids": ids, "mode": mode}, user)
        return ids

    def revert_to(self, event_id: str, user: str) -> list[str]:
        """Revenir à ce point : annule toutes les modifications postérieures (en une fois)."""
        index = next((i for i, e in enumerate(self.events) if e["id"] == event_id), None)
        if index is None:
            raise ValueError("Modification introuvable")
        later = [e["id"] for e in self.events[index + 1:] if self._active(e) and e["type"] != "version.save"]
        if later:
            self.append("event.revert", {"ids": later, "mode": "point", "to": event_id}, user)
        return later

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
                        "journal_until": self.segment,
                        "state": {k: copy.deepcopy(self.state[k]) for k in SNAPSHOT_KEYS},
                        "metadata": self.state.get("metadata")}
            self._write_json(self.versions_dir, file, snapshot)
            return self.append("version.save", {"file": file, "label": label, "comment": comment,
                                                "journal": self.segment}, user)

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
    """Différences lisibles entre deux états (version enregistrée ou point du journal → état actuel)."""
    def link_label(link):
        arrow = "→" if link["kind"] == "FLOW" else "="
        return f"{link['source']} {arrow} {link['target']}"
    old_links, new_links = old.get("links", {}), new.get("links", {})
    old_tags, new_tags = old.get("tags", {}), new.get("tags", {})
    old_custom, new_custom = old.get("custom", {}), new.get("custom", {})
    old_tickets, new_tickets = old.get("tickets", {}), new.get("tickets", {})
    return {
        "links_added": [link_label(new_links[i]) for i in new_links if i not in old_links],
        "links_removed": [link_label(old_links[i]) for i in old_links if i not in new_links],
        "links_validated": [link_label(new_links[i]) for i in new_links if i in old_links
                            and new_links[i]["status"] == "VALIDE" and old_links[i]["status"] != "VALIDE"],
        "tags_added": [new_tags[t]["name"] for t in new_tags if t not in old_tags],
        "tags_removed": [old_tags[t]["name"] for t in old_tags if t not in new_tags],
        "objects_created": [new_custom[o]["id"] for o in new_custom if o not in old_custom],
        "objects_removed": [old_custom[o]["id"] for o in old_custom if o not in new_custom],
        "tickets_added": [new_tickets[t].get("title", "") for t in new_tickets if t not in old_tickets],
        "assignments_changed": sum(1 for o in set(old.get("assign", {})) | set(new.get("assign", {}))
                                   if old.get("assign", {}).get(o) != new.get("assign", {}).get(o)),
        "rules_changed": len(set(old.get("rules", {})) ^ set(new.get("rules", {}))),
        "notes_changed": sum(1 for o in set(old.get("notes", {})) | set(new.get("notes", {}))
                             if len(old.get("notes", {}).get(o, [])) != len(new.get("notes", {}).get(o, []))),
        "objects_documented": sum(1 for o in set(old.get("objects", {})) | set(new.get("objects", {}))
                                  if old.get("objects", {}).get(o) != new.get("objects", {}).get(o)),
        "metadata_changed": (old.get("metadata") or {}).get("file") != (new.get("metadata") or {}).get("file"),
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

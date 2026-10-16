"""Journal v2 : un fichier par version et par poste, regroupement des anciens fichiers, annulation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from engine.workspace import Workspace


class JournalV2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = Workspace(self.tmp, host="poste-a")

    def files(self):
        return sorted(os.listdir(self.ws.journal_dir))

    def test_one_file_per_user_and_computer(self):
        for i in range(5):
            self.ws.append("tag.save", {"id": f"t{i}", "name": f"T{i}", "color": "#000"}, "alice")
        other = Workspace(self.tmp, host="poste-b")
        other.append("tag.save", {"id": "tb", "name": "B", "color": "#000"}, "bob")
        self.assertEqual(self.files(), ["debut__alice__poste-a.jsonl", "debut__bob__poste-b.jsonl"])
        self.assertTrue(self.ws.refresh(force=True))
        self.assertEqual(len(self.ws.state["tags"]), 6)
        self.assertEqual(self.ws.revision, other.revision)

    def test_partial_line_and_conflict_copy_are_tolerated(self):
        self.ws.append("tag.save", {"id": "t1", "name": "A", "color": "#000"}, "alice")
        path = os.path.join(self.ws.journal_dir, self.files()[0])
        shutil.copy(path, path.replace(".jsonl", "-POSTE-B.jsonl"))         # copie « conflit » OneDrive
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"id": "x", "ts": "2026')                          # ligne en cours de synchronisation
        fresh = Workspace(self.tmp, host="poste-c")
        self.assertEqual(len(fresh.events), 1)

    def test_legacy_files_are_merged_then_removed(self):
        legacy = os.path.join(self.tmp, "ancien")
        os.makedirs(os.path.join(legacy, "journal"))
        for i in range(3):
            event = {"id": f"e{i}", "ts": f"2026-10-0{i + 1}T10:00:00+00:00", "user": "alice", "type": "tag.save",
                     "data": {"id": f"t{i}", "name": f"T{i}", "color": "#000"}}
            with open(os.path.join(legacy, "journal", f"2026100{i}_alice_tag-save_e{i}.json"), "w", encoding="utf-8") as h:
                json.dump(event, h)
        plan = Workspace(legacy, host="poste-a", compact=False).compact_legacy(dry_run=True)
        self.assertEqual((plan["files"], plan["events"]), (3, 3))                 # simulation : rien ne bouge
        self.assertEqual(len(os.listdir(os.path.join(legacy, "journal"))), 3)
        ws = Workspace(legacy, host="poste-a")
        names = sorted(os.listdir(os.path.join(legacy, "journal")))
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("archive__") and names[0].endswith(".jsonl"))
        backups = os.listdir(os.path.join(legacy, "journal", "sauvegardes"))       # originaux gardés en zip
        import zipfile
        with zipfile.ZipFile(os.path.join(legacy, "journal", "sauvegardes", backups[0])) as z:
            self.assertEqual(len(z.namelist()), 3)
        self.assertEqual(len(ws.state["tags"]), 3)
        self.assertEqual(len(Workspace(legacy, host="poste-b").events), 3)       # relu à l'identique ailleurs

    def test_compact_command(self):
        from engine.__main__ import main
        legacy = os.path.join(self.tmp, "cmd")
        os.makedirs(os.path.join(legacy, "journal"))
        event = {"id": "e1", "ts": "2026-10-01T10:00:00+00:00", "user": "alice", "type": "tag.save",
                 "data": {"id": "t1", "name": "T1", "color": "#000"}}
        with open(os.path.join(legacy, "journal", "20261001_alice_tag-save_e1.json"), "w", encoding="utf-8") as h:
            json.dump(event, h)
        self.assertEqual(main(["compact", "--workspace", legacy, "--dry-run"]), 0)
        self.assertIn("20261001_alice_tag-save_e1.json", os.listdir(os.path.join(legacy, "journal")))
        self.assertEqual(main(["compact", "--workspace", legacy]), 0)
        self.assertNotIn("20261001_alice_tag-save_e1.json", os.listdir(os.path.join(legacy, "journal")))
        self.assertIn("t1", Workspace(legacy, host="z").state["tags"])

    def test_saving_a_version_opens_a_new_journal_file(self):
        self.ws.append("tag.save", {"id": "t1", "name": "A", "color": "#000"}, "alice")
        event = self.ws.save_version("Recette", "", "alice")
        self.ws.append("tag.save", {"id": "t2", "name": "B", "color": "#000"}, "alice")
        stem = os.path.splitext(event["data"]["file"])[0].lower()
        self.assertIn(f"{stem}__alice__poste-a.jsonl", self.files())
        self.assertEqual(len(Workspace(self.tmp, host="x").state["tags"]), 2)

    def test_undo_redo(self):
        self.ws.append("link.create", {"id": "l1", "kind": "FLOW", "source": "a.x", "target": "a.y"}, "alice")
        self.ws.append("link.validate", {"id": "l1"}, "alice")
        self.ws.append("tag.save", {"id": "t1", "name": "Bob", "color": "#000"}, "bob")   # action d'un autre
        self.ws.undo("alice")
        self.assertEqual(self.ws.state["links"]["l1"]["status"], "A_VALIDER")
        self.assertIn("t1", self.ws.state["tags"])                                         # bob n'est pas annulé
        self.ws.undo("alice")
        self.assertNotIn("l1", self.ws.state["links"])
        self.ws.redo("alice")
        self.assertEqual(self.ws.state["links"]["l1"]["status"], "A_VALIDER")
        self.ws.redo("alice")
        self.assertEqual(self.ws.state["links"]["l1"]["status"], "VALIDE")
        self.assertIsNone(self.ws.redo_candidate("alice"))
        self.ws.undo("alice")
        self.ws.append("tag.save", {"id": "t2", "name": "Nouveau", "color": "#000"}, "alice")
        self.assertIsNone(self.ws.redo_candidate("alice"))                                 # une nouvelle action vide « rétablir »

    def test_revert_one_change_and_back_to_a_point(self):
        first = self.ws.append("tag.save", {"id": "t1", "name": "A", "color": "#000"}, "alice")
        middle = self.ws.append("tag.save", {"id": "t2", "name": "B", "color": "#000"}, "bob")
        self.ws.append("tag.save", {"id": "t3", "name": "C", "color": "#000"}, "carole")
        self.ws.revert([middle["id"]], "alice")
        self.assertEqual(set(self.ws.state["tags"]), {"t1", "t3"})
        self.assertEqual(set(self.ws.state_at(middle["id"])["tags"]), {"t1", "t2"})       # retour dans le temps
        self.ws.append("event.restore", {"ids": [middle["id"]]}, "alice")
        self.assertEqual(set(self.ws.state["tags"]), {"t1", "t2", "t3"})
        self.ws.revert_to(first["id"], "alice")
        self.assertEqual(set(self.ws.state["tags"]), {"t1"})
        self.assertEqual(len(self.ws.events), 6)                                           # rien n'est effacé


if __name__ == "__main__":
    unittest.main()

"""Tests de l'espace partagé : journal, rejeu, plusieurs postes, versions."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from engine.workspace import Workspace, diff_metadata, effective_tag


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = Workspace(self.tmp)

    def test_tags_rules_and_priority(self):
        self.ws.append("tag.save", {"id": "t1", "name": "Fiche client", "color": "#000", "order": 2}, "alice")
        self.ws.append("tag.save", {"id": "t2", "name": "Web", "color": "#111", "order": 1}, "alice")
        self.ws.append("rule.save", {"id": "r1", "pattern": "fiche_*", "tag_id": "t1"}, "alice")
        st = self.ws.state
        self.assertEqual(effective_tag(st, "db.fiche_x", "fiche_x"), "t1")
        self.ws.append("object.tag", {"object_ids": ["db.fiche_x"], "tag_id": "t2"}, "bob")
        self.assertEqual(effective_tag(self.ws.state, "db.fiche_x", "fiche_x"), "t2")    # choix manuel prioritaire
        self.ws.append("tag.delete", {"id": "t2"}, "bob")
        self.assertEqual(effective_tag(self.ws.state, "db.fiche_x", "fiche_x"), "t1")    # la règle reprend la main

    def test_link_lifecycle(self):
        self.ws.append("link.create", {"id": "l1", "kind": "FLOW", "source": "a.x", "target": "a.y", "columns": []}, "alice")
        self.assertEqual(self.ws.state["links"]["l1"]["status"], "A_VALIDER")
        self.ws.append("link.validate", {"id": "l1"}, "alice")                          # validable par son auteur
        self.assertEqual(self.ws.state["links"]["l1"]["validated_by"], "alice")
        self.ws.append("link.delete", {"id": "l1", "reason": "erreur"}, "bob")
        self.assertNotIn("l1", self.ws.state["links"])
        self.assertEqual(self.ws.state["deleted_links"]["l1"]["deleted_by"], "bob")

    def test_two_computers_share_the_journal(self):
        other = Workspace(self.tmp)                                                   # 2e poste, même dossier
        other.append("tag.save", {"id": "t1", "name": "Mart", "color": "#000"}, "bob")
        self.assertTrue(self.ws.refresh(force=True))
        self.assertIn("t1", self.ws.state["tags"])
        self.assertEqual(self.ws.revision, other.revision)

    def test_sync_artifacts_are_tolerated(self):
        self.ws.append("tag.save", {"id": "t1", "name": "A", "color": "#000"}, "alice")
        journal = self.ws.journal_dir
        name = os.listdir(journal)[0]
        shutil.copy(os.path.join(journal, name), os.path.join(journal, name.replace(".json", "-PC-BOB.json")))
        with open(os.path.join(journal, "zz_incomplet.json"), "w", encoding="utf-8") as handle:
            handle.write('{"id": "x", "ts"')                                             # en cours de synchronisation
        fresh = Workspace(self.tmp)
        self.assertEqual(len(fresh.events), 1)                                         # doublon et fichier partiel ignorés

    def test_versions_save_diff_restore(self):
        self.ws.append("link.create", {"id": "l1", "kind": "JOIN", "source": "a.x", "target": "a.y"}, "alice")
        event = self.ws.save_version("avant", "", "alice")
        self.ws.append("link.delete", {"id": "l1"}, "bob")
        self.ws.append("link.create", {"id": "l2", "kind": "FLOW", "source": "a.y", "target": "a.z"}, "bob")
        from engine.workspace import diff_states
        diff = diff_states(self.ws.read_version(event["data"]["file"])["state"], self.ws.state)
        self.assertEqual(len(diff["links_added"]), 1)
        self.assertEqual(len(diff["links_removed"]), 1)
        self.ws.restore_version(event["data"]["file"], "alice")
        self.assertEqual(set(self.ws.state["links"]), {"l1"})
        self.assertEqual(len(self.ws.state["versions"]), 1)
        with open(os.path.join(self.ws.versions_dir, event["data"]["file"]), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["label"], "avant")

    def test_metadata_diff(self):
        old = {"objects": [{"id": "a.t", "columns": [{"name": "x"}, {"name": "y"}]}, {"id": "a.old", "columns": []}]}
        new = {"objects": [{"id": "a.t", "columns": [{"name": "x"}, {"name": "z"}]}, {"id": "a.new", "columns": []}]}
        d = diff_metadata(old, new)
        self.assertEqual((d["objects_added"], d["objects_removed"]), (["a.new"], ["a.old"]))
        self.assertEqual((d["columns_added"], d["columns_removed"]), (["a.t.z"], ["a.t.y"]))


if __name__ == "__main__":
    unittest.main()

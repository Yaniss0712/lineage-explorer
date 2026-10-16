"""Vues d'ensemble (périmètre, chaîne d'une colonne, retour dans le temps) et suivi des anomalies."""

from __future__ import annotations

import io
import unittest
import zipfile

from .helpers import DB, demo_api


class ViewsTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731
        self.tags = {name: self.w("tags/save", {"name": name, "order": i})["id"]
                     for i, name in enumerate(["Objet métier", "Lac", "BV", "Mart", "Viz"], 1)}
        self.metier = self.w("objects/create", {"name": "client", "type": "OBJET_METIER", "database": "metier",
                                                "tag_id": self.tags["Objet métier"]})["id"]
        self.viz = self.w("objects/create", {"name": "tableau_clients", "type": "VIZ", "database": "powerbi",
                                             "tag_id": self.tags["Viz"], "columns": "code_client"})["id"]
        assign = {"Lac": "client_fiche_identite", "BV": "client_fiche_prets", "Mart": "client_vision_360"}
        for tag, table in assign.items():
            self.w("tags/assign", {"object_ids": [f"{DB}.{table}"], "tag_id": self.tags[tag]})
        chain = [self.metier, f"{DB}.client_fiche_identite", f"{DB}.client_fiche_prets", f"{DB}.client_vision_360", self.viz]
        for a, b in zip(chain, chain[1:]):
            cols = [] if a == self.metier else [{"source": "code_client", "target": "code_client"}]
            self.w("links/create", {"kind": "FLOW", "source": a, "target": b, "columns": cols})

    def test_overview_blocks_follow_tag_order(self):
        o = self.api.overview({})
        names = [g["name"] for g in o["groups"]]
        self.assertEqual(names[:5], ["Objet métier", "Lac", "BV", "Mart", "Viz"])
        edges = {(e["source_object"], e["target_object"]): e["count"] for e in o["edges"]}
        self.assertEqual(edges[(f"g:{self.tags['Mart']}", f"g:{self.tags['Viz']}")], 1)
        detail = self.api.overview({"tags": f"{self.tags['Mart']},{self.tags['Viz']}", "detail": "1"})
        self.assertEqual({n["id"] for n in detail["nodes"]}, {f"{DB}.client_vision_360", self.viz})
        self.assertEqual(len(detail["edges"]), 1)

    def test_column_existence_chain_up_to_business_object(self):
        lin = self.api.column_lineage({"object": self.viz, "column": "code_client", "dir": "up", "tables": "1"})
        objects = [s["object"] for s in lin["steps"]]
        self.assertEqual(objects, [f"{DB}.client_vision_360", f"{DB}.client_fiche_prets", f"{DB}.client_fiche_identite",
                                   self.metier])
        self.assertEqual(lin["steps"][-1]["level"], "table")             # l'objet métier n'a pas la colonne
        view = self.api.column_view({"name": "code_client"})
        self.assertIn(self.metier, {t["id"] for t in view["tables"]})    # la chaîne remonte jusqu'à l'origine
        self.assertTrue(view["chains"])
        self.assertEqual([lane["name"] for lane in view["lanes"]], ["Objet métier", "Lac", "BV", "Mart", "Viz"])

    def test_history_at_a_point(self):
        events = self.api.activity({"limit": 500})
        first_link = next(e for e in reversed(events) if e["type"] == "link.create")
        past = self.api.history_at({"event": first_link["id"]})
        self.assertEqual(past["counts"]["links"], 1)
        self.assertEqual(len(past["diff"]["links_added"]), 3)
        self.w("history/revert_to", {"event": first_link["id"]})
        self.assertEqual(len(self.api.state["links"]), 1)


class TrackingTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731

    def test_ticket_lifecycle(self):
        tid = self.w("tickets/save", {"title": "Encours négatif", "kind": "ANOMALIE", "priority": "HAUTE",
                                      "object_id": f"{DB}.client_vision_360", "column": "montant_total_crd"})["id"]
        self.assertEqual(self.api.node(f"{DB}.client_vision_360")["tickets"], 1)
        self.api.write("tickets/save", {"id": tid, "status": "EN_COURS", "assignee": "bob"}, "bob")
        self.api.write("tickets/comment", {"id": tid, "text": "Filtre statut manquant dans le SQL"}, "bob")
        self.api.write("tickets/save", {"id": tid, "status": "RESOLU"}, "bob")
        t = self.api.tickets({})["items"][0]
        self.assertEqual([h["status"] for h in t["history"]], ["OUVERT", "EN_COURS", "RESOLU"])
        self.assertEqual(t["comments"][0]["author"], "bob")
        self.assertEqual(self.api.node(f"{DB}.client_vision_360")["tickets"], 0)   # résolu : plus de pastille
        self.assertEqual(self.api.tickets({"status": "OUVERT"})["items"], [])
        self.assertEqual(len(self.api.object_detail(f"{DB}.client_vision_360")["tickets"]), 1)

    def test_tool_improvement_without_object_and_export(self):
        self.w("tickets/save", {"title": "Exporter le graphe en PNG", "kind": "AMELIORATION"})
        self.w("tickets/save", {"title": "Ajouter la colonne date_maj", "kind": "MODIFICATION",
                                "object_id": f"{DB}.web_visites"})
        kinds = {t["kind"] for t in self.api.tickets({})["items"]}
        self.assertEqual(kinds, {"AMELIORATION", "MODIFICATION"})
        with zipfile.ZipFile(io.BytesIO(self.api.export_xlsx())) as z:
            workbook = z.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("Suivi", workbook)
        self.assertIn("Objets créés", workbook)

    def test_detected_anomalies(self):
        a = self.api.anomalies()
        self.assertIn("untagged", a)
        self.assertTrue(all({"kind", "title"} <= set(x) for x in a["items"]))


if __name__ == "__main__":
    unittest.main()

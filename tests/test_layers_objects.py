"""Ordre des tags (sens d'alimentation, graphe de gauche à droite) et objets créés à la main (New)."""

from __future__ import annotations

import json
import unittest

from .helpers import DB, DEMO, demo_api


class LayersTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731
        self.lac = self.w("tags/save", {"name": "Lac", "order": 1})["id"]
        self.mart = self.w("tags/save", {"name": "Mart", "order": 3})["id"]
        self.viz = self.w("tags/save", {"name": "Viz", "order": 4})["id"]
        self.w("tags/assign", {"object_ids": [f"{DB}.client_fiche_identite"], "tag_id": self.lac})
        self.w("tags/assign", {"object_ids": [f"{DB}.client_vision_360"], "tag_id": self.mart})

    def test_flow_is_oriented_by_tag_order(self):
        r = self.w("links/create", {"kind": "FLOW", "source": f"{DB}.client_vision_360", "target": f"{DB}.client_fiche_identite",
                                    "columns": [{"source": "code_client", "target": "code_client"}]})
        self.assertTrue(r["swapped"])                                     # le lac alimente le mart, pas l'inverse
        link = self.api.state["links"][r["id"]]
        self.assertEqual((link["source"], link["target"]), (f"{DB}.client_fiche_identite", f"{DB}.client_vision_360"))

    def test_keep_direction_on_request_is_flagged(self):
        r = self.w("links/create", {"kind": "FLOW", "source": f"{DB}.client_vision_360", "target": f"{DB}.client_fiche_identite",
                                    "keep_direction": True})
        self.assertFalse(r["swapped"])
        out = self.api.link_out(self.api.state["links"][r["id"]])
        self.assertTrue(out["against_order"])
        self.assertIn("SENS", {a["kind"] for a in self.api.anomalies()["items"]})

    def test_join_needs_no_validation(self):
        r = self.w("links/create", {"kind": "JOIN", "source": f"{DB}.client_fiche_prets", "target": f"{DB}.client_fiche_identite"})
        self.assertEqual(self.api.state["links"][r["id"]]["status"], "VALIDE")

    def test_graph_lanes_follow_tag_order(self):
        self.w("links/create", {"kind": "FLOW", "source": f"{DB}.client_fiche_identite", "target": f"{DB}.client_vision_360"})
        g = self.api.graph(f"{DB}.client_vision_360", {"sugg": "0"})
        self.assertEqual([lane["name"] for lane in g["lanes"]], ["Lac", "Mart"])
        orders = {n["id"]: n["tag_order"] for n in g["nodes"]}
        self.assertEqual(orders[f"{DB}.client_fiche_identite"], 1)


class ManualObjectsTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731
        self.viz = self.w("tags/save", {"name": "Viz", "order": 9})["id"]

    def test_create_tag_link_and_find_a_manual_object(self):
        r = self.w("objects/create", {"name": "Tableau de bord risques", "type": "VIZ", "tag_id": self.viz,
                                      "database": "powerbi", "columns": "code_client string\nscore int"})
        self.assertEqual(r["id"], "powerbi.tableau_de_bord_risques")
        node = self.api.node(r["id"])
        self.assertTrue(node["is_new"] and node["custom"])
        self.assertEqual((node["tag"], node["type"], node["nb_columns"]), ("Viz", "VIZ", 2))
        detail = self.api.object_detail(r["id"])
        self.assertEqual([c["name"] for c in detail["columns"]], ["code_client", "score"])
        found = self.api.search({"q": "tableau de bord"})["objects"]
        self.assertEqual([o["id"] for o in found], [r["id"]])
        link = self.w("links/create", {"kind": "FLOW", "source": r["id"], "target": f"{DB}.client_vision_360",
                                       "columns": [{"source": "code_client", "target": "code_client"}]})
        self.assertTrue(link["swapped"] is False)                         # pas d'ordre sur la table : sens gardé
        self.assertIn(r["id"], {n["id"] for n in self.api.graph(f"{DB}.client_vision_360", {})["nodes"]})

    def test_validate_directly_removes_new_badge(self):
        oid = self.w("objects/create", {"name": "dossier_credit", "type": "OBJET_METIER"})["id"]
        self.w("objects/confirm", {"id": oid})
        self.assertFalse(self.api.node(oid)["is_new"])
        self.assertTrue(self.api.node(oid)["custom"])

    def test_duplicates_and_deletion(self):
        oid = self.w("objects/create", {"name": "x_cible"})["id"]
        with self.assertRaises(Exception):
            self.w("objects/create", {"name": "x_cible"})
        with self.assertRaises(Exception):
            self.w("objects/create", {"name": "client_vision_360", "database": DB})
        self.w("objects/delete", {"id": oid})
        self.assertNotIn(oid, self.api._objects)

    def test_manual_objects_survive_imports_and_undo(self):
        oid = self.w("objects/create", {"name": "viz_finale", "type": "VIZ"})["id"]
        with open(DEMO, "rb") as handle:
            self.api.import_metadata(handle.read(), {"format": "json", "name": "demo bis"}, "bob")
        self.assertIn(oid, self.api._objects)
        self.api.write("history/undo", {}, "alice")                       # Ctrl+Z : l'objet disparaît
        self.assertNotIn(oid, self.api._objects)
        self.api.write("history/redo", {}, "alice")
        self.assertIn(oid, self.api._objects)

    def test_works_without_any_import(self):
        from engine.server import LineageApi
        from .helpers import temp_config
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        api = LineageApi(config)
        a = api.write("objects/create", {"name": "source_mere", "type": "OBJET_METIER"}, "alice")["id"]
        b = api.write("objects/create", {"name": "viz", "type": "VIZ"}, "alice")["id"]
        api.write("links/create", {"kind": "FLOW", "source": a, "target": b}, "alice")
        self.assertEqual(len(api.graph(a, {})["edges"]), 1)
        self.assertTrue(api.state_info("alice")["has_data"])


class MergeNewTest(unittest.TestCase):
    SQL = """CREATE TABLE db_demo_dw.client_scoring AS
             SELECT f.code_client, s.segment_valeur AS segment, count(p.id_pret) AS nb_prets
             FROM client_fiche_identite f
             JOIN client_segmentation s ON s.code_client = f.code_client
             LEFT JOIN client_fiche_prets p ON p.code_client = f.code_client
             GROUP BY f.code_client, s.segment_valeur"""

    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731
        self.new = self.w("objects/create", {"name": "client_scoring", "columns": "code_client\nsegment"})["id"]
        self.link = self.w("links/create", {"kind": "FLOW", "source": self.new, "target": f"{DB}.client_vision_360"})["id"]
        self.w("notes/add", {"object_id": self.new, "text": "prévue au sprint 4"})

    def test_preview_proposes_the_new_table(self):
        p = self.api.merge_preview(self.SQL.encode(), {"format": "sql"})
        self.assertEqual(p["objects"][0]["object"], f"{DB}.client_scoring")
        self.assertEqual(p["objects"][0]["proposed"], self.new)
        self.assertEqual([n["id"] for n in p["news"]], [self.new])

    def test_merge_keeps_links_without_duplicate(self):
        r = self.api.merge_apply(self.SQL.encode(), {"format": "sql", "new": self.new, "object": f"{DB}.client_scoring"}, "alice")
        self.assertTrue(r["ok"])
        real = f"{DB}.client_scoring"
        self.assertNotIn(self.new, self.api._objects)
        self.assertEqual(sum(1 for o in self.api._objects if o.endswith(".client_scoring")), 1)
        link = self.api.state["links"][self.link]
        self.assertEqual(link["source"], real)                            # le lien dessiné à la main a suivi
        self.assertEqual(len(self.api.state["notes"][real]), 1)
        detail = self.api.object_detail(real)
        self.assertTrue(detail["rules"])                                   # règles de gestion de la vraie requête
        self.assertEqual({c["name"] for c in detail["columns"]}, {"code_client", "segment", "nb_prets"})

    def test_plain_import_reports_the_match(self):
        r = self.api.import_metadata(self.SQL.encode(), {"format": "sql", "name": "scoring.sql"}, "alice")
        self.assertEqual(r["new_matches"], [{"new": self.new, "object": f"{DB}.client_scoring"}])
        self.assertIn("DOUBLON", {a["kind"] for a in self.api.anomalies()["items"]})
        self.w("objects/merge", {"id": self.new, "into": f"{DB}.client_scoring"})
        self.assertEqual(self.api.state["links"][self.link]["source"], f"{DB}.client_scoring")

    def test_json_fragment_can_define_the_table(self):
        fragment = {"objects": [{"database": DB, "name": "client_scoring",
                                 "columns": [{"name": "code_client", "type": "string"}, {"name": "segment", "type": "string"}]}]}
        r = self.api.merge_apply(json.dumps(fragment).encode(), {"format": "json", "new": self.new,
                                                                 "object": f"{DB}.client_scoring"}, "alice")
        self.assertTrue(r["ok"])
        self.assertEqual(self.api.object_detail(f"{DB}.client_scoring")["columns"][0]["data_type"], "string")
        self.assertEqual(len(self.api._objects), 21)                      # 20 du JSON + la table définie


if __name__ == "__main__":
    unittest.main()

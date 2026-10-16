"""Un import JSON ou SQL ne casse jamais le travail de l'équipe (tags, liens, objets créés, docs, suivi)."""

from __future__ import annotations

import json
import unittest

from .helpers import DB, DEMO, demo_api
from .test_merge_sql import SQL


class ImportNonRegressionTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        w = lambda action, body: self.api.write(action, body, "alice")  # noqa: E731
        self.tag = w("tags/save", {"name": "Mart", "order": 3})["id"]
        w("tags/assign", {"object_ids": [f"{DB}.client_vision_360"], "tag_id": self.tag})
        self.link = w("links/create", {"kind": "FLOW", "source": f"{DB}.client_fiche_identite",
                                       "target": f"{DB}.client_vision_360"})["id"]
        self.flux = w("links/create", {"kind": "FLOW", "source": f"{DB}.flux1_base_emprunteurs",
                                       "target": f"{DB}.flux2_montants_signes"})["id"]
        self.custom = w("objects/create", {"name": "dossier_credit", "type": "OBJET_METIER"})["id"]
        w("links/create", {"kind": "FLOW", "source": self.custom, "target": f"{DB}.client_fiche_identite"})
        w("object/update", {"id": f"{DB}.client_vision_360", "description": "Vue client complète"})
        w("notes/add", {"object_id": f"{DB}.client_vision_360", "text": "à revoir"})
        w("tickets/save", {"title": "Montant faux", "object_id": f"{DB}.client_vision_360"})
        with open(DEMO, encoding="utf-8") as handle:
            self.demo = json.load(handle)

    def team_work(self):
        st = self.api.state
        return {"tags": set(st["tags"]), "assign": dict(st["assign"]), "custom": set(st["custom"]),
                "manual_links": {i for i, l in st["links"].items() if l["origin"] == "MANUEL"},
                "doc": st["objects"].get(f"{DB}.client_vision_360", {}).get("description"),
                "notes": len(st["notes"].get(f"{DB}.client_vision_360", [])), "tickets": len(st["tickets"])}

    def test_json_then_sql_then_json_keeps_everything(self):
        before = self.team_work()
        self.api.import_metadata(SQL.encode(), {"format": "sql", "name": "alim.sql"}, "bob")
        nb_rules = len(self.api.object_detail(f"{DB}.client_vision_360")["rules"])
        self.assertGreater(nb_rules, 0)
        result = self.api.import_metadata(json.dumps(self.demo).encode(), {"format": "json", "name": "demo v2"}, "bob")
        self.assertEqual(result["mode"], "refresh")
        self.assertEqual(self.team_work(), before)
        # La nouvelle structure JSON garde ce que le SQL avait apporté (règles de gestion, liens SQL).
        self.assertEqual(len(self.api.object_detail(f"{DB}.client_vision_360")["rules"]), nb_rules)
        self.assertIn(self.custom, self.api._objects)                                   # objet créé à la main conservé

    def test_table_removed_from_json_leaves_orphan_links(self):
        self.demo["objects"] = [o for o in self.demo["objects"] if o["name"] != "flux2_montants_signes"]
        self.api.import_metadata(json.dumps(self.demo).encode(), {"format": "json", "name": "amputé"}, "bob")
        self.assertIn(self.flux, self.api.state["links"])                               # jamais supprimé
        link = next(l for l in self.api.review({})["items"] if l["id"] == self.flux)
        self.assertTrue(link["orphan"])
        kinds = {a["kind"] for a in self.api.anomalies()["items"]}
        self.assertIn("ORPHELIN", kinds)

    def test_replace_mode_keeps_team_work(self):
        before = self.team_work()
        self.api.import_metadata(json.dumps(self.demo).encode(), {"format": "json", "name": "x", "mode": "replace"}, "bob")
        self.assertEqual(self.team_work(), before)

    def test_sql_table_found_later_in_json_is_renamed_not_duplicated(self):
        self.api.import_metadata(SQL.encode(), {"format": "sql", "name": "alim.sql"}, "bob")
        join = next(l for l in self.api.state["links"].values() if "default.scoring_marketing" in (l["source"], l["target"]))
        self.demo["objects"].append({"database": DB, "name": "scoring_marketing",
                                     "columns": [{"name": "id_visiteur", "type": "string"},
                                                 {"name": "score_appetence", "type": "int"}]})
        result = self.api.import_metadata(json.dumps(self.demo).encode(), {"format": "json", "name": "demo + scoring"}, "bob")
        self.assertEqual(result["renamed"], {"default.scoring_marketing": f"{DB}.scoring_marketing"})
        moved = self.api.state["links"][join["id"]]
        self.assertIn(f"{DB}.scoring_marketing", (moved["source"], moved["target"]))
        self.assertNotIn("default.scoring_marketing", self.api._objects)


if __name__ == "__main__":
    unittest.main()

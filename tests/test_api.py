"""Tests de l'API (sans réseau) et du serveur HTTP local."""

from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

from engine.server import ApiError, LineageApi, create_server

from .helpers import DB, demo_api

U = "testeur"


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)

    def tag(self, name, order=None):
        return self.api.write("tags/save", {"name": name, "order": order}, U)

    def test_tree_groups_by_tag_then_database(self):
        t = self.tag("Fiche client")
        self.api.write("rules/save", {"pattern": "client_*", "tag_id": t["id"]}, U)
        tree = self.api.tree({})
        fiche = next(g for g in tree if g["tag_id"] == t["id"])
        self.assertEqual(fiche["count"], 6)
        self.assertEqual(fiche["databases"], [{"database": DB, "count": 6}])
        self.assertEqual(tree[-1]["tag_id"], "__none__")
        objs = self.api.tree_objects({"tag": t["id"], "db": DB})
        self.assertTrue(all(o["tag"] == "Fiche client" for o in objs))

    def test_search_everywhere_with_prefixes(self):
        t = self.tag("Fiche client")
        self.api.write("rules/save", {"pattern": "client_fiche_*", "tag_id": t["id"]}, U)
        self.assertEqual(self.api.search({"q": "code_client"})["total"], 12)              # par colonne
        self.assertEqual(self.api.search({"q": "fiche client"})["total"], 4)              # par nom de tag
        self.assertEqual(self.api.search({"q": "db_demo"})["total"], 20)                  # par nom de base
        r = self.api.search({"q": "tag:fiche col:siret"})
        self.assertEqual([o["name"] for o in r["objects"]], ["client_fiche_identite"])
        self.assertEqual(r["objects"][0]["matched_columns"][0]["name"], "siret")

    def test_link_creation_both_ways_and_validation(self):
        a, b = f"{DB}.client_fiche_identite", f"{DB}.client_vision_360"
        r = self.api.write("links/create", {"kind": "FLOW", "source": b, "target": a,
                                            "columns": [{"source": "code_client", "target": "code_client"}]}, U)
        self.assertEqual(self.api.review({})["items"][0]["id"], r["id"])
        with self.assertRaises(ApiError):                                                 # doublon refusé
            self.api.write("links/create", {"kind": "FLOW", "source": b, "target": a,
                                            "columns": [{"source": "code_client", "target": "code_client"}]}, U)
        self.api.write("links/update", {"id": r["id"], "kind": "FLOW", "source": a, "target": b,
                                        "columns": [{"source": "code_client", "target": "code_client"}]}, U)
        detail = self.api.object_detail(a)
        self.assertEqual(detail["downstream"][0]["target"], b)                            # sens inversé
        self.api.write("links/validate", {"id": r["id"]}, U)
        self.assertEqual(self.api.review({})["items"], [])
        lineage = self.api.column_lineage({"object": b, "column": "code_client", "dir": "up"})
        self.assertEqual(lineage["steps"][0]["object"], a)
        self.api.write("links/delete", {"id": r["id"]}, U)
        self.assertEqual(self.api.object_detail(a)["downstream"], [])

    def test_unknown_column_and_object_refused(self):
        with self.assertRaises(ApiError):
            self.api.write("links/create", {"kind": "FLOW", "source": f"{DB}.web_visites", "target": f"{DB}.inconnu"}, U)
        with self.assertRaises(ApiError):
            self.api.write("links/create", {"kind": "FLOW", "source": f"{DB}.web_visites", "target": f"{DB}.web_visites_pret",
                                            "columns": [{"source": "pas_la", "target": "url"}]}, U)

    def test_graph_shows_links_and_suggestions(self):
        a, b = f"{DB}.campagne_emailing", f"{DB}.campagne_emailing_courante"
        g = self.api.graph(a, {})
        self.assertIn("SUGGESTION", {e["status"] for e in g["edges"]})
        self.api.write("links/create", {"kind": "FLOW", "source": a, "target": b}, U)
        g = self.api.graph(a, {})
        statuses = {e["status"] for e in g["edges"] if b in (e["source_object"], e["target_object"])}
        self.assertEqual(statuses, {"A_VALIDER"})                                         # la suggestion disparaît
        self.assertEqual(len(self.api.graph(a, {"pending": "0", "sugg": "0"})["nodes"]), 1)

    def test_suggestion_direction_from_tag_order(self):
        src, mart = self.tag("Source", 1), self.tag("Mart", 3)
        self.api.write("tags/assign", {"object_ids": [f"{DB}.campagne_emailing"], "tag_id": src["id"]}, U)
        self.api.write("tags/assign", {"object_ids": [f"{DB}.campagne_emailing_courante"], "tag_id": mart["id"]}, U)
        sug = next(s for s in self.api.suggestions({})["items"] if "campagne" in s["key"])
        self.assertEqual(sug["proposed_source"], f"{DB}.campagne_emailing")
        self.api.write("suggestions/dismiss", {"key": sug["key"]}, U)
        self.assertNotIn(sug["key"], {s["key"] for s in self.api.suggestions({})["items"]})

    def test_column_view(self):
        view = self.api.column_view({"name": "code_client"})
        self.assertEqual(len(view["tables"]), 12)
        self.assertTrue(view["type_conflict"])
        odd = [t["name"] for t in view["tables"] if t["type_differs"]]
        self.assertEqual(odd, ["client_segmentation"])

    def test_settings_rebuild_and_hide(self):
        self.api.write("settings/save", {"ignore_columns": ["id_campagne"], "hidden_objects": [f"{DB}.table_technique"]}, U)
        self.assertEqual(self.api.search({"q": "table_technique"})["total"], 0)
        self.assertNotIn(f"{DB}.table_technique", {o["id"] for g in self.api.tree({}) if g["tag_id"] == "__none__"
                                                    for o in self.api.tree_objects({"tag": "__none__", "db": DB})})

    def test_docs_notes_versions_activity_export(self):
        obj = f"{DB}.web_visites"
        self.api.write("object/update", {"id": obj, "description": "Visites", "spec_url": "https://wiki.exemple/x"}, U)
        with self.assertRaises(ApiError):
            self.api.write("object/update", {"id": obj, "spec_url": "javascript:alert(1)"}, U)
        self.api.write("notes/add", {"object_id": obj, "text": "à vérifier"}, U)
        v = self.api.write("versions/save", {"label": "v1"}, U)
        self.assertEqual(self.api.versions()[0]["label"], "v1")
        self.assertIn("links_added", self.api.version_detail({"file": v["file"]})["diff"])
        self.assertEqual(self.api.activity({})[0]["type"], "version.save")
        data = self.api.export_xlsx()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
        self.assertEqual(self.api.report()["indicators"]["documented"], 1)

    def test_reimport_keeps_team_work_and_reports_diff(self):
        t = self.tag("Web")
        self.api.write("tags/assign", {"object_ids": [f"{DB}.web_visites"], "tag_id": t["id"]}, U)
        md = json.dumps({"objects": [{"id": f"{DB}.web_visites", "columns": [{"name": "id_visite", "type": "string"}]},
                                     {"id": f"{DB}.nouvelle", "columns": []}]}).encode()
        result = self.api.import_metadata(md, {"format": "json", "name": "v2"}, U)
        self.assertIn(f"{DB}.nouvelle", result["diff"]["objects_added"])
        self.assertEqual(self.api.node(f"{DB}.web_visites")["tag"], "Web")

    def test_second_computer_sees_changes(self):
        other = LineageApi(dict(self.config, paths=dict(self.config["paths"])))
        self.api.write("tags/save", {"name": "Partagé"}, "alice")
        refreshed = other.write("refresh", {}, U)                                    # bouton « Actualiser »
        self.assertEqual(refreshed["revision"], self.api.workspace.revision)
        self.assertEqual(refreshed["objects"], 20)
        self.assertIn("Partagé", {t["name"] for t in other.state["tags"].values()})
        self.assertTrue(other.revision("0")["nb_changes"] >= 2)


class HttpTest(unittest.TestCase):
    def test_http_roundtrip_and_exclusive_port(self):
        api, config, tmp = demo_api()
        self.addCleanup(tmp.cleanup)
        httpd, _ = create_server(config, "127.0.0.1", 0)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/api/search?q=code_client") as res:
            self.assertEqual(json.load(res)["total"], 12)
        req = urllib.request.Request(base + "/api/tags/save", method="POST", data=json.dumps({"name": "HTTP"}).encode(),
                                     headers={"Content-Type": "application/json", "X-User": "alice"})
        with urllib.request.urlopen(req) as res:
            self.assertEqual(json.load(res)["name"], "HTTP")
        with urllib.request.urlopen(base + "/api/activity") as res:
            self.assertEqual(json.load(res)[0]["user"], "alice")
        with urllib.request.urlopen(base + "/") as res:
            self.assertIn(b"Lineage Explorer", res.read())
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/../engine/server.py")
        second, _ = create_server(config, "127.0.0.1", port)                                # port déjà pris
        self.addCleanup(second.server_close)
        self.assertNotEqual(second.server_address[1], port)


if __name__ == "__main__":
    unittest.main()

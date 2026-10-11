"""Tests de l'API et des annotations partagées (sans réseau, puis via HTTP local)."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from engine import build, ingest
from engine.server import LineageApi, make_handler

from .helpers import SAMPLE, STRUCTURE_ONLY, temp_config


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.config, self.tmp = temp_config()
        self.addCleanup(self.tmp.cleanup)
        build.run_build(ingest.load_metadata(STRUCTURE_ONLY), self.config, self.config["paths"]["database"], "test")
        self.api = LineageApi(self.config)

    def test_object_detail(self):
        d = self.api.object_detail("bv_client.tiers")
        self.assertEqual(d["object"]["layer"], "BV")
        self.assertEqual([e["source_object"] for e in d["upstream"]], ["lac_crm.clients_raw"])
        self.assertEqual(d["upstream"][0]["status"], "SUGGESTED")
        self.assertEqual(d["unexplained_columns"], ["categorie_client"])

    def test_graph_up_down(self):
        g = self.api.graph("dm_pret.encours", {"dir": "up", "depth": "3", "rel": "0", "min_score": "0.4"})
        ids = {n["id"] for n in g["nodes"]}
        self.assertEqual(ids, {"dm_pret.encours", "bv_client.tiers", "lac_crm.clients_raw"})
        depths = {n["id"]: n["depth"] for n in g["nodes"]}
        self.assertEqual(depths["lac_crm.clients_raw"], -2)
        down = self.api.graph("lac_crm.clients_raw", {"dir": "down", "depth": "5", "rel": "0", "min_score": "0.4"})
        self.assertIn("dm_pret.encours", {n["id"] for n in down["nodes"]})
        high = self.api.graph("dm_pret.encours", {"dir": "up", "depth": "3", "rel": "0", "min_score": "0.5"})
        self.assertEqual(len(high["nodes"]), 1)                                  # curseur de confiance

    def test_column_lineage(self):
        r = self.api.column_lineage({"object": "dm_pret.encours", "column": "code_tiers_ref", "dir": "up"})
        reached = {(s["object"], s["column"]) for s in r["steps"]}
        self.assertIn(("lac_crm.clients_raw", "code_tiers"), reached)

    def test_reject_persists_after_rebuild(self):
        self.api.annotate("lac_crm.clients_raw", {"author": "test", "link": {
            "kind": "FLOW", "target": "bv_client.tiers", "status": "REJECTED"}})
        self.assertEqual(self.api.object_detail("bv_client.tiers")["upstream"][0]["status"], "REJECTED")
        build.run_build(ingest.load_metadata(STRUCTURE_ONLY), self.config, self.config["paths"]["database"], "re-build")
        g = self.api.graph("bv_client.tiers", {"dir": "up", "rel": "0"})
        self.assertEqual(len(g["nodes"]), 1)                                     # le flux rejeté ne revient pas

    def test_validate_and_manual_link(self):
        self.api.annotate("bv_client.tiers", {"author": "jdupont", "link": {
            "kind": "FLOW", "target": "dm_pret.encours", "status": "VALIDATED"}})
        self.api.annotate("bv_pret.contrats", {"author": "jdupont", "link": {
            "kind": "FLOW", "target": "dm_pret.encours", "status": "MANUAL",
            "source_column": "montant_pret", "target_column": "montant_encours", "comment": "sum(montant_pret)"}})
        d = self.api.object_detail("dm_pret.encours")
        status = {e["source_object"]: e for e in d["upstream"]}
        self.assertEqual(status["bv_client.tiers"]["status"], "VALIDATED")
        self.assertEqual(status["bv_client.tiers"]["score"], 1.0)
        self.assertEqual(status["bv_pret.contrats"]["status"], "MANUAL")
        self.assertNotIn("montant_encours", d["unexplained_columns"])
        r = self.api.column_lineage({"object": "dm_pret.encours", "column": "montant_encours", "dir": "up"})
        self.assertIn(("lac_pret.contrats_raw", "montant"), {(s["object"], s["column"]) for s in r["steps"]})
        self.assertEqual(self.api.review({})["reviewed"], 1)

    def test_spec_and_notes(self):
        self.api.annotate("bv_client.tiers", {"author": "a", "spec_url": "https://confluence.exemple.fr/x"})
        data = self.api.annotate("bv_client.tiers", {"author": "b", "note": "Code tiers re-normalisé"})
        self.assertEqual(data["spec_url"], "https://confluence.exemple.fr/x")
        self.assertEqual(data["notes"][0]["author"], "b")
        with self.assertRaises(Exception):
            self.api.annotate("bv_client.tiers", {"spec_url": "javascript:alert(1)"})
        self.assertEqual(self.api.stats()["annotations"]["spec_urls"], 1)

    def test_path_and_impact(self):
        p = self.api.path({"from": "lac_pret.contrats_raw", "to": "dm_pret.encours"})
        self.assertTrue(p["found"])
        self.assertEqual(p["nodes"][0]["id"], "lac_pret.contrats_raw")
        self.assertEqual(p["nodes"][-1]["id"], "dm_pret.encours")
        csv_text = self.api.impact_csv("lac_crm.clients_raw")
        self.assertIn("dm_pret.encours", csv_text)

    def test_import(self):
        with open(SAMPLE, "rb") as handle:
            raw = handle.read()
        result = self.api.import_metadata(raw, {"name": "sample"})
        self.assertTrue(result["ok"])
        self.assertIn("DECLARED", result["report"]["flows_by_origin"])
        bad = self.api.import_metadata(b'{"objects":[{"id":"a.b","columns":[]},{"id":"a.b","columns":[]}]}', {})
        self.assertFalse(bad["ok"])


class HttpTest(unittest.TestCase):
    def test_http_roundtrip(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        build.run_build(ingest.load_metadata(SAMPLE), config, config["paths"]["database"], "test")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(LineageApi(config)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urllib.request.urlopen(base + "/api/search?q=tiers") as res:
            self.assertGreater(json.load(res)["total"], 0)
        with urllib.request.urlopen(base + "/") as res:
            self.assertIn(b"Lineage Explorer", res.read())
        req = urllib.request.Request(base + "/api/annotations/bv_client.tiers", method="POST",
                                     data=json.dumps({"author": "t", "note": "ok"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as res:
            self.assertEqual(json.load(res)["notes"][0]["text"], "ok")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(base + "/api/object/inconnu.x")
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/../engine/server.py")


if __name__ == "__main__":
    unittest.main()

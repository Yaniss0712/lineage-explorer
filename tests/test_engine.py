"""Tests du moteur : normalisation, ingestion, inférence, SQL, relations, build."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from engine import build, ingest, lineage, store
from engine.catalog import build_catalog
from engine.inference import infer_flows
from engine.normalize import normalize_column, simplify, types_compatible
from engine.relations import detect_relations

from .helpers import SAMPLE, STRUCTURE_ONLY, temp_config


class NormalizeTest(unittest.TestCase):
    def test_simplify(self):
        self.assertEqual(simplify("CodeTiers"), "code_tiers")
        self.assertEqual(simplify("Catégorie-Client"), "categorie_client")

    def test_normalize_column(self):
        prefixes, suffixes = ["id_", "cd_", "code_", "hk_"], ["_ref", "_reference", "_id"]
        self.assertEqual(normalize_column("code_tiers_ref", prefixes, suffixes), "tiers")
        self.assertEqual(normalize_column("code_tiers", prefixes, suffixes), "tiers")
        self.assertEqual(normalize_column("code", prefixes, suffixes), "code")

    def test_types(self):
        self.assertTrue(types_compatible("decimal(18,2)", "double"))
        self.assertFalse(types_compatible("date", "decimal(18,2)"))
        self.assertTrue(types_compatible("string", "unknown_type"))


class IngestTest(unittest.TestCase):
    def test_check_sample_ok(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        report = ingest.check_metadata(ingest.load_metadata(SAMPLE), config)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["objects_by_layer"], {"LAC": 2, "BV": 2, "MART": 1})

    def test_check_detects_errors(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        md = ingest.parse_metadata({"objects": [
            {"id": "lac_a.t", "columns": [{"name": "x", "type": "string"}]},
            {"id": "lac_a.t", "columns": [{"name": "x"}]},
            {"id": "zzz.t", "columns": []}],
            "flows": [{"source": "lac_a.t", "target": "absent.t"}]})
        report = ingest.check_metadata(md, config)
        self.assertFalse(report["ok"])
        self.assertTrue(any("dupliqué" in e for e in report["errors"]))
        self.assertTrue(any("inexistant" in e for e in report["errors"]))
        self.assertTrue(any("sans couche" in w for w in report["warnings"]))

    def test_jsonl(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as handle:
            handle.write('{"record":"meta","format_version":"1.0"}\n')
            handle.write('{"record":"object","id":"lac_a.t","database":"lac_a","name":"t","type":"TABLE",'
                         '"columns":[{"name":"x","type":"string"}]}\n')
            handle.write('{"record":"flow","source":"lac_a.t","target":"bv_a.t"}\n')
        self.addCleanup(os.remove, handle.name)
        md = ingest.load_metadata(handle.name)
        self.assertEqual(len(md["objects"]), 1)
        self.assertEqual(md["flows"][0]["target"], "bv_a.t")
        self.assertEqual(md["meta"]["format_version"], "1.0")


class InferenceTest(unittest.TestCase):
    """Reproduit les scores du prototype décrits dans docs/05 § 1.2."""

    @classmethod
    def setUpClass(cls):
        config, tmp = temp_config()
        tmp.cleanup()
        catalog = build_catalog(ingest.load_metadata(STRUCTURE_ONLY), config)
        cls.flows = {(e["source_object"], e["target_object"]): e for e in infer_flows(catalog, config)}

    def test_lac_to_bv(self):
        edge = self.flows[("lac_pret.contrats_raw", "bv_pret.contrats")]
        self.assertAlmostEqual(edge["score"], 0.85, delta=0.02)
        mapped = {c["target"]: c["source"] for c in edge["columns"]}
        self.assertEqual(mapped["montant_pret"], "montant")
        self.assertEqual(mapped["code_tiers_ref"], "code_tiers")

    def test_rename_unexplained(self):
        edge = self.flows[("lac_crm.clients_raw", "bv_client.tiers")]
        self.assertAlmostEqual(edge["score"], 0.53, delta=0.02)
        self.assertIn("categorie_client", edge["details"]["unexplained"])
        self.assertIn("Non expliquées", edge["explanation"])

    def test_mart(self):
        edge = self.flows[("bv_client.tiers", "dm_pret.encours")]
        self.assertAlmostEqual(edge["score"], 0.46, delta=0.02)
        self.assertNotIn(("bv_pret.contrats", "dm_pret.encours"), self.flows)

    def test_direction_follows_layers(self):
        for source, target in self.flows:
            self.assertFalse(target.startswith("lac_"), f"un objet du lac ne peut pas être alimenté : {source} → {target}")


class SqlTest(unittest.TestCase):
    def test_view_sql(self):
        known = ["lac_crm.clients_raw", "bv_client.tiers"]
        parsed = lineage.parse_sql("SELECT trim(upper(c.code_tiers)) AS code_tiers_ref, c.raison_sociale, "
                                   "c.segment AS categorie_client FROM lac_crm.clients_raw c -- commentaire",
                                   "bv_client.tiers", known)
        self.assertEqual(parsed["target"], "bv_client.tiers")
        self.assertEqual(parsed["sources"], ["lac_crm.clients_raw"])
        cols = {c["target"]: c for c in parsed["columns"]}
        self.assertEqual(cols["categorie_client"]["source"], "segment")
        self.assertEqual(cols["code_tiers_ref"]["transformation"], "trim(upper(c.code_tiers))")
        self.assertIsNone(cols["raison_sociale"]["transformation"])

    def test_insert_and_join(self):
        known = ["dm_pret.encours", "bv_pret.contrats", "bv_client.tiers"]
        sql = ("INSERT OVERWRITE TABLE dm_pret.encours SELECT t.code_tiers_ref, sum(c.montant_pret) AS montant_encours "
               "FROM bv_pret.contrats c JOIN bv_client.tiers t ON c.code_tiers_ref = t.code_tiers_ref GROUP BY t.code_tiers_ref")
        parsed = lineage.parse_sql(sql, "dm_pret.encours", known)
        self.assertEqual(parsed["target"], "dm_pret.encours")
        self.assertEqual(sorted(parsed["sources"]), ["bv_client.tiers", "bv_pret.contrats"])
        cols = {(c["source_object"], c["target"]): c for c in parsed["columns"]}
        self.assertIn(("bv_pret.contrats", "montant_encours"), cols)

    def test_cte_ignored(self):
        parsed = lineage.parse_sql("WITH x AS (SELECT a FROM lac_a.t) SELECT a FROM x", "bv_a.v", ["lac_a.t", "bv_a.v"])
        self.assertEqual(parsed["sources"], ["lac_a.t"])


class BuildTest(unittest.TestCase):
    def test_build_sample_arbitration_and_relations(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        md = ingest.load_metadata(SAMPLE)
        report = build.run_build(md, config, config["paths"]["database"], "test")
        self.assertIn("DECLARED", report["flows_by_origin"])
        self.assertIn("SQL_PARSED", report["flows_by_origin"])
        con = store.connect(config["paths"]["database"])
        self.addCleanup(con.close)
        up = {e["source_object"]: e for e in store.object_edges(con, "dm_pret.encours", "FLOW", "up")}
        self.assertEqual(set(up), {"bv_pret.contrats", "bv_client.tiers"})      # le SQL trouve les 2 sources
        declared = store.object_edges(con, "bv_client.tiers", "FLOW", "up")[0]
        self.assertEqual(declared["origin"], "DECLARED")
        self.assertIn("Confirme le flux inféré", declared["explanation"])
        rels = store.object_edges(con, "bv_client.tiers", "RELATION")
        self.assertTrue(any(r["origin"] == "DECLARED" for r in rels))
        cols = store.column_edges(con, declared["id"])
        self.assertIn(("segment", "categorie_client"), {(c["source_column"], c["target_column"]) for c in cols})
        self.assertTrue(os.path.exists(os.path.join(tmp.name, "build_report.json")))

    def test_relations_require_key_like_columns(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        md = ingest.parse_metadata({"objects": [
            {"id": "bv_a.t1", "columns": [{"name": "montant", "type": "decimal"}, {"name": "num_contrat", "type": "string"}]},
            {"id": "bv_b.t2", "columns": [{"name": "montant", "type": "decimal"}, {"name": "num_contrat", "type": "string"}]}]})
        catalog = build_catalog(md, config)
        rels, _ = detect_relations(catalog, md, [], config)
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["source_column"], "num_contrat")

    def test_search(self):
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        build.run_build(ingest.load_metadata(SAMPLE), config, config["paths"]["database"], "test")
        con = store.connect(config["paths"]["database"])
        self.addCleanup(con.close)
        res = store.search(con, "TIERS")
        ids = [r["ref_id"] for r in res["results"]]
        self.assertIn("bv_client.tiers", ids)
        self.assertIn("lac_crm.clients_raw.code_tiers", ids)
        self.assertEqual(store.search(con, "tiers", layer="MART")["total"], 1)
        self.assertGreaterEqual(store.search(con, "catégorie")["total"], 1)       # accents ignorés
        self.assertEqual(store.search(con, "", dtype="decimal")["total"], 3)
        self.assertGreaterEqual(store.search(con, "crm", in_desc=True)["total"], 1)


class EvaluateTest(unittest.TestCase):
    def test_precision_recall(self):
        from engine.evaluate import evaluate, load_truth
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        build.run_build(ingest.load_metadata(STRUCTURE_ONLY), config, config["paths"]["database"], "test")
        con = store.connect(config["paths"]["database"])
        self.addCleanup(con.close)
        result = evaluate(con, load_truth(os.path.join(os.path.dirname(SAMPLE), "ground_truth.sample.json")), 0.4)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["recall"], 0.75)
        self.assertEqual(result["false_negatives"][0]["source"], "bv_pret.contrats")


if __name__ == "__main__":
    unittest.main()

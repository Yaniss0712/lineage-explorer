"""Tests de l'ingestion de requêtes SQL (structure, lineage et jointures déduits du SQL)."""

from __future__ import annotations

import json
import os
import unittest

from engine import ingest
from engine.config import PROJECT_ROOT
from engine.sql_ingest import parse_sql_script, split_statements

from .helpers import temp_config

ARCHI = os.path.join(PROJECT_ROOT, "architecture test")


class SqlIngestTest(unittest.TestCase):
    def test_split_ignores_comments_and_strings(self):
        stmts = split_statements("-- commentaire ; ici\nSELECT 'a;b' FROM t; /* x; */ SELECT 1 FROM u")
        self.assertEqual(len(stmts), 2)
        self.assertIn("'a;b'", stmts[0])

    def test_ddl_structure(self):
        md = parse_sql_script("""
            CREATE TABLE IF NOT EXISTS lac_a.clients (
              `id_client` STRING COMMENT 'Identifiant',
              montant DECIMAL(18, 2),
              id_agence STRING REFERENCES lac_a.agences (id_agence),
              PRIMARY KEY (id_client)
            ) COMMENT 'Clients' PARTITIONED BY (dt STRING) STORED AS PARQUET;
            ALTER TABLE lac_a.clients ADD CONSTRAINT fk FOREIGN KEY (id_client) REFERENCES lac_b.contrats (id_client);
        """)
        obj = md["objects"][0]
        self.assertEqual(obj["id"], "lac_a.clients")
        self.assertEqual(obj["description"], "Clients")
        cols = {c["name"]: c for c in obj["columns"]}
        self.assertEqual(list(cols), ["id_client", "montant", "id_agence", "dt"])
        self.assertTrue(cols["id_client"]["is_key"])
        self.assertEqual(cols["montant"]["type"], "decimal(18,2)")
        self.assertEqual(cols["id_client"]["description"], "Identifiant")
        self.assertEqual(len(md["relations"]), 2)

    def test_structure_deduced_from_queries_only(self):
        md = parse_sql_script("""
            INSERT OVERWRITE TABLE dm_x.synthese
            SELECT c.code_client, count(k.num_contrat) AS nb, sum(k.montant) AS total
            FROM bv_x.clients c JOIN bv_x.contrats k ON k.code_client = c.code_client
            GROUP BY c.code_client
        """)
        objs = {o["id"]: o for o in md["objects"]}
        self.assertEqual(set(objs), {"dm_x.synthese", "bv_x.clients", "bv_x.contrats"})
        self.assertEqual({c["name"] for c in objs["bv_x.contrats"]["columns"]}, {"code_client", "num_contrat", "montant"})
        cols = {c["name"]: c["type"] for c in objs["dm_x.synthese"]["columns"]}
        self.assertEqual(cols, {"code_client": None, "nb": "bigint", "total": "decimal(18,2)"})
        self.assertEqual(sorted((f["source"], f["target"]) for f in md["flows"]),
                         [("bv_x.clients", "dm_x.synthese"), ("bv_x.contrats", "dm_x.synthese")])
        rel = md["relations"][0]
        self.assertEqual({rel["a_column"], rel["b_column"]}, {"code_client"})

    def test_view_with_star_cte_and_positional_insert(self):
        md = parse_sql_script("""
            CREATE TABLE lac_a.t (a INT, b STRING);
            CREATE VIEW bv_a.v AS SELECT * FROM lac_a.t;
            CREATE TABLE dm_a.m (x INT, y STRING);
            INSERT INTO dm_a.m WITH w AS (SELECT a, b FROM bv_a.v) SELECT w.a, upper(w.b) FROM w;
        """)
        objs = {o["id"]: o for o in md["objects"]}
        self.assertEqual([c["name"] for c in objs["bv_a.v"]["columns"]], ["a", "b"])
        self.assertEqual([c["name"] for c in objs["dm_a.m"]["columns"]], ["x", "y"])
        flows = {(f["source"], f["target"]) for f in md["flows"]}
        self.assertIn(("lac_a.t", "bv_a.v"), flows)
        self.assertIn(("bv_a.v", "dm_a.m"), flows)

    def test_architecture_sql_matches_truth(self):
        md = ingest.load_metadata(os.path.join(ARCHI, "requetes_architecture.sql"))
        with open(os.path.join(ARCHI, "verite_terrain.json"), encoding="utf-8") as handle:
            truth = {(f["source"], f["target"]) for f in json.load(handle)["flows"]}
        self.assertEqual({(f["source"], f["target"]) for f in md["flows"]}, truth)
        self.assertEqual(len(md["objects"]), 23)
        tiers = {c["name"]: c for o in md["objects"] if o["id"] == "bv_client.tiers" for c in o["columns"]}
        self.assertEqual(tiers["code_tiers_ref"]["type"], "string")              # type déduit du DDL source

    def test_sql_becomes_directed_suggestions(self):
        from engine.server import LineageApi
        config, tmp = temp_config()
        self.addCleanup(tmp.cleanup)
        api = LineageApi(config)
        with open(os.path.join(ARCHI, "requetes_architecture.sql"), "rb") as handle:
            result = api.import_metadata(handle.read(), {"format": "sql", "name": "archi"}, "testeur")
        self.assertTrue(result["ok"])
        sql = [s for s in api.suggestions({"limit": 1000})["items"] if s["origin"] == "SQL"]
        self.assertEqual(len(sql), 28)                          # aucun lien créé : 28 suggestions orientées
        tiers = next(s for s in sql if s["key"] == "bv_client.tiers|lac_crm.clients_raw")
        self.assertEqual(tiers["proposed_source"], "lac_crm.clients_raw")
        self.assertIn({"source": "segment", "target": "categorie_client", "transformation": "renommage"}, tiers["mapping"])
        self.assertEqual(api.state["links"], {})

if __name__ == "__main__":
    unittest.main()

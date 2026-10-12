"""Tests du moteur : normalisation, ingestion, suggestions, cache et recherche."""

from __future__ import annotations

import os
import tempfile
import unittest

from engine import build, ingest, store
from engine.catalog import build_catalog
from engine.config import DEFAULTS
from engine.normalize import normalize_column, simplify, types_compatible
from engine.suggestions import compute_suggestions, pair_key

from .helpers import DB, DEMO, SAMPLE

SETTINGS = DEFAULTS["suggestions"]


class NormalizeTest(unittest.TestCase):
    def test_simplify_and_normalize(self):
        self.assertEqual(simplify("CodeTiers"), "code_tiers")
        self.assertEqual(simplify("Région"), "region")
        self.assertEqual(normalize_column("code_tiers", ["code_"], ["_ref"]), "tiers")
        self.assertEqual(normalize_column("code", ["code_"], []), "code")

    def test_types(self):
        self.assertTrue(types_compatible("decimal(18,2)", "bigint"))
        self.assertFalse(types_compatible("int", "string"))


class IngestTest(unittest.TestCase):
    def test_keys_and_layers_are_ignored(self):
        md = ingest.parse_metadata({"objects": [{"id": "lac_a.t", "layer": "LAC",
                                                 "columns": [{"name": "X", "type": "STRING", "is_key": True}]}]})
        col = md["objects"][0]["columns"][0]
        self.assertEqual(col, {"name": "x", "type": "string", "description": "", "position": 1})
        self.assertNotIn("layer", md["objects"][0])

    def test_check(self):
        md = ingest.parse_metadata({"objects": [{"id": "a.t", "columns": [{"name": "x"}]},
                                                {"id": "a.t", "columns": []}],
                                    "flows": [{"source": "a.t", "target": "absent.t"}]})
        report = ingest.check_metadata(md)
        self.assertFalse(report["ok"])
        self.assertTrue(any("dupliqué" in e for e in report["errors"]))
        self.assertTrue(any("inexistant" in w for w in report["warnings"]))

    def test_parse_text_formats(self):
        self.assertEqual(len(ingest.parse_text('{"record":"object","id":"a.t","columns":[]}', "jsonl")["objects"]), 1)
        md = ingest.parse_text("CREATE VIEW b.v AS SELECT t.x FROM a.t t", "sql")
        self.assertEqual({o["id"] for o in md["objects"]}, {"a.t", "b.v"})
        self.assertEqual(md["flows"][0]["origin"], "SQL")
        with self.assertRaises(ValueError):
            ingest.parse_text("{pas du json", "json")


class SuggestionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        md = ingest.load_metadata(DEMO)
        cls.sugs = {s["key"]: s for s in compute_suggestions(build_catalog(md, SETTINGS), md, SETTINGS)}

    def test_copies_score_high(self):
        s = self.sugs[pair_key(f"{DB}.campagne_emailing", f"{DB}.campagne_emailing_courante")]
        self.assertGreaterEqual(s["score"], 0.9)
        self.assertIsNone(s["direction"])                          # aucune direction inventée
        self.assertIn("colonne(s) commune(s)", s["explanation"])

    def test_type_mismatch_flagged(self):
        s = self.sugs[pair_key(f"{DB}.client_fiche_identite", f"{DB}.client_segmentation")]
        self.assertTrue(any(c["mismatch"] for c in s["columns"]))
        self.assertIn("Types différents", s["explanation"])

    def test_ignored_and_generic_columns_do_not_link(self):
        for s in self.sugs.values():
            names = {c["a"] for c in s["columns"]}
            self.assertFalse(names & {"dt_chargement", "xx_date_insertion", "url"})

    def test_hints_from_file(self):
        md = ingest.load_metadata(SAMPLE)
        sugs = compute_suggestions(build_catalog(md, SETTINGS), md, SETTINGS)
        hint = next(s for s in sugs if s["origin"] == "DECLARE" and s["kind"] == "FLOW")
        self.assertEqual(hint["direction"], "lac_crm.clients_raw")
        self.assertTrue(hint["mapping"])


class CacheTest(unittest.TestCase):
    def test_build_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.db")
            report = build.build_cache(ingest.load_metadata(DEMO), SETTINGS, path)
            self.assertEqual(report["nb_objects"], 20)
            con = store.connect(path)
            try:
                hits = store.search_hits(con, "CODE_CLIENT", kinds=("COLUMN",))
                self.assertEqual(len({h["object_id"] for h in hits}), 12)
                self.assertEqual(len(store.column_occurrences(con, "code_client")), 12)
                stats = store.column_dictionary(con, "code_client")["items"][0]
                self.assertEqual(stats["conflict"], 1)                 # int dans une table, string ailleurs
                self.assertTrue(store.search_hits(con, "region"))
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()

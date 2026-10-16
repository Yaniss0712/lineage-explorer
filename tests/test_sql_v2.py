"""Analyseur SQL v2 (sqlglot) : requêtes complexes comparées à une vérité terrain."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from engine import sql_glot
from engine.sql_ingest import parse_sql_script

from .helpers import ARCHI

with open(os.path.join(ARCHI, "requetes_complexes.sql"), encoding="utf-8") as _h:
    COMPLEX = _h.read()
with open(os.path.join(ARCHI, "verite_terrain_sql.json"), encoding="utf-8") as _h:
    TRUTH = json.load(_h)


def scores(md: dict) -> dict:
    flows = {(f["source"], f["target"]) for f in md["flows"]}
    maps = {(f["source"], m["source"], f["target"], m["target"]) for f in md["flows"] for m in f["column_mapping"]}
    joins = {tuple(sorted([(r["a"], r["a_column"]), (r["b"], r["b_column"])])) for r in md["relations"]}
    t_flows = {tuple(x) for x in TRUTH["flows"]}
    t_maps = {tuple(x) for x in TRUTH["column_mappings"]}
    t_joins = {tuple(sorted([(a, b), (c, d)])) for a, b, c, d in TRUTH["joins"]}
    return {"flows": len(flows & t_flows) / len(t_flows), "extra_flows": flows - t_flows,
            "mappings": len(maps & t_maps) / len(t_maps), "joins": len(joins & t_joins) / len(t_joins)}


@unittest.skipUnless(sql_glot.AVAILABLE, "sqlglot absent (dossier vendor/)")
class SqlV2Test(unittest.TestCase):
    def test_complex_queries_fully_recognised(self):
        md = parse_sql_script(COMPLEX)
        self.assertEqual(md["meta"]["parsers"], {"sqlglot": 5, "regex": 0})
        result = scores(md)
        self.assertEqual(result["flows"], 1.0)
        self.assertEqual(result["extra_flows"], set())            # pas de faux flux vers une CTE
        self.assertEqual(result["mappings"], 1.0)
        self.assertEqual(result["joins"], 1.0)

    def test_better_than_regex_parser(self):
        glot, regex = scores(parse_sql_script(COMPLEX)), scores(parse_sql_script(COMPLEX, engine="regex"))
        self.assertGreater(glot["mappings"], regex["mappings"])
        self.assertGreater(glot["joins"], regex["joins"])

    def test_business_rules_with_cte_steps(self):
        rules = {r["target"]: r for r in parse_sql_script(COMPLEX)["rules"] if r["target"]}
        for target, expected in TRUTH["rules"].items():
            rule = rules[target]
            self.assertEqual(rule["kind"], expected["kind"])
            if "having" in expected:
                self.assertEqual(rule["having"], expected["having"])
            if "filters" in expected:
                self.assertEqual(rule["filters"], expected["filters"])
            for text in expected.get("step_filters", []):
                self.assertIn(text, [s["filters"] for s in rule["steps"]])
        score = rules["dm_risque.score_client"]
        formula = next(c["expression"] for c in score["columns"] if c["column"] == "encours_total")
        self.assertEqual(formula, "sum(prets_actifs.montant_initial)")
        risk = next(c["expression"] for c in score["columns"] if c["column"] == "classe_risque")
        self.assertTrue(risk.startswith("CASE WHEN max(retards.retard_max) > 90"))

    def test_column_lineage_goes_through_ctes(self):
        md = parse_sql_script(COMPLEX)
        flow = next(f for f in md["flows"] if (f["source"], f["target"]) == ("bv_pret.echeances", "dm_risque.score_client"))
        mapping = {m["target"]: m for m in flow["column_mapping"]}
        self.assertEqual(mapping["retard_max"]["source"], "jours_retard")
        self.assertIn("max(echeances.jours_retard)", mapping["retard_max"]["transformation"])   # calcul fait dans la CTE

    def test_fallback_to_regex_when_sqlglot_missing(self):
        with mock.patch.object(sql_glot, "AVAILABLE", False):
            md = parse_sql_script(COMPLEX)
        self.assertEqual(md["meta"]["parsers"]["sqlglot"], 0)
        self.assertIn(("lac_crm.contacts", "dm_client.dernier_contact"), {(f["source"], f["target"]) for f in md["flows"]})


if __name__ == "__main__":
    unittest.main()

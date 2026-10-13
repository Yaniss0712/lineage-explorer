"""JSON de structure + SQL qui le complète : reconnaissance des tables, règles de gestion, jointures."""

from __future__ import annotations

import unittest

from .helpers import DB, demo_api

SQL = """
-- requête d'alimentation : les tables sont citées sans leur base
INSERT OVERWRITE TABLE client_vision_360
SELECT f.code_client, f.categorie_client, sum(p.montant_crd) AS montant_total_crd, count(p.id_pret) AS nb_prets
FROM client_fiche_identite f
LEFT JOIN client_fiche_prets p ON p.code_client = f.code_client AND p.statut_pret = 'ACTIF'
WHERE f.date_creation >= '2020-01-01'
GROUP BY f.code_client, f.categorie_client;

-- requête d'analyse : nouvelle table inconnue du JSON + colonne absente du JSON
SELECT v.id_visite, s.score_appetence
FROM db_demo_dw.web_visites v
JOIN scoring_marketing s ON s.id_visiteur = v.id_visiteur
WHERE v.colonne_inexistante = 1;
"""


class MergeSqlTest(unittest.TestCase):
    def setUp(self):
        self.api, self.config, self.tmp = demo_api()
        self.addCleanup(self.tmp.cleanup)
        self.result = self.api.import_metadata(SQL.encode(), {"format": "sql", "name": "alimentation.sql"}, "testeur")

    def test_sql_completes_instead_of_replacing(self):
        self.assertTrue(self.result["ok"])
        self.assertEqual(self.result["mode"], "merge")
        self.assertEqual(len(self.api._objects), 21)                       # 20 du JSON + 1 nouvelle table
        rec = self.result["recognition"]
        for name in ("client_vision_360", "client_fiche_identite", "client_fiche_prets", "web_visites"):
            self.assertIn(f"{DB}.{name}", rec["matched"])
        self.assertEqual(rec["added"], ["default.scoring_marketing"])
        self.assertIn(f"{DB}.web_visites.colonne_inexistante", rec["unknown_columns"])
        cols = {c["name"]: c["data_type"] for c in self.api.object_detail(f"{DB}.client_vision_360")["columns"]}
        self.assertEqual(cols["montant_total_crd"], "decimal(18,2)")       # la structure JSON fait foi

    def test_business_rules_attached_to_existing_tables(self):
        rules = self.api.object_detail(f"{DB}.client_vision_360")["rules"]
        feed = next(r for r in rules if r["role"] == "target")
        self.assertEqual(feed["kind"], "INSERT")
        self.assertEqual(feed["filters"], "client_fiche_identite.date_creation >= '2020-01-01'")
        self.assertEqual(feed["joins"][0]["table"], f"{DB}.client_fiche_prets")
        self.assertIn("client_fiche_prets.statut_pret = 'ACTIF'", feed["joins"][0]["condition"])
        self.assertIn({"column": "montant_total_crd", "expression": "sum(client_fiche_prets.montant_crd)"}, feed["columns"])
        used = self.api.object_detail(f"{DB}.client_fiche_prets")["rules"]
        self.assertEqual(used[0]["role"], "source")

    def test_suggestions_and_column_lineage_from_sql(self):
        sugs = {s["key"]: s for s in self.api.suggestions({"limit": 500})["items"]}
        s = sugs[f"{DB}.client_fiche_prets|{DB}.client_vision_360"]
        self.assertEqual(s["origin"], "SQL")
        self.assertEqual(s["proposed_source"], f"{DB}.client_fiche_prets")
        lineage = self.api.column_lineage({"object": f"{DB}.client_vision_360", "column": "montant_total_crd", "dir": "up"})
        self.assertEqual((lineage["steps"][0]["object"], lineage["steps"][0]["column"]), (f"{DB}.client_fiche_prets", "montant_crd"))
        self.assertEqual(lineage["steps"][0]["status"], "SUGGESTION")
        self.assertEqual(self.api.state["links"], {})                      # toujours aucun lien automatique

    def test_replace_mode_still_available(self):
        r = self.api.import_metadata(SQL.encode(), {"format": "sql", "name": "seul.sql", "mode": "replace"}, "testeur")
        self.assertEqual(r["mode"], "replace")
        self.assertLess(len(self.api._objects), 20)


if __name__ == "__main__":
    unittest.main()

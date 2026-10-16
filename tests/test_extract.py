"""Script d'extraction Hive, testé avec un faux curseur ODBC (aucune base réelle)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

from engine import ingest
from engine.config import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
import extract_hive_metadata as ex  # noqa: E402

DESCRIBE = {
    "`db_lac`.`clients`": [
        ("# col_name", "data_type", "comment"), ("code_client", "string", "Identifiant client"),
        ("segment", "string", None), ("", None, None),
        ("# Partition Information", None, None), ("# col_name", "data_type", "comment"), ("dt", "string", ""),
        ("", None, None), ("# Detailed Table Information", None, None), ("Database:", "db_lac", None),
        ("Table Type:", "MANAGED_TABLE", None), ("Table Parameters:", None, None), ("", "comment", "Clients bruts"),
    ],
    "`db_bv`.`v_clients`": [
        ("# col_name", "data_type", "comment"), ("code_client", "string", "from deserializer"),
        ("segment_maj", "string", None), ("", None, None), ("# Detailed Table Information", None, None),
        ("Table Type:", "VIRTUAL_VIEW", None),
    ],
}
CREATE = {"`db_bv`.`v_clients`": [("CREATE VIEW `db_bv`.`v_clients` AS SELECT c.code_client, upper(c.segment) AS segment_maj",),
                                  ("FROM `db_lac`.`clients` c",)]}


class FakeCursor:
    def __init__(self):
        self.rows = []

    def execute(self, query):
        if query.startswith("SHOW TABLES IN `db_lac`"):
            self.rows = [("db_lac", "clients", "false")]
        elif query.startswith("SHOW TABLES IN `db_bv`"):
            self.rows = [("v_clients",)]
        elif query.startswith("SHOW TABLES IN"):
            raise RuntimeError("Base introuvable")
        elif query.startswith("DESCRIBE FORMATTED "):
            self.rows = DESCRIBE[query[len("DESCRIBE FORMATTED "):]]
        elif query.startswith("SHOW CREATE TABLE "):
            self.rows = CREATE[query[len("SHOW CREATE TABLE "):]]

    def fetchall(self):
        return self.rows


class ExtractTest(unittest.TestCase):
    def test_structure_views_sql_and_errors(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = {**ex.DEFAULTS, "dsn": "X", "databases": ["db_lac", "db_bv", "db_absente"],
                  "output": os.path.join(tmp.name, "m.json"), "sql_output": os.path.join(tmp.name, "v.sql"),
                  "errors": os.path.join(tmp.name, "e.json")}
        stats = ex.export_metadata(FakeCursor(), config, log=lambda *_: None)
        self.assertEqual(stats, {"objects": 2, "views": 1, "errors": 1})
        md = ingest.load_metadata(config["output"])
        clients = next(o for o in md["objects"] if o["name"] == "clients")
        self.assertEqual([c["name"] for c in clients["columns"]], ["code_client", "segment", "dt"])
        self.assertEqual(clients["description"], "Clients bruts")
        self.assertEqual(clients["columns"][0]["description"], "Identifiant client")
        view = next(o for o in md["objects"] if o["name"] == "v_clients")
        self.assertEqual(view["type"], "VIEW")
        self.assertEqual(view["columns"][0]["description"], "")                   # « from deserializer » ignoré
        with open(config["errors"], encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["errors"][0]["database"], "db_absente")
        sql = ingest.load_metadata(config["sql_output"])                           # le SQL des vues s'importe
        flow = sql["flows"][0]
        self.assertEqual((flow["source"], flow["target"]), ("db_lac.clients", "db_bv.v_clients"))
        self.assertIn("upper", flow["column_mapping"][1]["transformation"])

    def test_no_real_configuration_in_the_repository(self):
        with open(os.path.join(PROJECT_ROOT, "tools", "extraction.example.json"), encoding="utf-8") as handle:
            example = json.load(handle)
        self.assertTrue(all(db.startswith("db_exemple") for db in example["databases"]))
        with open(os.path.join(PROJECT_ROOT, ".gitignore"), encoding="utf-8") as handle:
            self.assertIn("extraction.local.json", handle.read())


if __name__ == "__main__":
    unittest.main()

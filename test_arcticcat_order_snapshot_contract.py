"""Regression of frozen non-actual order reference basis without importing bot globals."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import oem_reference_service as reference

class FrozenOrderBasis(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path=Path(self.root.name)/"snapshot.db"
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE order_items (
                reference_actual_weight_kg REAL,
                reference_calculation_weight_kg REAL,
                reference_weight_quality TEXT,
                reference_weight_source TEXT)""")
    def freeze(self,raw):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                       (None,raw,"approximate_legacy","arctic_cat_legacy:oem_reference_db"))
    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            actual,raw,quality,source=db.execute("SELECT * FROM order_items").fetchone()
        return {"actual_weight_kg":actual,"reference_calculation_weight_kg":raw,
                "reference_weight_quality":quality,"reference_weight_source":source}
    def test_tiny_approx_freeze(self):
        self.freeze(0.0045359)
        self.assertIsNone(self.snapshot()["actual_weight_kg"])
        self.assertEqual(reference.reference_client_calculation_weight_kg(self.snapshot()),.1)
    def test_fractional_approx_freeze(self):
        self.freeze(.81)
        self.assertEqual(reference.reference_client_calculation_weight_kg(self.snapshot()),.9)
        self.assertEqual(self.snapshot()["reference_weight_quality"],"approximate_legacy")
        self.assertEqual(self.snapshot()["reference_weight_source"],"arctic_cat_legacy:oem_reference_db")
    def test_reference_database_change_cannot_mutate_existing_order(self):
        self.freeze(.81)
        new_db_answer={"actual_weight_kg":None,"reference_calculation_weight_kg":2.7}
        self.assertEqual(reference.reference_client_calculation_weight_kg(new_db_answer),2.7)
        self.assertEqual(reference.reference_client_calculation_weight_kg(self.snapshot()),.9)
    def test_order_insert_has_matching_columns_and_bindings(self):
        source=Path("extremizer_bot.py").read_text(encoding="utf-8")
        import re
        match=re.search(r"INSERT INTO order_items\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",source,re.S)
        self.assertIsNotNone(match)
        columns=[x.strip() for x in match[1].split(",")]
        self.assertEqual(len(columns),match[2].count("?"))
        self.assertIn("reference_calculation_weight_kg",columns)
        self.assertIn("reference_weight_quality",columns)
    def test_quote_does_not_reread_current_reference_weight(self):
        src=Path("extremizer_bot.py").read_text(encoding="utf-8")
        tree=ast.parse(src)
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=="format_customer_quote")
        body=ast.get_source_segment(src,fn) or ""
        self.assertNotIn("get_oem_reference(",body)
    def test_web_raw_and_calculated_are_separate(self):
        src=Path("web_app.py").read_text(encoding="utf-8")
        tree=ast.parse(src)
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=="_weight_for_oem")
        import types
        scope={"oem_reference_service":types.SimpleNamespace(
            lookup_oem=lambda oem:{"actual_weight_kg":None,"volume_weight_kg":None,
                "reference_calculation_weight_kg":.81,
                "reference_weight_quality":"approximate_legacy",
                "reference_weight_source":"arctic_cat_legacy:oem_reference_db"},
            reference_client_calculation_weight_kg=reference.reference_client_calculation_weight_kg)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),"web_app.py","exec"),scope)
        result=scope["_weight_for_oem"]("0746-435")
        self.assertEqual(result["reference_weight_raw_kg"],.81)
        self.assertEqual(result["reference_calculation_weight_kg"],.9)
        self.assertIsNone(result["actual_kg"])
if __name__=="__main__":unittest.main()

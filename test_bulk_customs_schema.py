import sqlite3
import tempfile
import unittest
from pathlib import Path

from bulk_customs_schema import ensure_bulk_customs_schema
from bulk_order_contract import EQUIPMENT_TYPE_VALUES


class BulkCustomsSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "isolated.db"
        ensure_bulk_customs_schema(self.path)
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys = ON")

    def test_exact_tables_and_columns(self):
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables - {"sqlite_sequence"},
                         {"part_customs_profiles", "bulk_order_imports", "bulk_order_import_items"})
        expected = {
            "part_customs_profiles": "id manufacturer oem description_ru description_en country_of_origin gross_weight_kg net_weight_kg equipment_type created_at updated_at",
            "bulk_order_imports": "id status input_type raw_lines_count parsed_items_count review_items_count invalid_items_count created_at",
            "bulk_order_import_items": "id bulk_order_import_id source_line_number raw_text raw_oem normalized_oem quantity parse_status error_code duplicate_group_id",
        }
        for table, columns in expected.items():
            self.assertEqual([r[1] for r in self.db.execute(f"PRAGMA table_info({table})")],
                             columns.split())
        indexes = {r[1] for r in self.db.execute("PRAGMA index_list(bulk_order_import_items)")}
        self.assertEqual(indexes, {"idx_bulk_items_import", "idx_bulk_items_oem"})

    def test_unique_manufacturer_oem_and_string_storage(self):
        insert = "INSERT INTO part_customs_profiles(manufacturer,oem,created_at,updated_at) VALUES(?,?,?,?)"
        self.db.execute(insert, ("Ski-Doo", "0746-435", "now", "now"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(insert, ("Ski-Doo", "0746-435", "now", "now"))
        self.db.execute(insert, ("Other brand", "0746-435", "now", "now"))
        self.assertEqual(self.db.execute("SELECT oem FROM part_customs_profiles").fetchall(),
                         [("0746-435",), ("0746-435",)])

    def test_import_item_foreign_key_and_defaults(self):
        insert = "INSERT INTO bulk_order_import_items(bulk_order_import_id,source_line_number,raw_text,parse_status) VALUES(?,?,?,?)"
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(insert, (999, 1, "0746-435", "PARSED"))
        parent = self.db.execute("INSERT INTO bulk_order_imports(status,input_type,created_at) VALUES('READY','TEXT','now')").lastrowid
        self.db.execute(insert, (parent, 1, "0746-435", "PARSED"))
        self.assertEqual(self.db.execute("SELECT raw_lines_count,parsed_items_count,review_items_count,invalid_items_count FROM bulk_order_imports").fetchone(), (0, 0, 0, 0))
        self.assertEqual(self.db.execute("SELECT bulk_order_import_id,raw_text FROM bulk_order_import_items").fetchone(), (parent, "0746-435"))
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_idempotency_preserves_schema_and_all_rows(self):
        self.db.execute("INSERT INTO part_customs_profiles(manufacturer,oem,created_at,updated_at) VALUES('Ski-Doo','0746-435','now','now')")
        parent = self.db.execute("INSERT INTO bulk_order_imports(status,input_type,created_at) VALUES('READY','TEXT','now')").lastrowid
        self.db.execute("INSERT INTO bulk_order_import_items(bulk_order_import_id,source_line_number,raw_text,parse_status) VALUES(?,1,'0746-435','PARSED')", (parent,))
        self.db.commit()
        before = list(self.db.iterdump())
        ensure_bulk_customs_schema(self.path)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_equipment_values_exact(self):
        expected = ("ATV", "AUTO", "BOAT", "MOTO", "SNOWMOBILE", "WATERCRAFT",
                    "ACCESSORIES", "APPAREL & GEAR", "CLOTHES", "COMPUTER",
                    "FOOD & SUPPLEMENTS", "HOUSEHOLD PRODUCTS", "HUNTING & FISHING",
                    "MOBILE GADGETS", "PERFUMERY", "PET GOODS", "SHOES", "SPORT",
                    "THINGS", "TOOL", "VALUABLES", "ДРУГОЕ")
        self.assertEqual(EQUIPMENT_TYPE_VALUES, expected)
        self.assertNotIn("Выберите", EQUIPMENT_TYPE_VALUES)
        self.assertNotIn("НЕ ЗАПЧАСТЬ", EQUIPMENT_TYPE_VALUES)


if __name__ == "__main__":
    unittest.main()

import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import oem_reference_service as ref
from oemixibot_telegram import price_card

FIELDS="oem TEXT,item_type TEXT,actual_weight_kg REAL,volume_weight_kg REAL,weight_state TEXT,needs_review INTEGER,type_conflict INTEGER,actual_weight_conflict INTEGER,volume_weight_conflict INTEGER,direct_observation_count INTEGER,linked_group_count INTEGER"
class ReferenceLegacyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/"fixture.db"
  with sqlite3.connect(self.path) as c:c.execute("CREATE TABLE oem_reference ("+FIELDS+")")
  self.db_patch=patch.object(ref,"DB_FILE",self.path);self.db_patch.start();self.addCleanup(self.db_patch.stop)
  self.seed_patch=patch.object(ref,"ensure_persistent_seed",return_value={"ok":True});self.seed_patch.start();self.addCleanup(self.seed_patch.stop)
 def record(self,state="APPROX_LEGACY",weight=.81,volume=None,review=0,conflict=0):
  with sqlite3.connect(self.path) as c:
   c.execute("DELETE FROM oem_reference")
   c.execute("INSERT INTO oem_reference VALUES (?,?,?,?,?,?,?,?,?,?,?)",("0746-435","part",weight,volume,state,review,0,conflict,0,1,0))
  return ref.lookup_oem("0746-435")
 def test_approx_nonactual(self):
  v=self.record()
  self.assertIsNone(v["actual_weight_kg"])
  self.assertEqual(v["reference_calculation_weight_kg"],.81)
  self.assertEqual(v["reference_weight_quality"],"approximate_legacy")
  self.assertEqual(ref.reference_client_calculation_weight_kg(v),.9)
 def test_tiny_input_rounds_point_one(self):
  self.assertEqual(ref.reference_client_calculation_weight_kg(self.record(weight=.0045359)),.1)
 def test_actual_only_wins(self):
  v=self.record(state="ACTUAL_ONLY")
  self.assertEqual(v["actual_weight_kg"],.81)
  self.assertIsNone(v["reference_calculation_weight_kg"])
 def test_both_unchanged(self):
  v=self.record(state="BOTH",volume=.45)
  self.assertEqual((v["actual_weight_kg"],v["volume_weight_kg"]),(.81,.45))
  self.assertIsNone(v["reference_calculation_weight_kg"])
 def test_conflict_blocked(self):
  v=self.record(conflict=1)
  self.assertIsNone(v["reference_calculation_weight_kg"])
 def test_review_blocked(self):
  self.assertIsNone(self.record(review=1)["reference_calculation_weight_kg"])
 def test_nonpositive_blocked(self):
  for n in (0,-.3,None):
   self.assertIsNone(self.record(weight=n)["reference_calculation_weight_kg"])
 def test_approx_no_volume(self):
  self.assertIsNone(self.record(volume=.5)["volume_weight_kg"])
 def test_dealer_card_advisory(self):
  s=price_card("Arctic Cat","0746-435","Part",100,1,reference_calculation_weight_kg=.81)
  self.assertIn("Расчётный вес",s)
  self.assertIn("Данные по весу носят справочный характер",s)
  self.assertNotIn("Фактический вес",s)
 def test_dealer_actual_preserved(self):
  s=price_card("Arctic Cat","0746-435","Part",100,1,weight=.81)
  self.assertNotIn("Расчётный вес",s)
if __name__=="__main__":unittest.main()

from pathlib import Path
import unittest
from dcp_reliability_scope_guard import require_web_admin_unchanged

SOURCE=Path(__file__).with_name("web_app.py").read_text()


class DcpScopeTests(unittest.TestCase):
    def test_authorized_changes_preserve_accepted_admin_bytes(self):
        require_web_admin_unchanged(SOURCE)

    def test_health_admin_checkout_signature_and_unrelated_routes_rejected(self):
        for old,new in (("return {\n        \"ok\": True,\n        \"project\"", "return {\n        \"ok\": False,\n        \"project\""),
                        ("import supplier_admin_auth","import fake_auth"),
                        ("def _require_dp_sync_signature(","def _weakened_signature("),
                        ("@app.post(\"/api/v1/handoff\")","@app.post(\"/api/v1/new-handoff\")")):
            with self.subTest(old=old):
                self.assertIn(old,SOURCE)
                with self.assertRaises(AssertionError):require_web_admin_unchanged(SOURCE.replace(old,new))

    def test_dcp_route_cannot_be_repurposed_as_financial_route(self):
        with self.assertRaises(AssertionError):
            require_web_admin_unchanged(SOURCE.replace('@app.post("/internal/dp-sync/result")','@app.post("/api/finance/write")'))

    def test_extra_module_or_route_cannot_hide_in_scope(self):
        with self.assertRaises(AssertionError):
            require_web_admin_unchanged(SOURCE+'\n@app.post("/api/finance/new")\ndef unwanted(): pass\n')


if __name__=="__main__":unittest.main()

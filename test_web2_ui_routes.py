"""Isolated WEB2 registration tests: no production app import required."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web2_ui_routes import attach_web2_ui


class Web2UiRoutesTests(unittest.TestCase):
    def test_explicit_registration_and_static_assets(self):
        app = FastAPI()
        self.assertNotIn("/web2", [route.path for route in app.routes])
        attach_web2_ui(app)
        client = TestClient(app)
        self.assertEqual(client.get("/web2").status_code, 200)
        self.assertIn("EXTREMIZER", client.get("/web2").text)
        self.assertEqual(client.get("/web2-static/app.js").status_code, 200)
        self.assertEqual(client.get("/web2-static/styles.css").status_code, 200)

    def test_registration_is_not_implicit(self):
        app = FastAPI()
        self.assertEqual(TestClient(app).get("/web2").status_code, 404)

    def test_duplicate_registration_rejected(self):
        app = FastAPI()
        attach_web2_ui(app)
        with self.assertRaises(RuntimeError):
            attach_web2_ui(app)

    def test_no_arbitrary_prefix(self):
        with self.assertRaises(ValueError):
            attach_web2_ui(FastAPI(), prefix="/")


if __name__ == "__main__":
    unittest.main()

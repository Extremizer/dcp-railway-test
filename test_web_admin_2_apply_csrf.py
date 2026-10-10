"""CSRF checks are deterministic, local, and independent of production."""
import unittest
from unittest.mock import patch
import supplier_admin_auth as auth

class ApplyCsrfRegression(unittest.TestCase):
    def test_binding_to_session_and_order(self):
        with patch.dict("os.environ", {"EXTREMIZER_WEB_ADMIN_TOKEN": "unit-test-secret"}):
            first = auth.issue_web_admin_session()
            second = auth.issue_web_admin_session()
            token = auth.issue_apply_csrf_token(first, "TEST-1")
            self.assertTrue(auth.verify_apply_csrf_token(first, "TEST-1", token))
            self.assertFalse(auth.verify_apply_csrf_token(first, "TEST-2", token))
            self.assertFalse(auth.verify_apply_csrf_token(second, "TEST-1", token))
            self.assertFalse(auth.verify_apply_csrf_token(first, "TEST-1", "bad"))
            self.assertFalse(auth.verify_apply_csrf_token(first, "TEST-1", ""))

    def test_expiry_rotation_and_malformed_input(self):
        with patch.dict("os.environ", {"EXTREMIZER_WEB_ADMIN_TOKEN": "unit-test-secret"}):
            session = auth.issue_web_admin_session()
            token = auth.issue_apply_csrf_token(session, "TEST-1")
            expired = auth.issue_web_admin_session(now=1)
            self.assertFalse(auth.verify_apply_csrf_token(expired, "TEST-1", token))
            self.assertFalse(auth.verify_web_admin_session(session.rsplit(".", 1)[0] + "." + "я" * 64))
            self.assertFalse(auth.verify_apply_csrf_token(session, "TEST-1", "я" * 64))
            with patch.dict("os.environ", {"EXTREMIZER_WEB_ADMIN_TOKEN": "rotated"}):
                self.assertFalse(auth.verify_apply_csrf_token(session, "TEST-1", token))

if __name__ == "__main__":
    unittest.main()

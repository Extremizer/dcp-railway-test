"""Synthetic isolation regression: NO production API/DB access."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oem_identity_service
from oemixi_unknown_enrollment import validate_selected_manufacturer, TRUSTED_SOURCE


class Gateway:
    def __init__(self, proof):
        self.proof = proof
        self.calls = []
    def get_dealer_price_dict(self, manufacturer, oem):
        self.calls.append((manufacturer, oem))
        if isinstance(self.proof, Exception):
            raise self.proof
        return self.proof


def valid():
    return {"status": "FOUND", "dealer_price_usd": 184.07,
            "current_oem": "3211070", "source": TRUSTED_SOURCE,
            "server_cache_committed": True,
            "msrp_usd": 114.99}


class UnknownManufacturerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dbpatch = patch.object(
            oem_identity_service, "DB", Path(self.temp.name) / "identities.db"
        )
        self.dbpatch.start()

    def tearDown(self):
        self.dbpatch.stop()
        self.temp.cleanup()

    def enroll(self, proof, manufacturer="Polaris", oem="3211070"):
        return validate_selected_manufacturer(oem, manufacturer, Gateway(proof), category_bootstrap=lambda m,o: {"status":"CATEGORY_VERIFIED","manufacturer":m,"oem":o})

    def test_positive_trusted_proof_enrolls(self):
        result = self.enroll(valid())
        self.assertEqual(result["status"], "VERIFIED")
        record = oem_identity_service.lookup("3211070")
        self.assertEqual(record["manufacturer"], "Polaris")
        self.assertEqual(record["source"], "dealer_cart_exact_line_confirmed")

    def test_absent_gateway_no_identity(self):
        self.assertEqual(
            validate_selected_manufacturer("3211070", "Polaris", None)["status"],
            "VALIDATION_UNAVAILABLE",
        )
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_manufacturer_not_autoselected(self):
        self.assertEqual(self.enroll(valid(), "")["status"], "INVALID_MANUFACTURER")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_invalid_oem_no_write(self):
        self.assertEqual(self.enroll(valid(), oem="../bad")["status"], "INVALID_OEM")

    def test_msrp_only_no_write(self):
        self.assertEqual(
            self.enroll({"status": "FOUND", "msrp_usd": 114.99,
                         "source": "public_msrp"})["status"],
            "UNTRUSTED_PRICE_SOURCE",
        )
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_untrusted_cache_source_no_write(self):
        proof = {**valid(), "source": "msrp"}
        self.assertEqual(self.enroll(proof)["status"], "UNTRUSTED_PRICE_SOURCE")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_uncommitted_proof_no_write(self):
        proof = {**valid(), "server_cache_committed": False}
        self.assertEqual(self.enroll(proof)["status"], "UNCOMMITTED_DEALER_PRICE")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_invalid_current_oem_no_write(self):
        proof = {**valid(), "current_oem": "../bad"}
        self.assertEqual(self.enroll(proof)["status"], "INVALID_CURRENT_OEM")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_price_failure_no_write(self):
        self.assertEqual(
            self.enroll({"status": "ADMISSION_CLOSED"})["status"],
            "VALIDATION_FAILED",
        )
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_network_failure_no_write(self):
        self.assertEqual(self.enroll(TimeoutError())["status"], "VALIDATION_UNAVAILABLE")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_alias_requires_independent_authority(self):
        proof = {**valid(), "current_oem": "9999999"}
        self.assertEqual(self.enroll(proof)["status"], "ALIAS_AUTHORITY_REQUIRED")
        self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_existing_conflict(self):
        oem_identity_service.remember("3211070", "Honda", source="trusted_existing")
        self.assertEqual(self.enroll(valid())["status"], "IDENTITY_CONFLICT")
        self.assertEqual(oem_identity_service.lookup("3211070")["manufacturer"], "Honda")

    def test_nan_and_zero_denied(self):
        for value in (0, -1, float("nan"), None, 114.99):
            # MSRP number itself is not proof; 114.99 with trusted exact line
            # would be a valid dealer quote, but no such proof is available.
            if value == 114.99:
                continue
            proof = {**valid(), "dealer_price_usd": value}
            self.assertEqual(self.enroll(proof)["status"], "INVALID_DEALER_PRICE")
            self.assertIsNone(oem_identity_service.lookup("3211070"))

    def test_reenrollment_same_manufacturer(self):
        self.assertEqual(self.enroll(valid())["status"], "VERIFIED")
        self.assertEqual(self.enroll(valid())["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()

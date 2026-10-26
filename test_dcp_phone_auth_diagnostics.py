"""Offline-only M4.7 diagnostics tests. No network, bots, or live DCP."""
import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode
from dcp_phone_recovery_core import RecoveryDenied, RecoveryGrants, validate_telegram_init_data

TOKEN = "12345678:" + "A" * 35
OWNER = 314159
NOW = 1800000000

def signed(*, auth_date=NOW, owner=OWNER):
    values = {"auth_date": str(auth_date), "user": json.dumps({"id": owner}, separators=(",", ":"))}
    check = "\n".join(f"{k}={values[k]}" for k in sorted(values))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)

class AuthDiagnosticsTests(unittest.TestCase):
    def check_denial(self, code, operation):
        with self.assertRaises(RecoveryDenied) as ctx:
            operation()
        self.assertEqual(ctx.exception.diagnostic_code, code)
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertNotIn(str(OWNER), str(ctx.exception))
        self.assertNotIn("user=", str(ctx.exception))

    def test_successful_exchange_unchanged(self):
        grants = RecoveryGrants(TOKEN, OWNER)
        ticket = grants.invite(now=NOW)
        bearer = grants.exchange(ticket, signed(), now=NOW)
        self.assertTrue(grants.authorized(bearer, now=NOW))
        self.assertEqual(validate_telegram_init_data(signed(), TOKEN, OWNER, now=NOW)["user_id"], OWNER)

    def test_expired_initdata(self):
        self.check_denial("INITDATA_EXPIRED", lambda: validate_telegram_init_data(signed(auth_date=NOW-301), TOKEN, OWNER, now=NOW))

    def test_invalid_signature(self):
        bad = signed().replace("auth_date=", "auth_date=1")
        self.check_denial("INITDATA_SIGNATURE", lambda: validate_telegram_init_data(bad, TOKEN, OWNER, now=NOW))

    def test_wrong_owner(self):
        self.check_denial("INITDATA_OWNER", lambda: validate_telegram_init_data(signed(owner=OWNER+1), TOKEN, OWNER, now=NOW))

    def test_future_initdata(self):
        self.check_denial("INITDATA_FUTURE", lambda: validate_telegram_init_data(signed(auth_date=NOW+31), TOKEN, OWNER, now=NOW))

    def test_initdata_format(self):
        self.check_denial("INITDATA_FORMAT", lambda: validate_telegram_init_data("hash=bad", TOKEN, OWNER, now=NOW))

    def test_invalid_invitation(self):
        self.check_denial("INVITATION_FORMAT", lambda: RecoveryGrants(TOKEN, OWNER).exchange("short", signed(), now=NOW))

    def test_expired_invitation(self):
        grants = RecoveryGrants(TOKEN, OWNER)
        ticket = grants.invite(now=NOW-601)
        self.check_denial("INVITATION_EXPIRED", lambda: grants.exchange(ticket, signed(), now=NOW))

    def test_replayed_invitation(self):
        grants = RecoveryGrants(TOKEN, OWNER)
        ticket = grants.invite(now=NOW)
        grants.exchange(ticket, signed(), now=NOW)
        self.check_denial("INVITATION_MISSING_OR_USED", lambda: grants.exchange(ticket, signed(), now=NOW))

    def test_missing_invitation_indistinguishable(self):
        grants = RecoveryGrants(TOKEN, OWNER)
        self.check_denial("INVITATION_MISSING_OR_USED", lambda: grants.exchange("x"*43, signed(), now=NOW))

    def test_no_secrets_in_diagnostics(self):
        data = signed(auth_date=NOW-301)
        with self.assertRaises(RecoveryDenied) as ctx:
            validate_telegram_init_data(data, TOKEN, OWNER, now=NOW)
        for secret in (TOKEN, data, str(OWNER)):
            self.assertNotIn(secret, str(ctx.exception))
        self.assertEqual(ctx.exception.diagnostic_code, "INITDATA_EXPIRED")

if __name__ == "__main__":
    unittest.main()

"""Offline security and state contract tests. No DCP, Telegram, Railway or browser I/O."""
import hashlib
import hmac
import json
import urllib.parse
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from dcp_phone_recovery_core import (
    validate_telegram_init_data, RecoveryDenied, RecoveryGrants,
    dcp_status_from_observation, RemoteBrowser, BrowserUnavailable,
)
import dcp_phone_recovery_app as appmod


def signed_data(token="fake-token",user_id=12345,auth_date=1000):
    fields={"auth_date":str(auth_date),"user":json.dumps({"id":user_id},separators=(",",":"))}
    check="\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
    sig=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields,"hash":sig})


class RecoveryAuthTests(unittest.TestCase):
    def test_verified_telegram_and_wrong_user_rejected(self):
        x=signed_data()
        self.assertEqual(validate_telegram_init_data(x,"fake-token",12345,now=1100)["user_id"],12345)
        for user in (1,9999):
            with self.assertRaises(RecoveryDenied):
                validate_telegram_init_data(x,"fake-token",user,now=1100)

    def test_replay_forgery_and_stale_disallowed(self):
        x=signed_data()
        for data,now in ((x+"x",1100),(x,2000),("bad",1100),
                         (x+"&user=%7B%7D",1100)):
            with self.subTest(data=data[:12],now=now),self.assertRaises(RecoveryDenied):
                validate_telegram_init_data(data,"fake-token",12345,now=now)

    def test_single_use_invitation_and_temporary_session(self):
        x=RecoveryGrants("fake-token",12345,ttl=100)
        ticket=x.invite(now=1100)
        token=x.exchange(ticket,signed_data(auth_date=1100),now=1101)
        self.assertTrue(x.authorized(token,now=1110))
        with self.assertRaises(RecoveryDenied):
            x.exchange(ticket,signed_data(auth_date=1100),now=1102)
        self.assertFalse(x.authorized(token,now=1101+721))
        x.revoke(token)
        self.assertFalse(x.authorized(token,now=1110))

    def test_claims_are_fail_closed(self):
        for obj,expected in (({"host":"www.dealercostparts.com","sign_out":True},"READY"),
                             ({"host":"www.dealercostparts.com","sign_out":True,"challenged":True},"CLOUDFLARE"),
                             ({"host":"www.dealercostparts.com","sign_out":False},"AUTH_REQUIRED"),
                             ({"host":"example.com","sign_out":True},"BROWSER_DOWN")):
            self.assertEqual(dcp_status_from_observation(obj),expected)

    def test_cdp_never_exposes_arbitrary_endpoint(self):
        with self.assertRaises(BrowserUnavailable):
            RemoteBrowser("http://127.0.0.1:1234/json")
        with self.assertRaises(RecoveryDenied):
            RemoteBrowser().tap(float("nan"),0)
        with self.assertRaises(RecoveryDenied):
            RemoteBrowser().type_text("secret\nprint('bad')")


class RecoveryAppTests(unittest.TestCase):
    def setUp(self):
        self.was_enabled=appmod.app.state.enabled
        self.was_grants=appmod.app.state.grants
        self.was_browser=appmod.app.state.browser
        appmod.app.state.enabled=False
        appmod.app.state.grants=None
        appmod.app.state.browser=None
        self.client=TestClient(appmod.app)

    def tearDown(self):
        appmod.app.state.enabled=self.was_enabled
        appmod.app.state.grants=self.was_grants
        appmod.app.state.browser=self.was_browser

    def test_disabled_by_default(self):
        for path in ("/recovery","/recovery/status","/recovery/frame"):
            self.assertEqual(self.client.get(path).status_code,503)

    def test_auth_required_for_all_browser_io(self):
        appmod.app.state.enabled=True
        appmod.app.state.grants=RecoveryGrants("fake-token",12345)
        appmod.app.state.browser=object()
        for method,path,body in (("GET","/recovery/status",None),
                                 ("GET","/recovery/frame",None),
                                 ("POST","/recovery/tap",{"x":40,"y":60}),
                                 ("POST","/recovery/text",{"text":"hello"}),
                                 ("POST","/recovery/scroll",{"dy":100}),
                                 ("POST","/recovery/close",None)):
            response=self.client.request(method,path,json=body)
            self.assertEqual(response.status_code,401)

    def test_ticket_exchange_requires_telegram_init_data(self):
        appmod.app.state.enabled=True
        appmod.app.state.grants=RecoveryGrants("fake-token",12345)
        appmod.app.state.browser=object()
        t=appmod.app.state.grants.invite(now=1000)
        with patch("time.time",return_value=1100):
            response=self.client.post("/recovery/exchange",json={"ticket":t,"init_data":signed_data(auth_date=1100)})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["expires_in_seconds"],720)
        self.assertEqual(self.client.post("/recovery/exchange",json={"ticket":t,"init_data":signed_data(auth_date=1100)}).status_code,401)

    def test_headers_no_cache_or_frame(self):
        response=self.client.get("/health")
        self.assertEqual(response.headers["cache-control"],"no-store")
        self.assertEqual(response.headers["x-frame-options"],"DENY")
        self.assertEqual(response.headers["referrer-policy"],"no-referrer")


if __name__=="__main__":
    unittest.main()

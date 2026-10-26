"""M4.7 Telegram WebApp fragment conflict: isolated offline contract tests."""
import hashlib
import hmac
import json
import unittest
from urllib.parse import parse_qs, urlsplit, urlencode
from unittest.mock import patch
from fastapi import HTTPException
import dcp_phone_staging_app as stage
from dcp_phone_recovery_core import RecoveryGrants

BOT="12345678:"+"A"*35
OWNER=314159
NOW=1800000000

def signed():
    fields={"auth_date":str(NOW),"user":json.dumps({"id":OWNER},separators=(",",":"))}
    key=hmac.new(b"WebAppData",BOT.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

class WebAppReferenceTests(unittest.TestCase):
    def setUp(self):
        self.previous_grants=stage.app.state.grants
        self.previous_refs=stage.app.state.invite_refs
        stage.app.state.grants=RecoveryGrants(BOT,OWNER)
        stage.app.state.invite_refs={}
    def tearDown(self):
        stage.app.state.grants=self.previous_grants
        stage.app.state.invite_refs=self.previous_refs
    def reference(self):
        ticket=stage.app.state.grants.invite(now=NOW)
        url=stage._staging_webapp_reference("https://example.com/recovery#"+ticket,stage.app.state.invite_refs)
        return ticket,url,parse_qs(urlsplit(url).query)["ref"][0]
    def test_ticket_not_in_url_fragment_or_query(self):
        ticket,url,ref=self.reference()
        self.assertEqual(urlsplit(url).fragment,"")
        self.assertEqual(urlsplit(url).path,"/recovery")
        self.assertNotIn(ticket,url)
        self.assertEqual(len(ref),64)
        self.assertEqual(stage.app.state.invite_refs[ref],ticket)
    def test_success_and_replay_fail_closed(self):
        _,_,ref=self.reference()
        with patch("dcp_phone_recovery_core.time.time",return_value=NOW):
            result=stage.exchange(stage.Exchange(ticket_ref=ref,init_data=signed()))
            self.assertTrue(result["bearer"])
            with self.assertRaises(HTTPException) as denial:
                stage.exchange(stage.Exchange(ticket_ref=ref,init_data=signed()))
            self.assertEqual(denial.exception.status_code,401)
            self.assertEqual(denial.exception.detail,"unauthorized")
    def test_unknown_reference_denied(self):
        with self.assertRaises(HTTPException) as denial:
            stage.exchange(stage.Exchange(ticket_ref="0"*64,init_data=signed()))
        self.assertEqual(denial.exception.status_code,401)
    def test_telegram_fragment_is_not_a_ticket(self):
        with self.assertRaises(HTTPException) as denial:
            stage.exchange(stage.Exchange(ticket="tgWebAppVersion=9.2&tgWebAppData=payload",init_data=signed()))
        self.assertEqual(denial.exception.status_code,401)
    def test_does_not_accept_ticket_and_ref_together(self):
        ticket,_,ref=self.reference()
        with self.assertRaises(HTTPException) as denial:
            stage.exchange(stage.Exchange(ticket=ticket,ticket_ref=ref,init_data=signed()))
        self.assertEqual(denial.exception.status_code,401)
    def test_tampered_identity_denied_without_consuming_ticket(self):
        _,_,ref=self.reference()
        with patch("dcp_phone_recovery_core.time.time",return_value=NOW):
            with self.assertRaises(HTTPException) as denial:
                stage.exchange(stage.Exchange(ticket_ref=ref,init_data="hash=bad"))
            self.assertEqual(denial.exception.status_code,401)
            self.assertTrue(stage.exchange(stage.Exchange(ticket_ref=ref,init_data=signed()))["bearer"])

if __name__=="__main__":
    unittest.main()

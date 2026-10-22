"""Stage 10.1 isolated ownership adversarial tests; no external network."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import patch
import secrets
import hmac
import hashlib
import dcp_stage10_1_mock_ownership_api as api
import dcp_stage10_1_secure_control as rc
import dcp_stage10_ownership_epoch as epoch
from fastapi.testclient import TestClient
from dcp_stage10_1_mock_http import create_mock_app

PRODUCER=b"fixture-producer-key-stage10.1"
OPERATOR=b"fixture-operator-key-stage10.1"

def race(args):
    db,owner,nonce=args
    body=rc.canonical({"owner":owner,"request_nonce":nonce})
    now=int(time.time())
    try:
        return api.dispatch(db,method="POST",path="/mock/ownership/acquire",body=body,
            timestamp=now,signature=rc.signature(hmac.new(PRODUCER,b"stage10.1-host-key"+owner.encode(),hashlib.sha256).digest(),"POST","/mock/ownership/acquire",now,body),
            producer_key=PRODUCER,operator_key=OPERATOR)
    except rc.Rejected:
        return None

class SecureOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=str(Path(self.temp.name)/"db.sqlite")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE dp_live_requests(status TEXT)")
        for owner in ("old-host","new-host"):
            api.grant_mock_owner(self.db,owner)

    def acquire(self,owner="old-host",nonce=None):
        return race((self.db,owner,nonce or secrets.token_hex(32)))

    def attestation(self,proof,digest,now=None):
        now=now or int(time.time())
        record={"authority":"release_control","scope":"producer.revoke",
                "owner":proof["owner"],"ceased_host":proof["owner"],
                "expected_epoch":proof["epoch"],"evidence_digest":digest,
                "nonce":secrets.token_hex(32),"issued_at":now-1,"expires_at":now+60}
        body=rc.canonical(record)
        return dict(path="/mock/ownership/revoke",body=body,ts=now,
                    sig=rc.signature(OPERATOR,"POST","/mock/ownership/revoke",now,body),
                    operator_key=OPERATOR,now=now)

    def test_authenticated_mock_http_acquire_status_and_revoke(self):
        client=TestClient(create_mock_app(self.db,producer_key=PRODUCER,operator_key=OPERATOR))
        owner="old-host"
        data=rc.canonical({"owner":owner,"request_nonce":secrets.token_hex(32)})
        ts=int(time.time())
        key=hmac.new(PRODUCER,b"stage10.1-host-key"+owner.encode(),hashlib.sha256).digest()
        signed={"x-owner-ts":str(ts),"x-owner-sig":rc.signature(
            key,"POST","/mock/ownership/acquire",ts,data)}
        self.assertEqual(client.post("/mock/ownership/acquire",content=data).status_code,403)
        result=client.post("/mock/ownership/acquire",content=data,headers=signed)
        self.assertEqual(result.status_code,200,result.text)
        proof=result.json()
        self.assertEqual(client.post("/mock/ownership/acquire",content=data,headers=signed).status_code,403)
        h={"x-owner-ts":str(ts),"x-owner-sig":rc.signature(OPERATOR,
            "GET","/mock/ownership/status",ts,b"")}
        status=client.get("/mock/ownership/status",headers=h)
        self.assertEqual(status.status_code,200,status.text)
        self.assertEqual(status.json()["state"],"ACTIVE")
        self.assertNotIn("nonce",status.json())
        digest="a"*64
        rc.register_verified_cessation(self.db,owner=owner,epoch_value=proof["epoch"],
            evidence_digest=digest,verified_at=ts)
        att=self.attestation(proof,digest,now=ts)
        reply=client.post("/mock/ownership/revoke",content=att["body"],
            headers={"x-owner-ts":str(ts),"x-owner-sig":att["sig"]})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(client.post("/mock/ownership/revoke",content=att["body"],
            headers={"x-owner-ts":str(ts),"x-owner-sig":att["sig"]}).status_code,403)

    def test_independent_hosts_single_owner_and_no_replay(self):
        with ProcessPoolExecutor(max_workers=2) as pool:
            answers=list(pool.map(race,[(self.db,"old-host",secrets.token_hex(32)),
                 (self.db,"new-host",secrets.token_hex(32))]))
        self.assertEqual(sum(x is not None for x in answers),1)
        winner=next(x for x in answers if x)
        with sqlite3.connect(self.db) as c:
            self.assertTrue(epoch.validate(c,winner))
        self.assertIsNone(self.acquire("new-host" if winner["owner"]=="old-host" else "old-host"))

    def test_wrong_signatures_and_untrusted_owner_rejected(self):
        now=int(time.time());body=rc.canonical({"owner":"old-host","request_nonce":secrets.token_hex(32)})
        with self.assertRaises(rc.Rejected):
            api.dispatch(self.db,method="POST",path="/mock/ownership/acquire",body=body,timestamp=now,
                signature="0"*64,producer_key=PRODUCER,operator_key=OPERATOR)
        self.assertIsNone(self.acquire("intruder"))

    def test_foreign_host_signature_cannot_acquire_another_identity(self):
        ts=int(time.time())
        body=rc.canonical({"owner":"new-host","request_nonce":secrets.token_hex(32)})
        old_host_key=hmac.new(PRODUCER,b"stage10.1-host-key"+b"old-host",hashlib.sha256).digest()
        forged=rc.signature(old_host_key,"POST","/mock/ownership/acquire",ts,body)
        with self.assertRaises(rc.Rejected):
            api.dispatch(self.db,method="POST",path="/mock/ownership/acquire",
                body=body,timestamp=ts,signature=forged,
                producer_key=PRODUCER,operator_key=OPERATOR)
        with sqlite3.connect(self.db) as conn:
            self.assertFalse(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dp_producer_ownership'").fetchone())

    def test_forged_cessation_and_expired_proof_fail_closed(self):
        old=self.acquire()
        req=self.attestation(old,"b"*64)
        with self.assertRaises(rc.Rejected):
            rc.control(self.db,**req)
        with sqlite3.connect(self.db) as c:self.assertTrue(epoch.validate(c,old))
        rc.register_verified_cessation(self.db,owner="old-host",epoch_value=old["epoch"],
                                       evidence_digest="b"*64,verified_at=int(time.time())-200)
        with self.assertRaises(rc.Rejected):
            rc.control(self.db,**req)
        self.assertIsNone(self.acquire("new-host"))

    def test_verified_revocation_once_then_new_epoch_and_stale_owner(self):
        old=self.acquire()
        digest="c"*64
        rc.register_verified_cessation(self.db,owner="old-host",epoch_value=old["epoch"],
                                       evidence_digest=digest,verified_at=int(time.time()))
        req=self.attestation(old,digest)
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            with self.assertRaises(epoch.OwnershipDenied):
                epoch.revoke(self.db,expected_epoch=old["epoch"],cessation_confirmed=True,evidence_ref="x"*64)
            self.assertTrue(rc.control(self.db,**req)["revoked"])
            with self.assertRaises(rc.Rejected):rc.control(self.db,**req)
        new=self.acquire("new-host")
        self.assertGreater(new["epoch"],old["epoch"])
        with sqlite3.connect(self.db) as c:
            self.assertFalse(epoch.validate(c,old));self.assertTrue(epoch.validate(c,new))

    def test_partition_and_inflight_block_revocation(self):
        old=self.acquire()
        with sqlite3.connect(self.db) as c:c.execute("INSERT INTO dp_live_requests VALUES('orphaned')")
        digest="d"*64
        rc.register_verified_cessation(self.db,owner="old-host",epoch_value=old["epoch"],
                                       evidence_digest=digest,verified_at=int(time.time()))
        with self.assertRaises(rc.Rejected):
            rc.control(self.db,**self.attestation(old,digest))
        self.assertIsNone(self.acquire("new-host"))

if __name__=="__main__":unittest.main()

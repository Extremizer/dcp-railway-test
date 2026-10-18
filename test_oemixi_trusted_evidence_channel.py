"""Cross-host producer evidence authenticity simulation, no live network."""
import sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from oemixi_trusted_evidence_channel import (
    initialize_evidence_store,create_evidence_router,trusted_evidence_reader,
    PATH,encode_evidence,headers,validate_observation
)
SECRET="producer-secret-"+"Q"*40
def proof():
    return dict(verified=True,manufacturer="Polaris",requested_oem="3211070",
                current_oem="3211070",catalog="Parts",name="DRIVE BELT",
                msrp_usd=114.99,source_kind="live_public_form_metadata",
                source_ref="https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070",
                verified_at=datetime.now(timezone.utc).isoformat())
class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db=str(Path(self.tmp.name)/"evidence.db")
        initialize_evidence_store(self.db)
        app=FastAPI()
        app.include_router(create_evidence_router(db=self.db,producer_secret=SECRET))
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
    def post(self,body=None,head=None):
        b=body or encode_evidence(proof())
        return self.client.post(PATH,content=b,headers=head or headers(SECRET,b))
    def test_authenticated_producer_evidence_read(self):
        self.assertEqual(self.post().status_code,200)
        reader=trusted_evidence_reader(self.db)
        self.assertEqual(reader("Polaris","3211070")["catalog"],"Parts")
        self.assertIsNone(reader("Honda","3211070"))
    def test_replay_after_restart_denied(self):
        b=encode_evidence(proof());h=headers(SECRET,b)
        self.assertEqual(self.post(b,h).status_code,200)
        restarted=FastAPI()
        restarted.include_router(create_evidence_router(db=self.db,producer_secret=SECRET))
        with TestClient(restarted) as client:
            self.assertEqual(client.post(PATH,content=b,headers=h).status_code,409)
    def test_tampered_payload_denied(self):
        b=encode_evidence(proof());h=headers(SECRET,b)
        self.assertEqual(self.post(encode_evidence({**proof(),"msrp_usd":999.0}),h).status_code,403)
        self.assertIsNone(trusted_evidence_reader(self.db)("Polaris","3211070"))
    def test_wrong_source_denied(self):
        b=encode_evidence({**proof(),"source_ref":"https://evil.example/oemparts/partsearch/polaris?partsearch=3211070"})
        self.assertEqual(self.post(b).status_code,403)
        self.assertIsNone(trusted_evidence_reader(self.db)("Polaris","3211070"))
    def test_wrong_current_oem_denied(self):
        b=encode_evidence({**proof(),"current_oem":"9999999"})
        self.assertEqual(self.post(b).status_code,403)
    def test_unverified_flag_denied(self):
        b=encode_evidence({**proof(),"verified":False})
        self.assertEqual(self.post(b).status_code,403)
if __name__=="__main__":unittest.main()

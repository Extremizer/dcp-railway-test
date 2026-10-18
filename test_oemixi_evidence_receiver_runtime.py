"""Real urllib producer-client to isolated local ASGI receiver over loopback HTTP.

HTTPS remains mandatory in production. An opener bridges HTTPS request to localhost
only within tests; actual network socket request/response is performed.
"""
import json,sqlite3,tempfile,threading,unittest,urllib.request,urllib.error
from datetime import datetime,timezone,timedelta
from pathlib import Path
from wsgiref.simple_server import make_server
from fastapi.testclient import TestClient
from oemixi_evidence_receiver_runtime import create_app
from oemixi_trusted_evidence_channel import (PATH,headers,encode_evidence,trusted_evidence_reader)
from oemixi_trusted_evidence_producer_client import publish_trusted_observation

SECRET="synthetic-"+"s"*40
def observation(**updates):
    data={"verified":True,"manufacturer":"Polaris","requested_oem":"3211070",
          "current_oem":"3211070","catalog":"Parts","name":"DRIVE BELT",
          "msrp_usd":114.99,"source_kind":"live_public_form_metadata",
          "source_ref":"https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070",
          "verified_at":datetime.now(timezone.utc).isoformat()}
    data.update(updates);return data

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db=str(Path(self.temp.name)/"evidence-only.db")
        self.app=create_app(db_path=self.db,producer_secret=SECRET,initialize=True)
        self.client=TestClient(self.app)
    def tearDown(self):
        self.client.close();self.temp.cleanup()
    def submit(self,obs=None,body=None,head=None):
        body=body if body is not None else encode_evidence(obs or observation())
        return self.client.post(PATH,content=body,headers=head or headers(SECRET,body))
    def count(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM oemixi_catalog_evidence").fetchone()[0]
    def test_health_and_exact_only(self):
        self.assertEqual(self.client.get("/health").status_code,200)
        self.assertEqual(self.client.get(PATH).status_code,405)
        self.assertEqual(self.client.get("/docs").status_code,404)
    def test_producer_client_request_response_http(self):
        # Network I/O over real loopback TCP. Production client URL stays HTTPS.
        # Only the injected test opener redirects requests to local HTTP.
        import socket,time,uvicorn
        sock=socket.socket()
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
        sock.close()
        server=uvicorn.Server(uvicorn.Config(self.app,host="127.0.0.1",port=port,
                                              log_level="critical",access_log=False))
        thread=threading.Thread(target=server.run,daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:break
                time.sleep(.02)
            self.assertTrue(server.started)
            def opener(req,timeout=6):
                local=urllib.request.Request(
                    f"http://127.0.0.1:{port}{PATH}",data=req.data,
                    headers=dict(req.header_items()),method="POST")
                return urllib.request.urlopen(local,timeout=timeout)
            result=publish_trusted_observation("https://synthetic-receiver.test",SECRET,
                                                observation(),opener=opener)
            self.assertEqual(result["status"],"EVIDENCE_ACCEPTED")
            self.assertEqual(self.count(),1)
            self.assertIsNotNone(trusted_evidence_reader(self.db)("Polaris","3211070"))
        finally:
            server.should_exit=True
            thread.join(timeout=5)
    def test_tampered_body_bad_signature_and_replay(self):
        good=encode_evidence(observation());h=headers(SECRET,good)
        changed=encode_evidence(observation(msrp_usd=400))
        self.assertEqual(self.submit(body=changed,head=h).status_code,403)
        self.assertEqual(self.count(),0)
        bad={**h,"X-Evidence-Signature":"0"*64}
        self.assertEqual(self.submit(body=good,head=bad).status_code,403)
        self.assertEqual(self.submit(body=good,head=h).status_code,200)
        self.assertEqual(self.submit(body=good,head=h).status_code,409)
    def test_stale_wrong_oem_manufacturer_source(self):
        stale=(datetime.now(timezone.utc)-timedelta(seconds=121)).isoformat()
        cases=[observation(verified_at=stale),observation(current_oem="9999999"),
               observation(manufacturer="Honda"),
               observation(source_ref="https://evil.test/oemparts/partsearch/polaris?partsearch=3211070")]
        for obs in cases:
            with self.subTest(obs=obs):
                self.assertEqual(self.submit(obs=obs).status_code,403)
        self.assertEqual(self.count(),0)
    def test_replay_survives_reconstruction(self):
        b=encode_evidence(observation());h=headers(SECRET,b)
        self.assertEqual(self.submit(body=b,head=h).status_code,200)
        app=create_app(db_path=self.db,producer_secret=SECRET,initialize=False)
        with TestClient(app) as client:
            self.assertEqual(client.post(PATH,content=b,headers=h).status_code,409)
if __name__=="__main__":unittest.main()

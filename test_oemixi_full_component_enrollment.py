"""End-to-end synthetic components, without DCP, Telegram network or production DB."""
import sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import oem_identity_service
import dcp_category_authority
from oemixi_trusted_evidence_channel import (
    initialize_evidence_store,create_evidence_router,trusted_evidence_reader,
    PATH as EVIDENCE_PATH,encode_evidence,headers as evidence_headers)
from oemixi_enrollment_transport import (
    initialize_nonce_store,create_router,canonical_payload,make_headers,PATH)
from oemixi_unknown_enrollment import validate_selected_manufacturer,TRUSTED_SOURCE

PRODUCER="producer-key-"+"p"*40
ENROLL="enrollment-key-"+"e"*40
def observed():
    return dict(verified=True,manufacturer="Polaris",requested_oem="3211070",
        current_oem="3211070",catalog="Parts",name="DRIVE BELT",msrp_usd=114.99,
        source_kind="live_public_form_metadata",
        source_ref="https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070",
        verified_at=datetime.now(timezone.utc).isoformat())
class Gateway:
    def __init__(self,db,success=True):
        self.db=db;self.success=success;self.calls=0
    def get_dealer_price_dict(self,m,o):
        self.calls+=1
        with sqlite3.connect(self.db) as c:
            authority=dcp_category_authority.resolve(c,m,o)
        if not authority or not self.success:return {"status":"ADMISSION_CLOSED"}
        return {"status":"FOUND","source":TRUSTED_SOURCE,"dealer_price_usd":88.77,
            "current_oem":o,"server_cache_committed":True}
class CompleteComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root=Path(self.temp.name)
        self.db=str(root/"authority.db")
        self.ev=str(root/"producer_evidence.db")
        self.nonce=str(root/"enrollment_nonce.db")
        self.ident=root/"identity.db"
        with sqlite3.connect(self.db) as c:
            c.executescript("""CREATE TABLE oem_catalog_cache(manufacturer TEXT,current_oem TEXT,
                name TEXT,catalog TEXT,msrp_usd REAL,msrp_verified INTEGER,
                previous_oems_json TEXT,source_kind TEXT,source_ref TEXT,
                first_seen_at TEXT,last_verified_at TEXT,PRIMARY KEY(manufacturer,current_oem));
                CREATE TABLE oem_catalog_aliases(manufacturer TEXT,alias_oem TEXT,
                current_oem TEXT,alias_kind TEXT,PRIMARY KEY(manufacturer,alias_oem));""")
        initialize_evidence_store(self.ev);initialize_nonce_store(self.nonce)
        app=FastAPI()
        app.include_router(create_evidence_router(db=self.ev,producer_secret=PRODUCER))
        app.include_router(create_router(db_file=self.db,secret=ENROLL,
            verifier=trusted_evidence_reader(self.ev),nonce_db=self.nonce))
        self.http=TestClient(app)
        self.p=patch.object(oem_identity_service,"DB",self.ident)
        self.p.start()
    def tearDown(self):
        self.p.stop();self.http.close();self.temp.cleanup()
    def evidence(self,proof=None):
        b=encode_evidence(proof or observed())
        return self.http.post(EVIDENCE_PATH,content=b,headers=evidence_headers(PRODUCER,b))
    def bootstrap(self,m,o):
        b=canonical_payload(m,o)
        response=self.http.post(PATH,content=b,headers=make_headers(ENROLL,b))
        if response.status_code!=200:return {"status":"CATEGORY_UNVERIFIED"}
        return response.json()
    def test_complete_empty_to_identity(self):
        with sqlite3.connect(self.db) as c:
            self.assertIsNone(dcp_category_authority.resolve(c,"Polaris","3211070"))
        self.assertEqual(self.evidence().status_code,200)
        gw=Gateway(self.db)
        result=validate_selected_manufacturer("3211070","Polaris",gw,
                                              category_bootstrap=self.bootstrap)
        self.assertEqual(result["status"],"VERIFIED")
        self.assertEqual(gw.calls,1)
        self.assertEqual(oem_identity_service.lookup("3211070")["manufacturer"],"Polaris")
    def test_real_callback_to_identity_and_card(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock,Mock
        import oemixibot_app as app
        self.assertEqual(self.evidence().status_code,200)
        gateway=Gateway(self.db)
        ix=app.manufacturer_choices().index("Polaris")
        dealer={"id":"d1","price_coefficient":1.0}
        context=SimpleNamespace(user_data={"unknown_manufacturer_pending":{
            "oem":"3211070","nonce":"01234567","dealer_id":"d1","user_id":51}},
            application=SimpleNamespace(bot_data={
                app._SHARED_PRICE_ADAPTER_KEY:gateway,
                "oemixi_verified_category_bootstrap":self.bootstrap}))
        q=SimpleNamespace(data=f"mf:01234567:{ix}",answer=AsyncMock(),
                          edit_message_text=AsyncMock())
        update=SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=51))
        offer={"status":"FOUND","manufacturer":"Polaris","oem":"3211070",
               "name":"DRIVE BELT","dl_usd":88.77,"item_type":"Parts",
               "actual_weight_kg":None,"volume_weight_kg":None}
        with patch.object(app,"_dealer",return_value=dealer):
            with patch.object(app,"_resolve_runtime_offer",new_callable=AsyncMock,return_value=offer) as resolver:
                with patch.object(app,"price_card",return_value="TEST CARD"):
                    asyncio.run(app.callback(update,context))
        self.assertEqual(gateway.calls,1)
        resolver.assert_awaited_once_with("3211070",context)
        self.assertEqual(context.user_data["offer:3211070"],offer)
        self.assertEqual(oem_identity_service.lookup("3211070")["manufacturer"],"Polaris")
        q.edit_message_text.assert_awaited_once()

    def test_no_evidence_zero_gateway_calls(self):
        gw=Gateway(self.db)
        result=validate_selected_manufacturer("3211070","Polaris",gw,category_bootstrap=self.bootstrap)
        self.assertNotEqual(result["status"],"VERIFIED")
        self.assertEqual(gw.calls,0)
        self.assertIsNone(oem_identity_service.lookup("3211070"))
    def test_wrong_manufacturer_zero_identity(self):
        self.assertEqual(self.evidence().status_code,200)
        gw=Gateway(self.db)
        result=validate_selected_manufacturer("3211070","Honda",gw,category_bootstrap=self.bootstrap)
        self.assertNotEqual(result["status"],"VERIFIED")
        self.assertEqual(gw.calls,0)
        self.assertIsNone(oem_identity_service.lookup("3211070"))
    def test_stage_b_failure_leaves_only_category(self):
        self.assertEqual(self.evidence().status_code,200)
        gw=Gateway(self.db,success=False)
        result=validate_selected_manufacturer("3211070","Polaris",gw,category_bootstrap=self.bootstrap)
        self.assertEqual(result["status"],"VALIDATION_FAILED")
        self.assertIsNone(oem_identity_service.lookup("3211070"))
        with sqlite3.connect(self.db) as c:
            self.assertIsNotNone(dcp_category_authority.resolve(c,"Polaris","3211070"))
if __name__=="__main__":unittest.main()

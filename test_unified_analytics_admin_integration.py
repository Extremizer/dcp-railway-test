"""Existing signed WEB ADMIN session -> isolated unified reporting RBAC/PII."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.testclient import TestClient
import pricing_analytics
import supplier_admin_auth as admin
import unified_analytics_core as core
from unified_analytics_admin_api import create_admin_router

class ExistingAdminRBAC(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=str(Path(self.tmp.name)/"canonical.sqlite")
        pricing_analytics.init_analytics(self.db)
        core.initialize(self.db)
        self.env=patch.dict(os.environ,{"EXTREMIZER_WEB_ADMIN_TOKEN":"synthetic-admin-token-very-long","EXTREMIZER_UNIFIED_ANALYTICS_ENABLE":"1"})
        self.env.start()
        self.audit=[]
        def require_admin(request:Request):
            try:
                admin.require_web_admin_access(session_cookie=request.cookies.get(admin.WEB_ADMIN_COOKIE))
            except PermissionError:raise HTTPException(403,"admin_forbidden")
            return "web_admin"
        def audit(**kwargs):self.audit.append(kwargs)
        api=FastAPI()
        api.include_router(create_admin_router(canonical_db=self.db,require_admin=require_admin,audit=audit))
        self.client=TestClient(api)
        for i,src in enumerate(("pricing","probnik","oemixibot")):
            core.begin(self.db,source_bot=src,source_kind="private",delivery_key="test:"+src,
                oem="3211070",telegram_user_id=200+i,source_chat_id=100+i,telegram_message_id=i+1)
    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()
    def url(self):return "/admin/internal/unified-analytics/requests"
    def auth(self):
        self.client.cookies.set(admin.WEB_ADMIN_COOKIE,admin.issue_web_admin_session())
    def test_anon_invalid_and_missing_token_deny_by_default(self):
        self.assertEqual(self.client.get(self.url()).status_code,403)
        self.client.cookies.set(admin.WEB_ADMIN_COOKIE,"invalid")
        self.assertEqual(self.client.get(self.url()).status_code,403)
        self.assertEqual(self.audit,[])
    def test_existing_session_filters_pagination_pii_and_audit(self):
        self.auth()
        r=self.client.get(self.url(),params={"source_bot":"oemixibot","limit":1,"offset":0})
        self.assertEqual(r.status_code,200)
        d=r.json();self.assertEqual(d["total"],1);self.assertEqual(len(d["items"]),1)
        row=d["items"][0]
        self.assertEqual(row["source_bot"],"oemixibot")
        self.assertTrue(str(row["telegram_user_id"]).startswith("***"))
        self.assertTrue(str(row["source_chat_id"]).startswith("***"))
        self.assertNotIn("username",row)
        self.assertNotIn("200",str(row))
        self.assertEqual(self.audit[-1]["action"],"analytics_read")
        self.assertNotIn("telegram_user_id",self.audit[-1]["filters"])
        page=self.client.get(self.url(),params={"limit":2,"offset":1}).json()
        self.assertEqual(page["total"],3)
        self.assertEqual(len(page["items"]),2)
        self.assertEqual(self.client.get(self.url(),params={"limit":101}).status_code,422)
if __name__=="__main__":unittest.main()

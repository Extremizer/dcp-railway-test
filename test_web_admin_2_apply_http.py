"""HTTP regression for WEB ADMIN 2 Apply without importing production runtime.

Extract the actual FastAPI route functions (AST), mount them on a minimal
FastAPI app, and stub their dependencies. No production DB or Railway access.
"""
import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.testclient import TestClient
import supplier_admin_auth as auth


SOURCE = Path(__file__).with_name("web_app.py").read_text(encoding="utf-8")
TARGETS = {"_admin_page_login_redirect", "admin_order_prepare_dry_run", "admin_order_prepare_apply"}


class HttpApplyRegression(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXTREMIZER_WEB_ADMIN_TOKEN": "isolated-http-test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.apply = Mock(return_value={"ok": True, "changed": False})
        self.admin_order_service = SimpleNamespace(
            get_order=Mock(return_value={"order": {"order_id": "T-1"}}),
            prepare_order_dry_run=Mock(return_value={"ready_to_apply": True, "would_change_db": True}),
            OrderNotFound=type("OrderNotFound", (Exception,), {}),
        )
        self.admin_order_web = SimpleNamespace(render_order_card=Mock(return_value="<p>Safe</p>"))
        app = FastAPI()
        tree = ast.parse(SOURCE)
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in TARGETS]
        self.assertEqual(len(nodes), len(TARGETS))
        env = {
            "app": app, "Request": Request, "Form": Form, "HTTPException": HTTPException,
            "HTMLResponse": HTMLResponse, "RedirectResponse": RedirectResponse,
            "supplier_admin_auth": auth, "admin_order_service": self.admin_order_service,
            "admin_order_web": self.admin_order_web,
            "admin_order_apply_service": SimpleNamespace(prepare_order_apply=self.apply),
            "core": SimpleNamespace(ORDERS_DB_FILE="ISOLATED-DB-NEVER-OPENED"),
            "urllib": __import__("urllib"),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "web_app.py", "exec"), env)
        self.client = TestClient(app, follow_redirects=False)
        self.cookie = auth.issue_web_admin_session()

    def test_anonymous_post_redirects_without_apply(self):
        response = self.client.post("/admin/orders/T-1/prepare-apply", data={"csrf_token": "x"})
        self.assertEqual(response.status_code, 303)
        self.apply.assert_not_called()

    def test_authenticated_post_without_csrf_denied(self):
        self.client.cookies.set(auth.WEB_ADMIN_COOKIE, self.cookie)
        response = self.client.post("/admin/orders/T-1/prepare-apply", data={})
        self.assertEqual(response.status_code, 403)
        self.apply.assert_not_called()

    def test_wrong_order_csrf_denied(self):
        self.client.cookies.set(auth.WEB_ADMIN_COOKIE, self.cookie)
        token = auth.issue_apply_csrf_token(self.cookie, "T-OTHER")
        response = self.client.post("/admin/orders/T-1/prepare-apply", data={"csrf_token": token})
        self.assertEqual(response.status_code, 403)
        self.apply.assert_not_called()

    def test_valid_session_and_csrf_calls_apply_once(self):
        self.client.cookies.set(auth.WEB_ADMIN_COOKIE, self.cookie)
        token = auth.issue_apply_csrf_token(self.cookie, "T-1")
        response = self.client.post("/admin/orders/T-1/prepare-apply", data={"csrf_token": token})
        self.assertEqual(response.status_code, 200)
        self.apply.assert_called_once_with("T-1", "ISOLATED-DB-NEVER-OPENED")

    def test_dry_run_supplies_csrf_to_form_renderer(self):
        self.client.cookies.set(auth.WEB_ADMIN_COOKIE, self.cookie)
        response = self.client.get("/admin/orders/T-1/prepare-dry-run")
        self.assertEqual(response.status_code, 200)
        kwargs = self.admin_order_web.render_order_card.call_args.kwargs
        self.assertTrue(auth.verify_apply_csrf_token(self.cookie, "T-1", kwargs["apply_csrf_token"]))


if __name__ == "__main__":
    unittest.main()

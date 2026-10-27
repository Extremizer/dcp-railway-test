"""Offline tests for exact Telegram staging identity and mock-only notice.

No Telegram requests, no Railway calls, no volume mutations, no external browser.
"""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch, Mock

import dcp_phone_staging_app as staging
from dcp_phone_recovery_notice import RecoveryNoticeError


class FakeResponse:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, *_):
        return self.body


def fake_getme(username):
    return FakeResponse(
        ('{"ok":true,"result":{"is_bot":true,"username":"' +
         username + '"}}').encode("utf-8")
    )


class StagingBotIdentityTests(unittest.TestCase):
    def test_exact_expected_bot_identity(self):
        opener = Mock(return_value=fake_getme("ExtremizerDCPBot"))
        result = staging._telegram_getme_readonly("123:fake", opener=opener)
        self.assertEqual(result, "OK")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(opener.call_args.kwargs["timeout"], 8)

    def test_other_bot_is_rejected_even_with_valid_token(self):
        for name in ("ExtremizerDCP", "OEMixiBOT", "SomeOtherBot"):
            with self.subTest(name=name):
                self.assertEqual(
                    staging._telegram_getme_readonly(
                        "123:fake",
                        opener=lambda *_a, **_kw: fake_getme(name),
                    ),
                    "UNCONFIRMED_RESPONSE",
                )

    def test_invalid_bot_response_is_fail_closed(self):
        opener = lambda *_a, **_kw: FakeResponse(
            b'{"ok":true,"result":{"is_bot":false,"username":"ExtremizerDCPBot"}}'
        )
        self.assertEqual(
            staging._telegram_getme_readonly("123:fake", opener=opener),
            "UNCONFIRMED_RESPONSE",
        )

    def test_http_client_exception_diagnostics_are_sanitized(self):
        from http.client import HTTPException, RemoteDisconnected, BadStatusLine
        from unittest.mock import patch
        cases = (
            (HTTPException("private-url-with-token"), "HTTP_CLIENT_HTTPException"),
            (RemoteDisconnected("private-url-with-token"), "HTTP_CLIENT_RemoteDisconnected"),
            (BadStatusLine("private-url-with-token"), "HTTP_CLIENT_BadStatusLine"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected), patch(
                "urllib.request.urlopen", side_effect=error
            ):
                result = staging._telegram_getme_readonly("123:PRIVATE")
                self.assertEqual(result, expected)
                self.assertNotIn("PRIVATE", result)
                self.assertNotIn("private-url", result)

    def test_malformed_token_fails_before_network_without_secret_leak(self):
        opener = Mock()
        cases = (
            ("123:ab cd", "INVALID_TOKEN_WHITESPACE_OR_CONTROL"),
            ("123:ab\ncd", "INVALID_TOKEN_WHITESPACE_OR_CONTROL"),
            ("123:ab\rcd", "INVALID_TOKEN_WHITESPACE_OR_CONTROL"),
            ("123:ab\x00cd", "INVALID_TOKEN_WHITESPACE_OR_CONTROL"),
            ("nonnumeric:abc", "INVALID_TOKEN_STRUCTURE"),
            ("123:abc:extra", "INVALID_TOKEN_STRUCTURE"),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(staging._telegram_getme_readonly(value, opener=opener), expected)
        opener.assert_not_called()

    def test_invalid_token_never_uses_network(self):
        opener = Mock()
        self.assertEqual(
            staging._telegram_getme_readonly("", opener=opener),
            "NOT_CONFIGURED",
        )
        opener.assert_not_called()

    def test_wrong_bot_blocks_mock_notice_before_send(self):
        with patch.dict(os.environ, {
                "DCP_PHONE_STAGING_MOCK_ONLY": "1",
                "DCP_RECOVERY_TELEGRAM_BOT_TOKEN": "123:fake",
                "DCP_RECOVERY_OWNER_ID": "12345",
                "DCP_RECOVERY_PUBLIC_URL": "https://example.com",
                "DCP_STAGING_TEST_NOTICE_ID": "",
            }, clear=True), patch("threading.Thread"), patch.object(
                staging, "_telegram_getme_readonly", return_value="UNCONFIRMED_RESPONSE"
            ) as getme, patch.object(staging, "send_recovery_message") as send:
            staging.configure()
            self.assertIsNotNone(staging.app.state.coordinator)
            with self.assertRaises(RecoveryNoticeError) as raised:
                staging.app.state.coordinator.notify("AUTH_REQUIRED", now=100)
            self.assertEqual(str(raised.exception), "telegram_identity_unverified")
            getme.assert_called_once_with("123:fake")
            send.assert_not_called()

    def test_correct_bot_builds_one_time_mock_only_webapp(self):
        with patch.dict(os.environ, {
                "DCP_PHONE_STAGING_MOCK_ONLY": "1",
                "DCP_RECOVERY_TELEGRAM_BOT_TOKEN": "123:fake",
                "DCP_RECOVERY_OWNER_ID": "12345",
                "DCP_RECOVERY_PUBLIC_URL": "https://example.com",
                "DCP_STAGING_TEST_NOTICE_ID": "",
            }, clear=True), patch("threading.Thread"), patch.object(
                staging, "_telegram_getme_readonly", return_value="OK"
            ), patch.object(staging, "send_recovery_message") as send:
            staging.configure()
            sent = staging.app.state.coordinator.notify("AUTH_REQUIRED", now=100)
            self.assertTrue(sent)
            send.assert_called_once()
            payload = send.call_args.kwargs["payload"]
            self.assertIn("MOCK_ONLY", payload["text"])
            button = payload["reply_markup"]["inline_keyboard"][0][0]
            self.assertEqual(button["text"], "Открыть тестовый Chromium")
            url = button["web_app"]["url"]
            from urllib.parse import urlsplit, parse_qs
            parsed = urlsplit(url)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "example.com")
            self.assertEqual(parsed.path, "/recovery")
            self.assertEqual(parsed.fragment, "")
            selector = parse_qs(parsed.query)["ref"][0]
            self.assertEqual(len(selector), 64)
            self.assertIn(selector, staging.app.state.invite_refs)
            self.assertNotIn(staging.app.state.invite_refs[selector], url)
            self.assertNotIn("123:fake", url)
            self.assertNotIn("12345", url)


class FrameCSPTests(unittest.IsolatedAsyncioTestCase):
    """CSP regression: blob frames allowed, all other restrictions fixed."""
    async def test_blob_allowed_only_for_images_with_other_restrictions_preserved(self):
        scope={"type":"http","method":"GET","path":"/recovery","headers":[],"query_string":b"",
               "scheme":"https","server":("test.invalid",443),"client":("127.0.0.1",1000),"http_version":"1.1"}
        from starlette.requests import Request
        from starlette.responses import Response
        request=Request(scope)
        async def next_handler(_):
            return Response("offline",media_type="text/plain")
        response=await staging.secure(request,next_handler)
        csp=response.headers["content-security-policy"]
        policies=dict(part.strip().split(" ",1) for part in csp.split(";") if part.strip())
        self.assertEqual(policies["img-src"],"'self' data: blob:")
        self.assertEqual(policies["default-src"],"'none'")
        self.assertEqual(policies["script-src"],"'self' https://telegram.org")
        self.assertEqual(policies["style-src"],"'self'")
        self.assertEqual(policies["connect-src"],"'self'")
        self.assertEqual(policies["frame-ancestors"],"'none'")
        self.assertEqual(response.headers["cache-control"],"no-store")
        self.assertEqual(response.headers["referrer-policy"],"no-referrer")
        self.assertEqual(response.headers["x-frame-options"],"DENY")


if __name__ == "__main__":
    unittest.main()

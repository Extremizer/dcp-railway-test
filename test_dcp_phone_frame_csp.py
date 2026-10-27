"""M4.7 isolated CSP regression for Telegram WebView mock frame rendering."""
import unittest
from unittest.mock import Mock
from starlette.requests import Request
from starlette.responses import Response
import dcp_phone_staging_app as staging

class FrameCSPTests(unittest.IsolatedAsyncioTestCase):
    async def test_blob_allowed_only_for_images_with_other_restrictions_preserved(self):
        scope={"type":"http","method":"GET","path":"/recovery","headers":[],"query_string":b"",
               "scheme":"https","server":("test.invalid",443),"client":("127.0.0.1",1000),"http_version":"1.1"}
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

if __name__=="__main__":
    unittest.main()

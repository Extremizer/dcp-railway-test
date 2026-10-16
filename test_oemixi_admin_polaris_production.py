import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

import oemixi_admin_polaris_production as prod
import oemixi_category_authority_client as client
from polaris_probe_errors import ProbeFailure


VECTOR_SECRET="0123456789abcdef0123456789abcdef"
VECTOR_TS="1760640000"
VECTOR_SIG="18ba501685a5550122c50f1795795ae4c01fcd4221e48e48dcc2491cfc90efe5"


class FakeResponse:
    def __init__(self,payload):
        self.raw=json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self,n=-1):
        return self.raw if n<0 else self.raw[:n]


class ProductionWiringTests(unittest.TestCase):
    def test_01_signature_vector(self):
        body=client.canonical_body("Polaris","2205250")
        self.assertEqual(
            body,
            b'{"manufacturer":"Polaris","oem":"2205250"}',
        )
        self.assertEqual(
            client.sign_request(VECTOR_SECRET,timestamp=VECTOR_TS,body=body),
            VECTOR_SIG,
        )

    def test_02_https_required(self):
        with self.assertRaisesRegex(ValueError,"https_authority_url_required"):
            client.HTTPSCategoryAuthorityResolver(
                "http://example.test",VECTOR_SECRET
            )

    def test_03_verified_response_parsed(self):
        seen={}
        def opener(req,timeout):
            seen["url"]=req.full_url
            seen["method"]=req.get_method()
            seen["body"]=req.data
            seen["headers"]={k.lower():v for k,v in req.header_items()}
            return FakeResponse({
                "status":"CATEGORY_VERIFIED",
                "manufacturer":"Polaris",
                "requested_oem":"2205250",
                "resolved_oem":"2206328",
                "item_category":"Parts",
                "category_source_kind":"validated_public_msrp",
                "category_source_ref":"fixture",
                "category_verified_at":"2026-10-16T18:00:00+00:00",
            })
        resolver=client.HTTPSCategoryAuthorityResolver(
            "https://authority.example",VECTOR_SECRET,opener=opener
        )
        result=resolver("Polaris","2205250")
        self.assertEqual(result["resolved_oem"],"2206328")
        self.assertEqual(result["item_category"],"Parts")
        self.assertEqual(seen["method"],"POST")
        self.assertTrue(seen["url"].endswith(client.PATH))
        self.assertIn("x-oemixi-authority-ts",seen["headers"])
        self.assertIn("x-oemixi-authority-sig",seen["headers"])

    def test_04_unverified_response_is_none(self):
        resolver=client.HTTPSCategoryAuthorityResolver(
            "https://authority.example",VECTOR_SECRET,
            opener=lambda req,timeout:FakeResponse({
                "status":"CATEGORY_UNVERIFIED",
                "manufacturer":"Polaris",
                "requested_oem":"9999999",
            }),
        )
        self.assertIsNone(resolver("Polaris","9999999"))

    def test_05_auth_http_error_is_stable(self):
        def opener(req,timeout):
            raise urllib.error.HTTPError(
                req.full_url,401,"Unauthorized",{},io.BytesIO(b"")
            )
        resolver=client.HTTPSCategoryAuthorityResolver(
            "https://authority.example",VECTOR_SECRET,opener=opener
        )
        with self.assertRaises(client.CategoryAuthorityTransportError) as cm:
            resolver("Polaris","2205250")
        self.assertEqual(cm.exception.code,"CATEGORY_AUTHORITY_AUTH_FAILED")

    def test_06_network_error_is_stable(self):
        def opener(req,timeout):
            raise OSError("offline")
        resolver=client.HTTPSCategoryAuthorityResolver(
            "https://authority.example",VECTOR_SECRET,opener=opener
        )
        with self.assertRaises(client.CategoryAuthorityTransportError) as cm:
            resolver("Polaris","2205250")
        self.assertEqual(cm.exception.code,"CATEGORY_AUTHORITY_UNAVAILABLE")

    def test_07_invalid_payload_is_stable(self):
        resolver=client.HTTPSCategoryAuthorityResolver(
            "https://authority.example",VECTOR_SECRET,
            opener=lambda req,timeout:FakeResponse({
                "status":"CATEGORY_VERIFIED",
                "resolved_oem":"2206328",
            }),
        )
        with self.assertRaises(client.CategoryAuthorityTransportError) as cm:
            resolver("Polaris","2205250")
        self.assertEqual(cm.exception.code,"CATEGORY_AUTHORITY_INVALID_RESPONSE")

    def test_08_production_resolver_maps_transport_failure(self):
        class Broken:
            def __init__(self,*a,**k): pass
            def __call__(self,*a,**k):
                raise client.CategoryAuthorityTransportError(
                    "CATEGORY_AUTHORITY_AUTH_FAILED"
                )
        with patch.object(prod,"HTTPSCategoryAuthorityResolver",Broken):
            resolver=prod.make_category_resolver(
                base_url="https://authority.example",
                root_secret=VECTOR_SECRET,
            )
            with self.assertRaises(ProbeFailure) as cm:
                resolver("Polaris","2205250")
        self.assertEqual(cm.exception.code,"CATEGORY_AUTHORITY_AUTH_FAILED")

    def test_09_provider_preserves_admin_only_fields(self):
        evidence={
            "resolved_oem":"2206328",
            "item_category":"Parts",
            "category_source_kind":"validated_public_msrp",
            "category_source_ref":"fixture",
            "category_verified_at":"2026-10-16T18:00:00+00:00",
        }
        class Transport:
            def __init__(self,*a,**k): pass
            def __call__(self,*a,**k): return evidence
        with patch.object(prod,"HTTPSCategoryAuthorityResolver",Transport), \
             patch.object(prod,"admin_browser_price") as price:
            price.return_value={
                "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                "admin_unit_price_usd":"184.07",
                "dealer_price_usd":None,
                "production_found":False,
            }
            provider=prod.make_admin_polaris_provider(
                base_url="https://authority.example",
                root_secret=VECTOR_SECRET,
            )
            result=provider("2205250")
        self.assertEqual(result["admin_unit_price_usd"],"184.07")
        self.assertIsNone(result["dealer_price_usd"])
        self.assertIs(result["production_found"],False)

    def test_10_build_application_binds_explicit_provider(self):
        fake_app=object()
        with patch("oemixibot_app.build_application",return_value=fake_app) as build, \
             patch("oemixibot_app.bind_admin_polaris_price_resolver") as bind, \
             patch.object(prod,"make_admin_polaris_provider",return_value="provider"):
            result=prod.build_application(
                VECTOR_SECRET,
                base_url="https://authority.example",
            )
        self.assertIs(result,fake_app)
        build.assert_called_once_with(VECTOR_SECRET)
        bind.assert_called_once_with(fake_app,"provider")

    def test_11_default_production_url_is_https(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(
                prod._base_url(),
                "https://dcp-railway-test-production.up.railway.app",
            )



    def test_12_provider_probes_canonical_resolved_oem(self):
        evidence={
            "resolved_oem":"2206328",
            "item_category":"Parts",
            "category_source_kind":"validated_public_msrp",
            "category_source_ref":"fixture",
            "category_verified_at":"2026-10-16T18:00:00+00:00",
        }
        class Transport:
            def __init__(self,*a,**k): pass
            def __call__(self,manufacturer,oem):
                self.request=(manufacturer,oem)
                return evidence
        with patch.object(prod,"HTTPSCategoryAuthorityResolver",Transport),              patch.object(prod,"admin_browser_price") as price:
            price.return_value={
                "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                "manufacturer":"Polaris",
                "item_category":"Parts",
                "oem":"2206328",
                "admin_unit_price_usd":"184.07",
                "dealer_price_usd":None,
                "production_found":False,
            }
            provider=prod.make_admin_polaris_provider(
                base_url="https://authority.example",
                root_secret=VECTOR_SECRET,
            )
            result=provider("2205250")
        self.assertEqual(price.call_args.args[0],"2206328")
        self.assertEqual(result["requested_oem"],"2205250")
        self.assertEqual(result["resolved_oem"],"2206328")
        self.assertEqual(result["admin_unit_price_usd"],"184.07")

if __name__=="__main__":
    unittest.main()

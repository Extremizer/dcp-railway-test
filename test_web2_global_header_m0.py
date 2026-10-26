"""Global Header v1.1 isolated structural contract and non-mutation gates."""
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
HTML=(ROOT/"web2/index.html").read_text(encoding="utf-8")
CSS=(ROOT/"web2/styles.css").read_text(encoding="utf-8")
ROUTES=(ROOT/"web2_ui_routes.py").read_text(encoding="utf-8")

class GlobalHeaderM0(unittest.TestCase):
    def test_approved_content_order(self):
        h=HTML.split('<header class="topbar global-header-v11"',1)[1].split("</header>",1)[0]
        labels=['gh-search','gh-life','gh-cart','gh-hub','gh-private']
        self.assertEqual([h.index(v) for v in labels],sorted(h.index(v) for v in labels))
        self.assertEqual(h.count('class="gh-separator"'),4)
        self.assertIn("МОЙ ХАБ",h)
        self.assertIn(">LIFE</a>",h)
    def test_existing_cart_and_oem_contracts(self):
        for ident in ['cartButton','cartCount','cartButtonTotal','oemInput','searchForm','searchButton','cartPanel','telegramButton']:
            self.assertEqual(HTML.count('id="'+ident+'"'),1,ident)
    def test_approved_css_tokens(self):
        for fragment in ['--gh-gray:#A5A5A5','--gh-lime:#BFF803','--gh-l1:','font-family:Manrope','font-size:24px','font-weight:500','letter-spacing:0','object-fit:contain']:
            self.assertIn(fragment,CSS)
    def test_exact_staging_provisioning_contract(self):
        src=(ROOT/"scripts/provision_header_m0.py").read_text(encoding="utf-8")
        self.assertIn("ZIP_SHA =",src)
        self.assertIn("Canonical ZIP SHA-256 mismatch",src)
        self.assertIn("Original {kind} SHA-256 mismatch",src)
        self.assertIn("len(matches)!=1",src)
        for kind in ("wordmark","search","cart","private"):
            self.assertIn('"'+kind+'":',src)

    def test_png_signature_uses_real_binary_magic(self):
        import importlib.util
        import tempfile
        from unittest.mock import patch
        import os
        spec = importlib.util.spec_from_file_location("web2_header_routes_m0", ROOT / "web2_ui_routes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        png = bytes.fromhex("89504e470d0a1a0a") + b"minimal-test"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search.png"
            path.write_bytes(png)
            import hashlib
            with patch.dict(module.HEADER_ASSET_SHA, {"search": hashlib.sha256(png).hexdigest()}):
                with patch.dict(os.environ, {"EXTREMIZER_HEADER_SEARCH_PATH": str(path)}):
                    self.assertEqual(module._verified_header_asset("search"), path)
                    path.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"minimal-test")
                    self.assertIsNone(module._verified_header_asset("search"))

    def test_local_manrope_without_external_dependency(self):
        self.assertIn('font-family: "Manrope"',CSS)
        self.assertIn('/web2-static/fonts/Manrope-variable.ttf',CSS)
        self.assertIn('font-weight: 200 800',CSS)
        self.assertNotIn('fonts.googleapis.com',HTML)
        font = ROOT / 'web2/fonts/Manrope-variable.ttf'
        self.assertTrue(font.is_file())
        self.assertEqual(font.read_bytes()[:4], bytes.fromhex('00010000'))
        self.assertTrue((ROOT / 'web2/fonts/OFL.txt').is_file())

    def test_asset_fail_closed(self):
        for k in ['wordmark','search','cart','private']:
            self.assertIn('"'+k+'":',ROUTES)
            self.assertIn('/web2-header-asset/'+k,HTML)
        self.assertIn("hashlib.sha256(data).hexdigest() != HEADER_ASSET_SHA[kind]",ROUTES)

if __name__=="__main__":
    unittest.main()

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
    def test_asset_fail_closed(self):
        for k in ['wordmark','search','cart','private']:
            self.assertIn('"'+k+'":',ROUTES)
            self.assertIn('/web2-header-asset/'+k,HTML)
        self.assertIn("hashlib.sha256(data).hexdigest() != HEADER_ASSET_SHA[kind]",ROUTES)

if __name__=="__main__":
    unittest.main()

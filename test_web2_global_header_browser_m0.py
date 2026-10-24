"""M0 browser evidence: viewport bounds and header layout metrics; original assets remain staging-only."""
import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server

OUT=Path("artifacts/header-m0")
OUT.mkdir(parents=True,exist_ok=True)

class GlobalHeaderBrowserM0(unittest.TestCase):
    def test_header_responsive_evidence(self):
        with mock_server() as (base,_),sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            for width,height in [(360,800),(390,844),(430,932),(768,900),(1280,900)]:
                with self.subTest(width=width):
                    page=browser.new_page(viewport={"width":width,"height":height})
                    page.goto(base+"/web2")
                    page.locator(".global-header-v11").wait_for(state="visible")
                    page.screenshot(path=str(OUT/f"{width}-header-default.png"),full_page=False)
                    metrics=page.evaluate("""() => {
                      const keys=['.global-header-v11','.brand-slot','.gh-search','.gh-life','.gh-cart','.gh-hub','.gh-private'];
                      const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
                      const nav=document.querySelector('.global-header-nav');
                      return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                        L1Token:getComputedStyle(document.querySelector('.global-header-v11')).getPropertyValue('--gh-l1'),
                        font:getComputedStyle(document.querySelector('.gh-life')).fontFamily,
                        size:getComputedStyle(document.querySelector('.gh-life')).fontSize,
                        weight:getComputedStyle(document.querySelector('.gh-life')).fontWeight,
                        nav:rect(nav),parts:Object.fromEntries(keys.map(k=>[k,rect(document.querySelector(k))])),
                        separators:[...document.querySelectorAll('.gh-separator')].map(rect),
                        assetResults:[...document.querySelectorAll('.global-header-v11 img')].map(i=>({src:i.getAttribute('src'),loaded:i.complete && i.naturalWidth>0})),
                        fontAvailable:document.fonts.check('500 24px Manrope')
                      };
                    }""")
                    (OUT/f"{width}-metrics.json").write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding="utf-8")
                    self.assertLessEqual(metrics["scrollWidth"],width+1)
                    self.assertEqual(len(metrics["separators"]),4)
                    self.assertEqual(metrics["weight"],"500")
                    if width>=768:self.assertEqual(metrics["size"],"24px")
                    page.close()
            browser.close()

if __name__=="__main__":unittest.main()

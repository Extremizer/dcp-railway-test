"""Isolated source-lock and contract checks for proposed Page Template v1."""
from pathlib import Path
import unittest
BASE=Path(__file__).resolve().parent
P=BASE/"web2/page-template-v1"
class PageTemplateContract(unittest.TestCase):
    def test_isolated_files(self):
        self.assertTrue(all((P/f).exists() for f in ("template.js","template.css","preview.html")))
    def test_declarative_blocks_and_directions(self):
        source=(P/"template.js").read_text()
        for part in ("createHero","createOemSearch","createCategoryGrid","createEditorialFeature","createGlobalFooter","renderPage","POWERSPORTS","WORKOUT & GYM","FOOD & HEALTH","OUTDOOR & TRAVEL","GET TOGETHER"):
            self.assertIn(part,source)
        self.assertIn("if(!items.length) return null",source)
        self.assertIn("resolveOemVisibility",source)\n        self.assertIn("fieldVisible",source)\n        self.assertIn("buttonVisible",source)\n        self.assertIn("button.disabled=!action",source)\n        self.assertIn("event.preventDefault()",source)\n        self.assertIn("if(oemSearch) page.append(oemSearch)",source)
    def test_independent_oem_contract(self):\n        source=(P/"template.js").read_text()\n        css=(P/"template.css").read_text()\n        self.assertIn('hasOwn("visible")',source)\n        self.assertIn('hasOwn("fieldVisible")',source)\n        self.assertIn('hasOwn("buttonVisible")',source)\n        self.assertIn('if(!fieldVisible&&!buttonVisible) return null',source)\n        self.assertIn('if(fieldVisible&&!oem)',source)\n        self.assertIn('action({oem,source:"hero"})',source)\n        self.assertIn(".pt-oem-controls :focus-visible",css)\n    def test_sha_locked_assets(self):
        source=(P/"template.js").read_text()
        for sha in ("46c3c4295313c0737094cc1b45a48fd64ea18cf3f971d48110640f64cd3a662f","26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8","920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b","c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c"):
            self.assertIn(sha,source)
    def test_preview_does_not_load_business_js(self):
        html=(P/"preview.html").read_text()
        self.assertNotIn('src="/web2-static/app.js"',html)
        self.assertIn("PAGE TEMPLATE",html)
if __name__=="__main__":unittest.main()

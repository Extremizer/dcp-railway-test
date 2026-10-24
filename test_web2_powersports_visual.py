"""POWERSPORTS Visual Master v1 integration guards (isolated, no deployment)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "web2" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web2" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "web2" / "app.js").read_text(encoding="utf-8")
ROUTES = (ROOT / "web2_ui_routes.py").read_text(encoding="utf-8")

class PowersportsVisualGuards(unittest.TestCase):
    def test_existing_functional_anchors_unique(self):
        for id_ in ("searchForm", "oemInput", "searchButton", "state", "product",
                    "offers", "cartButton", "cartPanel", "cartItems", "telegramButton",
                    "qtyMinus", "qtyPlus"):
            self.assertEqual(HTML.count(f'id="{id_}"'), 1, id_)

    def test_owner_visual_contract(self):
        for needle in ("POWERSPORTS", "MACHINE", "ПРАКТИКА ВЛАДЕНИЯ",
                       "ОБСЛУЖИВАНИЕ", "ЭКСПЛУАТАЦИЯ", "ПОДГОТОВКА",
                       "Введи", "каталожный номер", "Искать", "LIFE"):
            self.assertIn(needle, HTML)
        self.assertIn('src="/web2-header-asset/wordmark"', HTML)
        self.assertIn('href="#oemInput" aria-label="Поиск OEM"', HTML)
        self.assertIn('src="/web2-powersports-hero"', HTML)
        self.assertIn('--acid:#BFF803', CSS)
        self.assertIn('--metal:#D8DAD7', CSS)
        self.assertIn('transition:background-color .2s', CSS)
        self.assertIn('.search-field:hover', CSS)
        self.assertIn('button#searchButton:hover', CSS)

    def test_search_logic_unchanged_and_icon_persists(self):
        self.assertIn('const caption = $("searchButtonText")', JS)
        self.assertIn('caption.textContent = busy ? "Ищем…" : "Искать"', JS)
        self.assertNotIn('searchButton.textContent = busy ? "Ищем…" : "Найти"', JS)
        self.assertIn('id="searchButtonText"', HTML)
        self.assertIn('class="search-icon"', HTML)
        self.assertEqual(HTML.count('id="searchForm"'), 1)

    def test_fail_closed_original_assets(self):
        self.assertIn('EXTREMIZER_POWERSPORTS_GARAGE_SHA256', ROUTES)
        self.assertIn('EXTREMIZER_POWERSPORTS_GARAGE_PATH', ROUTES)
        self.assertIn('"/web2-powersports-hero"', ROUTES)
        self.assertIn('_verified_png_path(Path(raw_path), raw_sha)', ROUTES)
        self.assertIn('powersports garage unavailable', ROUTES)

if __name__ == "__main__":
    unittest.main()

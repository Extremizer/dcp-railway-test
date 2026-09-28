import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urljoin

import requests

INTERVAL = int(os.getenv("STOCK_TEST_INTERVAL", "900"))
RUN_ONCE = os.getenv("STOCK_RUN_ONCE", "0").strip().lower() in {"1","true","yes","on"}
TIMEOUT = int(os.getenv("STOCK_HTTP_TIMEOUT", "15"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

KRS_SITEMAP_CACHE = {"text": None, "loaded_at": 0.0}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch(url, timeout=TIMEOUT):
    r = SESSION.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    if not r.encoding:
        r.encoding = r.apparent_encoding or "utf-8"
    return r.url, r.text


def title_from_html(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if not m:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()


def clean_text(text):
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def result(warehouse, oem, status, quantity=None, url=None, extra=None, elapsed=None):
    return {
        "warehouse": warehouse,
        "oem": oem,
        "status": status,
        "quantity": quantity,
        "url": url,
        "elapsed_sec": elapsed,
        "extra": extra or {},
    }


def test_msk():
    started = time.perf_counter()
    oem = "417300574"
    try:
        search_url = (
            "https://orangeatv.ru/index.php?route=product/search&search="
            + quote(oem)
            + "&description=true&sub_category=true"
        )
        _, search_html = fetch(search_url)
        links = re.findall(
            r'href=["\']([^"\']*route=product/product[^"\']*)',
            search_html,
            re.I,
        )
        candidates = []
        for link in links:
            url = urljoin("https://orangeatv.ru/", html.unescape(link))
            if url not in candidates:
                candidates.append(url)

        exact = []
        for url in candidates[:8]:
            try:
                final_url, page = fetch(url)
            except Exception:
                continue
            if re.search(rf"(?<!\d){re.escape(oem)}(?!\d)", page):
                exact.append((final_url, page))

        if len(exact) == 0:
            return result("MSK", oem, "not_found", elapsed=time.perf_counter()-started)
        if len(exact) > 1:
            return result("MSK", oem, "ambiguous", extra={"count": len(exact)}, elapsed=time.perf_counter()-started)

        final_url, page = exact[0]
        m = re.search(r"Доступно\s*([0-9]+(?:[.,][0-9]+)?)\s*шт\.?", page, re.I)
        if m:
            q = float(m.group(1).replace(",", "."))
            return result("MSK", oem, "in_stock" if q > 0 else "out_of_stock", q, final_url, elapsed=time.perf_counter()-started)

        plain = clean_text(page)
        if re.search(r"Нет\s+в\s+наличии", plain, re.I):
            return result("MSK", oem, "out_of_stock", 0, final_url, elapsed=time.perf_counter()-started)
        if re.search(r"В\s+наличии", plain, re.I):
            return result("MSK", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
        return result("MSK", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
    except Exception as exc:
        return result("MSK", oem, "check_failed", extra={"error": type(exc).__name__, "message": str(exc)[:250]}, elapsed=time.perf_counter()-started)


def krs_sitemap():
    if KRS_SITEMAP_CACHE["text"] is None or time.time() - KRS_SITEMAP_CACHE["loaded_at"] > 3600:
        _, text = fetch("https://vladextremelife.ru/sitemap-iblock-138.xml", timeout=30)
        KRS_SITEMAP_CACHE["text"] = text
        KRS_SITEMAP_CACHE["loaded_at"] = time.time()
    return KRS_SITEMAP_CACHE["text"]


def test_krs():
    started = time.perf_counter()
    oem = "417300574"
    try:
        urls = re.findall(r"<loc>(.*?)</loc>", krs_sitemap(), re.I | re.S)
        token = re.compile(rf"(?<![A-Za-z0-9]){re.escape(oem)}(?![A-Za-z0-9])", re.I)
        candidates = [u.strip() for u in urls if token.search(u)][:30]

        exact = []
        for url in candidates:
            try:
                final_url, page = fetch(url)
            except Exception:
                continue
            plain = clean_text(page)
            if re.search(rf"Артикул:\s*{re.escape(oem)}(?:\s|$)", plain, re.I):
                exact.append((final_url, page))

        if len(exact) == 0:
            return result("KRS", oem, "not_found", elapsed=time.perf_counter()-started)
        if len(exact) > 1:
            return result("KRS", oem, "ambiguous", extra={"count": len(exact)}, elapsed=time.perf_counter()-started)

        final_url, page = exact[0]
        m = re.search(r"CATALOG_QUANTITY\s*:\s*['\"]?([0-9]+(?:[.,][0-9]+)?)", page, re.I)
        if not m:
            m = re.search(r"QTY_MAX\s*:\s*['\"]?([0-9]+(?:[.,][0-9]+)?)", page, re.I)

        if m:
            q = float(m.group(1).replace(",", "."))
            store_max = [
                float(x.replace(",", "."))
                for x in re.findall(r'data-max=["\']([0-9]+(?:[.,][0-9]+)?)["\']', page, re.I)
            ]
            return result(
                "KRS", oem, "in_stock" if q > 0 else "out_of_stock",
                q, final_url,
                extra={"store_max_sum": sum(store_max), "store_max_values": store_max},
                elapsed=time.perf_counter()-started,
            )

        plain = clean_text(page)
        if re.search(r"Нет\s+в\s+наличии", plain, re.I):
            return result("KRS", oem, "out_of_stock", 0, final_url, elapsed=time.perf_counter()-started)
        if re.search(r"В\s+наличии", plain, re.I):
            return result("KRS", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
        return result("KRS", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
    except Exception as exc:
        return result("KRS", oem, "check_failed", extra={"error": type(exc).__name__, "message": str(exc)[:250]}, elapsed=time.perf_counter()-started)


def test_yrs():
    started = time.perf_counter()
    oem = "518327485"
    try:
        search_url = (
            "https://xn--76-dlclqvoatief.xn--p1ai/"
            "poisk_produktov/?search_text=" + quote(oem)
        )
        _, search_html = fetch(search_url, timeout=12)
        links = re.findall(
            r'href=["\']([^"\']*/products/[0-9]+/?)["\']',
            search_html,
            re.I,
        )
        candidates = []
        for link in links:
            url = urljoin("https://xn--76-dlclqvoatief.xn--p1ai/", link)
            if url not in candidates:
                candidates.append(url)

        exact = []
        for url in candidates[:12]:
            try:
                final_url, page = fetch(url, timeout=12)
            except Exception:
                continue
            plain = clean_text(page)
            if re.search(rf"Артикул:\s*{re.escape(oem)}(?:\s|$)", plain, re.I):
                exact.append((final_url, page))

        if len(exact) == 0:
            return result("YRS", oem, "not_found", elapsed=time.perf_counter()-started)
        if len(exact) > 1:
            return result("YRS", oem, "ambiguous", extra={"count": len(exact)}, elapsed=time.perf_counter()-started)

        final_url, page = exact[0]
        plain = clean_text(page)
        m = re.search(r"Есть\s+в\s+наличии\s*,?\s*([0-9]+(?:[.,][0-9]+)?)\s*шт\.?", plain, re.I)
        if m:
            q = float(m.group(1).replace(",", "."))
            return result("YRS", oem, "in_stock" if q > 0 else "out_of_stock", q, final_url, elapsed=time.perf_counter()-started)
        if re.search(r"Под\s+заказ|Нет\s+в\s+наличии", plain, re.I):
            return result("YRS", oem, "out_of_stock", 0, final_url, elapsed=time.perf_counter()-started)
        if re.search(r"Есть\s+в\s+наличии|В\s+наличии", plain, re.I):
            return result("YRS", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
        return result("YRS", oem, "quantity_unknown", None, final_url, elapsed=time.perf_counter()-started)
    except Exception as exc:
        return result("YRS", oem, "check_failed", extra={"error": type(exc).__name__, "message": str(exc)[:250]}, elapsed=time.perf_counter()-started)


def run_cycle(cycle):
    print("#" * 80, flush=True)
    print(f"WAREHOUSE STOCK RAILWAY TEST — cycle {cycle} — {now()}", flush=True)
    print("Mode: read-only / public HTTP GET only", flush=True)
    print("#" * 80, flush=True)

    funcs = [test_msk, test_yrs, test_krs]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in funcs}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({
                    "warehouse": futures[future],
                    "status": "test_crashed",
                    "error": f"{type(exc).__name__}: {exc}",
                })

    order = {"MSK": 1, "YRS": 2, "KRS": 3}
    results.sort(key=lambda x: order.get(x.get("warehouse"), 99))

    for item in results:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)

    print("-" * 80, flush=True)
    for item in results:
        q = item.get("quantity")
        q_text = "?" if q is None else f"{q:g}"
        print(
            f"{item.get('warehouse','?'):3}  "
            f"{item.get('status','?'):18}  qty={q_text:>6}  "
            f"elapsed={item.get('elapsed_sec',0):.2f}s",
            flush=True,
        )
    print("-" * 80, flush=True)


def main():
    cycle = 1
    while True:
        run_cycle(cycle)
        if RUN_ONCE:
            print("STOCK_RUN_ONCE=1 -> finished.", flush=True)
            return
        print(f"Next cycle in {INTERVAL} seconds.", flush=True)
        time.sleep(INTERVAL)
        cycle += 1


if __name__ == "__main__":
    main()

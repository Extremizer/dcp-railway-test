import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

# DCP Railway diagnostic.
# Read-only: only public HTTP GET requests. No login, CAPTCHA solving, or challenge bypass.

DEFAULT_URLS = [
    "https://www.dealercostparts.com/",
    "https://www.dealercostparts.com/oemparts/c/ski_doo_snowmobile/parts",
    "https://www.dealercostparts.com/oemparts/c/ski_doo_snowmobile_2025/parts",
    "https://www.dealercostparts.com/oemparts/a/ski/673e2b6adf096aca04d91830/drive-pulley",
]

USER_AGENT = os.getenv(
    "DCP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36",
)

TIMEOUT = int(os.getenv("DCP_TIMEOUT", "30"))
INTERVAL = int(os.getenv("DCP_TEST_INTERVAL", "900"))
RUN_ONCE = os.getenv("DCP_RUN_ONCE", "0").strip().lower() in {"1", "true", "yes", "on"}

extra_urls = [
    x.strip()
    for x in os.getenv("DCP_TEST_URLS", "").split(",")
    if x.strip()
]
URLS = extra_urls or DEFAULT_URLS

CHALLENGE_MARKERS = (
    "verify you are human",
    "confirm you are human",
    "подтвердите, что вы человек",
    "checking your browser",
    "just a moment",
    "cf-chl-",
    "challenge-platform",
    "cloudflare",
)

NORMAL_DCP_MARKERS = (
    "oemparts",
    "oem parts",
    "data-retail=",
    "data-sku=",
    "/cart/addoempart",
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def title_from_html(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:180]


def classify(response):
    text = response.text.lower()
    title = title_from_html(response.text)

    challenge_hits = [x for x in CHALLENGE_MARKERS if x in text]
    normal_hits = [x for x in NORMAL_DCP_MARKERS if x in text]

    # Cloudflare can legitimately proxy a normal page, so "cloudflare" alone
    # is not enough to call the response a challenge.
    strong_challenge = any(
        x in text for x in (
            "verify you are human",
            "confirm you are human",
            "подтвердите, что вы человек",
            "checking your browser",
            "just a moment",
            "cf-chl-",
            "challenge-platform",
        )
    )

    if response.status_code in (403, 429, 503) and (strong_challenge or "cloudflare" in text):
        verdict = "BLOCKED_OR_CHALLENGED"
    elif strong_challenge:
        verdict = "HUMAN_CHECK_PRESENT"
    elif response.ok and normal_hits:
        verdict = "DCP_PAGE_REACHED"
    elif response.ok:
        verdict = "HTTP_OK_UNCONFIRMED_PAGE"
    else:
        verdict = "HTTP_ERROR"

    return verdict, title, challenge_hits, normal_hits


def test_url(session, url):
    started = time.perf_counter()
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        elapsed = time.perf_counter() - started
        verdict, title, challenge_hits, normal_hits = classify(r)

        print("=" * 78, flush=True)
        print(f"[{now()}] {verdict}", flush=True)
        print(f"requested_url : {url}", flush=True)
        print(f"final_url     : {r.url}", flush=True)
        print(f"status        : {r.status_code}", flush=True)
        print(f"elapsed_sec   : {elapsed:.2f}", flush=True)
        print(f"bytes         : {len(r.content)}", flush=True)
        print(f"title         : {title or '(none)'}", flush=True)
        print(f"server        : {r.headers.get('server', '(none)')}", flush=True)
        print(f"cf-ray        : {r.headers.get('cf-ray', '(none)')}", flush=True)
        print(f"challenge_hits: {challenge_hits or 'none'}", flush=True)
        print(f"dcp_hits      : {normal_hits or 'none'}", flush=True)

        # For the known Drive Pulley page, confirm whether the public HTML
        # contains our known test OEM/MSRP markers. This is diagnostic only.
        if "drive-pulley" in url:
            body = r.text
            print(f"known_oem_417224332 : {'YES' if '417224332' in body else 'NO'}", flush=True)
            print(f"known_msrp_189.99   : {'YES' if '189.99' in body else 'NO'}", flush=True)

        return verdict
    except requests.RequestException as e:
        elapsed = time.perf_counter() - started
        print("=" * 78, flush=True)
        print(f"[{now()}] REQUEST_FAILED", flush=True)
        print(f"requested_url : {url}", flush=True)
        print(f"elapsed_sec   : {elapsed:.2f}", flush=True)
        print(f"error         : {type(e).__name__}: {e}", flush=True)
        return "REQUEST_FAILED"


def run_cycle(session, cycle):
    print("\n" + "#" * 78, flush=True)
    print(f"DCP RAILWAY TEST — cycle {cycle} — {now()}", flush=True)
    print(f"Python: {sys.version.split()[0]}", flush=True)
    print(f"Targets: {len(URLS)}", flush=True)
    print("#" * 78, flush=True)

    verdicts = {}
    for url in URLS:
        verdicts[url] = test_url(session, url)

    print("-" * 78, flush=True)
    print("CYCLE SUMMARY", flush=True)
    for url, verdict in verdicts.items():
        print(f"{verdict:26} {urlparse(url).path or '/'}", flush=True)
    print("-" * 78, flush=True)


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    cycle = 1
    while True:
        run_cycle(session, cycle)

        if RUN_ONCE:
            print("DCP_RUN_ONCE=1 -> finished.", flush=True)
            return

        print(f"Next read-only cycle in {INTERVAL} seconds.", flush=True)
        time.sleep(INTERVAL)
        cycle += 1


if __name__ == "__main__":
    main()

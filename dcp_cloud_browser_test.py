import asyncio
from datetime import datetime, timezone

from playwright.async_api import async_playwright


TARGETS = [
    "https://www.dealercostparts.com/",
    "https://www.dealercostparts.com/oemparts/c/ski_doo_snowmobile/parts",
    "https://www.dealercostparts.com/oemparts/c/ski_doo_snowmobile_2025/parts",
    "https://www.dealercostparts.com/oemparts/a/ski/673e2b6adf096aca04d91830/drive-pulley",
]

CHALLENGE_MARKERS = (
    "just a moment",
    "verify you are human",
    "verification",
    "challenge-platform",
    "cf-chl-",
    "turnstile",
    "captcha",
)


async def test_page(page, url):
    print("=" * 78)
    print("requested_url :", url)

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Give normal page JavaScript a few seconds to run.
        # We do NOT click or interact with any verification.
        await page.wait_for_timeout(5000)

        final_url = page.url
        title = await page.title()
        html = await page.content()
        text = (title + "\n" + html).lower()

        status = response.status if response else None

        challenge_hits = [
            marker for marker in CHALLENGE_MARKERS if marker in text
        ]

        print("final_url     :", final_url)
        print("status        :", status)
        print("title         :", title)
        print("html_bytes    :", len(html.encode("utf-8")))
        print(
            "challenge_hits:",
            challenge_hits if challenge_hits else "none",
        )

        if challenge_hits:
            print("RESULT        : HUMAN_VERIFICATION_REQUIRED")
            return "HUMAN_VERIFICATION_REQUIRED"

        if status is not None and status >= 400:
            print("RESULT        : HTTP_BLOCKED")
            return "HTTP_BLOCKED"

        print("RESULT        : PAGE_OPENED")
        return "PAGE_OPENED"

    except Exception as exc:
        print("RESULT        : BROWSER_ERROR")
        print("error         :", type(exc).__name__, str(exc))
        return "BROWSER_ERROR"


async def main():
    print("#" * 78)
    print("DCP CLOUD BROWSER TEST")
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("Mode: read-only / no login / no CAPTCHA interaction")
    print("#" * 78)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )

        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1365, "height": 768},
        )

        page = await context.new_page()

        results = []

        for url in TARGETS:
            result = await test_page(page, url)
            results.append((url, result))

            # If Cloudflare explicitly requires human verification,
            # stop immediately. No CAPTCHA interaction or bypass.
            if result == "HUMAN_VERIFICATION_REQUIRED":
                print()
                print("STOP: interactive human verification detected.")
                print("No attempt will be made to solve or bypass it.")
                break

        print()
        print("-" * 78)
        print("SUMMARY")
        for url, result in results:
            print(f"{result:30} {url}")
        print("-" * 78)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
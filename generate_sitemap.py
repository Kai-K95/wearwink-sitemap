import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# Wir probieren 2 Shop-URLs (manchmal liefert eine davon “voller” HTML)
SHOP_URLS = [
    "https://wearwink.redbubble.com/shop",
    "https://www.redbubble.com/people/wearwink/shop?asc=u",
]

MAX_PAGES = 10
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")

DEBUG_HTML = Path("debug_page1.html")
DEBUG_PNG = Path("debug_page1.png")


def with_page(url: str, page: int) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q.setdefault("asc", "u")
    if page > 1:
        q["page"] = str(page)
    else:
        q.pop("page", None)
    return u._replace(query=urlencode(q)).geturl()


def normalize(u: str) -> str | None:
    if not u or not u.startswith("http"):
        return None
    u = u.split("#", 1)[0].split("?", 1)[0]
    if "redbubble.com" not in u:
        return None
    return u


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines += [
            "  <url>",
            f"    <loc>{u}</loc>",
            f"    <lastmod>{lastmod}</lastmod>",
            "  </url>",
        ]
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    all_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1400, "height": 900},
        )

        # kleines Stealth: webdriver=false
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        page = context.new_page()

        used_shop = None

        for base in SHOP_URLS:
            try:
                page.goto(base, wait_until="domcontentloaded", timeout=90_000)
                # Warte kurz, ob Produktlinks auftauchen
                page.wait_for_timeout(2500)
                # Wenn Links da sind, nehmen wir diese Basis-URL
                links_now = page.eval_on_selector_all(
                    'a[href*="/i/"], a[href*="/shop/ap/"]',
                    "els => els.map(e => e.href)"
                )
                if links_now and len(links_now) > 0:
                    used_shop = base
                    break
            except Exception:
                continue

        if not used_shop:
            # Debug sichern: was hat er überhaupt geladen?
            DEBUG_HTML.write_text(page.content(), encoding="utf-8")
            try:
                page.screenshot(path=str(DEBUG_PNG), full_page=True)
            except Exception:
                pass

            write_sitemap([])
            OUT_COUNT.write_text("0\n", encoding="utf-8")
            print("page 1: +0 (total 0)")
            print("✅ OK: 0 URLs")
            print("Found 0 product URLs.")
            print("DEBUG saved: debug_page1.html / debug_page1.png")
            return

        # Seiten durchgehen
        for i in range(1, MAX_PAGES + 1):
            url = with_page(used_shop, i)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)

            # Warten bis Grid evtl. nachlädt
            try:
                page.wait_for_selector('a[href*="/i/"], a[href*="/shop/ap/"]', timeout=20_000)
            except PWTimeout:
                pass

            # scroll, damit mehr Kacheln geladen werden
            for _ in range(8):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(900)

            hrefs = page.eval_on_selector_all(
                'a[href]',
                "els => els.map(e => e.href)"
            )

            before = len(all_urls)
            for h in hrefs:
                h = normalize(h)
                if not h:
                    continue
                if "/i/" in h or "/shop/ap/" in h:
                    all_urls.add(h)

            print(f"page {i}: +{len(all_urls)-before} (total {len(all_urls)})")

            if len(all_urls) == before and i >= 2:
                break

        context.close()
        browser.close()

    urls_sorted = sorted(all_urls)
    write_sitemap(urls_sorted)
    OUT_COUNT.write_text(str(len(urls_sorted)) + "\n", encoding="utf-8")
    print(f"✅ OK: {len(urls_sorted)} URLs")


if __name__ == "__main__":
    main()

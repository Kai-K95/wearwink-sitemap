import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


RB_USER = os.getenv("RB_USER", "wearwink")

SHOP_BASE_URLS = [
    f"https://{RB_USER}.redbubble.com/shop",
    f"https://www.redbubble.com/people/{RB_USER}/shop?asc=u",
]

MAX_SHOP_PAGES = int(os.getenv("MAX_SHOP_PAGES", "20"))                 # wie viele Shop-Seiten scannen
MAX_DESIGNS_TO_EXPAND = int(os.getenv("MAX_DESIGNS_TO_EXPAND", "200"))  # wie viele Designs wir besuchen
SCROLL_ROUNDS_LISTING = int(os.getenv("SCROLL_ROUNDS_LISTING", "10"))
SCROLL_ROUNDS_DESIGN = int(os.getenv("SCROLL_ROUNDS_DESIGN", "12"))
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "900"))
WAIT_FIRST_MS = int(os.getenv("WAIT_FIRST_MS", "2500"))

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS = Path("urls.txt")

DEBUG_HTML = Path("debug_page1.html")
DEBUG_PNG = Path("debug_page1.png")


def with_page(url: str, page: int) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    if "redbubble.com/people/" in url:
        q.setdefault("asc", "u")
    if page > 1:
        q["page"] = str(page)
    else:
        q.pop("page", None)
    return u._replace(query=urlencode(q)).geturl()


def normalize_url(u: str) -> str | None:
    if not u or not isinstance(u, str):
        return None
    if not u.startswith("http"):
        return None
    if "redbubble.com" not in u:
        return None
    return u.split("#", 1)[0].split("?", 1)[0]


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def try_accept_cookies(page) -> None:
    patterns = [
        r"(accept|agree|i agree|allow all)",
        r"(zustimmen|akzeptieren|alle akzeptieren|einverstanden)",
    ]
    for pat in patterns:
        try:
            page.get_by_role("button", name=re.compile(pat, re.I)).click(timeout=2500)
            break
        except Exception:
            pass


def scroll_page(page, rounds: int) -> None:
    last_h = 0
    for _ in range(rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        h = page.evaluate("document.body.scrollHeight")
        if h == last_h:
            break
        last_h = h


def pick_working_shop_url(page) -> str | None:
    for base in SHOP_BASE_URLS:
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)

            try:
                page.wait_for_selector('a[href*="/shop/ap/"], a[href*="/i/"]', timeout=12_000)
            except PWTimeout:
                pass

            scroll_page(page, 6)
            hrefs = page.eval_on_selector_all('a[href]', "els => els.map(e => e.href)")
            ok = 0
            for h in hrefs:
                h = normalize_url(h)
                if h and ("/shop/ap/" in h or "/i/" in h):
                    ok += 1
            if ok > 0:
                return base
        except Exception:
            continue
    return None


def collect_design_links_from_listing(page) -> set[str]:
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    designs: set[str] = set()
    for h in hrefs:
        h = normalize_url(h)
        if not h:
            continue
        if "/shop/ap/" in h:
            designs.add(h)
    return designs


def collect_product_links_from_design(page) -> set[str]:
    # Auf Design-Seiten tauchen oft mehrere /i/<product>/... Links auf (Mockups)
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    prods: set[str] = set()
    for h in hrefs:
        h = normalize_url(h)
        if not h:
            continue
        if "/i/" in h:
            prods.add(h)
    return prods


def main():
    design_urls: set[str] = set()
    product_urls: set[str] = set()

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
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = context.new_page()

        base = pick_working_shop_url(page)
        if not base:
            DEBUG_HTML.write_text(page.content(), encoding="utf-8")
            try:
                page.screenshot(path=str(DEBUG_PNG), full_page=True)
            except Exception:
                pass

            write_sitemap([])
            OUT_URLS.write_text("", encoding="utf-8")
            OUT_COUNT.write_text("0\n", encoding="utf-8")
            print("✅ OK: 0 URLs (no working shop url)")
            context.close()
            browser.close()
            return

        # Phase 1: Design-URLs aus dem Listing sammeln
        for i in range(1, MAX_SHOP_PAGES + 1):
            url = with_page(base, i)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)
            scroll_page(page, SCROLL_ROUNDS_LISTING)

            before = len(design_urls)
            design_urls |= collect_design_links_from_listing(page)
            added = len(design_urls) - before
            print(f"listing page {i}: +{added} designs (total {len(design_urls)})")

            if added == 0 and i >= 2:
                break

        # Phase 2: Jede Design-Seite besuchen und /i/ Produktlinks sammeln
        designs_list = sorted(list(design_urls))[:MAX_DESIGNS_TO_EXPAND]
        for idx, durl in enumerate(designs_list, start=1):
            page.goto(durl, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)
            scroll_page(page, SCROLL_ROUNDS_DESIGN)

            before = len(product_urls)
            product_urls |= collect_product_links_from_design(page)
            added = len(product_urls) - before
            print(f"design {idx}/{len(designs_list)}: +{added} product links (total {len(product_urls)})")

        context.close()
        browser.close()

    # Sitemap: sowohl Designs als auch Produktlinks (damit BlogToPin beides kann)
    all_urls = sorted(list(design_urls | product_urls))
    write_sitemap(all_urls)
    OUT_URLS.write_text("\n".join(all_urls) + ("\n" if all_urls else ""), encoding="utf-8")
    OUT_COUNT.write_text(str(len(all_urls)) + "\n", encoding="utf-8")

    print(f"✅ OK: {len(all_urls)} URLs (designs={len(design_urls)}, products={len(product_urls)})")


if __name__ == "__main__":
    main()

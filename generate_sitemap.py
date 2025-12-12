import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# =========================
# CONFIG (optional via ENV)
# =========================
RB_USER = os.getenv("RB_USER", "WearWink")  # Groß/Klein egal bei URL

# Start ist jetzt EXPLIZIT "Designs entdecken"
EXPLORE_BASE = os.getenv(
    "EXPLORE_URL",
    f"https://www.redbubble.com/de/people/{RB_USER}/explore?asc=u&sortOrder=recent&page=1",
)

MAX_EXPLORE_PAGES = int(os.getenv("MAX_EXPLORE_PAGES", "30"))           # wie viele explore Seiten prüfen
SCROLL_ROUNDS_EXPLORE = int(os.getenv("SCROLL_ROUNDS_EXPLORE", "12"))
SCROLL_ROUNDS_AP = int(os.getenv("SCROLL_ROUNDS_AP", "10"))
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "900"))
WAIT_FIRST_MS = int(os.getenv("WAIT_FIRST_MS", "2200"))

MAX_DESIGNS_TO_EXPAND = int(os.getenv("MAX_DESIGNS_TO_EXPAND", "200"))  # wie viele /shop/ap/ öffnen
MAX_PRODUCTS_PER_DESIGN = int(os.getenv("MAX_PRODUCTS_PER_DESIGN", "300"))

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS = Path("urls.txt")

DEBUG_EXPLORE_HTML = Path("debug_explore_p1.html")
DEBUG_EXPLORE_PNG = Path("debug_explore_p1.png")
DEBUG_AP1_HTML = Path("debug_ap1.html")
DEBUG_AP1_PNG = Path("debug_ap1.png")


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
    stable = 0
    for _ in range(rounds):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        h = page.evaluate("document.body.scrollHeight")
        if h == last_h:
            stable += 1
        else:
            stable = 0
            last_h = h
        if stable >= 2:
            break


def explore_page_url(base: str, page_no: int) -> str:
    u = urlparse(base)
    q = dict(parse_qsl(u.query))
    q["page"] = str(page_no)
    return u._replace(query=urlencode(q)).geturl()


def collect_ap_links(page) -> set[str]:
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    out = set()
    for h in hrefs:
        h = normalize_url(h)
        if h and "/shop/ap/" in h:
            out.add(h)
    return out


def collect_i_links(page) -> set[str]:
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    out = set()
    for h in hrefs:
        h = normalize_url(h)
        if h and "/i/" in h:
            out.add(h)
    return out


def main():
    ap_urls: set[str] = set()
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

        # =========================
        # 1) EXPLORE: alle /shop/ap/
        # =========================
        no_add_pages = 0
        for pno in range(1, MAX_EXPLORE_PAGES + 1):
            url = explore_page_url(EXPLORE_BASE, pno)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)

            # manchmal lazy-load -> etwas scrollen
            try:
                page.wait_for_selector('a[href*="/shop/ap/"]', timeout=10_000)
            except PWTimeout:
                pass
            scroll_page(page, SCROLL_ROUNDS_EXPLORE)

            if pno == 1:
                DEBUG_EXPLORE_HTML.write_text(page.content(), encoding="utf-8")
                try:
                    page.screenshot(path=str(DEBUG_EXPLORE_PNG), full_page=True)
                except Exception:
                    pass

            before = len(ap_urls)
            ap_urls |= collect_ap_links(page)
            added = len(ap_urls) - before
            print(f"explore page {pno}: +{added} designs (total {len(ap_urls)})")

            if added == 0:
                no_add_pages += 1
            else:
                no_add_pages = 0

            # wenn 2 Seiten hintereinander nichts Neues: Ende
            if pno >= 2 and no_add_pages >= 2:
                break

        # =========================================
        # 2) pro /shop/ap/ -> /i/ Produktlinks holen
        # =========================================
        ap_list = sorted(list(ap_urls))[:MAX_DESIGNS_TO_EXPAND]

        for idx, ap in enumerate(ap_list, start=1):
            page.goto(ap, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)

            # auf Designseite scrollen, damit Produktkacheln laden
            scroll_page(page, SCROLL_ROUNDS_AP)

            links = sorted(list(collect_i_links(page)))[:MAX_PRODUCTS_PER_DESIGN]
            before = len(product_urls)
            product_urls.update(links)
            added = len(product_urls) - before

            if idx == 1:
                DEBUG_AP1_HTML.write_text(page.content(), encoding="utf-8")
                try:
                    page.screenshot(path=str(DEBUG_AP1_PNG), full_page=True)
                except Exception:
                    pass

            if idx % 10 == 0 or added > 0:
                print(f"design {idx}/{len(ap_list)}: +{added} product URLs (total {len(product_urls)})")

        context.close()
        browser.close()

    # sitemap = Produktseiten (/i/) für Mockup-Bilder
    urls_sorted = sorted(product_urls)

    write_sitemap(urls_sorted)
    OUT_URLS.write_text("\n".join(urls_sorted) + ("\n" if urls_sorted else ""), encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls_sorted)) + "\n", encoding="utf-8")

    print(f"✅ OK: {len(urls_sorted)} URLs (products), designs_found={len(ap_urls)}")


if __name__ == "__main__":
    main()

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# =========================
# CONFIG (optional via ENV)
# =========================
RB_USER = os.getenv("RB_USER", "wearwink")

# Wir probieren mehrere Shop-URLs, weil RB je nach Host anders ausliefert
SHOP_BASE_URLS = [
    f"https://{RB_USER}.redbubble.com/shop",
    f"https://www.redbubble.com/people/{RB_USER}/shop?asc=u",
]

MAX_PAGES = int(os.getenv("MAX_PAGES", "20"))            # max. Shop-Seiten
SCROLL_ROUNDS = int(os.getenv("SCROLL_ROUNDS", "10"))    # wie oft scrollen pro Seite
SCROLL_PAUSE_MS = int(os.getenv("SCROLL_PAUSE_MS", "900"))
WAIT_FIRST_MS = int(os.getenv("WAIT_FIRST_MS", "2500"))

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS = Path("urls.txt")

DEBUG_HTML = Path("debug_page1.html")
DEBUG_PNG = Path("debug_page1.png")


def with_page(url: str, page: int) -> str:
    """Setzt/ändert page= in der URL (wenn page= unterstützt wird)."""
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
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u


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
    """Best-effort Cookie/Consent Klick (falls vorhanden)."""
    patterns = [
        r"(accept|agree|i agree|allow all)",
        r"(zustimmen|akzeptieren|alle akzeptieren|einverstanden)",
    ]
    for pat in patterns:
        try:
            page.get_by_role("button", name=re.compile(pat, re.I)).click(timeout=2000)
            break
        except Exception:
            pass


def pick_working_shop_url(page) -> str | None:
    """Wählt eine Shop-URL, die tatsächlich Produktlinks im DOM hat."""
    for base in SHOP_BASE_URLS:
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)

            # Versuche kurz zu warten, ob Produktlinks auftauchen
            try:
                page.wait_for_selector('a[href*="/i/"], a[href*="/shop/ap/"]', timeout=12_000)
            except PWTimeout:
                pass

            links_now = page.eval_on_selector_all(
                'a[href*="/i/"], a[href*="/shop/ap/"]',
                "els => els.map(e => e.href)"
            )
            if links_now and len(links_now) > 0:
                return base
        except Exception:
            continue

    return None


def collect_from_current_page(page) -> set[str]:
    """Scrollt und sammelt Produkt-/Design-Links aus dem DOM."""
    # Scrollen, damit mehr Kacheln laden
    last_height = 0
    for _ in range(SCROLL_ROUNDS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_PAUSE_MS)
        h = page.evaluate("document.body.scrollHeight")
        if h == last_height:
            break
        last_height = h

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    out: set[str] = set()

    for h in hrefs:
        h = normalize_url(h)
        if not h:
            continue
        if "/i/" in h or "/shop/ap/" in h:
            out.add(h)

    return out


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

        base = pick_working_shop_url(page)
        if not base:
            # Debug sichern: was wurde geladen?
            DEBUG_HTML.write_text(page.content(), encoding="utf-8")
            try:
                page.screenshot(path=str(DEBUG_PNG), full_page=True)
            except Exception:
                pass

            write_sitemap([])
            OUT_URLS.write_text("", encoding="utf-8")
            OUT_COUNT.write_text("0\n", encoding="utf-8")
            print("page 1: +0 (total 0)")
            print("✅ OK: 0 URLs")
            print("DEBUG saved: debug_page1.html / debug_page1.png")
            context.close()
            browser.close()
            return

        empty_streak = 0

        for i in range(1, MAX_PAGES + 1):
            url = with_page(base, i)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(WAIT_FIRST_MS)
            try_accept_cookies(page)

            before = len(all_urls)
            found = collect_from_current_page(page)
            all_urls |= found

            added = len(all_urls) - before
            print(f"page {i}: +{added} (total {len(all_urls)})")

            if added == 0:
                empty_streak += 1
            else:
                empty_streak = 0

            # Wenn 2 Seiten hintereinander nichts Neues bringen -> Ende
            if empty_streak >= 2 and i >= 2:
                break

        context.close()
        browser.close()

    urls_sorted = sorted(all_urls)

    # Dateien schreiben
    write_sitemap(urls_sorted)
    OUT_URLS.write_text("\n".join(urls_sorted) + ("\n" if urls_sorted else ""), encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls_sorted)) + "\n", encoding="utf-8")

    print(f"✅ OK: {len(urls_sorted)} URLs")


if __name__ == "__main__":
    main()

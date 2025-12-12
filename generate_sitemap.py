import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


SHOP_URL = "https://wearwink.redbubble.com/shop"  # dein Shop
OUT_SITEMAP = Path("sitemap.xml")
OUT_INDEX = Path("index.html")

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl

from playwright.sync_api import sync_playwright


BASE_SHOP = "https://www.redbubble.com/people/wearwink/shop"
MAX_PAGES = 20

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")


def with_page(url: str, page: int) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q["asc"] = q.get("asc", "u")
    if page > 1:
        q["page"] = str(page)
    else:
        q.pop("page", None)
    return u._replace(query=urlencode(q)).geturl()


def normalize(u: str) -> str | None:
    if not u:
        return None
    u = u.split("#", 1)[0]
    u = u.split("?", 1)[0]
    if not u.startswith("http"):
        return None
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
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()

        for i in range(1, MAX_PAGES + 1):
            url = with_page(BASE_SHOP, i)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)

            # kurz warten, damit Grid wirklich da ist
            page.wait_for_timeout(2000)

            # etwas scrollen (manche Shops laden erst dann Kacheln)
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(900)

            # Links einsammeln (Produktseiten + Designseiten)
            hrefs = page.eval_on_selector_all(
                'a[href*="redbubble.com/i/"], a[href*="/i/"], a[href*="redbubble.com/shop/ap/"], a[href*="/shop/ap/"]',
                "els => els.map(e => e.href)"
            )

            before = len(all_urls)
            for h in hrefs:
                h = normalize(h)
                if not h:
                    continue
                if ("/i/" in h) or ("/shop/ap/" in h):
                    all_urls.add(h)

            print(f"page {i}: +{len(all_urls) - before} (total {len(all_urls)})")

            # Stop, wenn eine Seite nichts Neues bringt (Shop hat nicht endlos Seiten)
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

def _is_valid_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def collect_product_links() -> list[str]:
    links: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
        )
        page = context.new_page()

        page.goto(SHOP_URL, wait_until="domcontentloaded", timeout=90_000)

        # Cookie/Consent Banner (falls vorhanden) entschärfen
        try:
            page.get_by_role("button", name=re.compile(r"(accept|agree|zustimmen|alle akzeptieren)", re.I)).click(timeout=3000)
        except Exception:
            pass

        # kurz warten, damit Grid initial lädt
        page.wait_for_timeout(2500)

        # Scrollen, bis keine neuen Items mehr kommen
        last_height = 0
        for _ in range(40):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            height = page.evaluate("document.body.scrollHeight")
            if height == last_height:
                break
            last_height = height

        # Produktseiten sind typischerweise /i/<product>/...
        hrefs = page.eval_on_selector_all(
            'a[href*="/i/"]',
            "els => els.map(e => e.href)"
        )

        for h in hrefs:
            if isinstance(h, str) and _is_valid_url(h):
                links.add(h.split("#")[0])

        context.close()
        browser.close()

    return sorted(links)


def write_sitemap(urls: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{now}</lastmod>")
        lines.append("  </url>")

    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(urls: list[str]) -> None:
    OUT_INDEX.write_text(
        f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>WearWink – Sitemap</title>
</head>
<body>
  <h1>WearWink – Sitemap</h1>
  <p>Gefundene Produkt-URLs: <strong>{len(urls)}</strong></p>
  <p><a href="sitemap.xml">➡️ sitemap.xml öffnen</a></p>
  <p>Letztes Update: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
</body>
</html>
""",
        encoding="utf-8",
    )


def main():
    urls = collect_product_links()
    write_sitemap(urls)
    write_index(urls)
    print(f"Found {len(urls)} product URLs.")


if __name__ == "__main__":
    main()

import os
import re
import time
import datetime
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

import requests


# === CONFIG ===
SHOP_URL = os.getenv("SHOP_URL", "https://www.redbubble.com/people/wearwink/shop?asc=u")
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))          # wie viele Shop-Seiten wir versuchen
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", "1")) # Pause zwischen Seiten (freundlicher)
OUTFILE = os.getenv("OUTFILE", "sitemap.xml")

# GitHub Pages Base-URL deines Repos (für robots.txt / Hinweise)
PAGES_BASE = os.getenv("PAGES_BASE", "https://kai-k95.github.io/wearwink-sitemap")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def fetch_html(url: str) -> str:
    """
    Versucht zuerst normal zu holen.
    Wenn geblockt/leer: Holt über r.jina.ai mit x-respond-with: html
    (liefert documentElement.outerHTML)  -> enthält die /i/ Links.
    """
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    try:
        r = s.get(url, timeout=30)
        if r.status_code == 200 and "/i/" in r.text:
            return r.text
    except Exception:
        pass

    # Fallback: Jina Reader (HTML Mode)
    jina_url = "https://r.jina.ai/" + url
    headers = {
        "User-Agent": UA,
        "x-respond-with": "html",          # <- wichtig: echtes HTML zurückgeben
        "x-no-cache": "true",
        "x-timeout": "30",
        # warte bis Produktlinks im DOM sind (CSS selector)
        "x-wait-for-selector": 'a[href*="/i/"]',
    }
    r2 = requests.get(jina_url, headers=headers, timeout=60)
    r2.raise_for_status()
    return r2.text


def with_page(url: str, page: int) -> str:
    """Fügt page= hinzu/ändert es (Redbubble shop akzeptiert meist page=)."""
    u = urlparse(url)
    q = parse_qs(u.query)
    q["page"] = [str(page)]
    new_query = urlencode(q, doseq=True)
    return u._replace(query=new_query).geturl()


def extract_product_urls(html: str) -> set[str]:
    """
    Holt alle Redbubble Produkt-URLs aus dem HTML.
    Produktseiten sind typischerweise /i/<product>/.../<id>....
    """
    urls = set()

    # href=".../i/..."
    for m in re.finditer(r'href="([^"]*?/i/[^"]+)"', html):
        href = m.group(1)
        urls.add(href)

    # Falls Reader Links als Markdown o.ä. liefert:
    for m in re.finditer(r"\((https?://[^)]+/i/[^)]+)\)", html):
        urls.add(m.group(1))

    # Normalize (relative -> absolute, remove fragments)
    cleaned = set()
    for u in urls:
        abs_u = urljoin("https://www.redbubble.com", u)
        abs_u = abs_u.split("#", 1)[0]
        cleaned.add(abs_u)

    # Nur echte Produktseiten behalten
    cleaned = {u for u in cleaned if "redbubble.com/i/" in u}
    return cleaned


def write_sitemap(urls: list[str], outfile: str) -> None:
    now = datetime.datetime.utcnow().date().isoformat()
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{now}</lastmod>")
        lines.append("  </url>")

    lines.append("</urlset>")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    all_urls = set()
    empty_streak = 0

    for page in range(1, MAX_PAGES + 1):
        url = with_page(SHOP_URL, page)
        html = fetch_html(url)
        found = extract_product_urls(html)

        new = found - all_urls
        all_urls |= found

        print(f"[page {page}] found={len(found)} new={len(new)} total={len(all_urls)}")

        if len(found) == 0 or len(new) == 0:
            empty_streak += 1
        else:
            empty_streak = 0

        # Wenn 2 Seiten hintereinander nichts Neues liefern -> abbrechen
        if empty_streak >= 2 and page >= 2:
            break

        time.sleep(SLEEP_SECONDS)

    urls_sorted = sorted(all_urls)
    write_sitemap(urls_sorted, OUTFILE)

    # kleine Hilfsdatei für dich/Debug
    with open("last_count.txt", "w", encoding="utf-8") as f:
        f.write(str(len(urls_sorted)) + "\n")

    print(f"✅ sitemap written: {OUTFILE} ({len(urls_sorted)} urls)")


if __name__ == "__main__":
    main()

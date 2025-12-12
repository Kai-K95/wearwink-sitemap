import re
import time
from urllib.parse import urljoin, urlencode
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.redbubble.com"
SHOP_USER = "wearwink"

MAX_PAGES = 200          # max. Seiten, die er versucht
SLEEP = 1.5              # Pause zwischen Seiten (sek)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
})

def fetch(page: int) -> str:
    params = {"asc": "u"}  # sort (wie bei dir getestet)
    if page > 1:
        params["page"] = str(page)

    url = f"{BASE}/people/{SHOP_USER}/shop?{urlencode(params)}"
    r = session.get(url, timeout=30)

    # Redbubble blockt GitHub Actions häufig (403) -> Proxy-Fallback:
    if r.status_code == 403:
        proxy_url = "https://r.jina.ai/" + url
        r = session.get(proxy_url, timeout=30)

    r.raise_for_status()
    return r.text

def normalize_rb_url(u: str) -> str | None:
    u = u.strip().strip('"').strip("'")
    if not u:
        return None

    # Voll-URL sicherstellen
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = urljoin(BASE, u)

    # Query entfernen
    u = u.split("?")[0]

    # Nur Redbubble Produktseiten behalten
    if not u.startswith("https://www.redbubble.com/"):
        return None
    if "/i/" not in u:
        return None

    return u

def extract_product_urls(html: str) -> set[str]:
    urls: set[str] = set()

    # 1) Klassisch: Links aus <a href="...">
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        nu = normalize_rb_url(a["href"])
        if nu:
            urls.add(nu)

    # 2) Robust: Produkt-URLs direkt aus dem HTML-Text ziehen (Regex)
    # (hilft, wenn Redbubble Links nicht als <a> rendert oder Proxy-HTML anders ist)
    patterns = [
        r"https?://www\.redbubble\.com/i/[^\s\"\'<>]+",
        r"//www\.redbubble\.com/i/[^\s\"\'<>]+",
        r"\/i\/[^\s\"\'<>]+",
    ]
    for pat in patterns:
        for m in re.findall(pat, html):
            nu = normalize_rb_url(m)
            if nu:
                urls.add(nu)

    return urls

def write_sitemap(urls: list[str], out_path: str = "sitemap.xml"):
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    all_urls: set[str] = set()
    prev_count = 0

    for page in range(1, MAX_PAGES + 1):
        html = fetch(page)
        found = extract_product_urls(html)
        all_urls |= found

        print(f"page {page}: +{len(found)} (total {len(all_urls)})")

        # Stop, wenn sich nichts mehr ändert
        if len(all_urls) == prev_count:
            break
        prev_count = len(all_urls)

        time.sleep(SLEEP)

    urls_sorted = sorted(all_urls)
    write_sitemap(urls_sorted, "sitemap.xml")

    with open("urls.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(urls_sorted))

    print(f"OK: {len(urls_sorted)} product URLs")

if __name__ == "__main__":
    main()

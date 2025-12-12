import re, time
from urllib.parse import urljoin, urlencode
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://www.redbubble.com"
SHOP_USER = "wearwink"
MAX_PAGES = 200
SLEEP = 1.5

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
})

def fetch(page: int) -> str:
    params = {"asc": "u"}
    if page > 1:
        params["page"] = str(page)
    url = f"{BASE}/people/{SHOP_USER}/shop?{urlencode(params)}"

    r = session.get(url, timeout=30)

    # Redbubble blockt oft GitHub Actions (403). Dann über Proxy laden:
    if r.status_code == 403:
        proxy_url = "https://r.jina.ai/" + url
        r = session.get(proxy_url, timeout=30)

    r.raise_for_status()
    return r.text

def extract_product_urls(html: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        abs_url = urljoin(BASE, href)
        if re.search(r"redbubble\.com\/i\/", abs_url):
            urls.add(abs_url.split("?")[0])
    return urls

def write_sitemap(urls: list[str], out_path: str = "sitemap.xml"):
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    all_urls = set()
    prev_count = 0

    for page in range(1, MAX_PAGES + 1):
        html = fetch(page)
        all_urls |= extract_product_urls(html)

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


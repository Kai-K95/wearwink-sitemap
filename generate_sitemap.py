import os
import re
import sys
import time
import random
import datetime
from pathlib import Path
from typing import Iterable, List, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
ARTIST = "WearWink"
BASE = "https://www.redbubble.com"  # bewusst OHNE /de/

# Quelle für Design-IDs (liefert /shop/ap/<id>)
EXPLORE_URL_TEMPLATE = BASE + f"/people/{ARTIST}/explore?asc=u&page={{page}}&sortOrder=recent"

# Aus jedem Design (/shop/ap/<id>) holen wir ALLE Produktlinks (/i/<type>/.../<id>.<code>)
DESIGN_PAGE_TEMPLATE = BASE + "/shop/ap/{id}"

# Sicherheitslimits (damit GitHub Actions nicht ewig läuft)
MAX_EXPLORE_PAGES = 25        # reicht meist; bei Bedarf höher
MAX_DESIGNS = 0               # 0 = alle gefundenen; sonst z.B. 200
SLEEP_BETWEEN_REQUESTS = 0.8  # höflich + weniger Block-Risiko

# Rotation/Limits für BlogToPin
ENABLE_ROTATION = True
ROTATION_MODE = "daily_random"     # "off" | "daily_random" | "newest_only"
MAX_URLS_IN_SITEMAP = 2500         # 0 = keine Begrenzung

# Output
OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
OUT_DESIGNS = Path("design_ids.txt")

# =========================
# HTTP
# =========================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def is_cloudflare_challenge(html: str) -> bool:
    h = html.lower()
    return (
        "confirm you are human" in h
        or "cloudflare" in h and "verify" in h
        or "checking your browser" in h
        or "attention required" in h
    )


def fetch(url: str, retries: int = 3, timeout: int = 30) -> Optional[str]:
    for attempt in range(1, retries + 1):
        try:
            r = SESSION.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code >= 400:
                # 403/429 sind typisch bei Bot-Block; wir versuchen Retry
                time.sleep(1.5 * attempt)
                continue
            text = r.text or ""
            if is_cloudflare_challenge(text):
                return None
            return text
        except Exception:
            time.sleep(1.5 * attempt)
    return None


# =========================
# PARSING
# =========================
DESIGN_ID_RE = re.compile(r"/shop/ap/(\d+)")
PRODUCT_URL_RE = re.compile(r"^https?://www\.redbubble\.com/(?:de/)?i/[^/]+/.+/\d+\.[A-Z0-9]+", re.IGNORECASE)


def normalize_url(u: str) -> str:
    # Entfernt /de/ falls vorhanden
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("http://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("http://www.redbubble.com/", "https://www.redbubble.com/")
    return u


def extract_design_ids_from_explore(html: str) -> List[str]:
    # /shop/ap/<id> kommt im HTML vor
    ids = DESIGN_ID_RE.findall(html)
    # dedupe, aber Reihenfolge behalten (newest-first ist hilfreich)
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def extract_product_urls_from_design(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = BASE + href
        href = normalize_url(href)

        if PRODUCT_URL_RE.match(href):
            urls.add(href)

    return urls


# =========================
# SITEMAP
# =========================
def write_sitemap(urls: List[str]) -> None:
    # simples XML; reicht für BlogToPin + Tools
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stable_daily_sample(urls: List[str], k: int) -> List[str]:
    if k <= 0 or k >= len(urls):
        return urls
    # stabil pro Tag, damit es nicht bei jedem Run komplett anders ist
    seed = int(datetime.datetime.utcnow().strftime("%Y%m%d"))
    rng = random.Random(seed)
    return rng.sample(urls, k)


# =========================
# MAIN
# =========================
def load_previous_design_ids() -> List[str]:
    if OUT_DESIGNS.exists():
        return [x.strip() for x in OUT_DESIGNS.read_text(encoding="utf-8").splitlines() if x.strip().isdigit()]
    return []


def save_design_ids(ids: List[str]) -> None:
    OUT_DESIGNS.write_text("\n".join(ids) + "\n", encoding="utf-8")


def main() -> int:
    all_design_ids: List[str] = []
    total_new = 0

    print("== Collecting design IDs from Explore ==")
    for page in range(1, MAX_EXPLORE_PAGES + 1):
        url = EXPLORE_URL_TEMPLATE.format(page=page)
        html = fetch(url)
        if not html:
            print(f"explore page {page}: BLOCKED/EMPTY")
            break

        ids = extract_design_ids_from_explore(html)
        if not ids:
            print(f"explore page {page}: +0 designs (total {len(all_design_ids)})")
            break

        before = len(all_design_ids)
        for i in ids:
            if i not in all_design_ids:
                all_design_ids.append(i)
        total_new = len(all_design_ids) - before
        print(f"explore page {page}: +{total_new} designs (total {len(all_design_ids)})")

        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # Wenn die Seite nichts neues bringt, abbrechen
        if total_new == 0:
            break

    # Fallback: wenn RB blockt, nimm die letzte bekannte Liste
    if len(all_design_ids) == 0:
        prev = load_previous_design_ids()
        if prev:
            print(f"⚠️ Explore blocked. Using cached design_ids.txt ({len(prev)} IDs).")
            all_design_ids = prev
        else:
            print("❌ No design IDs found and no cache available. Not updating outputs.")
            return 0

    # ggf. limit Designs
    if MAX_DESIGNS and MAX_DESIGNS > 0:
        all_design_ids = all_design_ids[:MAX_DESIGNS]

    # speichern (Cache)
    save_design_ids(all_design_ids)

    print(f"\n== Fetching product URLs from {len(all_design_ids)} design pages ==")
    all_product_urls: Set[str] = set()

    for idx, did in enumerate(all_design_ids, start=1):
        durl = DESIGN_PAGE_TEMPLATE.format(id=did)
        html = fetch(durl)
        if not html:
            print(f"[{idx}/{len(all_design_ids)}] design {did}: BLOCKED/EMPTY")
            continue

        urls = extract_product_urls_from_design(html)
        all_product_urls.update(urls)
        print(f"[{idx}/{len(all_design_ids)}] design {did}: +{len(urls)} products (total {len(all_product_urls)})")

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    urls_sorted = sorted(all_product_urls)

    # Wenn 0 (z.B. plötzlich block), NICHT überschreiben (damit last_count nicht 0 wird)
    if len(urls_sorted) == 0:
        print("⚠️ Collected 0 product URLs. Keeping existing sitemap/last_count unchanged.")
        return 0

    # Rotation / Limit
    if MAX_URLS_IN_SITEMAP and MAX_URLS_IN_SITEMAP > 0:
        if ENABLE_ROTATION and ROTATION_MODE == "daily_random":
            urls_sorted = stable_daily_sample(urls_sorted, MAX_URLS_IN_SITEMAP)
        elif ENABLE_ROTATION and ROTATION_MODE == "newest_only":
            # newest_only ist schwierig ohne Metadaten; wir nehmen einfach die ersten N der sortierten Liste nicht.
            # -> daher: keine besondere Logik, nimm die ersten N (stabil).
            urls_sorted = urls_sorted[:MAX_URLS_IN_SITEMAP]
        else:
            urls_sorted = urls_sorted[:MAX_URLS_IN_SITEMAP]

    urls_sorted = sorted(urls_sorted)

    write_sitemap(urls_sorted)
    OUT_URLS.write_text("\n".join(urls_sorted) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls_sorted)) + "\n", encoding="utf-8")

    print(f"\n✅ OK: {len(urls_sorted)} product URLs written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

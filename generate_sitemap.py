from __future__ import annotations

import json
import re
import time
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Set, Tuple

import requests
from bs4 import BeautifulSoup
from html import escape as xml_escape

# =========================
# CONFIG
# =========================
USER = "WearWink"
BASE = "https://www.redbubble.com"

# Deine iaCodes (wie von dir gepostet)
IA_CODES: List[str] = [
    "w-dresses",
    "u-sweatshirts",
    "u-tees",
    "u-tanks",
    "u-case-iphone",
    "u-case-samsung",
    "all-stickers",
    "u-print-board-gallery",
    "u-print-art",
    "u-print-canvas",
    "u-print-frame",
    "u-print-photo",
    "u-print-poster",
    "u-block-acrylic",
    "u-apron",
    "u-bath-mat",
    "u-bedding",
    "u-clock",
    "u-coasters",
    "u-die-cut-magnet",
    "u-mugs",
    "u-pillows",
    "u-shower-curtain",
    "u-print-tapestry",
    "u-card-greeting",
    "u-notebook-hardcover",
    "all-mouse-pads",
    "u-card-post",
    "u-notebook-spiral",
    "u-backpack",
    "u-bag-drawstring",
    "u-duffle-bag",
    "all-hats",
    "u-pin-button",
    "w-scarf",
    "u-tech-accessories",
    "all-totes",
    "u-bag-studiopouch",
]

# Wie viele URLs insgesamt in die sitemap (BlogToPin Plan)
MAX_URLS_TOTAL = 2000

# Pro Run: wie viele "Listing-Seiten" pro Kategorie abklappern (klein halten -> weniger Cloudflare Risiko)
CRAWL_PAGES_PER_CATEGORY = 1

# Wie tief maximal scannen wir (Page-Parameter). Reicht, weil wir über Tage rotieren.
MAX_LISTING_PAGE_SCAN = 250

# Pool pro Kategorie (Cache) begrenzen, damit Repo klein bleibt
POOL_CAP_PER_CATEGORY = 8000

# Output-Dateien
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS_TXT = Path("urls.txt")
OUT_POOL = Path("urls_pool.json")

# =========================
# Helpers
# =========================

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

SESSION_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",  # wichtig: keine /de/ Pfade provozieren
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

PRODUCT_ABS_RE = re.compile(r"https?://www\.redbubble\.com/(?:[a-z]{2}/)?i/[^\"\'<>\s]+", re.IGNORECASE)
PRODUCT_REL_RE = re.compile(r'href="(/(?:[a-z]{2}/)?i/[^"]+)"', re.IGNORECASE)

CLOUDFLARE_MARKERS = [
    "Confirm you are human",
    "Überprüfen Sie, dass Sie ein Mensch sind",
    "cloudflare",
    "Checking your browser",
]


def day_seed() -> int:
    # stabil pro Tag
    return int(date.today().strftime("%Y%m%d"))


def stable_hash_int(s: str) -> int:
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def normalize_url(u: str) -> str:
    """
    - force https
    - remove locale '/de/' etc. (nur www.redbubble.com/...)
    - strip fragments
    """
    u = u.strip()
    u = u.replace("http://www.redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("http://redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("https://redbubble.com/", "https://www.redbubble.com/")

    # Locale entfernen (z.B. /de/)
    u = re.sub(r"^https://www\.redbubble\.com/[a-z]{2}/", "https://www.redbubble.com/", u, flags=re.IGNORECASE)

    # Fragment weg
    if "#" in u:
        u = u.split("#", 1)[0]

    return u


def build_listing_url(page: int, ia_code: str) -> str:
    # Diese URL-Struktur hast du auch im Screenshot/Tests genutzt
    # (artistUserName + asc + sortOrder + iaCode + page)
    return (
        f"{BASE}/people/{USER}/shop"
        f"?artistUserName={USER}"
        f"&asc=u"
        f"&sortOrder=recent"
        f"&page={page}"
        f"&iaCode={ia_code}"
    )


def looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(m.lower() in low for m in CLOUDFLARE_MARKERS)


def fetch(url: str, session: requests.Session, timeout: int = 25) -> str | None:
    try:
        r = session.get(url, headers=SESSION_HEADERS, timeout=timeout, allow_redirects=True)
        # 403/429 sind typisch bei Bot-Block
        if r.status_code in (403, 429):
            return None
        text = r.text or ""
        if looks_blocked(text):
            return None
        return text
    except Exception:
        return None


def extract_product_urls(html: str) -> Set[str]:
    """
    Holt echte Produktseiten:
      https://www.redbubble.com/i/<type>/<slug>/<id>.<code>
    """
    urls: Set[str] = set()

    # absolute
    for m in PRODUCT_ABS_RE.findall(html):
        urls.add(normalize_url(m))

    # relative (falls vorhanden)
    for m in PRODUCT_REL_RE.findall(html):
        rel = m
        if rel.startswith("/"):
            urls.add(normalize_url("https://www.redbubble.com" + rel))

    # zusätzlich via BeautifulSoup (falls href anders formatiert ist)
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "/i/" in href:
            if href.startswith("http"):
                urls.add(normalize_url(href))
            elif href.startswith("/"):
                urls.add(normalize_url("https://www.redbubble.com" + href))

    # Nur echte /i/ behalten
    urls = {u for u in urls if "/i/" in u}

    return urls


def load_pool() -> Dict[str, List[str]]:
    if OUT_POOL.exists():
        try:
            data = json.loads(OUT_POOL.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out: Dict[str, List[str]] = {}
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, list):
                        out[k] = [normalize_url(x) for x in v if isinstance(x, str)]
                return out
        except Exception:
            pass
    return {ia: [] for ia in IA_CODES}


def save_pool(pool: Dict[str, List[str]]) -> None:
    OUT_POOL.write_text(json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rotate_slice(items: List[str], take: int, seed: int, salt: str) -> List[str]:
    if not items or take <= 0:
        return []
    n = len(items)
    if n <= take:
        return items[:]

    # Rotation ohne Random-Import: Offset hängt von Tag + Kategorie ab
    offset = (seed + stable_hash_int(salt)) % n
    out = []
    for i in range(take):
        out.append(items[(offset + i) % n])
    return out


def write_sitemap(urls: List[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        # XML braucht Escaping, vor allem für & in Querystrings
        loc = xml_escape(u, quote=True)
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    seed = day_seed()

    pool = load_pool()
    for ia in IA_CODES:
        pool.setdefault(ia, [])

    session = requests.Session()

    print("== Crawling listing pages (low) ==")
    scanned = 0
    blocked = 0
    newly_found_total = 0

    # Pro Kategorie rotieren wir, welche Listing-Page wir heute abholen
    # (so kriegst du über Tage/Wochen auch ältere Designs rein)
    for ia in IA_CODES:
        # Startpage hängt von Kategorie + Tag ab
        start_page = 1 + ((seed + stable_hash_int(ia)) % MAX_LISTING_PAGE_SCAN)

        for step in range(CRAWL_PAGES_PER_CATEGORY):
            page = start_page + step
            url = build_listing_url(page, ia)
            html = fetch(url, session=session)
            scanned += 1

            if not html:
                blocked += 1
                print(f"BLOCKED/EMPTY: {ia} page={page}")
                continue

            urls = extract_product_urls(html)
            if not urls:
                # nicht gleich als "blocked" zählen – kann auch echte leere Seite sein
                print(f"EMPTY: {ia} page={page}")
                continue

            before = set(pool[ia])
            merged = list(before.union(urls))

            # Stabil sortieren (damit Git-Diffs sauber bleiben)
            merged = sorted(set(merged))

            # Cap
            if len(merged) > POOL_CAP_PER_CATEGORY:
                # Neueste behalten: wir nehmen die letzten N nach stabiler Sortierung
                merged = merged[-POOL_CAP_PER_CATEGORY :]

            pool[ia] = merged
            newly = len(set(merged) - before)
            newly_found_total += newly
            print(f"{ia} page={page}: +{newly} (pool={len(pool[ia])})")

            # kleine Pause (hilft minimal gegen Rate Limits)
            time.sleep(0.6)

    print("== Picking rotating URLs per category ==")
    categories = len(IA_CODES)
    base_quota = MAX_URLS_TOTAL // categories
    remainder = MAX_URLS_TOTAL % categories

    picked: List[str] = []
    for idx, ia in enumerate(IA_CODES):
        quota = base_quota + (1 if idx < remainder else 0)
        items = pool.get(ia, [])
        # pro Kategorie stabil rotieren
        chosen = rotate_slice(items, quota, seed=seed, salt=ia)
        picked.extend(chosen)

    # global dedupe (falls ein Produkt in mehreren Kategorien auftaucht)
    picked = sorted(set(picked))

    if not picked:
        # WICHTIG: wenn Redbubble heute komplett blockt UND Pool leer ist,
        # NICHT sitemap.xml überschreiben (sonst wird sie leer).
        if OUT_SITEMAP.exists():
            print("! Picked is 0. Keeping existing sitemap.xml/last_count.txt unchanged.")
            return

    write_sitemap(picked)
    OUT_COUNT.write_text(str(len(picked)) + "\n", encoding="utf-8")
    OUT_URLS_TXT.write_text("\n".join(picked) + "\n", encoding="utf-8")
    save_pool(pool)

    print(f"OK: wrote {len(picked)} URLs")
    print(f"categories={categories} | base_quota={base_quota} | remainder={remainder}")
    print(f"scanned={scanned} | blocked/empty={blocked} | newly_found_total={newly_found_total}")


if __name__ == "__main__":
    main()

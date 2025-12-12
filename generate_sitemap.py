from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Set, Tuple

import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
ARTIST = "WearWink"

# Deine Kategorien (iaCode)
IA_CODES = [
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

# Wie viele Listing-Seiten pro Kategorie wir pro Run anfassen (klein halten -> weniger Block)
PAGES_PER_CATEGORY_POOL = 1

# Wie viele URLs total in die Sitemap sollen (Rotation)
TOTAL_DAILY_LIMIT = 2000

# Wartezeit zwischen Requests (klein aber hilft)
SLEEP_MIN = 0.8
SLEEP_MAX = 1.8

# Output Files
OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")

CACHE_POOL = Path("cache_by_category.json")   # iaCode -> [product_url...]
CACHE_USED = Path("state_used.json")          # iaCode -> [already_used_url...]

DEBUG_LISTING_HTML = Path("debug_listing.html")

BASE = "https://www.redbubble.com"
LISTING_BASE = f"{BASE}/people/{ARTIST}/shop"

# =========================
# Helpers
# =========================

def now_lastmod() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def xml_escape_loc(u: str) -> str:
    # Wichtig: & muss in XML escaped werden, sonst bekommst du genau den Browser-Fehler "EntityRef expecting ;"
    return (
        u.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )

def is_cloudflare_block(html: str) -> bool:
    h = html.lower()
    # typische Marker
    return (
        "cloudflare" in h
        or "verify you are human" in h
        or "confirm you are human" in h
        or "checking your browser" in h
        or "cdn-cgi" in h
        or "ray id" in h
        or "muss die sicherheit ihrer verbindung" in h
        or "bestätigen sie, dass sie ein mensch sind" in h
    )

def build_listing_url(ia_code: str, page: int) -> str:
    # Wichtig: keine /de/ URLs, nur www.redbubble.com
    # sortOrder=recent damit du immer "neu" zuerst bekommst
    return (
        f"{LISTING_BASE}"
        f"?artistUserName={ARTIST}"
        f"&asc=u"
        f"&sortOrder=recent"
        f"&page={page}"
        f"&iaCode={ia_code}"
    )

def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch(url: str) -> Tuple[str | None, int]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=30)
        return r.text, r.status_code
    except Exception:
        return None, 0

PRODUCT_RE = re.compile(r"^https?://www\.redbubble\.com/i/[^?#]+", re.IGNORECASE)

def extract_product_urls(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = BASE + href
        if PRODUCT_RE.match(href):
            # normalize: https + without fragments
            href = href.split("#", 1)[0]
            out.add(href)
    return out

def compute_quota(categories: List[str], total_limit: int) -> Dict[str, int]:
    n = len(categories)
    base = total_limit // n
    rem = total_limit % n
    # stabile Verteilung: erste rem Kategorien bekommen +1
    cats_sorted = sorted(categories)
    q = {}
    for i, c in enumerate(cats_sorted):
        q[c] = base + (1 if i < rem else 0)
    return q

def pick_rotating(pool: List[str], used: Set[str], k: int, seed: int) -> Tuple[List[str], Set[str]]:
    # nur URLs, die noch nicht benutzt wurden
    candidates = [u for u in pool if u not in used]

    rnd = random.Random(seed)
    rnd.shuffle(candidates)

    picked = candidates[:k]

    # wenn nicht genug da: reset used (Rotation neu starten), dann nochmal versuchen
    if len(picked) < k:
        used = set()  # reset
        candidates = list(pool)
        rnd.shuffle(candidates)
        picked = candidates[:k]

    used.update(picked)
    return picked, used

def write_sitemap(urls: List[str]) -> None:
    lastmod = now_lastmod()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape_loc(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_outputs(urls: List[str]) -> None:
    # urls.txt ist optional für dich, aber praktisch zum Debuggen
    OUT_URLS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    write_sitemap(urls)

def main() -> None:
    # Cache laden
    pool_by_cat: Dict[str, List[str]] = load_json(CACHE_POOL, {})
    used_by_cat: Dict[str, List[str]] = load_json(CACHE_USED, {})

    # sicherstellen, dass alle Kategorien existieren
    for ia in IA_CODES:
        pool_by_cat.setdefault(ia, [])
        used_by_cat.setdefault(ia, [])

    print("== Crawling listing pages (low) ==")
    scanned = 0
    blocked = 0
    newly_found = 0

    first_debug_saved = False

    for ia in IA_CODES:
        for page in range(1, PAGES_PER_CATEGORY_POOL + 1):
            url = build_listing_url(ia, page)
            scanned += 1

            html, status = fetch(url)
            if html is None:
                print(f"BLOCKED/EMPTY: {ia} page={page} (no response)")
                blocked += 1
                continue

            if is_cloudflare_block(html) or status in (403, 429):
                print(f"BLOCKED/EMPTY: {ia} page={page}")
                blocked += 1
                if not first_debug_saved:
                    DEBUG_LISTING_HTML.write_text(html, encoding="utf-8")
                    first_debug_saved = True
                continue

            urls = extract_product_urls(html)

            if not urls:
                print(f"EMPTY: {ia} page={page}")
                continue

            before = set(pool_by_cat[ia])
            merged = list(before.union(urls))
            pool_by_cat[ia] = merged
            add = len(set(merged) - before)
            newly_found += add

            print(f"OK: {ia} page={page} +{add} (pool={len(pool_by_cat[ia])})")

            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    total_pool = sum(len(v) for v in pool_by_cat.values())

    print("\n== Picking rotating URLs per category ==")
    print(f"Scanned listing pages: {scanned}")
    print(f"Blocked/empty pages: {blocked}")
    print(f"Newly found total: {newly_found}")
    print(f"Total pool size: {total_pool}")

    # WICHTIG: Wenn wir heute komplett geblockt sind UND der Pool leer ist,
    # DANN NICHT outputs auf 0 überschreiben -> alte files bleiben.
    if total_pool == 0:
        if OUT_SITEMAP.exists() and OUT_COUNT.exists():
            print("❗ Pool is 0. Keeping existing sitemap.xml/last_count.txt unchanged.")
            # trotzdem Cache speichern (leer/alt)
            save_json(CACHE_POOL, pool_by_cat)
            save_json(CACHE_USED, used_by_cat)
            return
        else:
            print("❌ Pool is 0 and no previous outputs exist. Nothing to write.")
            # Cache speichern
            save_json(CACHE_POOL, pool_by_cat)
            save_json(CACHE_USED, used_by_cat)
            return

    # Cache/State speichern (damit Rotation täglich funktioniert)
    save_json(CACHE_POOL, pool_by_cat)

    # Quoten: 2000 / Anzahl Kategorien
    quota = compute_quota(IA_CODES, TOTAL_DAILY_LIMIT)
    base = TOTAL_DAILY_LIMIT // len(IA_CODES)
    rem = TOTAL_DAILY_LIMIT % len(IA_CODES)
    print(f"Quota: base={base}, remainder(+1)={rem}, categories={len(IA_CODES)}")

    # täglicher Seed: rotiert automatisch pro Tag, aber stabil innerhalb eines Tages
    seed = int(date.today().strftime("%Y%m%d"))

    final_urls: List[str] = []

    for ia in sorted(IA_CODES):
        pool = pool_by_cat.get(ia, [])
        if not pool:
            continue

        used_set = set(used_by_cat.get(ia, []))
        k = quota[ia]

        picked, new_used = pick_rotating(pool, used_set, k, seed + hash(ia) % 100000)
        used_by_cat[ia] = list(new_used)

        final_urls.extend(picked)

    # used speichern
    save_json(CACHE_USED, used_by_cat)

    # dedupe final (nur falls)
    final_urls = list(dict.fromkeys(final_urls))

    write_outputs(final_urls)
    print(f"✅ OK: wrote {len(final_urls)} product URLs")

if __name__ == "__main__":
    main()

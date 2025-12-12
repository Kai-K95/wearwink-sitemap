from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from math import gcd
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from xml.sax.saxutils import escape as xml_escape

import requests

# =========================
# CONFIG
# =========================
USERNAME = "WearWink"
BASE = "https://www.redbubble.com"

# Wie viele "Design entdecken"-Seiten versuchen (newest-first). Klein halten -> weniger Block-Risiko.
DISCOVER_PAGES = 5

# Pro Run maximal so viele Produkt-URLs in die Sitemap
MAX_URLS_TOTAL = 2000

# Requests
TIMEOUT = 25
SLEEP_BETWEEN_REQUESTS = 0.6  # sanft bleiben
RETRIES = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",  # verhindert /de/
}

# =========================
# FILES
# =========================
OUT_SITEMAP = Path("sitemap.xml")
OUT_LAST_COUNT = Path("last_count.txt")

CACHE_DESIGN_IDS = Path("design_ids.txt")         # persistente Liste Design-IDs
CACHE_POOL_URLS = Path("urls_pool.txt")           # persistente Produkt-URL-Pool (optional Debug)
ROTATION_STATE = Path("rotation_state.json")      # pro Kategorie Index-Stand

# =========================
# REGEX
# =========================
# Design-Seiten:
#   https://www.redbubble.com/shop/ap/176728075
DESIGN_ID_RE = re.compile(r"/shop/ap/(\d+)", re.IGNORECASE)

# Produktseiten:
#   https://www.redbubble.com/i/hoodie/.../176728003.6N9P2
#   https://www.redbubble.com/de/i/hoodie/...   -> wird normalisiert
PRODUCT_URL_RE = re.compile(
    r"https?://www\.redbubble\.com/(?:[a-z]{2}/)?i/[^\"<>\s]+",
    re.IGNORECASE,
)

# =========================
# HELPERS
# =========================
def normalize_url(u: str) -> str:
    u = u.strip()
    # immer https
    u = u.replace("http://", "https://")
    # Sprache raus
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("https://www.redbubble.com/en/", "https://www.redbubble.com/")
    # manchmal doppelte Slashes
    u = re.sub(r"^https://www\.redbubble\.com//+", "https://www.redbubble.com/", u)
    return u

def is_cloudflare_or_bot_page(html: str) -> bool:
    h = html.lower()
    # typische Marker (de/en)
    return (
        "cloudflare" in h
        or "cf-ray" in h
        or "attention required" in h
        or "checking your browser" in h
        or "bestätigen sie, dass sie ein mensch sind" in h
        or "captcha" in h
    )

def fetch(url: str) -> Optional[str]:
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code in (403, 429):
                return None
            r.raise_for_status()
            text = r.text
            if is_cloudflare_or_bot_page(text):
                return None
            return text
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return None

def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]

def write_lines(path: Path, lines: List[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def category_from_product_url(url: str) -> str:
    # /i/<type>/...
    try:
        parts = url.split("://", 1)[1].split("/", 4)  # www.redbubble.com, i, type, ...
        # parts: ["www.redbubble.com", "i", "<type>", ...]
        if len(parts) >= 3 and parts[1] == "i":
            return parts[2].lower()
    except Exception:
        pass
    return "other"

def load_rotation_state() -> Dict[str, int]:
    if ROTATION_STATE.exists():
        try:
            return json.loads(ROTATION_STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_rotation_state(state: Dict[str, int]) -> None:
    ROTATION_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def coprime_step(cat: str, n: int) -> int:
    # Schrittgröße so wählen, dass gcd(step, n)==1 (damit wir alle URLs einmal durchlaufen)
    if n <= 1:
        return 1
    base = abs(hash(cat)) % (n - 1) + 1
    step = base
    while gcd(step, n) != 1:
        step += 1
        if step >= n:
            step = 1
    return step

# =========================
# DESIGN ID DISCOVERY
# =========================
def discover_design_ids() -> Tuple[List[str], bool]:
    """
    Returns (ids_newest_first, success_fetch)
    success_fetch = False wenn wir gleich am Anfang geblockt sind.
    """
    found: List[str] = []
    seen: Set[str] = set()

    for page in range(1, DISCOVER_PAGES + 1):
        url = f"{BASE}/people/{USERNAME}/explore?asc=u&page={page}&sortOrder=recent"
        html = fetch(url)
        if html is None:
            # beim ersten Page-Call schon None -> sehr wahrscheinlich geblockt
            return ([], False)

        ids = DESIGN_ID_RE.findall(html)
        page_new = 0
        for i in ids:
            if i not in seen:
                seen.add(i)
                found.append(i)
                page_new += 1

        print(f"discover page {page}: +{page_new} ids (total {len(found)})")
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # wenn auf einer Seite gar nichts kommt, abbrechen
        if page_new == 0:
            break

    return (found, True)

def update_design_id_cache() -> List[str]:
    """
    - Versucht neue IDs zu holen (newest first)
    - Wenn geblockt: nutzt vorhandenen Cache
    """
    cached = read_lines(CACHE_DESIGN_IDS)
    cached_set = set(cached)

    new_ids, ok = discover_design_ids()
    if not ok:
        print("⚠️  Design-ID discovery BLOCKED. Using cached design_ids.txt")
        return cached

    # new_ids ist newest-first; wir wollen Cache: newest-first, ohne Duplikate
    merged: List[str] = []
    for i in new_ids:
        if i not in merged:
            merged.append(i)
    for i in cached:
        if i not in set(merged):
            merged.append(i)

    write_lines(CACHE_DESIGN_IDS, merged)
    print(f"✅ design_ids.txt updated: {len(merged)} ids (new found: {len(new_ids)})")
    return merged

# =========================
# PRODUCT URL COLLECTION
# =========================
def extract_product_urls_from_design_page(html: str) -> Set[str]:
    urls = set()
    for m in PRODUCT_URL_RE.findall(html):
        urls.add(normalize_url(m))
    return urls

def collect_product_pool(design_ids: List[str], max_design_pages: int = 120) -> Dict[str, List[str]]:
    """
    Holt Produkt-URLs aus /shop/ap/<id>.
    max_design_pages begrenzt Requests pro Run.
    """
    pool_by_cat: Dict[str, List[str]] = defaultdict(list)
    total_urls: Set[str] = set()

    # newest-first -> zuerst neueste Designs scannen
    scan_ids = design_ids[:max_design_pages]
    print(f"== Scanning design pages: {len(scan_ids)} ==")

    for idx, did in enumerate(scan_ids, start=1):
        url = f"{BASE}/shop/ap/{did}"
        html = fetch(url)
        if html is None:
            print(f"BLOCKED/EMPTY: ap/{did}")
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        urls = extract_product_urls_from_design_page(html)
        added = 0
        for u in urls:
            if u not in total_urls:
                total_urls.add(u)
                cat = category_from_product_url(u)
                pool_by_cat[cat].append(u)
                added += 1

        print(f"ap/{did}: +{added} (pool total {len(total_urls)})")
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        # safety: wenn Pool schon sehr groß, nicht weiter
        if len(total_urls) >= 50000:
            break

    # stabile Sortierung je Kategorie
    for cat in list(pool_by_cat.keys()):
        pool_by_cat[cat] = sorted(set(pool_by_cat[cat]))

    # optional: pool dump
    all_sorted = sorted(total_urls)
    write_lines(CACHE_POOL_URLS, all_sorted)

    print(f"✅ pool categories: {len(pool_by_cat)} | pool size: {len(all_sorted)}")
    return pool_by_cat

# =========================
# ROTATION PICK
# =========================
def pick_rotating_urls(pool_by_cat: Dict[str, List[str]], max_total: int) -> List[str]:
    cats = sorted([c for c in pool_by_cat.keys() if pool_by_cat[c]])
    if not cats:
        return []

    # Ziel: max_total gleichmäßig auf Kategorien verteilen
    n_cats = len(cats)
    base = max_total // n_cats
    rem = max_total % n_cats

    state = load_rotation_state()
    chosen: List[str] = []

    print(f"== Rotation == categories={n_cats} | base={base} | remainder={rem}")

    for i, cat in enumerate(cats):
        urls = pool_by_cat[cat]
        n = len(urls)
        if n == 0:
            continue

        quota = base + (1 if i < rem else 0)
        quota = min(quota, n)  # nicht mehr als vorhanden

        idx = int(state.get(cat, 0)) % n
        step = coprime_step(cat, n)

        picked_here = 0
        for _ in range(quota):
            idx = (idx + step) % n
            chosen.append(urls[idx])
            picked_here += 1

        state[cat] = idx
        print(f"{cat}: picked {picked_here} of {n}")

    save_rotation_state(state)
    # global dedupe + cap (falls irgendeine Kategorie doppelt war)
    out = []
    seen = set()
    for u in chosen:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= max_total:
            break
    return out

# =========================
# SITEMAP
# =========================
def write_sitemap(urls: List[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        # XML muss & als &amp; schreiben
        loc = xml_escape(u, entities={"'": "&apos;", '"': "&quot;"})
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> int:
    # 1) Design IDs aktualisieren (oder Cache nehmen)
    design_ids = update_design_id_cache()
    if not design_ids:
        print("❌ No design IDs available (cache empty + blocked).")
        return 1

    # 2) Produktpool sammeln
    pool_by_cat = collect_product_pool(design_ids, max_design_pages=140)

    # Wenn wir komplett geblockt sind, NICHT sitemap leeren.
    total_pool = sum(len(v) for v in pool_by_cat.values())
    if total_pool == 0:
        print("⚠️ Pool is 0 (blocked). Keeping existing sitemap.xml/last_count.txt unchanged.")
        return 0

    # 3) Rotierende Auswahl ziehen
    picked = pick_rotating_urls(pool_by_cat, MAX_URLS_TOTAL)

    # 4) Outputs schreiben
    write_sitemap(picked)
    OUT_LAST_COUNT.write_text(str(len(picked)) + "\n", encoding="utf-8")

    print(f"✅ OK: wrote {len(picked)} URLs")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

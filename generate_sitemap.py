import re
import time
import json
import random
from pathlib import Path
from datetime import datetime, timezone

import requests


# =========================
# SETTINGS (hier anpassen)
# =========================
USERNAME = "WearWink"
BASE = "https://www.redbubble.com"

# WIE VIEL CRAWLEN? (wenig!)
PAGES_PER_CATEGORY_SCAN = 1  # 1 = sehr wenig / stabil. (optional 2)

# WIE VIELE URLs SOLLEN IN DIE SITEMAP?
MAX_SITEMAP_URLS = 2000

# Optional: Pool-Limit pro Kategorie (damit repo klein bleibt)
MAX_POOL_PER_CATEGORY = 5000

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

# Output
OUT_SITEMAP_XML = Path("sitemap.xml")
OUT_SITEMAP_TXT = Path("sitemap.txt")
LAST_COUNT = Path("last_count.txt")

POOL_JSON = Path("pool_by_category.json")
USED_JSON = Path("used_by_category.json")


# =========================
# HTTP / Parsing
# =========================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",  # kein /de/
    "Connection": "keep-alive",
}

# Produkt-URLs (abs/rel), optional /de/
RE_PRODUCT_ABS = re.compile(r"https?://www\.redbubble\.com/(?:de/)?i/[^\"<>\s]+", re.I)
RE_PRODUCT_REL = re.compile(r"/(?:de/)?i/[^\"<>\s]+", re.I)

CF_MARKERS = [
    "cloudflare",
    "cf-chl",
    "verify you are human",
    "checking your browser",
    "Bestätigen Sie, dass Sie ein Mensch sind",
    "muss die Sicherheit Ihrer Verbindung überprüfen",
]


def is_cloudflare_block(html: str) -> bool:
    h = html.lower()
    return any(m.lower() in h for m in CF_MARKERS)


def normalize_product_url(u: str) -> str:
    u = u.strip()
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]

    # /de/ raus
    u = u.replace("https://www.redbubble.com/de/i/", "https://www.redbubble.com/i/")
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")

    u = u.split("#", 1)[0]
    return u


def build_listing_url(page: int, ia_code: str) -> str:
    return (
        f"{BASE}/people/{USERNAME}/shop"
        f"?artistUserName={USERNAME}"
        f"&asc=u"
        f"&sortOrder=recent"
        f"&page={page}"
        f"&iaCode={ia_code}"
    )


def fetch_html(url: str, timeout: int = 30) -> str | None:
    time.sleep(0.35 + random.random() * 0.55)

    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code != 200:
                time.sleep(1.0 * (attempt + 1))
                continue

            html = r.text
            if is_cloudflare_block(html):
                return None
            return html
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None


def extract_product_urls(html: str) -> set[str]:
    urls: set[str] = set()

    for u in RE_PRODUCT_ABS.findall(html):
        urls.add(normalize_product_url(u))

    for rel in RE_PRODUCT_REL.findall(html):
        urls.add(normalize_product_url(BASE + rel))

    return urls


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_sitemap_xml(urls: list[str]) -> None:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{esc(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")

    OUT_SITEMAP_XML.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sitemap_txt(urls: list[str]) -> None:
    OUT_SITEMAP_TXT.write_text("\n".join(urls) + "\n", encoding="utf-8")


def pick_rotating(pool: list[str], used: set[str], k: int, rnd: random.Random) -> tuple[list[str], set[str]]:
    if not pool or k <= 0:
        return [], used

    available = [u for u in pool if u not in used]
    if len(available) < k:
        used = set()
        available = pool[:]

    rnd.shuffle(available)
    chosen = available[:k]
    used.update(chosen)
    return chosen, used


def main() -> None:
    random.seed()

    pool_by_cat: dict[str, list[str]] = load_json(POOL_JSON, {})
    used_by_cat_raw: dict[str, list[str]] = load_json(USED_JSON, {})

    # normalize structures
    for ia in IA_CODES:
        pool_by_cat.setdefault(ia, [])
        used_by_cat_raw.setdefault(ia, [])

    used_by_cat: dict[str, set[str]] = {ia: set(used_by_cat_raw.get(ia, [])) for ia in IA_CODES}

    newly_found_total = 0
    blocked_pages = 0
    scanned_pages = 0

    print("== Crawling listing pages (low) ==")

    for ia in IA_CODES:
        for page in range(1, PAGES_PER_CATEGORY_SCAN + 1):
            url = build_listing_url(page, ia)
            html = fetch_html(url)
            scanned_pages += 1

            if not html:
                blocked_pages += 1
                print(f"BLOCKED/EMPTY: {ia} page={page}")
                continue

            found = sorted(extract_product_urls(html))
            if found:
                before = set(pool_by_cat[ia])
                merged = list(before | set(found))
                # limit per category pool
                if len(merged) > MAX_POOL_PER_CATEGORY:
                    merged = merged[-MAX_POOL_PER_CATEGORY:]
                pool_by_cat[ia] = sorted(merged)
                newly_found_total += len(set(found) - before)
                print(f"OK: {ia} page={page} +{len(found)} products (pool={len(pool_by_cat[ia])})")
            else:
                print(f"OK: {ia} page={page} +0 products")

    # Wenn komplett blockiert (und schon eine Sitemap existiert) -> nichts überschreiben
    total_pool_size = sum(len(pool_by_cat[ia]) for ia in IA_CODES)
    if newly_found_total == 0 and blocked_pages > 0 and OUT_SITEMAP_XML.exists() and total_pool_size > 0:
        print("No new products found (likely blocked). Keeping existing outputs.")
        return

    # ======= QUOTA pro Kategorie =======
    ncat = len(IA_CODES)
    base_quota = MAX_SITEMAP_URLS // ncat
    remainder = MAX_SITEMAP_URLS - base_quota * ncat  # diese Kategorien bekommen +1

    # Tages-Seed für stabile Rotation pro Tag
    day_seed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rnd_global = random.Random(day_seed)

    cats_today = IA_CODES[:]
    rnd_global.shuffle(cats_today)

    chosen_all: list[str] = []

    print("== Picking rotating URLs per category ==")
    for idx, ia in enumerate(cats_today):
        quota = base_quota + (1 if idx < remainder else 0)

        pool = pool_by_cat.get(ia, [])
        if not pool:
            continue

        # eigener RNG pro Kategorie (damit jede Kategorie unabhängig rotiert)
        rnd_cat = random.Random(f"{day_seed}|{ia}")

        chosen, used_new = pick_rotating(pool, used_by_cat.get(ia, set()), quota, rnd_cat)
        used_by_cat[ia] = used_new
        chosen_all.extend(chosen)

    # final mischen (damit nicht Kategorie-Blockweise)
    rnd_global.shuffle(chosen_all)

    # hart begrenzen (falls leere Kategorien -> weniger als 2000, ist ok)
    chosen_all = chosen_all[:MAX_SITEMAP_URLS]

    # Outputs schreiben
    write_sitemap_xml(chosen_all)
    write_sitemap_txt(chosen_all)
    LAST_COUNT.write_text(str(len(chosen_all)) + "\n", encoding="utf-8")

    # Persist
    save_json(POOL_JSON, pool_by_cat)
    save_json(USED_JSON, {ia: sorted(list(used_by_cat[ia])) for ia in IA_CODES})

    print("===================================")
    print(f"Scanned listing pages: {scanned_pages}")
    print(f"Blocked/empty pages:   {blocked_pages}")
    print(f"Newly found total:     {newly_found_total}")
    print(f"Total pool size:       {total_pool_size}")
    print(f"Wrote sitemap URLs:    {len(chosen_all)}")
    print(f"Quota: base={base_quota}, remainder(+1)={remainder}, categories={ncat}")
    print("===================================")


if __name__ == "__main__":
    main()

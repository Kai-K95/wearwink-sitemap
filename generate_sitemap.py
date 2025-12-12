from __future__ import annotations

from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlencode
import html

# =========================
# CONFIG
# =========================

ARTIST = "WearWink"
BASE_SHOP = f"https://www.redbubble.com/people/{ARTIST}/shop"

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

MAX_TOTAL_URLS = 2000
PAGES_PER_CATEGORY_POOL = 200  # “Rotation-Range” pro Kategorie

ASC = "u"
SORT_ORDER = "recent"

OUT_SITEMAP_XML = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")


# =========================
# HELPERS
# =========================

def build_listing_url(page: int, ia_code: str) -> str:
    params = {
        "artistUserName": ARTIST,
        "asc": ASC,
        "sortOrder": SORT_ORDER,
        "page": str(page),
        "iaCode": ia_code,
    }
    return f"{BASE_SHOP}?{urlencode(params)}"


def daily_rotating_pages(pool_size: int, take: int, seed: int) -> list[int]:
    if pool_size <= 0 or take <= 0:
        return []
    start = seed % pool_size
    return [((start + i) % pool_size) + 1 for i in range(take)]


def compute_daily_quotas(total: int, n_categories: int, day_seed: int) -> list[int]:
    base = total // n_categories
    rem = total % n_categories
    quotas = [base] * n_categories
    start = day_seed % n_categories
    for i in range(rem):
        quotas[(start + i) % n_categories] += 1
    return quotas


def write_sitemap_xml(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        # WICHTIG: XML-escape, sonst ist & ein XML-Fehler!
        loc = html.escape(u, quote=True)
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP_XML.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    n = len(IA_CODES)
    if n == 0:
        print("❌ IA_CODES ist leer.")
        return

    day_seed = date.today().toordinal()
    quotas = compute_daily_quotas(MAX_TOTAL_URLS, n, day_seed)

    urls: list[str] = []
    for idx, ia in enumerate(IA_CODES):
        take = quotas[idx]
        seed = day_seed + idx * 97
        pages = daily_rotating_pages(PAGES_PER_CATEGORY_POOL, take, seed)
        for p in pages:
            urls.append(build_listing_url(p, ia))

    # dedupe + Reihenfolge behalten
    urls = list(dict.fromkeys(urls))

    write_sitemap_xml(urls)
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")

    print(f"✅ OK: wrote {len(urls)} URLs")
    print(f"   categories: {n} | pool per category: {PAGES_PER_CATEGORY_POOL}")


if __name__ == "__main__":
    main()

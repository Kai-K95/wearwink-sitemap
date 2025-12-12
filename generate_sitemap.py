from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")

ARTIST = "WearWink"
BASE = f"https://www.redbubble.com/people/{ARTIST}/shop"

# Deine Produktkategorien (iaCode) – bitte hier pflegen
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

PAGES_PER_CATEGORY = 53  # “immer 53 Seiten”

def build_url(page: int, ia_code: str) -> str:
    params = {
        "artistUserName": ARTIST,
        "asc": "u",
        "sortOrder": "recent",
        "page": str(page),
        "iaCode": ia_code,
    }
    return f"{BASE}?{urlencode(params)}"

def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        # Wichtig: & wird beim Schreiben automatisch als &amp; escaped,
        # weil wir keine Roh-XML-Entities manuell reinpacken, sondern nur Text.
        lines.append(f"    <loc>{u.replace('&', '&amp;')}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> None:
    urls: list[str] = []
    seen = set()

    for ia in IA_CODES:
        for p in range(1, PAGES_PER_CATEGORY + 1):
            u = build_url(p, ia)
            if u not in seen:
                seen.add(u)
                urls.append(u)

    # stabil sortieren (BlogToPin mag das oft)
    urls_sorted = sorted(urls)

    write_sitemap(urls_sorted)
    OUT_COUNT.write_text(str(len(urls_sorted)) + "\n", encoding="utf-8")
    print(f"✅ OK: wrote {len(urls_sorted)} URLs | categories={len(set(IA_CODES))} | pages_per_category={PAGES_PER_CATEGORY}")

if __name__ == "__main__":
    main()

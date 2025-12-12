from __future__ import annotations

import random
import shutil
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

# === Einstellungen ===
SITE_BASE = "https://kai-k95.github.io/wearwink-sitemap"  # dein GitHub Pages Base
ARTIST = "WearWink"
RB_BASE = f"https://www.redbubble.com/people/{ARTIST}/shop"

TOTAL_URLS = 2000                 # Gesamtlimit (wie von dir gewünscht)
POOL_PAGES_PER_CATEGORY = 200     # aus wie vielen Seiten wir pro Kategorie "ziehen" (Rotation)
# =====================

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
C_DIR = Path("c")

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

def rb_url(page: int, ia_code: str) -> str:
    params = {
        "artistUserName": ARTIST,
        "asc": "u",
        "sortOrder": "recent",
        "page": str(page),
        "iaCode": ia_code,
    }
    return f"{RB_BASE}?{urlencode(params)}"

def xml_escape(s: str) -> str:
    # wichtig: & muss zu &amp; werden, sonst XML-Fehler
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )

def write_redirect_page(path: Path, target_url: str) -> None:
    # BlogToPin sieht hier eine echte Seite unter /c/<cat>/pXX.html
    # und kann (je nach Setup) dem Link/Redirect folgen.
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta http-equiv="refresh" content="0; url={target_url}"/>
  <link rel="canonical" href="{target_url}"/>
  <title>Redirect</title>
</head>
<body>
  <p>Redirecting to Redbubble…</p>
  <p><a href="{target_url}">{target_url}</a></p>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")

def quota_per_category(categories: int, total: int) -> int:
    # gleichmäßig verteilen, aufrunden damit wir nah an TOTAL_URLS kommen
    # Beispiel: 2000/38 => 52.x -> 53
    return max(1, (total + categories - 1) // categories)

def pick_rotating_pages(ia_code: str, k: int, pool_max: int) -> list[int]:
    day_seed = int(datetime.now(timezone.utc).strftime("%Y%m%d"))  # täglicher Seed
    salt = zlib.crc32(ia_code.encode("utf-8")) & 0xFFFFFFFF
    rnd = random.Random(day_seed ^ salt)

    k = min(k, pool_max)
    pages = rnd.sample(range(1, pool_max + 1), k=k)
    pages.sort()
    return pages

def write_sitemap(locs: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc in locs:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(loc)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> None:
    # 1) Alte Redirect-Pages löschen -> damit “alte” wirklich weggehen
    if C_DIR.exists():
        shutil.rmtree(C_DIR)

    cats = len(IA_CODES)
    k = quota_per_category(cats, TOTAL_URLS)

    locs: list[str] = []

    for ia in IA_CODES:
        pages = pick_rotating_pages(ia, k=k, pool_max=POOL_PAGES_PER_CATEGORY)

        for p in pages:
            # GitHub-Pages URL (damit BlogToPin Kategorien sieht)
            local_rel = Path("c") / ia / f"p{p}.html"
            local_url = f"{SITE_BASE}/{local_rel.as_posix()}"

            # Redbubble Ziel
            target = rb_url(p, ia)

            # Redirect-Seite schreiben
            write_redirect_page(local_rel, target)

            locs.append(local_url)

    locs.sort()
    write_sitemap(locs)
    OUT_COUNT.write_text(str(len(locs)) + "\n", encoding="utf-8")
    print(f"✅ OK: wrote {len(locs)} URLs | categories={cats} | per_category={k}")

if __name__ == "__main__":
    main()

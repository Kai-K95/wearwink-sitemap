from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import math
import random

BASE = "https://www.redbubble.com"
USERNAME = "WearWink"
ASC = "u"
SORT = "recent"

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

# Wie viele Seiten pro Kategorie in den Pool (Pagination)
PAGES_PER_CATEGORY_POOL = 120

# Gesamtlimit: so viele Kategorie-Seiten willst du pro Run maximal
MAX_URLS_PER_RUN = 2000

# Rotation: täglich stabil anderer Mix
DAILY_STABLE_ROTATION = True

OUT_SITEMAP = Path("sitemap.xml")
OUT_SITEMAP_INDEX = Path("sitemap_index.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
OUT_INDEX = Path("index.html")
FEEDS_DIR = Path("feeds")       # feeds/<iaCode>.txt
SITEMAPS_DIR = Path("sitemaps") # sitemaps/<iaCode>.xml


def build_url(page: int, ia_code: str) -> str:
    params = {
        "artistUserName": USERNAME,
        "asc": ASC,
        "page": page,
        "sortOrder": SORT,
        "iaCode": ia_code,
    }
    return f"{BASE}/people/{USERNAME}/shop?{urlencode(params)}"


def xml_escape_loc(url: str) -> str:
    # In XML muss & als &amp; stehen
    return url.replace("&", "&amp;")


def write_sitemap_file(path: Path, urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    SITEMAPS_DIR.mkdir(parents=True, exist_ok=True)

    # Gleichmäßig auf Kategorien verteilen, damit nicht nur eine Kategorie alles belegt
    per_cat_limit = max(1, math.ceil(MAX_URLS_PER_RUN / len(IA_CODES)))

    # Daily seed für Rotation
    if DAILY_STABLE_ROTATION:
        seed = int(datetime.utcnow().strftime("%Y%m%d"))
    else:
        seed = random.randint(1, 10_000_000)

    all_urls: list[str] = []
    sitemap_index_entries: list[str] = []

    for ia in IA_CODES:
        # Pool für diese Kategorie bauen
        pool = [build_url(p, ia) for p in range(1, PAGES_PER_CATEGORY_POOL + 1)]

        rng = random.Random(f"{seed}-{ia}")
        rng.shuffle(pool)

        picked = pool[:per_cat_limit]
        picked = sorted(set(picked))

        # Feed TXT je Kategorie
        feed_path = FEEDS_DIR / f"{ia}.txt"
        feed_path.write_text("\n".join(picked) + "\n", encoding="utf-8")

        # XML Sitemap je Kategorie
        sm_path = SITEMAPS_DIR / f"{ia}.xml"
        write_sitemap_file(sm_path, picked)

        # Für sitemap_index.xml: Link auf GitHub Pages Pfad (relativ reicht in Pages nicht, daher absolut über index später)
        sitemap_index_entries.append(f"sitemaps/{ia}.xml")

        all_urls.extend(picked)

    # Global begrenzen (falls Rundung über 2000 geht)
    # (Kann passieren, wenn len(IA_CODES) nicht exakt in 2000 aufgeht)
    all_urls = sorted(set(all_urls))[:MAX_URLS_PER_RUN]

    # Gesamt-URLs TXT + last_count
    OUT_URLS.write_text("\n".join(all_urls) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(all_urls)) + "\n", encoding="utf-8")

    # Gesamt-Sitemap
    write_sitemap_file(OUT_SITEMAP, all_urls)

    # Sitemap index (referenziert die Kategorie-Sitemaps)
    # Hinweis: Suchmaschinen erwarten absolute URLs, BlogToPin ist oft tolerant.
    # Wir packen relative Pfade rein, die auf GitHub Pages funktionieren.
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    idx_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for rel in sitemap_index_entries:
        idx_lines.append("  <sitemap>")
        idx_lines.append(f"    <loc>{rel}</loc>")
        idx_lines.append(f"    <lastmod>{lastmod}</lastmod>")
        idx_lines.append("  </sitemap>")
    idx_lines.append("</sitemapindex>")
    OUT_SITEMAP_INDEX.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")

    # Kleine Index-Seite zum Testen/Klicken
    OUT_INDEX.write_text(
        f"""<!doctype html><meta charset="utf-8">
<title>WearWink Feeds</title>
<h1>WearWink Feeds</h1>
<p>Total URLs (global cap): <b>{len(all_urls)}</b></p>
<ul>
  <li><a href="urls.txt">urls.txt (alle)</a></li>
  <li><a href="sitemap.xml">sitemap.xml (alle)</a></li>
  <li><a href="sitemap_index.xml">sitemap_index.xml (pro Kategorie)</a></li>
  <li><a href="last_count.txt">last_count.txt</a></li>
</ul>
<h2>Kategorie-Feeds</h2>
<ul>
""" + "\n".join(
            [f'  <li><a href="feeds/{ia}.txt">{ia}.txt</a></li>' for ia in IA_CODES]
        ) + """
</ul>
""",
        encoding="utf-8",
    )

    print(f"✅ OK: wrote {len(all_urls)} URLs total (cap={MAX_URLS_PER_RUN}), per_cat≈{per_cat_limit}")


if __name__ == "__main__":
    main()

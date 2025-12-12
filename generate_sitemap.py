from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import random

BASE = "https://www.redbubble.com"
USERNAME = "WearWink"

# Deine iaCodes (1:1 übernommen)
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

# Wichtig: recent sorgt dafür, dass neue Uploads schneller auftauchen
SORT = "recent"
ASC = "u"

# Wie viele Pagination-Seiten pro Kategorie in den "Pool" sollen
PAGES_PER_CATEGORY_POOL = 80

# Gesamtlimit pro Run (dein Wunsch)
MAX_URLS_PER_RUN = 2000

# Rotation: täglich anderer Mix (stabil pro Tag)
DAILY_STABLE_ROTATION = True

OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
OUT_INDEX = Path("index.html")

def build_url(page: int, ia_code: str) -> str:
    params = {
        "artistUserName": USERNAME,
        "asc": ASC,
        "page": page,
        "sortOrder": SORT,
        "iaCode": ia_code,
    }
    return f"{BASE}/people/{USERNAME}/shop?{urlencode(params)}"

def generate_pool() -> list[str]:
    pool = []
    for ia in IA_CODES:
        for p in range(1, PAGES_PER_CATEGORY_POOL + 1):
            pool.append(build_url(p, ia))
    return sorted(set(pool))

def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{u}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_index(count: int) -> None:
    OUT_INDEX.write_text(
        f"""<!doctype html><meta charset="utf-8">
<title>WearWink Sitemap</title>
<h1>WearWink Sitemap</h1>
<p>URLs in Sitemap: <b>{count}</b></p>
<ul>
<li><a href="sitemap.xml">sitemap.xml</a></li>
<li><a href="urls.txt">urls.txt</a></li>
<li><a href="last_count.txt">last_count.txt</a></li>
</ul>
""",
        encoding="utf-8",
    )

def main():
    pool = generate_pool()

    # Rotation
    if DAILY_STABLE_ROTATION:
        seed = int(datetime.utcnow().strftime("%Y%m%d"))
        rng = random.Random(seed)
        rng.shuffle(pool)
    else:
        random.shuffle(pool)

    urls = pool[:MAX_URLS_PER_RUN]
    urls = sorted(set(urls))

    OUT_URLS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    write_sitemap(urls)
    write_index(len(urls))

    print(f"✅ OK: {len(urls)} category listing URLs (pool={len(pool)})")

if __name__ == "__main__":
    main()

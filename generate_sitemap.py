from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

RB_BASE = "https://www.redbubble.com"
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

OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
OUT_INDEX = Path("index.html")


def build_category_url(ia_code: str) -> str:
    # Nur Seed-URL (page=1). BlogToPin findet Pagination/Unterseiten selbst.
    params = {
        "artistUserName": USERNAME,
        "asc": ASC,
        "page": 1,
        "sortOrder": SORT,
        "iaCode": ia_code,
    }
    return f"{RB_BASE}/people/{USERNAME}/shop?{urlencode(params)}"


def xml_escape(s: str) -> str:
    # Für <loc> muss & zu &amp; werden, sonst ist sitemap.xml kaputt
    return s.replace("&", "&amp;")


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    urls = [build_category_url(ia) for ia in IA_CODES]
    # optional: sort+dedupe
    urls = sorted(set(urls))

    # outputs
    OUT_URLS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    write_sitemap(urls)

    OUT_INDEX.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<h1>WearWink Category Seeds</h1>"
        f"<p>Count: <b>{len(urls)}</b></p>"
        "<ul>"
        "<li><a href='sitemap.xml'>sitemap.xml</a></li>"
        "<li><a href='urls.txt'>urls.txt</a></li>"
        "<li><a href='last_count.txt'>last_count.txt</a></li>"
        "</ul>",
        encoding="utf-8",
    )

    print(f"✅ OK: wrote {len(urls)} category seed URLs")


if __name__ == "__main__":
    main()

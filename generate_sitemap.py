from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

# =========================
# CONFIG
# =========================

ARTIST = "WearWink"

# Nur "www.redbubble.com" (ohne /de/)
BASE = f"https://www.redbubble.com/people/{ARTIST}/shop"

# Deine iaCodes (38 Stück – wie in deinen Logs)
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

# Zielgröße ~2000 URLs insgesamt (bei 38 Kategorien => 53 Seiten pro Kategorie => 2014 URLs)
TOTAL_TARGET_URLS = 2000

# Output
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS_TXT = Path("urls.txt")


def build_category_page_url(ia_code: str, page: int) -> str:
    """
    Redbubble Category-Listing URL für deinen Shop.
    """
    params = {
        "artistUserName": ARTIST,
        "asc": "u",
        "sortOrder": "recent",
        "page": str(page),
        "iaCode": ia_code,
    }
    return f"{BASE}?{urlencode(params)}"


def generate_urls() -> list[str]:
    categories = list(dict.fromkeys(IA_CODES))  # unique, order behalten
    n = len(categories)
    pages_per_category = max(1, ceil(TOTAL_TARGET_URLS / n))  # z.B. 53

    urls: list[str] = []
    for ia in categories:
        for page in range(1, pages_per_category + 1):
            urls.append(build_category_page_url(ia, page))

    # unique + stabil sortiert (nur falls du doppelte hast)
    urls = sorted(set(urls))

    print(f"✅ OK: wrote {len(urls)} URLs")
    print(f"categories: {n} | pages per category: {pages_per_category}")

    return urls


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    urlset = ET.Element("urlset", attrib={"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})

    for u in urls:
        url_el = ET.SubElement(urlset, "url")
        loc_el = ET.SubElement(url_el, "loc")
        loc_el.text = u  # ElementTree escaped '&' automatisch korrekt

        lm_el = ET.SubElement(url_el, "lastmod")
        lm_el.text = lastmod

    tree = ET.ElementTree(urlset)
    tree.write(OUT_SITEMAP, encoding="utf-8", xml_declaration=True)


def main() -> None:
    urls = generate_urls()

    write_sitemap(urls)

    # Debug/Count
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    OUT_URLS_TXT.write_text("\n".join(urls) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

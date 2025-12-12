from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")

USERNAME = "WearWink"
BASE = f"https://www.redbubble.com/people/{USERNAME}/shop"

# Deine iaCodes (wie von dir gepostet)
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

# Wie viele Seiten je Kategorie du als "Seed" geben willst.
# BlogToPin kann daraus weitere Seiten/Produkte finden.
PAGES_PER_CATEGORY = 30

def build_rb_url(page: int, ia_code: str) -> str:
    # KEIN /de/ !
    # Wichtig: & wird später im XML korrekt escaped (-> &amp;)
    return (
        f"{BASE}"
        f"?artistUserName={USERNAME}"
        f"&asc=u"
        f"&sortOrder=recent"
        f"&page={page}"
        f"&iaCode={ia_code}"
    )

def xml_escape(s: str) -> str:
    # Minimales XML-Escaping (entscheidend ist &)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )

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

def main() -> None:
    urls: list[str] = []
    for ia in IA_CODES:
        for p in range(1, PAGES_PER_CATEGORY + 1):
            urls.append(build_rb_url(p, ia))

    # Duplikate raus
    urls = sorted(set(urls))

    write_sitemap(urls)
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    print(f"✅ OK: wrote {len(urls)} Redbubble category page URLs")

if __name__ == "__main__":
    main()

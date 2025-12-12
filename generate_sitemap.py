from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import shutil

# === DEINE KONFIG ===
SITE_BASE = "https://kai-k95.github.io/wearwink-sitemap"
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

# === OUTPUTS ===
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_INDEX = Path("index.html")
PAGES_DIR = Path("c")  # -> /c/<iaCode>/index.html


def rb_category_url(ia_code: str) -> str:
    params = {
        "artistUserName": USERNAME,
        "asc": ASC,
        "page": 1,  # nur Kategorie-Seed
        "sortOrder": SORT,
        "iaCode": ia_code,
    }
    return f"{RB_BASE}/people/{USERNAME}/shop?{urlencode(params)}"


def local_category_url(ia_code: str) -> str:
    # Wichtig: BlogToPin bekommt nur DEINE Domain in der Sitemap
    return f"{SITE_BASE}/c/{ia_code}/"


def write_redirect_page(path: Path, target: str, title: str) -> None:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="robots" content="noindex">
  <link rel="canonical" href="{target}">
  <meta http-equiv="refresh" content="0; url={target}">
  <script>window.location.replace({target!r});</script>
</head>
<body>
  <p>Redirecting to <a href="{target}">{target}</a></p>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        # local URLs haben keine &, trotzdem safe:
        u_xml = u.replace("&", "&amp;")
        lines.append("  <url>")
        lines.append(f"    <loc>{u_xml}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    # alte Redirect-Seiten entfernen
    if PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)

    urls = []
    for ia in IA_CODES:
        target = rb_category_url(ia)
        out_dir = PAGES_DIR / ia
        out_dir.mkdir(parents=True, exist_ok=True)
        write_redirect_page(out_dir / "index.html", target, title=f"{ia} category")
        urls.append(local_category_url(ia))

    urls = sorted(set(urls))
    write_sitemap(urls)
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")

    OUT_INDEX.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<h1>WearWink Category Seeds</h1>"
        f"<p>Count: <b>{len(urls)}</b></p>"
        "<p><a href='sitemap.xml'>sitemap.xml</a> | <a href='last_count.txt'>last_count.txt</a></p>",
        encoding="utf-8",
    )

    print(f"✅ OK: wrote {len(urls)} category redirect pages")


if __name__ == "__main__":
    main()

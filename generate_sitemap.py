from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

# =========================
# CONFIG
# =========================
USERNAME = "WearWink"
LOCALE = "de"  # "de" für /de/ URLs; wenn du lieber ohne Locale willst, setz LOCALE = ""
MAX_SHOP_PAGES = 250     # 250 * ~60 Tiles ≈ sehr viel Abdeckung
MAX_EXPLORE_PAGES = 50   # Explore Designs Seiten
SHOP_SORT = "bestselling"  # "bestselling" oder "recent"
EXPLORE_SORT = "recent"    # meist "recent"

# Dateien im Repo
OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
OUT_INDEX = Path("index.html")

# =========================
# HELPERS
# =========================
def base() -> str:
    # https://www.redbubble.com/de/... oder ohne /de/
    if LOCALE:
        return f"https://www.redbubble.com/{LOCALE}"
    return "https://www.redbubble.com"

def build_people_url(path: str, params: dict) -> str:
    # path z.B. "/people/WearWink/shop"
    qs = urlencode(params, doseq=True)
    return f"{base()}{path}?{qs}"

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

def write_index(urls_count: int) -> None:
    # damit GitHub Pages nicht 404 auf / zeigt
    sitemap_link = "sitemap.xml"
    urls_link = "urls.txt"
    OUT_INDEX.write_text(
        f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>WearWink Sitemap</title>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 16px;}}
    code{{background:#f2f2f2;padding:2px 6px;border-radius:6px;}}
  </style>
</head>
<body>
  <h1>WearWink Sitemap</h1>
  <p>Letztes Update: <code>{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</code></p>
  <p>URLs in Sitemap: <code>{urls_count}</code></p>
  <ul>
    <li><a href="{sitemap_link}">{sitemap_link}</a></li>
    <li><a href="{urls_link}">{urls_link}</a></li>
  </ul>
</body>
</html>
""",
        encoding="utf-8",
    )

# =========================
# MAIN
# =========================
def main() -> None:
    urls: list[str] = []

    # Basis-Seiten
    urls.append(build_people_url(f"/people/{USERNAME}/shop", {"asc": "u", "sortOrder": SHOP_SORT}))
    urls.append(build_people_url(f"/people/{USERNAME}/explore", {"asc": "u", "sortOrder": EXPLORE_SORT}))

    # Shop-Pagination (Mockup-Produktkacheln!)
    for page in range(1, MAX_SHOP_PAGES + 1):
        urls.append(
            build_people_url(
                f"/people/{USERNAME}/shop",
                {"asc": "u", "page": page, "sortOrder": SHOP_SORT},
            )
        )

    # Explore-Pagination (Design-Feed)
    for page in range(1, MAX_EXPLORE_PAGES + 1):
        urls.append(
            build_people_url(
                f"/people/{USERNAME}/explore",
                {"asc": "u", "page": page, "sortOrder": EXPLORE_SORT},
            )
        )

    # Dedupe + stabil sort
    urls = sorted(set(urls))

    # Outputs
    OUT_URLS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    write_sitemap(urls)
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    write_index(len(urls))

    print(f"✅ OK: {len(urls)} URLs geschrieben (shop_pages={MAX_SHOP_PAGES}, explore_pages={MAX_EXPLORE_PAGES})")

if __name__ == "__main__":
    main()

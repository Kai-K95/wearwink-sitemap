import re
import time
import random
from pathlib import Path
from datetime import datetime, timezone

import requests


# =========================
# SETTINGS (hier anpassen)
# =========================
USERNAME = "WearWink"
BASE = "https://www.redbubble.com"

# Wie viele Listing-Seiten pro Kategorie crawlen?
# (1–3 reicht meistens, sonst wird es schnell viel und triggert eher Cloudflare)
PAGES_PER_CATEGORY_SCAN = 3

# Max. Produkt-URLs, die in die Sitemap sollen (BlogToPin-Planung)
MAX_SITEMAP_URLS = 2000

# Optional: Max. Größe des Pools (damit repo nicht riesig wird)
MAX_POOL_SIZE = 50000

# Deine Kategorien (iaCode-Liste)
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

# Output / Cache Files
OUT_SITEMAP_XML = Path("sitemap.xml")
OUT_SITEMAP_TXT = Path("sitemap.txt")
LAST_COUNT = Path("last_count.txt")
POOL_FILE = Path("pool_urls.txt")
USED_FILE = Path("used_urls.txt")


# =========================
# HTTP / Parsing
# =========================
HEADERS = {
    # wichtig: eher "normaler Browser" wirken
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    # wichtig: wir wollen KEIN /de/ erzwingen -> lieber englische Defaults
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# findet Produkt-URLs (absolut oder relativ), optional mit /de/
RE_PRODUCT_ABS = re.compile(r"https?://www\.redbubble\.com/(?:de/)?i/[^\"<>\s]+", re.I)
RE_PRODUCT_REL = re.compile(r"/(?:de/)?i/[^\"<>\s]+", re.I)

# Cloudflare / Bot-Challenge Erkennung (englisch + deutsch)
CF_MARKERS = [
    "cloudflare",
    "cf-chl",
    "verify you are human",
    "checking your browser",
    "Bestätigen Sie, dass Sie ein Mensch sind",
    "muss die Sicherheit Ihrer Verbindung überprüfen",
]


def is_cloudflare_block(html: str) -> bool:
    h = html.lower()
    return any(m.lower() in h for m in CF_MARKERS)


def normalize_product_url(u: str) -> str:
    u = u.strip()
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]

    # /de/ raus
    u = u.replace("https://www.redbubble.com/de/i/", "https://www.redbubble.com/i/")
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")

    # fragment weg
    u = u.split("#", 1)[0]
    return u


def build_listing_url(page: int, ia_code: str) -> str:
    # Listing Seite (Shop -> Kategorie -> Seite X)
    # sortOrder=recent damit "neu" zuerst kommt
    return (
        f"{BASE}/people/{USERNAME}/shop"
        f"?artistUserName={USERNAME}"
        f"&asc=u"
        f"&sortOrder=recent"
        f"&page={page}"
        f"&iaCode={ia_code}"
    )


def fetch_html(url: str, timeout: int = 30) -> str | None:
    # kleine Random-Pause (weniger Bot-Trigger)
    time.sleep(0.4 + random.random() * 0.6)

    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code != 200:
                # 403/429 oft Bot/Rate-Limit -> kurz warten und retry
                time.sleep(1.2 * (attempt + 1))
                continue

            html = r.text
            if is_cloudflare_block(html):
                return None
            return html
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return None


def extract_product_urls(html: str) -> set[str]:
    urls: set[str] = set()

    for u in RE_PRODUCT_ABS.findall(html):
        urls.add(normalize_product_url(u))

    for rel in RE_PRODUCT_REL.findall(html):
        absu = BASE + rel
        urls.add(normalize_product_url(absu))

    return urls


def load_lines(p: Path) -> set[str]:
    if not p.exists():
        return set()
    return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}


def write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sitemap_xml(urls: list[str]) -> None:
    # XML-sicher escapen (wegen & in Querystrings!)
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{esc(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP_XML.write_text("\n".join(lines) + "\n", encoding="utf-8")


def pick_rotating(pool: list[str], used: set[str], k: int) -> tuple[list[str], set[str]]:
    # verfügbare = noch nicht benutzt
    available = [u for u in pool if u not in used]

    # wenn nicht genug übrig: Rotation reset
    if len(available) < k:
        used = set()
        available = pool[:]

    # täglich anders, aber stabil innerhalb eines Tages
    seed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rnd = random.Random(seed)
    rnd.shuffle(available)

    chosen = available[:k]
    used.update(chosen)
    return chosen, used


def main() -> None:
    random.seed()  # echte Randomness für Crawl-Timing

    pool = load_lines(POOL_FILE)
    used = load_lines(USED_FILE)

    newly_found: set[str] = set()
    scanned_pages = 0
    blocked_pages = 0

    # Crawl Kategorie-Listings und sammle Produktlinks
    for ia in IA_CODES:
        for page in range(1, PAGES_PER_CATEGORY_SCAN + 1):
            url = build_listing_url(page, ia)
            html = fetch_html(url)
            scanned_pages += 1

            if not html:
                blocked_pages += 1
                print(f"BLOCKED/EMPTY: iaCode={ia} page={page}")
                continue

            found = extract_product_urls(html)
            if found:
                newly_found |= found
                print(f"OK: iaCode={ia} page={page} +{len(found)} products (total_new={len(newly_found)})")
            else:
                print(f"OK: iaCode={ia} page={page} +0 products")

            # Safety: wenn wir schon sehr viele neue haben, abbrechen (sonst dauert’s ewig)
            if len(newly_found) >= 10000:
                break
        if len(newly_found) >= 10000:
            break

    # Pool updaten
    if newly_found:
        pool |= newly_found

    # Pool begrenzen
    pool_list = sorted(pool)
    if len(pool_list) > MAX_POOL_SIZE:
        pool_list = pool_list[-MAX_POOL_SIZE:]  # behalte "neueste" (sortiert) – ok als pragmatischer Cut

    # Wenn wir in diesem Run GAR NICHTS finden UND es gibt schon eine Sitemap:
    # NICHT überschreiben (sonst wird wieder alles 0)
    if not newly_found and OUT_SITEMAP_XML.exists() and len(pool_list) > 0:
        print("No new products found (possibly blocked). Keeping existing outputs.")
        # trotzdem counts/pool/used nicht killen
        return

    # Rotation: wähle bis MAX_SITEMAP_URLS aus dem Pool
    chosen, used2 = pick_rotating(pool_list, used, min(MAX_SITEMAP_URLS, len(pool_list)))

    # Outputs schreiben
    write_sitemap_xml(chosen)
    write_lines(OUT_SITEMAP_TXT, chosen)
    LAST_COUNT.write_text(str(len(chosen)) + "\n", encoding="utf-8")
    write_lines(POOL_FILE, pool_list)
    write_lines(USED_FILE, sorted(used2))

    print("===================================")
    print(f"Scanned listing pages: {scanned_pages}")
    print(f"Blocked/empty pages:   {blocked_pages}")
    print(f"Pool size:             {len(pool_list)}")
    print(f"Wrote sitemap URLs:    {len(chosen)}")
    print("===================================")


if __name__ == "__main__":
    main()

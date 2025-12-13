# generate_sitemap.py
from __future__ import annotations

import re
import sys
import time
import random
from pathlib import Path
from datetime import datetime, timezone, date
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================
SHOP_USER = "WearWink"

TARGET_URLS = 1100  # gewünschte Anzahl Produkt-URLs im sitemap.xml

# Wie viel wir für Discovery abklappern (klein halten, damit RB weniger nervt)
EXPLORE_PAGES_TO_SCAN = 3               # Explore (neueste Designs) – erhöht = mehr Coverage
LISTING_PAGES_PER_CATEGORY_TO_SCAN = 1  # pro Kategorie mindestens Seite 1 (meist reicht für neue Uploads)

SLEEP_BETWEEN_REQUESTS_SEC = 1.2
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3

# Deine iaCodes (du kannst hier jederzeit ergänzen/entfernen)
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

# URL-Templates
EXPLORE_URL = f"https://www.redbubble.com/people/{SHOP_USER}/explore?asc=u&page={{page}}&sortOrder=recent"
LISTING_URL = (
    f"https://www.redbubble.com/people/{SHOP_USER}/shop"
    f"?artistUserName={SHOP_USER}&asc=u&sortOrder=recent&page={{page}}&iaCode={{ia}}"
)

# Output/Cache
OUT_SITEMAP = Path("sitemap.xml")
DESIGN_IDS_FILE = Path("design_ids.txt")
USED_IDS_FILE = Path("used_ids.txt")
LAST_COUNT_FILE = Path("last_count.txt")
DEBUG_DIR = Path("debug")


# =========================
# REGEX: IDs aus Links
# =========================
ID_RE_AP = re.compile(r"/shop/ap/(\d+)")
# Häufig: /i/t-shirt/Title/123456789 oder /i/sticker/Name/123456789
ID_RE_I = re.compile(r"/i/[^\"'\s<>]+/(\d+)(?:[/?#\"'\s<>]|$)")


# =========================
# HELPERS
# =========================
def ensure_dirs() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return s


def is_blocked_html(html: str) -> bool:
    low = html.lower()
    # typische Bot/Challenge Hinweise
    needles = [
        "attention required",
        "verify you are human",
        "captcha",
        "cloudflare",
        "/cdn-cgi/",
        "access denied",
        "request blocked",
    ]
    return any(n in low for n in needles)


def debug_write(filename: str, content: str) -> None:
    ensure_dirs()
    (DEBUG_DIR / filename).write_text(content, encoding="utf-8", errors="ignore")


def fetch_html(s: requests.Session, url: str, debug_name: str) -> str | None:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = s.get(url, timeout=REQUEST_TIMEOUT_SEC)
            # Redbubble blockt gern mit 403/429 oder liefert Challenge HTML
            if r.status_code in (403, 429):
                debug_write(f"{debug_name}_status{r.status_code}.html", r.text)
                return None

            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * attempt)
                continue

            text = r.text or ""
            if not text.strip():
                debug_write(f"{debug_name}_empty.html", text)
                return None

            if is_blocked_html(text):
                debug_write(f"{debug_name}_blocked.html", text)
                return None

            # Optional: speicher Page1 immer zur Diagnose
            if debug_name.endswith("_p1"):
                debug_write(f"{debug_name}.html", text)

            return text

        except Exception as e:
            last_err = str(e)
            time.sleep(1.5 * attempt)

    if last_err:
        debug_write(f"{debug_name}_error.txt", last_err)
    return None


def extract_ids_from_html(html: str) -> list[str]:
    ids: list[str] = []

    # 1) Regex direkt
    ids.extend(ID_RE_AP.findall(html))
    ids.extend(ID_RE_I.findall(html))

    # 2) Zusätzlich über BeautifulSoup (falls HTML “komisch” ist)
    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not isinstance(href, str):
                continue
            ids.extend(ID_RE_AP.findall(href))
            ids.extend(ID_RE_I.findall(href))
    except Exception:
        pass

    # Dedupe (order-preserving)
    seen = set()
    out = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def load_lines(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]


def save_lines(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge_unique(existing: list[str], new: list[str]) -> list[str]:
    seen = set(existing)
    out = existing[:]
    for x in new:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def discover_design_ids() -> tuple[list[str], bool]:
    """
    Returns: (ids_found, blocked_any)
    """
    s = session()
    found: list[str] = []
    blocked_any = False

    # --- Explore (neueste Designs) ---
    for p in range(1, EXPLORE_PAGES_TO_SCAN + 1):
        url = EXPLORE_URL.format(page=p)
        html = fetch_html(s, url, debug_name=f"explore_p{p}")
        if html is None:
            print(f"BLOCKED/EMPTY: explore page={p}")
            blocked_any = True
            break

        ids = extract_ids_from_html(html)
        if not ids:
            print(f"NO IDS: explore page={p}")
            break

        found.extend(ids)
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    # --- Shop Listings pro Kategorie ---
    ia_codes = sorted(set(IA_CODES))
    for ia in ia_codes:
        for p in range(1, LISTING_PAGES_PER_CATEGORY_TO_SCAN + 1):
            url = LISTING_URL.format(page=p, ia=ia)
            html = fetch_html(s, url, debug_name=f"listing_{ia}_p{p}")
            if html is None:
                print(f"BLOCKED/EMPTY: {ia} page={p}")
                blocked_any = True
                break

            ids = extract_ids_from_html(html)
            if not ids:
                print(f"NO IDS: {ia} page={p}")
                break

            found.extend(ids)
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    # Dedupe
    seen = set()
    out = []
    for i in found:
        if i not in seen:
            seen.add(i)
            out.append(i)

    return out, blocked_any


def pick_rotating_ids(all_ids: list[str], used_ids: set[str], target: int) -> tuple[list[str], set[str]]:
    """
    Tages-rotation:
    - nimmt zuerst aus unbenutzten IDs
    - wenn zu wenig verfügbar, reset used_ids
    - Auswahl deterministisch pro Tag (damit gleiche Tagesläufe identisch sind)
    """
    if not all_ids:
        return [], used_ids

    available = [i for i in all_ids if i not in used_ids]

    # Wenn Pool zu klein -> reset
    if len(available) < target:
        used_ids = set()
        available = all_ids[:]

    seed = int(date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    rng.shuffle(available)

    picked = available[: min(target, len(available))]
    used_ids.update(picked)

    return picked, used_ids


def write_sitemap(product_urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in product_urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ensure_dirs()

    # 1) Cache laden
    cached_ids = load_lines(DESIGN_IDS_FILE)
    used_ids = set(load_lines(USED_IDS_FILE))

    # 2) Discovery laufen lassen
    new_ids, blocked_any = discover_design_ids()
    if new_ids:
        cached_ids = merge_unique(cached_ids, new_ids)
        save_lines(DESIGN_IDS_FILE, cached_ids)
        print(f"OK: discovered {len(new_ids)} new IDs | total cache={len(cached_ids)}")
    else:
        print("WARN: discovery returned 0 IDs.")
        if blocked_any:
            print("WARN: looks blocked/empty. Check debug/*.html for the exact HTML.")

    # 3) Wenn cache leer -> hart abbrechen (sonst gibt’s keine Produktseiten)
    if not cached_ids:
        print("ERROR: No design IDs available (cache empty + discovery empty/blocked).")
        print("Open debug/explore_p1*.html or debug/listing_*_p1*.html and check for captcha/block.")
        return 1

    # 4) Tages-rotation / keine Wiederholung bis Pool leer
    picked_ids, used_ids = pick_rotating_ids(cached_ids, used_ids, TARGET_URLS)

    if not picked_ids:
        print("ERROR: Could not pick any IDs.")
        return 1

    # 5) Produkt-URLs bauen (BlogToPin braucht Produktseiten -> Bilder kommen dann)
    product_urls = [f"https://www.redbubble.com/shop/ap/{pid}" for pid in picked_ids]

    # 6) sitemap.xml schreiben
    write_sitemap(product_urls)

    # 7) used + last_count schreiben
    save_lines(USED_IDS_FILE, sorted(used_ids))
    LAST_COUNT_FILE.write_text(str(len(product_urls)) + "\n", encoding="utf-8")

    print(f"OK: wrote {len(product_urls)} product URLs to {OUT_SITEMAP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

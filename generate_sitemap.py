from __future__ import annotations

import re
import json
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone, date
from xml.sax.saxutils import escape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================
SHOP_USER = "WearWink"

TARGET_URLS_DEFAULT = 1100

# Discovery (best effort; RB blockt oft)
EXPLORE_PAGES_TO_SCAN = 3
LISTING_PAGES_PER_CATEGORY_TO_SCAN = 1
MAX_IA_CODES_PER_DISCOVERY_RUN = 6

SLEEP_BETWEEN_REQUESTS_SEC = 1.2
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3

# Wenn du wirklich NUR /i/ willst (Mockups), lass True.
# Wenn du als Notfall auch /shop/ap/<id> erlauben willst, setz False.
ONLY_I_URLS = True

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

EXPLORE_URL = f"https://www.redbubble.com/people/{SHOP_USER}/explore?asc=u&page={{page}}&sortOrder=recent"
LISTING_URL = (
    f"https://www.redbubble.com/people/{SHOP_USER}/shop"
    f"?artistUserName={SHOP_USER}&asc=u&sortOrder=recent&page={{page}}&iaCode={{ia}}"
)

# Repo structure
DATA_DIR = Path("data")
PUBLIC_DIR = Path("public")
DEBUG_DIR = Path("debug")

URL_POOL_JSON = DATA_DIR / "url_pool.json"
USED_URLS_JSON = DATA_DIR / "used_urls.json"
STATE_JSON = DATA_DIR / "state.json"

SEED_URLS_TXT = DATA_DIR / "seed_urls.txt"  # hier fügst du neue /i/ URLs ein
OUT_SITEMAP = PUBLIC_DIR / "sitemap.xml"


# =========================
# URL / Pattern Helpers
# =========================
# /i/<product>/<slug>/<id>  (manchmal id.<track>)
ID_RE_I = re.compile(r"/i/[^\"'\s<>]+/(\d+)(?:[/?#\.\"'\s<>]|$)")
ID_RE_AP = re.compile(r"/shop/ap/(\d+)")

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def debug_write(name: str, content: str) -> None:
    ensure_dirs()
    (DEBUG_DIR / name).write_text(content, encoding="utf-8", errors="ignore")


def normalize_rb_url(url: str) -> str | None:
    """
    Normalisiert auf https://www.redbubble.com/<path>
    Filtert alles raus, was keine Produktseite ist.
    """
    if not url:
        return None

    u = url.strip()
    if not u:
        return None

    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = "https://www.redbubble.com" + u

    try:
        p = urlparse(u)
    except Exception:
        return None

    host = (p.netloc or "").lower()
    if "redbubble.com" not in host:
        return None

    path = p.path or ""
    # Erlaubt:
    # - /i/...
    # - optional /shop/ap/...
    if path.startswith("/i/"):
        pass
    elif path.startswith("/shop/ap/"):
        if ONLY_I_URLS:
            return None
    else:
        return None

    return f"https://www.redbubble.com{path}"


def is_i_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return (p.path or "").startswith("/i/")
    except Exception:
        return False


def extract_product_urls_from_html(html: str) -> list[str]:
    """
    Extrahiert Produktseiten-URLs aus HTML:
    - /i/...
    - optional /shop/ap/...
    """
    out: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a.get("href")
            if not isinstance(href, str):
                continue
            nu = normalize_rb_url(href)
            if nu:
                out.append(nu)
    except Exception:
        pass

    # Dedupe order-preserving
    seen = set()
    dedup: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


# =========================
# JSON storage
# =========================
def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pool_add_urls(urls: list[str], source: str) -> int:
    """
    data/url_pool.json:
    {
      "urls": {
        "<url>": {"first_seen": "...", "last_seen": "...", "source": "...", "id": "123"}
      },
      "meta": {...}
    }
    """
    now = datetime.now(timezone.utc).isoformat()
    data = load_json(URL_POOL_JSON, {"urls": {}, "meta": {}})
    urls_map = data.get("urls", {})
    if not isinstance(urls_map, dict):
        urls_map = {}

    added = 0
    for u in urls:
        nu = normalize_rb_url(u)
        if not nu:
            continue

        pid = None
        m = ID_RE_I.search(nu) or ID_RE_AP.search(nu)
        if m:
            pid = m.group(1)

        if nu not in urls_map:
            urls_map[nu] = {"first_seen": now, "last_seen": now, "source": source, "id": pid}
            added += 1
        else:
            if isinstance(urls_map[nu], dict):
                urls_map[nu]["last_seen"] = now

    data["urls"] = urls_map
    data.setdefault("meta", {})
    data["meta"]["updated_at"] = now
    save_json(URL_POOL_JSON, data)
    return added


def load_pool_urls() -> list[str]:
    data = load_json(URL_POOL_JSON, {"urls": {}})
    urls_map = data.get("urls", {})
    if not isinstance(urls_map, dict):
        return []
    return list(urls_map.keys())


def load_used_urls() -> set[str]:
    data = load_json(USED_URLS_JSON, {"used": [], "last_reset": None})
    used = data.get("used", [])
    if not isinstance(used, list):
        used = []
    return {u for u in used if isinstance(u, str)}


def save_used_urls(used: set[str], last_reset: str | None = None) -> None:
    save_json(USED_URLS_JSON, {"used": sorted(used), "last_reset": last_reset})


# =========================
# Rotation
# =========================
def pick_rotating_urls(all_urls: list[str], used: set[str], target: int) -> tuple[list[str], set[str], bool]:
    if not all_urls:
        return [], used, False

    all_set = set(all_urls)
    used = {u for u in used if u in all_set}

    available = [u for u in all_urls if u not in used]
    did_reset = False

    if len(available) < target:
        used = set()
        available = all_urls[:]
        did_reset = True

    seed = int(date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    rng.shuffle(available)

    picked = available[: min(target, len(available))]
    used.update(picked)
    return picked, used, did_reset


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =========================
# Seed import (every run)
# =========================
def load_seed_urls() -> list[str]:
    if not SEED_URLS_TXT.exists():
        return []
    lines = [
        l.strip()
        for l in SEED_URLS_TXT.read_text(encoding="utf-8", errors="ignore").splitlines()
        if l.strip()
    ]
    out: list[str] = []
    for line in lines:
        nu = normalize_rb_url(line)
        if nu:
            out.append(nu)

    # dedupe
    seen = set()
    dedup = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


# =========================
# Discovery (best effort)
# =========================
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


def looks_blocked(status: int, html: str) -> bool:
    if status in (403, 429):
        return True
    low = (html or "").lower()
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


def fetch_html(s: requests.Session, url: str, debug_name: str) -> str | None:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = s.get(url, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)
            text = r.text or ""

            if looks_blocked(r.status_code, text) or not text.strip():
                debug_write(f"{debug_name}_blocked_status{r.status_code}.html", text)
                return None

            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * attempt)
                continue

            if debug_name.endswith("_p1"):
                debug_write(f"{debug_name}.html", text)

            return text
        except Exception as e:
            last_err = str(e)
            time.sleep(1.5 * attempt)

    if last_err:
        debug_write(f"{debug_name}_error.txt", last_err)
    return None


def choose_ia_codes_for_run() -> list[str]:
    ia = sorted(set(IA_CODES))
    if not ia:
        return []
    st = load_json(STATE_JSON, {"ia_cursor": 0})
    cursor = int(st.get("ia_cursor", 0)) if str(st.get("ia_cursor", "0")).isdigit() else 0

    k = min(MAX_IA_CODES_PER_DISCOVERY_RUN, len(ia))
    chosen = [ia[(cursor + i) % len(ia)] for i in range(k)]
    st["ia_cursor"] = (cursor + k) % len(ia)
    save_json(STATE_JSON, st)
    return chosen


def discover_urls() -> tuple[list[str], bool]:
    s = session()
    found: list[str] = []
    blocked_any = False

    # Explore
    for p in range(1, EXPLORE_PAGES_TO_SCAN + 1):
        url = EXPLORE_URL.format(page=p)
        html = fetch_html(s, url, debug_name=f"explore_p{p}")
        if html is None:
            print(f"WARN: blocked/empty explore page={p}")
            blocked_any = True
            break

        urls = extract_product_urls_from_html(html)
        # Filter: wenn ONLY_I_URLS -> nur /i/
        if ONLY_I_URLS:
            urls = [u for u in urls if is_i_url(u)]

        if not urls:
            print(f"INFO: no product urls on explore page={p}")
            break

        found.extend(urls)
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    # Listing by IA codes
    for ia in choose_ia_codes_for_run():
        for p in range(1, LISTING_PAGES_PER_CATEGORY_TO_SCAN + 1):
            url = LISTING_URL.format(page=p, ia=ia)
            html = fetch_html(s, url, debug_name=f"listing_{ia}_p{p}")
            if html is None:
                print(f"WARN: blocked/empty ia={ia} page={p}")
                blocked_any = True
                break

            urls = extract_product_urls_from_html(html)
            if ONLY_I_URLS:
                urls = [u for u in urls if is_i_url(u)]

            if not urls:
                print(f"INFO: no product urls ia={ia} page={p}")
                break

            found.extend(urls)
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    # Dedupe order-preserving
    seen = set()
    out = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, blocked_any


# =========================
# MAIN
# =========================
def main() -> int:
    ensure_dirs()

    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="Try to discover new /i/ URLs (best effort)")
    ap.add_argument("--build", action="store_true", help="Build sitemap.xml from pool")
    ap.add_argument("--target", type=int, default=TARGET_URLS_DEFAULT)
    args = ap.parse_args()

    # Default: do build (and not discover) unless requested
    run_discover = args.discover
    run_build = args.build or (not args.discover and not args.build)

    # 0) Seed import EVERY run
    seed_urls = load_seed_urls()
    if seed_urls:
        added = pool_add_urls(seed_urls, source="seed_urls")
        print(f"OK: imported seed_urls.txt | urls_in_file={len(seed_urls)} | added_new={added}")
    else:
        print("INFO: seed_urls.txt empty or missing (no seed import)")

    # 1) Discovery (optional)
    if run_discover:
        new_urls, blocked_any = discover_urls()
        if new_urls:
            added = pool_add_urls(new_urls, source="auto_discovery")
            print(f"OK: discovery found={len(new_urls)} | added_new={added}")
        else:
            print("WARN: discovery returned 0 URLs.")
            if blocked_any:
                print("WARN: looks blocked/empty. Check debug/*.html")

    # 2) Build sitemap
    if run_build:
        pool = load_pool_urls()
        used = load_used_urls()

        if not pool:
            # If sitemap already exists, keep it
            if OUT_SITEMAP.exists() and OUT_SITEMAP.stat().st_size > 200:
                print("WARN: url pool empty, but sitemap exists -> keeping existing sitemap.xml (exit 0).")
                return 0
            print("ERROR: url pool empty AND no existing sitemap.xml. Add /i/ URLs to data/seed_urls.txt.")
            return 1

        # Effective target: nicht größer als Pool
        effective_target = min(args.target, len(pool))

        picked, used, did_reset = pick_rotating_urls(pool, used, effective_target)
        if not picked:
            if OUT_SITEMAP.exists() and OUT_SITEMAP.stat().st_size > 200:
                print("WARN: could not pick urls, keeping existing sitemap.xml (exit 0).")
                return 0
            print("ERROR: could not pick any urls.")
            return 1

        write_sitemap(picked)

        reset_ts = datetime.now(timezone.utc).isoformat() if did_reset else load_json(USED_URLS_JSON, {"last_reset": None}).get("last_reset")
        save_used_urls(used, last_reset=reset_ts)

        print(f"OK: wrote {len(picked)} product URLs to {OUT_SITEMAP}")
        print(f"INFO: pool_size={len(pool)} | used_size={len(used)} | only_i_urls={ONLY_I_URLS}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

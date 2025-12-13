from __future__ import annotations

import re
import json
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone, date
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================
SHOP_USER = "WearWink"
TARGET_URLS_DEFAULT = 1100

# Discovery: klein halten (RB blockt oft)
EXPLORE_PAGES_TO_SCAN = 3
LISTING_PAGES_PER_CATEGORY_TO_SCAN = 1
MAX_IA_CODES_PER_DISCOVERY_RUN = 6

SLEEP_BETWEEN_REQUESTS_SEC = 1.2
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3

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

CACHE_JSON = DATA_DIR / "design_ids.json"
USED_JSON = DATA_DIR / "used_ids.json"
STATE_JSON = DATA_DIR / "state.json"

SEED_URLS = DATA_DIR / "seed_urls.txt"   # <-- DU FÜLLST DAS EINMALIG
OUT_SITEMAP = PUBLIC_DIR / "sitemap.xml"


# =========================
# REGEX: IDs aus Links
# =========================
ID_RE_AP = re.compile(r"/shop/ap/(\d+)")
ID_RE_I = re.compile(r"/i/[^\"'\s<>]+/(\d+)(?:[/?#\"'\s<>]|$)")
ID_RE_ANY_NUM = re.compile(r"\b(\d{6,})\b")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
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
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return s


def debug_write(name: str, content: str) -> None:
    ensure_dirs()
    (DEBUG_DIR / name).write_text(content, encoding="utf-8", errors="ignore")


def is_blocked_response(status: int, html: str) -> bool:
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

            if is_blocked_response(r.status_code, text):
                debug_write(f"{debug_name}_blocked_status{r.status_code}.html", text)
                return None

            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * attempt)
                continue

            if not text.strip():
                debug_write(f"{debug_name}_empty.html", text)
                return None

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
    ids.extend(ID_RE_AP.findall(html))
    ids.extend(ID_RE_I.findall(html))

    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if isinstance(href, str):
                ids.extend(ID_RE_AP.findall(href))
                ids.extend(ID_RE_I.findall(href))
    except Exception:
        pass

    seen: set[str] = set()
    out: list[str] = []
    for x in ids:
        if x and x.isdigit() and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cache_ids() -> list[str]:
    data = load_json(CACHE_JSON, {"ids": {}})
    ids_map = data.get("ids", {}) if isinstance(data.get("ids", {}), dict) else {}
    return [k for k in ids_map.keys() if isinstance(k, str) and k.isdigit()]


def upsert_cache_ids(new_ids: list[str], source: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    data = load_json(CACHE_JSON, {"ids": {}, "meta": {}})
    ids_map = data.get("ids", {})
    if not isinstance(ids_map, dict):
        ids_map = {}

    added = 0
    for pid in new_ids:
        if not isinstance(pid, str) or not pid.isdigit():
            continue
        if pid not in ids_map:
            ids_map[pid] = {"first_seen": now, "last_seen": now, "source": source}
            added += 1
        else:
            if isinstance(ids_map[pid], dict):
                ids_map[pid]["last_seen"] = now

    data["ids"] = ids_map
    data.setdefault("meta", {})
    data["meta"]["updated_at"] = now
    save_json(CACHE_JSON, data)
    return added


def load_used_set() -> set[str]:
    data = load_json(USED_JSON, {"used": [], "last_reset": None})
    used = data.get("used", [])
    if not isinstance(used, list):
        used = []
    return {u for u in used if isinstance(u, str) and u.isdigit()}


def save_used_set(used: set[str], last_reset: str | None = None) -> None:
    save_json(USED_JSON, {"used": sorted(used), "last_reset": last_reset})


def pick_rotating_ids(all_ids: list[str], used_ids: set[str], target: int) -> tuple[list[str], set[str], bool]:
    if not all_ids:
        return [], used_ids, False

    all_set = set(all_ids)
    used_ids = {u for u in used_ids if u in all_set}

    available = [i for i in all_ids if i not in used_ids]
    did_reset = False

    if len(available) < target:
        used_ids = set()
        available = all_ids[:]
        did_reset = True

    seed = int(date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    rng.shuffle(available)

    picked = available[: min(target, len(available))]
    used_ids.update(picked)
    return picked, used_ids, did_reset


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


def discover_design_ids() -> tuple[list[str], bool]:
    s = session()
    found: list[str] = []
    blocked_any = False

    for p in range(1, EXPLORE_PAGES_TO_SCAN + 1):
        url = EXPLORE_URL.format(page=p)
        html = fetch_html(s, url, debug_name=f"explore_p{p}")
        if html is None:
            print(f"WARN: blocked/empty explore page={p}")
            blocked_any = True
            break
        ids = extract_ids_from_html(html)
        if not ids:
            print(f"INFO: no ids on explore page={p} (stop explore)")
            break
        found.extend(ids)
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    for ia in choose_ia_codes_for_run():
        for p in range(1, LISTING_PAGES_PER_CATEGORY_TO_SCAN + 1):
            url = LISTING_URL.format(page=p, ia=ia)
            html = fetch_html(s, url, debug_name=f"listing_{ia}_p{p}")
            if html is None:
                print(f"WARN: blocked/empty ia={ia} page={p}")
                blocked_any = True
                break
            ids = extract_ids_from_html(html)
            if not ids:
                print(f"INFO: no ids ia={ia} page={p} (stop ia)")
                break
            found.extend(ids)
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    seen: set[str] = set()
    out: list[str] = []
    for i in found:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out, blocked_any


def load_seed_ids() -> list[str]:
    """
    Liest data/seed_urls.txt und extrahiert IDs.
    Akzeptiert:
    - https://www.redbubble.com/shop/ap/<id>
    - https://www.redbubble.com/i/.../<id>
    - oder nur rohe Zahlen in der Zeile
    """
    if not SEED_URLS.exists():
        return []
    lines = [l.strip() for l in SEED_URLS.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]

    ids: list[str] = []
    for line in lines:
        ids.extend(ID_RE_AP.findall(line))
        ids.extend(ID_RE_I.findall(line))
        if not ids:
            m = ID_RE_ANY_NUM.findall(line)
            ids.extend(m)

    out = []
    seen = set()
    for x in ids:
        if x and x.isdigit() and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def main() -> int:
    ensure_dirs()

    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--target", type=int, default=TARGET_URLS_DEFAULT)
    args = ap.parse_args()

    run_discover = args.discover or (not args.discover and not args.build)
    run_build = args.build or (not args.discover and not args.build)

    # 0) Seed in Cache bringen (wenn Cache leer)
    cached_ids = load_cache_ids()
    if not cached_ids:
        seed_ids = load_seed_ids()
        if seed_ids:
            added = upsert_cache_ids(seed_ids, source="seed_urls")
            print(f"OK: seeded cache from {SEED_URLS} | ids={len(seed_ids)} | added_new={added}")
        else:
            print(f"INFO: cache empty and no seed file or no IDs in {SEED_URLS}")

    # 1) Discovery (best effort)
    if run_discover:
        new_ids, blocked_any = discover_design_ids()
        if new_ids:
            added = upsert_cache_ids(new_ids, source="auto_discovery")
            print(f"OK: discovery found={len(new_ids)} | added_new={added}")
        else:
            print("WARN: discovery returned 0 IDs.")
            if blocked_any:
                print("WARN: looks blocked/empty. See debug/*.html for details.")

    # 2) Build sitemap (stabil)
    if run_build:
        cached_ids = load_cache_ids()
        used_ids = load_used_set()

        if not cached_ids:
            # Wenn noch keine IDs: nur dann failen, wenn sitemap auch nicht existiert
            if OUT_SITEMAP.exists() and OUT_SITEMAP.stat().st_size > 200:
                print("WARN: cache empty, but sitemap exists -> keeping existing sitemap.xml (exit 0).")
                return 0
            print("ERROR: cache empty AND no existing sitemap.xml. Run discovery or seed cache first.")
            return 1

        picked, used_ids, did_reset = pick_rotating_ids(cached_ids, used_ids, args.target)
        if not picked:
            if OUT_SITEMAP.exists() and OUT_SITEMAP.stat().st_size > 200:
                print("WARN: could not pick ids, keeping existing sitemap.xml (exit 0).")
                return 0
            print("ERROR: could not pick any IDs.")
            return 1

        product_urls = [f"https://www.redbubble.com/shop/ap/{pid}" for pid in picked]
        write_sitemap(product_urls)

        reset_ts = datetime.now(timezone.utc).isoformat() if did_reset else load_json(USED_JSON, {"last_reset": None}).get("last_reset")
        save_used_set(used_ids, last_reset=reset_ts)

        print(f"OK: wrote {len(product_urls)} product URLs to {OUT_SITEMAP}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

# Playwright (Browser-Discovery)
from playwright.sync_api import sync_playwright


# =========================
# CONFIG
# =========================
SHOP_USER = "WearWink"

TARGET_URLS_DEFAULT = 1100

# URL Pool / Rotation
ONLY_I_URLS = True  # True = Sitemap nur /i/ URLs (Mockups)

# Seed wird JEDEN Run importiert (damit neue Designs sofort rein können)
SEED_URLS_TXT = Path("data/seed_urls.txt")

# Playwright Discovery (inkrementell, damit weniger Block)
PW_DESIGNS_PER_RUN = 20           # pro Run wie viele Design-IDs abarbeiten
PW_SLEEP_MIN = 2.0                # Sekunden
PW_SLEEP_MAX = 4.5
PW_HEADLESS_DEFAULT = True        # kannst du per Flag umstellen

# Requests-Discovery (optional; bei dir oft geblockt, kann aber mal helfen)
EXPLORE_PAGES_TO_SCAN = 2
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 2
SLEEP_BETWEEN_REQUESTS_SEC = 1.2

# Repo structure
DATA_DIR = Path("data")
PUBLIC_DIR = Path("public")
DEBUG_DIR = Path("debug")

URL_POOL_JSON = DATA_DIR / "url_pool.json"
USED_URLS_JSON = DATA_DIR / "used_urls.json"
DESIGN_IDS_JSON = DATA_DIR / "design_ids.json"
STATE_JSON = DATA_DIR / "state.json"

OUT_SITEMAP = PUBLIC_DIR / "sitemap.xml"


# =========================
# Patterns
# =========================
ID_RE_I = re.compile(r"/i/[^\"'\s<>]+/(\d+)(?:[/?#\.\"'\s<>]|$)")
ID_RE_AP = re.compile(r"/shop/ap/(\d+)(?:[/?#]|$)")


# =========================
# FS helpers
# =========================
def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def debug_write(name: str, content: str) -> None:
    ensure_dirs()
    (DEBUG_DIR / name).write_text(content, encoding="utf-8", errors="ignore")


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# =========================
# URL normalize + filters
# =========================
def normalize_rb_url(url: str) -> str | None:
    """
    Normalisiert auf https://www.redbubble.com/<path>
    Erlaubt nur:
      - /i/... (Produktseite)
      - /shop/ap/<id> (Designseite, optional)
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
        return (urlparse(url).path or "").startswith("/i/")
    except Exception:
        return False


def extract_design_id_from_url(url: str) -> str | None:
    m = ID_RE_I.search(url)
    if m:
        return m.group(1)
    m = ID_RE_AP.search(url)
    if m:
        return m.group(1)
    return None


# =========================
# Pool: urls
# =========================
def pool_add_urls(urls: list[str], source: str) -> int:
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

        pid = extract_design_id_from_url(nu)

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


# =========================
# used urls
# =========================
def load_used_urls() -> set[str]:
    data = load_json(USED_URLS_JSON, {"used": [], "last_reset": None})
    used = data.get("used", [])
    if not isinstance(used, list):
        used = []
    return {u for u in used if isinstance(u, str)}


def save_used_urls(used: set[str], last_reset: str | None = None) -> None:
    save_json(USED_URLS_JSON, {"used": sorted(used), "last_reset": last_reset})


# =========================
# design ids store
# =========================
def ids_add(ids: list[str], source: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    data = load_json(DESIGN_IDS_JSON, {"ids": {}, "meta": {}})
    ids_map = data.get("ids", {})
    if not isinstance(ids_map, dict):
        ids_map = {}

    added = 0
    for pid in ids:
        if not pid or not pid.isdigit():
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
    save_json(DESIGN_IDS_JSON, data)
    return added


def load_all_ids() -> list[str]:
    data = load_json(DESIGN_IDS_JSON, {"ids": {}})
    ids_map = data.get("ids", {})
    if not isinstance(ids_map, dict):
        return []
    ids = [k for k in ids_map.keys() if isinstance(k, str) and k.isdigit()]
    ids.sort()
    return ids


# =========================
# Rotation + sitemap
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
# Seed import (EVERY run)
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
# Requests discovery (best effort)
# =========================
def requests_session() -> requests.Session:
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
    needles = ["verify you are human", "captcha", "cloudflare", "/cdn-cgi/", "access denied", "request blocked"]
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
                time.sleep(1.0 * attempt)
                continue
            return text
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 * attempt)

    if last_err:
        debug_write(f"{debug_name}_error.txt", last_err)
    return None


def extract_urls_from_html(html: str) -> list[str]:
    out: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a.get("href")
            if isinstance(href, str):
                nu = normalize_rb_url(href)
                if nu:
                    out.append(nu)
    except Exception:
        pass

    if ONLY_I_URLS:
        out = [u for u in out if is_i_url(u)]

    # dedupe
    seen = set()
    dedup = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def discover_with_requests() -> tuple[list[str], bool]:
    s = requests_session()
    found: list[str] = []
    blocked_any = False

    for p in range(1, EXPLORE_PAGES_TO_SCAN + 1):
        url = f"https://www.redbubble.com/people/{SHOP_USER}/explore?asc=u&page={p}&sortOrder=recent"
        html = fetch_html(s, url, debug_name=f"req_explore_p{p}")
        if html is None:
            print(f"WARN: requests blocked/empty explore page={p}")
            blocked_any = True
            break
        urls = extract_urls_from_html(html)
        if not urls:
            break
        found.extend(urls)
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    return found, blocked_any


# =========================
# Playwright discovery (from /shop/ap/<id> -> collect many /i/ links)
# =========================
def load_state() -> dict:
    return load_json(STATE_JSON, {"pw_id_cursor": 0})


def save_state(st: dict) -> None:
    save_json(STATE_JSON, st)


def sleep_random() -> None:
    time.sleep(random.uniform(PW_SLEEP_MIN, PW_SLEEP_MAX))


def pw_collect_i_urls_for_design(context, design_id: str) -> list[str]:
    """
    Open /shop/ap/<id>, collect all /i/ product links visible in DOM.
    """
    url = f"https://www.redbubble.com/shop/ap/{design_id}"
    page = context.new_page()

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else None

        # kleine Wartezeit damit Elemente rendern
        page.wait_for_timeout(1500)

        html = page.content()
        if html and ("captcha" in html.lower() or "verify you are human" in html.lower() or "/cdn-cgi/" in html.lower()):
            debug_write(f"pw_ap_{design_id}_blocked.html", html)
            return []

        if status in (403, 429):
            debug_write(f"pw_ap_{design_id}_status{status}.html", html or "")
            return []

        # Links sammeln
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        urls: list[str] = []
        for h in hrefs:
            if isinstance(h, str) and "/i/" in h:
                nu = normalize_rb_url(h)
                if nu and is_i_url(nu):
                    urls.append(nu)

        # dedupe
        seen = set()
        dedup = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                dedup.append(u)

        # Optional Debug bei leeren Ergebnissen
        if not dedup:
            debug_write(f"pw_ap_{design_id}_no_i_links.html", html or "")

        return dedup
    finally:
        try:
            page.close()
        except Exception:
            pass


def discover_with_playwright(headless: bool, per_run: int) -> tuple[int, int]:
    """
    Returns (designs_processed, urls_added_total)
    """
    ensure_dirs()

    # IDs aus DESIGN_IDS_JSON
    all_ids = load_all_ids()
    if not all_ids:
        print("INFO: no design IDs available for Playwright discovery (seed some /i/ URLs first).")
        return 0, 0

    st = load_state()
    cursor = int(st.get("pw_id_cursor", 0)) if str(st.get("pw_id_cursor", "0")).isdigit() else 0

    batch = []
    for i in range(per_run):
        batch.append(all_ids[(cursor + i) % len(all_ids)])

    new_cursor = (cursor + len(batch)) % len(all_ids)

    urls_added_total = 0

    with sync_playwright() as p:
        # Chromium ist meistens stabil. Du kannst auch p.chromium.launch(channel="msedge") probieren,
        # aber channel ist nicht überall verfügbar.
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        # Persistent Profil (Cookies/Storage) unter data/pw_profile
        user_data_dir = str((DATA_DIR / "pw_profile").resolve())

        # Leider unterstützt sync_playwright persistent context direkt über launch_persistent_context:
        browser.close()
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            locale="en-US",
            timezone_id="Europe/Berlin",
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        )

        try:
            for did in batch:
                urls = pw_collect_i_urls_for_design(context, did)
                if urls:
                    added = pool_add_urls(urls, source="pw_from_shop_ap")
                    urls_added_total += added
                    print(f"PW: design {did} -> found {len(urls)} /i/ links | added_new={added}")
                else:
                    print(f"PW: design {did} -> no /i/ links (maybe blocked or not rendered)")

                sleep_random()
        finally:
            try:
                context.close()
            except Exception:
                pass

    st["pw_id_cursor"] = new_cursor
    save_state(st)

    return len(batch), urls_added_total


# =========================
# MAIN
# =========================
def main() -> int:
    ensure_dirs()

    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--discover-requests", action="store_true")
    ap.add_argument("--discover-playwright", action="store_true")
    ap.add_argument("--target", type=int, default=TARGET_URLS_DEFAULT)
    ap.add_argument("--pw-per-run", type=int, default=PW_DESIGNS_PER_RUN)
    ap.add_argument("--pw-headful", action="store_true")  # falls du mal interaktiv testen willst
    args = ap.parse_args()

    run_build = args.build or (not args.build and not args.discover_requests and not args.discover_playwright)

    # 0) Seed import every run
    seed_urls = load_seed_urls()
    if seed_urls:
        added = pool_add_urls(seed_urls, source="seed_urls")
        seed_ids = [extract_design_id_from_url(u) for u in seed_urls]
        seed_ids = [x for x in seed_ids if x and x.isdigit()]
        ids_added = ids_add(seed_ids, source="seed_urls")
        print(f"OK: imported seed_urls.txt | urls_in_file={len(seed_urls)} | added_urls={added} | added_ids={ids_added}")
    else:
        print("INFO: seed_urls.txt empty or missing (no seed import)")

    # Also IDs aus vorhandenem URL pool ableiten (damit Playwright was hat)
    pool = load_pool_urls()
    pool_ids = []
    for u in pool:
        pid = extract_design_id_from_url(u)
        if pid and pid.isdigit():
            pool_ids.append(pid)
    ids_add(pool_ids, source="from_url_pool")

    # 1) Requests discovery optional
    if args.discover_requests:
        urls, blocked = discover_with_requests()
        if urls:
            added = pool_add_urls(urls, source="req_discovery")
            ids = [extract_design_id_from_url(u) for u in urls]
            ids = [x for x in ids if x and x.isdigit()]
            ids_added = ids_add(ids, source="req_discovery")
            print(f"OK: requests discovery found={len(urls)} | added_urls={added} | added_ids={ids_added}")
        else:
            print("WARN: requests discovery found 0 URLs.")
            if blocked:
                print("WARN: requests looked blocked. Check debug/req_explore_*.html")

    # 2) Playwright discovery optional (beste Chance für “mehr Produkte je Design”)
    if args.discover_playwright:
        headless = (not args.pw_headful) and PW_HEADLESS_DEFAULT
        processed, urls_added = discover_with_playwright(headless=headless, per_run=args.pw_per_run)
        print(f"OK: playwright processed_designs={processed} | urls_added_new={urls_added}")

    # 3) Build sitemap
    if run_build:
        pool = load_pool_urls()
        used = load_used_urls()

        # Filter: wenn ONLY_I_URLS True, sicherstellen dass nur /i/ im Pool genutzt wird
        if ONLY_I_URLS:
            pool = [u for u in pool if is_i_url(u)]

        if not pool:
            if OUT_SITEMAP.exists() and OUT_SITEMAP.stat().st_size > 200:
                print("WARN: pool empty but sitemap exists -> keep existing sitemap.xml (exit 0)")
                return 0
            print("ERROR: pool empty AND no sitemap exists. Add /i/ URLs to data/seed_urls.txt")
            return 1

        effective_target = min(args.target, len(pool))
        picked, used, did_reset = pick_rotating_urls(pool, used, effective_target)

        write_sitemap(picked)
        reset_ts = datetime.now(timezone.utc).isoformat() if did_reset else load_json(USED_URLS_JSON, {"last_reset": None}).get("last_reset")
        save_used_urls(used, last_reset=reset_ts)

        print(f"OK: wrote {len(picked)} URLs to {OUT_SITEMAP} | pool_size={len(pool)} | only_i={ONLY_I_URLS}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

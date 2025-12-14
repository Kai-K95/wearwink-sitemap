from __future__ import annotations

import re
import json
import time
import random
import argparse
from pathlib import Path
from datetime import datetime, timezone, date
from xml.sax.saxutils import escape
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

from playwright.sync_api import sync_playwright


# =========================
# CONFIG
# =========================
SHOP_USER = "WearWink"
TARGET_URLS_DEFAULT = 1100

# Sitemap nur /i/ URLs für Mockups
ONLY_I_URLS = True

# Seed wird JEDEN Run importiert (damit neue Designs sofort rein können)
SEED_URLS_TXT = Path("data/seed_urls.txt")

# Playwright Discovery (inkrementell, damit weniger Block)
PW_DESIGNS_PER_RUN_DEFAULT = 10
PW_SLEEP_MIN = 2.0
PW_SLEEP_MAX = 4.5
PW_HEADLESS_DEFAULT = True

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
# REGEX
# =========================
ID_RE_I = re.compile(r"/i/[^\"'\s<>]+/(\d+)(?:[/?#\.\"'\s<>]|$)")
ID_RE_AP = re.compile(r"/shop/ap/(\d+)(?:[/?#]|$)")
ID_RE_ANY_NUM = re.compile(r"\b(\d{6,})\b")

# Redbubble masking: https:{{%2F}}{{%2F}}www.redbubble.com{{%2F}}i{{%2F}}...
_RB_ESC = re.compile(r"\{\{%([0-9A-Fa-f]{2})\}\}")


# =========================
# HELPERS
# =========================
def rb_unescape(text: str) -> str:
    # macht aus "{{%2F}}" -> "%2F" und decodiert dann -> "/"
    t = _RB_ESC.sub(lambda m: "%" + m.group(1), text)
    return unquote(t)


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


def is_i_url(url: str) -> bool:
    try:
        return (urlparse(url).path or "").startswith("/i/")
    except Exception:
        return False


def normalize_rb_url(url: str) -> str | None:
    """
    Normalisiert auf https://www.redbubble.com/<path>
    Erlaubt nur /i/... URLs (wenn ONLY_I_URLS=True)
    """
    if not url:
        return None
    u = rb_unescape(url.strip())
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
        return f"https://www.redbubble.com{path}"

    if not ONLY_I_URLS and path.startswith("/shop/ap/"):
        return f"https://www.redbubble.com{path}"

    return None


def extract_design_id_from_text(text: str) -> str | None:
    t = rb_unescape(text)
    m = ID_RE_I.search(t)
    if m:
        return m.group(1)
    m = ID_RE_AP.search(t)
    if m:
        return m.group(1)
    m = ID_RE_ANY_NUM.search(t)
    if m:
        return m.group(1)
    return None


# =========================
# POOL URLS
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

        pid = extract_design_id_from_text(nu)

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
# USED URLS
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
# DESIGN IDS
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


def load_all_ids_unique() -> list[str]:
    data = load_json(DESIGN_IDS_JSON, {"ids": {}})
    ids_map = data.get("ids", {})
    if not isinstance(ids_map, dict):
        return []
    ids = [k for k in ids_map.keys() if isinstance(k, str) and k.isdigit()]
    return sorted(set(ids))


# =========================
# ROTATION + SITEMAP
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
# SEED IMPORT
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

    seen = set()
    dedup = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


# =========================
# PLAYWRIGHT DISCOVERY
# =========================
def load_state() -> dict:
    return load_json(STATE_JSON, {"pw_id_cursor": 0})


def save_state(st: dict) -> None:
    save_json(STATE_JSON, st)


def sleep_random() -> None:
    time.sleep(random.uniform(PW_SLEEP_MIN, PW_SLEEP_MAX))


def pw_collect_i_urls_for_design(context, design_id: str) -> list[str]:
    """
    Öffnet /shop/ap/<id> und sammelt /i/ URLs.
    RB liefert URLs oft maskiert: https:{{%2F}}{{%2F}}...
    -> wir normalisieren über rb_unescape(html).
    """
    url = f"https://www.redbubble.com/shop/ap/{design_id}"
    page = context.new_page()

    def is_block_html(html: str) -> bool:
        low = (html or "").lower()
        return any(x in low for x in ["captcha", "verify you are human", "/cdn-cgi/", "access denied", "request blocked"])

    try:
        resp = page.goto(url, wait_until="networkidle", timeout=60000)
        status = resp.status if resp else None

        # Lazy load / Render
        for _ in range(6):
            try:
                page.mouse.wheel(0, 1700)
            except Exception:
                pass
            page.wait_for_timeout(800)

        html = page.content() or ""
        html_norm = rb_unescape(html)

        # Wenn block, speichern wir Debug - versuchen aber trotzdem zu extrahieren
        if status in (403, 429) or is_block_html(html_norm):
            debug_write(f"pw_ap_{design_id}_blocked.html", html)

        urls: list[str] = []

        # A) DOM hrefs (falls vorhanden)
        try:
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
            for h in hrefs:
                if isinstance(h, str):
                    h2 = rb_unescape(h)
                    if "/i/" in h2:
                        nu = normalize_rb_url(h2)
                        if nu and is_i_url(nu):
                            urls.append(nu)
        except Exception:
            pass

        # B) aus HTML (auch JSON/Script)
        try:
            # absolute
            for m in re.findall(r"https?://www\.redbubble\.com/i/[^\"'\s<>]+", html_norm):
                nu = normalize_rb_url(m)
                if nu and is_i_url(nu):
                    urls.append(nu)

            # relative
            for m in re.findall(r"(/i/[^\"'\s<>]+)", html_norm):
                nu = normalize_rb_url(m)
                if nu and is_i_url(nu):
                    urls.append(nu)
        except Exception:
            pass

        # dedupe
        seen = set()
        dedup = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                dedup.append(u)

        if not dedup:
            debug_write(f"pw_ap_{design_id}_no_i_links.html", html)

        return dedup

    finally:
        try:
            page.close()
        except Exception:
            pass


def discover_with_playwright(headless: bool, per_run: int) -> tuple[int, int]:
    all_ids = load_all_ids_unique()
    if not all_ids:
        print("INFO: no design IDs available for Playwright discovery (seed at least 1 /i/ URL).")
        return 0, 0

    st = load_state()
    cursor = int(st.get("pw_id_cursor", 0)) if str(st.get("pw_id_cursor", "0")).isdigit() else 0

    batch = []
    i = 0
    while len(batch) < min(per_run, len(all_ids)) and i < len(all_ids) * 2:
        did = all_ids[(cursor + i) % len(all_ids)]
        if did not in batch:
            batch.append(did)
        i += 1

    new_cursor = (cursor + len(batch)) % len(all_ids)
    urls_added_total = 0

    with sync_playwright() as p:
        user_data_dir = str((DATA_DIR / "pw_profile").resolve())
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
                    print(f"PW: design {did} -> no /i/ links (blocked or not renderable)")
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
    ap.add_argument("--discover-playwright", action="store_true")
    ap.add_argument("--target", type=int, default=TARGET_URLS_DEFAULT)
    ap.add_argument("--pw-per-run", type=int, default=PW_DESIGNS_PER_RUN_DEFAULT)
    ap.add_argument("--pw-headful", action="store_true")
    args = ap.parse_args()

    run_build = args.build or (not args.build and not args.discover_playwright)

    # 0) Seed import every run
    seed_urls = load_seed_urls()
    if seed_urls:
        added_urls = pool_add_urls(seed_urls, source="seed_urls")
        seed_ids = []
        for u in seed_urls:
            pid = extract_design_id_from_text(u)
            if pid and pid.isdigit():
                seed_ids.append(pid)
        added_ids = ids_add(seed_ids, source="seed_urls")
        print(f"OK: imported seed_urls.txt | urls_in_file={len(seed_urls)} | added_urls={added_urls} | added_ids={added_ids}")
    else:
        print("INFO: seed_urls.txt empty or missing (no seed import)")

    # IDs aus URL pool ableiten
    pool_urls_now = load_pool_urls()
    derived_ids = []
    for u in pool_urls_now:
        pid = extract_design_id_from_text(u)
        if pid and pid.isdigit():
            derived_ids.append(pid)
    ids_add(derived_ids, source="from_url_pool")

    # 1) Playwright discovery
    if args.discover_playwright:
        headless = (not args.pw_headful) and PW_HEADLESS_DEFAULT
        processed, urls_added = discover_with_playwright(headless=headless, per_run=args.pw_per_run)
        print(f"OK: playwright processed_designs={processed} | urls_added_new={urls_added}")

    # 2) Build sitemap
    if run_build:
        pool = load_pool_urls()
        used = load_used_urls()

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

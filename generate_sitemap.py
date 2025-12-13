import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from playwright.sync_api import sync_playwright

# =========================
# SETTINGS
# =========================
ARTIST = "WearWink"
BASE = "https://www.redbubble.com"

EXPLORE_URL = f"{BASE}/people/{ARTIST}/explore?asc=u&page={{page}}&sortOrder=recent"

EXPLORE_PAGES_TO_SCAN = 10          # wie viele "Designs entdecken" Seiten scannen
MAX_DESIGNS_TO_VISIT = 160          # wie viele /shop/ap/ Seiten besuchen
TARGET_I_URLS_PER_DAY = 1100        # dein Ziel
MAX_POOL_SIZE = 20000               # Sicherheitslimit

GOTO_TIMEOUT_MS = 60_000
SLEEP_SEC = 0.5

OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
OUT_URLS = Path("urls.txt")

USED_URLS = Path("used_urls.txt")            # damit URLs nicht ständig wiederkommen
DESIGN_CACHE = Path("design_ids_cache.txt")  # Fallback, falls Explore mal leer/blocked

DEBUG_EXPLORE = Path("debug_explore.html")
DEBUG_DESIGN = Path("debug_design.html")

# =========================
# REGEX
# =========================
DESIGN_ID_RE = re.compile(r"/shop/ap/(\d+)", re.IGNORECASE)
I_URL_RE = re.compile(r"^https://www\.redbubble\.com/(?:[a-z]{2}/)?i/[^\"<>\s]+$", re.IGNORECASE)

BLOCK_MARKERS = (
    "cloudflare",
    "verify you are human",
    "checking your browser",
    "attention required",
    "captcha",
    "bestätigen sie, dass sie ein mensch sind",
)

def normalize_url(u: str) -> str:
    u = u.strip()
    u = u.replace("http://", "https://")
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("https://www.redbubble.com/en/", "https://www.redbubble.com/")
    return u

def is_blocked(html: str) -> bool:
    h = html.lower()
    return any(m in h for m in BLOCK_MARKERS)

def today_seed() -> int:
    # deterministisch “zufällig” pro Tag
    s = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:12], 16)

def read_lines(p: Path) -> list[str]:
    if not p.exists():
        return []
    return [x.strip() for x in p.read_text(encoding="utf-8", errors="ignore").splitlines() if x.strip()]

def write_lines(p: Path, lines: list[str]) -> None:
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

def unique_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        loc = xml_escape(u, {"'": "&apos;", '"': "&quot;"})
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")

def extract_design_ids_from_page_links(links: list[str]) -> list[str]:
    ids = []
    for href in links:
        m = DESIGN_ID_RE.search(href)
        if m:
            ids.append(m.group(1))
    return unique_keep_order(ids)

def pick_rotating(pool: list[str], used: set[str], k: int) -> list[str]:
    if not pool:
        return []

    # zuerst neue URLs, sonst recycle
    fresh = [u for u in pool if u not in used]
    if len(fresh) < k:
        used.clear()
        fresh = pool[:]

    # tägliche Rotation (deterministisch)
    fresh_sorted = sorted(fresh)
    seed = today_seed()
    start = seed % len(fresh_sorted)
    rotated = fresh_sorted[start:] + fresh_sorted[:start]
    return rotated[:k]

def main() -> None:
    used_set = set(read_lines(USED_URLS))
    cached_designs = read_lines(DESIGN_CACHE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) Explore -> Design IDs
        design_ids: list[str] = []
        for i in range(1, EXPLORE_PAGES_TO_SCAN + 1):
            url = EXPLORE_URL.format(page=i)
            page.goto(url, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
            html = page.content()

            if i == 1:
                DEBUG_EXPLORE.write_text(html, encoding="utf-8")

            if is_blocked(html):
                print(f"BLOCKED: explore page {i}")
                design_ids = []
                break

            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            ids = extract_design_ids_from_page_links(links)
            if ids:
                design_ids.extend(ids)

            design_ids = unique_keep_order(design_ids)
            print(f"explore {i}: total design ids = {len(design_ids)}")

            if len(design_ids) >= MAX_DESIGNS_TO_VISIT:
                break

        # Fallback cache
        if not design_ids and cached_designs:
            print("Using cached design IDs (explore empty/blocked)")
            design_ids = cached_designs[:MAX_DESIGNS_TO_VISIT]

        if not design_ids:
            browser.close()
            raise SystemExit("❌ No design IDs found (explore blocked and cache empty).")

        write_lines(DESIGN_CACHE, design_ids)

        # 2) Für jedes Design: /shop/ap/<id> -> /i/ URLs sammeln
        pool_set: set[str] = set()
        for idx, did in enumerate(design_ids[:MAX_DESIGNS_TO_VISIT], start=1):
            durl = f"{BASE}/shop/ap/{did}"
            page.goto(durl, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
            html = page.content()

            if idx == 1:
                DEBUG_DESIGN.write_text(html, encoding="utf-8")

            if is_blocked(html):
                print(f"BLOCKED: ap/{did}")
                continue

            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            added = 0
            for href in links:
                href = normalize_url(href)
                if I_URL_RE.match(href):
                    if href not in pool_set:
                        pool_set.add(href)
                        added += 1

            print(f"ap {idx}/{min(len(design_ids), MAX_DESIGNS_TO_VISIT)}: +{added} (pool={len(pool_set)})")

            if len(pool_set) >= MAX_POOL_SIZE:
                break

        browser.close()

    pool = sorted(pool_set)
    if not pool:
        raise SystemExit("❌ Pool is 0 (no /i/ URLs found).")

    picked = pick_rotating(pool, used_set, TARGET_I_URLS_PER_DAY)

    # used updaten (damit keine Wiederholung, bis Pool “durch” ist)
    for u in picked:
        used_set.add(u)
    used_list = sorted(used_set)
    # limit file growth
    if len(used_list) > 200000:
        used_list = used_list[-200000:]
    write_lines(USED_URLS, used_list)

    # Output
    write_lines(OUT_URLS, picked)
    write_sitemap(picked)
    OUT_COUNT.write_text(str(len(picked)) + "\n", encoding="utf-8")

    print(f"✅ OK: wrote {len(picked)} /i/ URLs")

if __name__ == "__main__":
    main()

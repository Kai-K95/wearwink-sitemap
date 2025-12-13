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

# Quelle für "deine Designs" (ohne /de/)
EXPLORE_URL = f"{BASE}/people/{ARTIST}/explore?asc=u&page={{page}}&sortOrder=recent"

# Wie viele Explore-Seiten wir pro Run abgrasen (mehr = mehr Requests)
EXPLORE_PAGES_TO_SCAN = 8

# Wie viele Designs (ap-IDs) wir maximal pro Run besuchen
MAX_DESIGNS_TO_VISIT = 120

# Ziel: täglich so viele /i/-Produktseiten
TARGET_PRODUCT_URLS_PER_DAY = 1100

# Wenn Pool sehr groß wird, stoppen wir früher (spart Requests)
MAX_POOL_SIZE = 12000

# Timeout
GOTO_TIMEOUT_MS = 60_000

# Dateien
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")
USED_URLS = Path("used_urls.txt")
DESIGN_CACHE = Path("design_ids_cache.txt")

DEBUG_EXPLORE_HTML = Path("debug_explore.html")
DEBUG_DESIGN_HTML = Path("debug_design.html")


# =========================
# HELPERS
# =========================
DESIGN_ID_RE = re.compile(r"/shop/ap/(\d+)", re.IGNORECASE)
PRODUCT_I_RE = re.compile(r"^https://www\.redbubble\.com/i/[^\"<>\s]+$", re.IGNORECASE)

CLOUDFLARE_HINTS = (
    "cloudflare",
    "verify you are human",
    "checking your browser",
    "attention required",
    "captcha",
)


def is_blocked(html: str) -> bool:
    h = html.lower()
    return any(x in h for x in CLOUDFLARE_HINTS)


def normalize_url(u: str) -> str:
    # Nur www.redbubble.com (kein /de/ usw.)
    u = u.strip()
    u = u.replace("http://www.redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("https://redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("http://redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("https://www.redbubble.com/de", "https://www.redbubble.com")
    return u


def today_seed() -> int:
    # deterministische Rotation pro Tag
    s = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16)


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_sitemap(urls: list[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        # XML-safe (wichtig wegen &)
        loc = xml_escape(u, {"'": "&apos;", '"': "&quot;"})
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


# =========================
# CRAWL LOGIC
# =========================
def extract_design_ids_from_links(links: list[str]) -> list[str]:
    ids = []
    for href in links:
        m = DESIGN_ID_RE.search(href)
        if m:
            ids.append(m.group(1))
    # unique, Reihenfolge behalten
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def pick_daily(urls_pool: list[str], used: set[str]) -> list[str]:
    # bevorzugt neue URLs, sonst reset
    fresh = [u for u in urls_pool if u not in used]
    if len(fresh) < TARGET_PRODUCT_URLS_PER_DAY:
        # nicht genug neue → reset used
        used.clear()
        fresh = urls_pool[:]

    # deterministisch pro Tag "zufällig" mischen
    seed = today_seed()
    fresh_sorted = sorted(fresh)
    rotated = fresh_sorted[seed % len(fresh_sorted):] + fresh_sorted[:seed % len(fresh_sorted)]

    return rotated[:TARGET_PRODUCT_URLS_PER_DAY]


def main() -> None:
    used_set = set(read_lines(USED_URLS))
    cached_designs = read_lines(DESIGN_CACHE)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1) Explore-Seiten → Design IDs sammeln
        print("== Collecting design IDs from Explore ==")
        design_ids: list[str] = []

        blocked_explore = False
        for i in range(1, EXPLORE_PAGES_TO_SCAN + 1):
            url = EXPLORE_URL.format(page=i)
            page.goto(url, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
            html = page.content()

            if i == 1:
                DEBUG_EXPLORE_HTML.write_text(html, encoding="utf-8")

            if is_blocked(html):
                print(f"BLOCKED/EMPTY: explore page={i}")
                blocked_explore = True
                break

            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            ids = extract_design_ids_from_links(links)
            if not ids:
                print(f"EMPTY: explore page={i}")
                continue

            print(f"explore page {i}: +{len(ids)} ids")
            design_ids.extend(ids)

            if len(design_ids) >= MAX_DESIGNS_TO_VISIT:
                break

        # Fallback: Cache nutzen, wenn Explore blockt
        design_ids = list(dict.fromkeys(design_ids))  # unique keep order
        if (not design_ids) and cached_designs:
            print("⚠️ Explore blocked/empty → using cached design_ids_cache.txt")
            design_ids = cached_designs[:MAX_DESIGNS_TO_VISIT]

        if not design_ids:
            browser.close()
            raise SystemExit("❌ No design IDs available (no cache + blocked).")

        # Cache aktualisieren
        write_lines(DESIGN_CACHE, design_ids)

        # 2) Pro Design → /i/ Produktlinks sammeln
        print("== Collecting /i/ product URLs from design pages ==")
        pool_set: set[str] = set()

        for idx, did in enumerate(design_ids, start=1):
            durl = f"{BASE}/shop/ap/{did}"
            page.goto(durl, wait_until="networkidle", timeout=GOTO_TIMEOUT_MS)
            html = page.content()

            if idx == 1:
                DEBUG_DESIGN_HTML.write_text(html, encoding="utf-8")

            if is_blocked(html):
                print(f"BLOCKED/EMPTY: ap={did}")
                continue

            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for href in links:
                href = normalize_url(href)
                if PRODUCT_I_RE.match(href):
                    pool_set.add(href)

            if len(pool_set) >= MAX_POOL_SIZE:
                break

        browser.close()

    pool = sorted(pool_set)
    print(f"pool size: {len(pool)}")

    if not pool:
        raise SystemExit("❌ Pool is 0. (Blocked or no /i/ links found)")

    # 3) Tages-Auswahl (Rotation + „nur einmal“ bis Pool leer ist)
    picked = pick_daily(pool, used_set)

    # used updaten + begrenzen (damit Datei nicht unendlich explodiert)
    for u in picked:
        used_set.add(u)
    used_list = sorted(used_set)
    if len(used_list) > 50_000:
        used_list = used_list[-50_000:]
    write_lines(USED_URLS, used_list)

    # 4) Output
    write_sitemap(picked)
    OUT_COUNT.write_text(str(len(picked)) + "\n", encoding="utf-8")

    print(f"✅ OK: wrote {len(picked)} /i/ URLs")


if __name__ == "__main__":
    main()

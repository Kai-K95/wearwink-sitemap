from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import random

# =========================
# CONFIG
# =========================
BASE = "https://www.redbubble.com"
USERNAME = "WearWink"  # wichtig: genau so wie dein Account heißt

# Deine neue Filter-URL entspricht im Kern:
# /people/WearWink/shop?artistUserName=WearWink&asc=u&iaCode=u-clothing
# -> Wir generieren daraus viele Seiten (page=1..N) und rotieren.

IA_CODES = [
    "u-clothing",   # Kleidung (dein Beispiel)
    # Wenn du später mehr willst, einfach ergänzen, z.B.:
    # "u-stationery", "u-home", "u-accessories" ...
]

SORT = "recent"       # "recent" oder "bestselling"
ASC = "u"

# Wie groß ist der Pool, aus dem wir ziehen?
# Je größer, desto länger ohne Wiederholung.
PAGES_PER_IACODE_POOL = 500   # z.B. 500 Seiten pro Kategorie im Pool

# Wie viele Links soll die Sitemap pro Run enthalten?
MAX_URLS_PER_RUN = 2000

# Rotation: täglich stabiler Shuffle, aber OHNE Wiederholung über Runs (State Datei)
DAILY_STABLE_SHUFFLE = True

# Output
OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS = Path("urls.txt")
OUT_COUNT = Path("last_count.txt")
STATE_USED = Path("used_urls.txt")  # merkt sich "verwendete" URLs
OUT_INDEX = Path("index.html")


# =========================
# URL Builder
# =========================
def build_shop_url(page: int, ia_code: str | None) -> str:
    params = {
        "artistUserName": USERNAME,
        "asc": ASC,
        "page": page,
        "sortOrder": SORT,
    }
    if ia_code:
        params["iaCode"] = ia_code

    q = urlencode(params)
    # canonical Pfad mit korrekter Groß-/Kleinschreibung:
    return f"{BASE}/people/{USERNAME}/shop?{q}"


def generate_pool() -> list[str]:
    pool: list[str] = []

    # optional: einmal "ohne iaCode" (alles gemischt)
    # pool += [build_shop_url(p, None) for p in range(1, PAGES_PER_IACODE_POOL + 1)]

    # mit iaCodes
    for ia in IA_CODES:
        for p in range(1, PAGES_PER_IACODE_POOL + 1):
            pool.append(build_shop_url(p, ia))

    # dedupe
    pool = sorted(set(pool))
    return pool


# =========================
# Sitemap writers
# =========================
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


def write_index(count: int) -> None:
    OUT_INDEX.write_text(
        f"""<!doctype html><meta charset="utf-8">
<title>WearWink Sitemap</title>
<h1>WearWink Sitemap</h1>
<p>URLs in Sitemap: <b>{count}</b></p>
<ul>
<li><a href="sitemap.xml">sitemap.xml</a></li>
<li><a href="urls.txt">urls.txt</a></li>
<li><a href="last_count.txt">last_count.txt</a></li>
<li><a href="used_urls.txt">used_urls.txt</a></li>
</ul>
""",
        encoding="utf-8",
    )


# =========================
# State (used urls)
# =========================
def load_used() -> set[str]:
    if not STATE_USED.exists():
        return set()
    return {line.strip() for line in STATE_USED.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_used(used: set[str]) -> None:
    STATE_USED.write_text("\n".join(sorted(used)) + "\n", encoding="utf-8")


# =========================
# Main selection logic
# =========================
def pick_urls(pool: list[str], used: set[str]) -> tuple[list[str], set[str], bool]:
    remaining = [u for u in pool if u not in used]

    # Shuffle (täglich stabil oder komplett random)
    if DAILY_STABLE_SHUFFLE:
        seed = int(datetime.utcnow().strftime("%Y%m%d"))
        rng = random.Random(seed)
        rng.shuffle(remaining)
    else:
        random.shuffle(remaining)

    # Wenn nicht genug übrig ist: reset (sonst könntest du nie wieder 2000 ohne Wiederholung liefern)
    did_reset = False
    if len(remaining) < MAX_URLS_PER_RUN:
        used.clear()
        did_reset = True
        remaining = pool[:]  # alles wieder verfügbar
        if DAILY_STABLE_SHUFFLE:
            seed = int(datetime.utcnow().strftime("%Y%m%d"))
            rng = random.Random(seed)
            rng.shuffle(remaining)
        else:
            random.shuffle(remaining)

    chosen = remaining[:MAX_URLS_PER_RUN]
    used.update(chosen)
    return sorted(set(chosen)), used, did_reset


def main():
    pool = generate_pool()
    used = load_used()

    urls, used, did_reset = pick_urls(pool, used)

    OUT_URLS.write_text("\n".join(urls) + "\n", encoding="utf-8")
    OUT_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")
    save_used(used)
    write_sitemap(urls)
    write_index(len(urls))

    print(f"✅ OK: {len(urls)} URLs in sitemap (pool={len(pool)}, used={len(used)})")
    if did_reset:
        print("♻️ Pool was exhausted -> used_urls.txt RESET (starting fresh).")


if __name__ == "__main__":
    main()

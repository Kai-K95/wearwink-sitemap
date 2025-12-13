from __future__ import annotations

import os
import re
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Set

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


# =========================
# CONFIG
# =========================
USERNAME = "WearWink"

# Wieviele Produkt-URLs sollen in der sitemap stehen (rotierend pro Tag)?
TOTAL_URLS = 1100

# Wie viele Listing-Seiten versuchen wir zum IDs sammeln? (niedrig halten -> weniger Risiko geblockt zu werden)
DISCOVERY_PAGES = 25

# Wartezeit zwischen Requests
SLEEP_SECONDS = 1.2

# Dateien (im Repo-Root)
OUT_SITEMAP = Path("sitemap.xml")
OUT_URLS_TXT = Path("urls.txt")
OUT_LAST_COUNT = Path("last_count.txt")
CACHE_IDS = Path("design_ids.txt")

# Patterns
DESIGN_ID_RE = re.compile(r"/shop/ap/(\d+)", re.IGNORECASE)

# Candidate listing pages to find /shop/ap/<id> links
LISTING_URL_TEMPLATES = [
    # Explore (oft gut zum "neueste")
    "https://www.redbubble.com/people/{u}/explore?asc=u&page={p}&sortOrder=recent",
    # Shop listing
    "https://www.redbubble.com/people/{u}/shop?artistUserName={u}&asc=u&page={p}&sortOrder=recent",
]


def _today_seed() -> int:
    # tägliche Rotation: deterministische “Zufalls”-Reihenfolge pro Tag
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h = hashlib.sha256(day.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _headers() -> dict:
    # “normale” Browser-Header (keine Umgehungs-Tricks)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    }


def fetch_html(session: requests.Session, url: str, timeout: int = 30) -> tuple[int, str]:
    r = session.get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
    return r.status_code, r.text or ""


def looks_blocked(status: int, html: str) -> bool:
    # grobe Heuristik: 403/429/503 oder typische Block-Seiten
    if status in (403, 429, 503):
        return True
    low = (html or "").lower()
    if "cloudflare" in low and ("attention required" in low or "checking your browser" in low):
        return True
    if "access denied" in low or "request blocked" in low:
        return True
    return False


def extract_design_ids(html: str) -> List[str]:
    # 1) schnell per regex
    ids = DESIGN_ID_RE.findall(html or "")
    if ids:
        # unique, stable order
        out = []
        seen = set()
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    # 2) fallback: bs4 links durchsuchen
    soup = BeautifulSoup(html or "", "lxml")
    out: List[str] = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DESIGN_ID_RE.search(href)
        if m:
            did = m.group(1)
            if did not in seen:
                seen.add(did)
                out.append(did)
    return out


def load_cached_ids() -> List[str]:
    if not CACHE_IDS.exists():
        return []
    ids = []
    for line in CACHE_IDS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            ids.append(line)
    # unique
    out = []
    seen = set()
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def save_cached_ids(ids: Iterable[str]) -> None:
    ids = list(ids)
    CACHE_IDS.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")


def discover_ids() -> tuple[List[str], bool]:
    """
    Returns: (ids, blocked_flag)
    """
    session = requests.Session()
    found: List[str] = []
    seen: Set[str] = set()
    blocked_any = False

    for p in range(1, DISCOVERY_PAGES + 1):
        for tmpl in LISTING_URL_TEMPLATES:
            url = tmpl.format(u=USERNAME, p=p)
            try:
                status, html = fetch_html(session, url)
            except Exception:
                continue

            if looks_blocked(status, html):
                blocked_any = True
                continue

            ids = extract_design_ids(html)
            for did in ids:
                if did not in seen:
                    seen.add(did)
                    found.append(did)

            time.sleep(SLEEP_SECONDS)

    return found, blocked_any


def pick_rotating(ids: List[str], n: int) -> List[str]:
    if not ids:
        return []

    # deterministisch shuffle per Tag
    seed = _today_seed()
    # einfacher deterministischer Shuffle: sort by hash(seed + id)
    def key_fn(did: str) -> str:
        h = hashlib.sha256(f"{seed}:{did}".encode("utf-8")).hexdigest()
        return h

    ordered = sorted(ids, key=key_fn)
    return ordered[: min(n, len(ordered))]


def write_sitemap(urls: List[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    urlset = ET.Element("urlset", attrib={"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})

    for u in urls:
        url_el = ET.SubElement(urlset, "url")
        loc_el = ET.SubElement(url_el, "loc")
        loc_el.text = u
        lastmod_el = ET.SubElement(url_el, "lastmod")
        lastmod_el.text = lastmod

    tree = ET.ElementTree(urlset)
    # XML declaration + UTF-8
    tree.write(OUT_SITEMAP, encoding="utf-8", xml_declaration=True)


def main() -> None:
    cached = load_cached_ids()
    newly_found, blocked = discover_ids()

    # Merge cache + new
    merged = []
    seen = set()
    for did in (newly_found + cached):
        if did not in seen:
            seen.add(did)
            merged.append(did)

    if not merged:
        # Nichts da -> hart fail, damit du es siehst
        raise SystemExit("❌ No design IDs available (cache empty + discovery failed/blocked).")

    # Cache aktualisieren (auch wenn discovery teilweise geblockt war)
    save_cached_ids(merged)

    picked = pick_rotating(merged, TOTAL_URLS)

    # -> /shop/ap/<id> (das sind “Produktseiten” die BTP normalerweise als Produkt/Artwork versteht)
    urls = [f"https://www.redbubble.com/shop/ap/{did}" for did in picked]

    write_sitemap(urls)
    OUT_URLS_TXT.write_text("\n".join(urls) + "\n", encoding="utf-8")
    OUT_LAST_COUNT.write_text(str(len(urls)) + "\n", encoding="utf-8")

    msg = f"✅ wrote {len(urls)} URLs | ids_total={len(merged)} | blocked={blocked}"
    print(msg)


if __name__ == "__main__":
    main()

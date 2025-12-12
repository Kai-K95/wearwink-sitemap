#!/usr/bin/env python3
from __future__ import annotations

import html as html_lib
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Iterable, List, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests


# =========================
# CONFIG
# =========================
DESIGN_IDS_FILE = Path("design_ids.txt")          # one /shop/ap/<id> per line
OUT_SITEMAP = Path("sitemap.xml")
OUT_COUNT = Path("last_count.txt")

POOL_FILE = Path("urls_pool.txt")                # all known product URLs (deduped)
STATE_FILE = Path("rotation_state.json")         # rotation cursor + shuffle seed

TARGET_PER_DAY = 2000                            # how many product URLs in sitemap.xml
REQUEST_TIMEOUT = 25
SLEEP_BETWEEN_REQUESTS_SEC = 0.8                 # keep it gentle
MAX_DESIGNS_PER_RUN = 5000                       # safety, won’t matter for you

BASE = "https://www.redbubble.com"
DESIGN_URL_TMPL = BASE + "/shop/ap/{id}"

# Product URLs look like:
# https://www.redbubble.com/i/hoodie/Title/176728003.6N9P2
# sometimes /de/i/... -> we normalize to non-/de/
PRODUCT_URL_RE = re.compile(
    r"https?://www\.redbubble\.com(?:/de)?/i/[^\s\"<>()]+",
    re.IGNORECASE,
)

# cloudflare-ish markers (we don't bypass; we just detect and fallback)
CF_MARKERS = (
    "cloudflare",
    "turnstile",
    "cf-chl",
    "verify you are human",
    "bestätigen sie, dass sie ein mensch sind",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# =========================
# HELPERS
# =========================

def normalize_url(u: str) -> str:
    u = html_lib.unescape(u).strip()

    # force https + strip /de
    u = u.replace("http://www.redbubble.com/", "https://www.redbubble.com/")
    u = u.replace("https://www.redbubble.com/de/", "https://www.redbubble.com/")
    u = u.replace("http://www.redbubble.com/de/", "https://www.redbubble.com/")

    # drop query + fragment (BlogToPin mag “clean” URLs)
    parts = urlsplit(u)
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return clean


def looks_like_cloudflare(html: str) -> bool:
    h = html.lower()
    return any(m in h for m in CF_MARKERS)


def load_design_ids() -> List[str]:
    if not DESIGN_IDS_FILE.exists():
        print("❌ design_ids.txt not found. Create it with one /shop/ap/<id> per line.")
        return []
    ids: List[str] = []
    for line in DESIGN_IDS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"(\d{6,})", line)
        if m:
            ids.append(m.group(1))
    # keep order but dedupe
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out[:MAX_DESIGNS_PER_RUN]


def fetch(url: str) -> Tuple[int, str]:
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    return r.status_code, r.text


def extract_product_urls(html: str) -> Set[str]:
    found = set()
    for raw in PRODUCT_URL_RE.findall(html):
        u = normalize_url(raw)
        # basic sanity: keep only /i/ paths
        if "/i/" in u and u.startswith("https://www.redbubble.com/"):
            found.add(u)
    return found


def read_pool() -> List[str]:
    if not POOL_FILE.exists():
        return []
    urls = [ln.strip() for ln in POOL_FILE.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    # dedupe keep order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def write_pool(urls: Iterable[str]) -> None:
    # stable, deterministic
    unique = sorted(set(urls))
    POOL_FILE.write_text("\n".join(unique) + "\n", encoding="utf-8")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def rotate_selection(pool: List[str], k: int) -> List[str]:
    """
    Rotation without repeats until pool exhausted:
    - keep a shuffled order in state
    - keep an index cursor
    - each day takes next k
    - when end reached -> reshuffle (new seed) and reset cursor
    """
    if not pool:
        return []

    state = load_state()
    order = state.get("order", [])
    idx = int(state.get("idx", 0))
    seed = state.get("seed", "")

    # rebuild order if mismatch
    pool_set = set(pool)
    order_filtered = [u for u in order if u in pool_set]

    # add any new URLs not in order yet
    missing = [u for u in pool if u not in set(order_filtered)]

    # if no usable order or too small, reshuffle fully
    need_reshuffle = (len(order_filtered) < max(100, min(len(pool), 500))) or (not seed)

    today = date.today().isoformat()  # local date is fine here
    if need_reshuffle:
        seed = f"seed:{today}:{len(pool)}"
        rng = random.Random(seed)
        order_filtered = list(pool)
        rng.shuffle(order_filtered)
        idx = 0
    else:
        # deterministic but gentle: shuffle only new URLs into the tail
        if missing:
            rng = random.Random(f"{seed}:missing:{today}")
            rng.shuffle(missing)
            order_filtered.extend(missing)

    # take slice
    if k >= len(order_filtered):
        picked = order_filtered[:]  # all
        idx = len(order_filtered)
    else:
        if idx + k <= len(order_filtered):
            picked = order_filtered[idx:idx + k]
            idx = idx + k
        else:
            # wrap: finish current, then reshuffle for next cycle
            picked = order_filtered[idx:]
            seed = f"seed:{today}:cycle:{int(time.time())}"
            rng = random.Random(seed)
            order_filtered = list(pool)
            rng.shuffle(order_filtered)
            idx = 0
            remaining = k - len(picked)
            picked.extend(order_filtered[idx:idx + remaining])
            idx += remaining

    state["order"] = order_filtered
    state["idx"] = idx
    state["seed"] = seed
    save_state(state)
    return picked


def xml_escape(s: str) -> str:
    # escape &, <, >, " safely for XML
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return s


def write_sitemap(urls: List[str]) -> None:
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(u)}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    OUT_SITEMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    design_ids = load_design_ids()
    if not design_ids:
        # do NOT overwrite existing outputs
        print("❌ No design IDs. Keeping existing sitemap.xml/last_count.txt unchanged.")
        return

    # start from existing pool (cache)
    pool_existing = read_pool()
    pool_set: Set[str] = set(pool_existing)

    newly_found_total = 0
    blocked = 0

    print(f"== Fetching product URLs from {len(design_ids)} design pages (/shop/ap/...) ==")

    for i, did in enumerate(design_ids, start=1):
        url = DESIGN_URL_TMPL.format(id=did)
        try:
            status, html = fetch(url)
        except Exception as e:
            print(f"ERR {did}: {e}")
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)
            continue

        # detect Cloudflare / blocking
        if status in (403, 429) or looks_like_cloudflare(html):
            blocked += 1
            print(f"BLOCKED {did} (status={status})")
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)
            continue

        urls = extract_product_urls(html)
        added = 0
        for u in urls:
            if u not in pool_set:
                pool_set.add(u)
                added += 1
        newly_found_total += added

        print(f"{i}/{len(design_ids)} ap/{did}: +{added} (pool={len(pool_set)})")
        time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

    if newly_found_total > 0:
        write_pool(pool_set)
        print(f"✅ Pool updated: +{newly_found_total} new URLs (pool={len(pool_set)})")
    else:
        print(f"⚠️ No new URLs found this run. (blocked={blocked}) Using existing pool (pool={len(pool_set)})")

    pool_list = sorted(pool_set)

    if not pool_list:
        print("❌ Pool is empty. Keeping existing sitemap.xml/last_count.txt unchanged.")
        return

    # pick rotated daily selection
    k = min(TARGET_PER_DAY, len(pool_list))
    picked = rotate_selection(pool_list, k)

    # write outputs
    write_sitemap(picked)
    OUT_COUNT.write_text(str(len(picked)) + "\n", encoding="utf-8")

    print(f"✅ OK: wrote {len(picked)} product URLs to sitemap.xml")
    print(f"   blocked design pages this run: {blocked}")


if __name__ == "__main__":
    main()

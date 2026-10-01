#!/usr/bin/env python3
"""Fetch an illustration for every story, using Serper's Images endpoint.

Stays inside the Serper + Jina chain: no extra API key, no third-party image
service. Serper returns publisher image URLs (not generated thumbnails), so
two things follow from that and the renderer has to handle them:

* the URLs are full-size originals, sometimes megabytes. Cards therefore load
  lazily and only the areas you asked for, or the page turns into 30MB;
* hotlinking can be refused by the CDN. Every image hides itself on error, so
  a blocked host degrades to a plain card instead of a broken-icon box.

Publisher photos belong to their publisher, so each card carries a visible
"Foto: <source>" credit. Results are cached by title hash, so re-renders are
free and only new stories cost a credit.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://google.serper.dev/images"


def load_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "")
    if key:
        return key
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line.startswith("SERPER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("Error: SERPER_API_KEY not set (and no ../.env).")


def digest(title: str) -> str:
    return hashlib.sha1(title.encode()).hexdigest()[:16]


def load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except (ValueError, OSError):
            return {}
    return {}


def save_cache(path: str, cache: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def serper_images(key: str, query: str, num: int = 6) -> list:
    body = json.dumps({"q": query, "num": num}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"X-API-KEY": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("images") or []
    except urllib.error.HTTPError as e:
        print(f"  ! images HTTP {e.code}: {e.read()[:80]!r}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  ! images {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
    return []


CC2 = {"co", "com", "org", "net", "gov", "ac", "edu"}


def registrable(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return host
    if len(parts) >= 3 and parts[-2] in CC2 and len(parts[-2]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def pick(results: list, title: str, want: list) -> dict:
    """Best surviving hit, preferring the article's own publisher.

    Searching Google Images on a headline returns whatever matches the words,
    which on a news corpus means article screenshots, clickbait banners and
    unrelated faces roughly a third of the time. But Serper also returns the
    page each image lives on, so the same-domain candidates are tried first:
    that is the article's own header photo, which is both relevant and the
    one the publisher actually consented to being linked.

    Each candidate is then verified with a real fetch. Serper hands back what
    Google Images has, and a chunk of it is unusable: Facebook lookaside URLs
    answer 200 with a 388-byte placeholder, Reuters resizer URLs need exact
    width parameters, tweets go 404.
    """
    junk = ("shutterstock", "gettyimages", "istockphoto", "alamy", "dreamstime",
            "123rf", "depositphotos", "vector", "clipart", "pngtree", "freepik",
            "stock-photo", "stockphoto", "placeholder", "logo", "icon",
            "lookaside.fbsbx.com", "pbs.twimg.com", "pinimg.com",
            "fbcdn.net/profile", "gravatar.com", "reddit.com", "linkedin.com",
            "quora.com", "fandom.com", "amazon.", "ebay.", "imgur.com/album")
    wanted = {registrable(u) for u in want if u}

    def usable(r):
        url = (r.get("imageUrl") or "").strip().replace("&amp;", "&")
        if not url.lower().startswith("http"):
            return None
        blob = f"{url} {r.get('link','')} {r.get('title','')}".lower()
        if any(j in blob for j in junk):
            return None
        if url.lower().split("?")[0].endswith((".svg", ".gif")):
            return None
        return r

    scored = []
    for r in results:
        r = usable(r)
        if not r:
            continue
        same = registrable(r.get("link", "")) in wanted
        scored.append((not same, len(scored), r))
    scored.sort(key=lambda x: (x[0], x[1]))

    for _, _, r in scored:
        url = r["imageUrl"].strip().replace("&amp;", "&")
        if not verify(url):
            continue
        return {"url": url,
                "credit": (r.get("source") or r.get("title") or "")[:40],
                "page": r.get("link", "")}
    return {}


def same_publisher(row: dict, links: list) -> bool:
    """Is this image hosted by a publisher that actually carries the story?

    The image search runs on the headline, so a hit from a different site is a
    lookalike: an illustration that matches the words, not the event. It once
    put a Pride banner on a human-trafficking trial. Only a photo from one of
    the story's own sources is trusted; otherwise the card uses the News
    thumbnail (which belongs to the article) or no picture."""
    page = (row or {}).get("page")
    return bool(page) and registrable(page) in {registrable(u) for u in links if u}


MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"RIFF", b"II*\x00", b"MM\x00*")


def verify(url: str, timeout: int = 12) -> bool:
    """Cheap ranged GET: asks for the first 4k and checks it is really a
    picture, rather than an HTML error page or a 388-byte placeholder."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
        "Referer": "https://www.google.com/",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Range": "bytes=0-4095",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status not in (200, 206):
                return False
            data = r.read(4096)
    except Exception:  # noqa: BLE001
        return False
    if len(data) < 512:
        return False
    head = data[:200].lower()
    if b"<html" in head or b"<!doctype" in head:
        return False
    return any(data.startswith(m) for m in MAGIC)


def query_for(title: str, lang: str) -> str:
    """Image search reads better on the English source title than on a
    translation, so the original is used deliberately."""
    return title


def fetch(titles: list, lang: str = "it", cache_path: str = None,
          quiet: bool = False, want: dict = None) -> tuple:
    """titles: list of strings, want: {title: [article urls]}.
    Returns (cache, stats) - cache is keyed by title hash."""
    key = load_key()
    cache_path = cache_path or os.path.join(HERE, "ai-news.img.json")
    cache = load_cache(cache_path)
    want = want or {}
    todo = [t for t in dict.fromkeys(titles) if digest(t) not in cache]
    stats = {"cached": len(titles) - len(todo), "found": 0, "empty": 0}

    for i, title in enumerate(todo, 1):
        row = pick(serper_images(key, query_for(title, lang)), title,
                   want.get(title, []))
        cache[digest(title)] = row
        stats["found" if row else "empty"] += 1
        if i % 10 == 0 or i == len(todo):
            save_cache(cache_path, cache)
            if not quiet:
                print(f"  immagini {i}/{len(todo)} (cache: {stats['cached']}, "
                      f"trovate: {stats['found']}, vuote: {stats['empty']})")
    return cache, stats


def lookup(cache: dict, title: str) -> dict:
    return cache.get(digest(title)) or {}

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
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

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


OG_META = (
    re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)'
               r'["\'][^>]*content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)='
               r'["\'](?:og:image|twitter:image)["\']', re.I),
)
OG_JUNK = ("logo", "icon", "placeholder", "default", "fallback", "sprite", "avatar",
           "blank", "/social/", "share-image")


def og_image(links: list, source: str = "") -> dict:
    """The article's own header photo: the og:image the publisher declares.

    Free (one page fetch), from the story's own site, and normally 1200px wide,
    where the News thumbnail is 92x92. Publisher fallback art (a logo used when
    an article has no picture) is recognised by its file name and refused."""
    for link in links[:3]:
        req = urllib.request.Request(link, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "en,it;q=0.8"})
        try:
            page = urllib.request.urlopen(req, timeout=15).read(400_000).decode(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for rx in OG_META:
            m = rx.search(page)
            if not m:
                continue
            url = urljoin(link, html.unescape(m.group(1)).strip())
            if not url.lower().startswith("http") or any(j in url.lower() for j in OG_JUNK):
                continue
            if verify(url):
                return {"url": url, "credit": (source or urlsplit(link).netloc)[:40], "page": link}
    return {}


def is_lowres(row: dict) -> bool:
    """Google News thumbnails (encrypted-tbn*.gstatic.com) are 92x92."""
    return "gstatic.com" in (row or {}).get("url", "")


def upgrade(featured: list, cache: dict, cache_path: str = None) -> int:
    """Swap a card's thumbnail for its article's og:image, when there is one.
    Results (including 'none') are cached under 'og:<title hash>'."""
    cache_path = cache_path or os.path.join(HERE, "ai-news.img.json")
    swapped = 0
    for c in featured:
        if c.get("img") and not is_lowres(c["img"]):
            continue
        k = "og:" + digest(c["title"])
        if k not in cache:
            cache[k] = og_image([s["link"] for s in c["sources"]], c["sources"][0]["name"])
        if cache[k]:
            c["img"] = dict(cache[k])
            swapped += 1
    save_cache(cache_path, cache)
    return swapped


def data_uri(url: str, width: int, cache: dict) -> str:
    """The picture as an inline data: URI, resized to `width` and re-encoded as JPEG.

    A page that hotlinks forty publisher CDNs is at the mercy of each one: a
    viewer such as Teams' embedded browser, a referer check, a CSP or a dead link
    each turn a photo into an empty box. Inline pictures cannot fail that way.
    Returns the original URL when the picture cannot be fetched or Pillow is
    missing, so the page degrades to the old behaviour. Results, failures
    included, are cached by (width, url)."""
    key = f"{width}|{url}"
    if key in cache:
        return cache[key] or url
    uri = ""
    try:
        import base64
        import io
        from PIL import Image
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": "https://www.google.com/", "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        raw = urllib.request.urlopen(req, timeout=20).read(8_000_000)
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            back = Image.new("RGB", im.size, (11, 13, 19))          # the page's dark background
            back.paste(im, mask=im.split()[-1])
            im = back
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        uri = ""
    cache[key] = uri
    return uri or url


def embed(cards: list, rows: list, cache_path: str = None) -> tuple:
    """Inline the pictures of the cards (720px) and of the compact rows (120px).
    Returns (pictures inlined, total bytes of the data URIs)."""
    cache_path = (cache_path or os.path.join(HERE, "ai-news.img.json")).replace(".json", ".data.json")
    cache = load_cache(cache_path)
    done = size = 0
    for group, width in ((cards, 720), (rows, 120)):
        for c in group:
            row = c.get("img")
            if not row or row["url"].startswith("data:"):
                continue
            lowres = is_lowres(row) or row.get("lowres", False)    # remember before the URL goes
            uri = data_uri(row["url"], width, cache)
            if uri.startswith("data:"):
                c["img"] = dict(row, url=uri, lowres=lowres)
                done += 1
                size += len(uri)
    save_cache(cache_path, cache)
    return done, size


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
          quiet: bool = False, want: dict = None, aliases: dict = None) -> tuple:
    """titles: list of strings, want: {title: [article urls]}.
    Returns (cache, stats) - cache is keyed by title hash."""
    key = load_key()
    cache_path = cache_path or os.path.join(HERE, "ai-news.img.json")
    cache = load_cache(cache_path)
    want = want or {}
    # a story whose representative headline changed is still the same story: reuse
    # a picture already found under any of its other headlines instead of paying
    # for a new search
    for title in titles:
        if digest(title) not in cache:
            for alt in (aliases or {}).get(title, []):
                if digest(alt) in cache:
                    cache[digest(title)] = cache[digest(alt)]
                    break
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

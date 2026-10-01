#!/usr/bin/env python3
"""Collect AI news from Serper News, keeping the thematic areas balanced.

The generic sweep behind main.py skews hard towards model launches, so the
sparse areas (business, chips) end up with a handful of cards while one area
eats a third of the page. This walks a per-area query list instead, stopping
early for areas that already have enough coverage.

Usage:
  python3 collect_news.py                    # target 20 per area
  python3 collect_news.py --target 25 --max-credits 60
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from format_news import AREAS, classify, hours

HERE = os.path.dirname(os.path.abspath(__file__))
DROP_QS = ("utm_", "fbclid", "gclid", "ref", "cmpid", "smid")

# Query list per area, ordered from most on-point to broadest.
QUERIES = {
    "policy": [
        "AI regulation law", "AI policy government", "AI safety lawsuit",
        "AI antitrust", "AI export controls", "AI copyright ruling",
        "artificial intelligence oversight", "AI data privacy enforcement",
    ],
    "models": [
        "AI model release", "new LLM model", "AI benchmark reasoning model",
        "open weight model", "AI research paper breakthrough", "multimodal AI model",
        "AI agent", "fine tuning new model",
    ],
    "chips": [
        "Nvidia AI chip", "AI data center investment", "TSMC AI chip",
        "AMD AI accelerator", "AI chip export ban", "AI compute capacity",
        "AI semiconductor supply", "HBM memory AI",
    ],
    "business": [
        "AI startup funding round", "AI acquisition", "AI revenue earnings",
        "AI company valuation", "AI partnership deal", "AI enterprise spending",
        "AI layoffs jobs", "AI IPO",
    ],
    "industry": [
        "AI in healthcare", "AI in education", "AI jobs workers",
        "AI adoption enterprise", "AI creative industry", "AI energy climate",
        "AI robotics", "AI consumers assistants",
    ],
}

FALLBACK = ["artificial intelligence", "AI news"]


def load_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "")
    if key:
        return key
    env = os.path.join(HERE, "..", ".env")
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line.startswith("SERPER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("Error: SERPER_API_KEY not set (and no ../.env).")


def canonical(url: str) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(DROP_QS)]
    netloc = p.netloc.lower().removeprefix("www.")
    return urlunsplit((p.scheme or "https", netloc, p.path.rstrip("/"), urlencode(q), ""))


def serper_news(key: str, q: str, num: int = 10) -> list[dict]:
    payload = json.dumps({"q": q, "num": num, "tbs": "qdr:w"}).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/news",
        data=payload,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("news") or []
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code} on {q!r}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {type(e).__name__} on {q!r}: {str(e)[:70]}", file=sys.stderr)
    return []


def main() -> None:
    args = sys.argv[1:]
    target, out_path = 20, os.path.join(HERE, "ai-news.json")
    max_credits = 80
    for i, a in enumerate(args):
        if a == "--target":
            target = int(args[i + 1])
        elif a == "--out":
            out_path = args[i + 1]

    key = load_key()
    seen: set[str] = set()
    items: list[dict] = []
    spent = 0

    def harvest(query: str, tag: str) -> int:
        nonlocal spent
        spent += 1
        new = 0
        for n in serper_news(key, query):
            link = n.get("link", "")
            if not link:
                continue
            c = canonical(link)
            if c in seen:
                continue
            seen.add(c)
            area, color = classify(n.get("title", ""), n.get("snippet", ""))
            items.append({
                "title": n.get("title", ""), "link": link, "url": c,
                "snippet": n.get("snippet", ""), "source": n.get("source", ""),
                "date": n.get("date", ""), "area": area, "color": color,
                "imageUrl": n.get("imageUrl", ""), "q": tag,
            })
            new += 1
        return new

    # pass 1: per-area queries, stop early once the area is well fed
    for a in AREAS:
        got = sum(1 for i in items if i["area"] == a["key"])
        if got >= target:
            continue
        for q in QUERIES.get(a["key"], []):
            if got >= target or spent >= max_credits:
                break
            got += harvest(q, a["key"])
        print(f"  {a['key']:10} {got:3} after {spent:2} queries")

    # pass 2: generic sweep to top up anything still short
    for q in FALLBACK:
        if spent >= max_credits:
            break
        short = {a["key"] for a in AREAS
                 if sum(1 for i in items if i["area"] == a["key"]) < target}
        if not short:
            break
        print(f"  fallback {q!r} (short: {sorted(short)})")
        harvest(q, "fallback")

    # pass 3: known-good per-area queries, for whatever is still short
    for a in AREAS:
        got = sum(1 for i in items if i["area"] == a["key"])
        if got >= target or spent >= max_credits:
            continue
        for q in QUERIES.get(a["key"], [])[::-1]:
            if got >= target or spent >= max_credits:
                break
            got += harvest(q, a["key"] + "-rerun")

    items.sort(key=lambda x: hours(x["date"]))
    with open(out_path, "w") as fh:
        json.dump(items, fh, indent=1)

    counts = Counter(i["area"] for i in items)
    print(f"\n  {len(items)} stories, {spent} queries spent -> {out_path}\n")
    for a in AREAS:
        n = counts.get(a["key"], 0)
        bar = "#" * n
        flag = "OK " if n >= target else "LOW"
        print(f"  {flag} {a['key']:10} {n:3}  {bar}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Italian summaries of the articles behind each card, via Jina + OpenRouter.

The page is static, so summaries are built ahead of time: for every story the
article is fetched (first source that yields real text), summarised in Italian
and cached on disk keyed by the English title. Paywalled or unreachable pages
yield no summary; the page then falls back to the translated snippet.
"""

import hashlib
import json
import os
import re
import sys

import main as M
import translate as TR

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_CHARS = 600          # below this the page is a paywall stub or a cookie wall
MAX_CHARS = 9000         # enough article for a summary, cheap to send

PROMPT = """\
Riassumi in italiano, in 3-5 frasi (max 90 parole), l'articolo qui sotto.
Stile giornalistico, neutro, solo fatti presenti nel testo: niente opinioni,
niente informazioni aggiunte. Lascia in inglese nomi propri e sigle.
Se il testo non e' un articolo (paywall, errore, cookie), rispondi esattamente: NONE
Rispondi solo con il riassunto, senza titoli ne' elenchi.

Titolo: %s

Testo:
%s"""


def digest(title: str) -> str:
    return hashlib.sha1(title.encode()).hexdigest()[:16]


def body_only(text: str) -> str:
    """Jina markdown -> prose: drop images, unwrap links, and start at the first
    real paragraph so cookie banners and menus do not look like the article."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    lines = [l.strip() for l in text.split("\n")]
    start = next((i for i, l in enumerate(lines) if len(l) >= 100), 0)
    return "\n".join(l for l in lines[start:] if l)


def article_text(links: list, log=print) -> str:
    for link in links[:3]:
        ok, text = M.jina_read(link)
        if not ok:
            log(f"      jina KO  {link[:70]} -> {text[:60]}")
            continue
        text = body_only(M.clean_content(text))
        log(f"      jina {'ok' if len(text) >= MIN_CHARS else 'corto'} "
            f"{len(text)} car  {link[:70]}")
        if len(text) >= MIN_CHARS:
            return text[:MAX_CHARS]
    return ""


def ask(key: str, model: str, title: str, text: str, quiet: bool,
        tries: int = 4):
    """The free router sometimes lands on a safety classifier that answers
    'User Safety: safe'; anything too short to be a summary is retried."""
    for t in range(tries):
        try:
            out = TR.call(key, model, PROMPT % (title, text)).strip()
        except Exception as e:  # noqa: BLE001
            print(f"      ! errore: {str(e)[:100]}", file=sys.stderr)
            continue
        if out.upper().startswith("NONE") or len(out) >= 80:
            return out
        if not quiet:
            print(f"      risposta scartata ({out[:30]!r}), riprovo {t + 1}/{tries}")
    return None


def summarize(stories: list, model: str = TR.DEFAULT_MODEL,
              cache_path: str = None, quiet: bool = False) -> tuple:
    """stories: [{"title":..., "links":[...]}] -> (cache, stats)."""
    key = TR.load_key()
    cache_path = cache_path or os.path.join(HERE, "ai-news.sum.json")
    cache = TR.load_cache(cache_path)
    todo = [s for s in stories if digest(s["title"]) not in cache]
    stats = {"cached": len(stories) - len(todo), "done": 0, "empty": 0}
    for n, s in enumerate(todo, 1):
        d = digest(s["title"])
        if not quiet:
            print(f"  [{n}/{len(todo)}] {s['title'][:80]}")
        text = article_text(s["links"], log=(lambda m: None) if quiet else print)
        summary = ""
        if text:
            out = ask(key, model, s["title"], text, quiet)
            if out is None:
                continue                       # not cached: retried next run
            summary = "" if out.upper().startswith("NONE") else out
        if summary or not text:                # model NONE is retried next run
            cache[d] = summary
            TR.save_cache(cache_path, cache)
        stats["done" if summary else "empty"] += 1
        if not quiet:
            print(f"      -> {'riassunto ' + str(len(summary)) + ' car' if summary else 'non disponibile'}")
    return cache, stats


def lookup(cache: dict, title: str) -> str:
    return cache.get(digest(title), "")

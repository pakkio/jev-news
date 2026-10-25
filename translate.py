#!/usr/bin/env python3
"""Translate news titles and snippets into Italian via OpenRouter.

Runs after clustering, never before: the English titles are what the
same-event matcher reads, and a translated title loses the proper nouns and the
word overlap the matcher depends on.

Results are cached on disk keyed by a hash of the English source, so a second
run is free and only genuinely new stories cost anything.
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "opencodego:deepseek-v4.1-flash")
API = "https://openrouter.ai/api/v1/chat/completions"
# "<provider>:<model>" selects another OpenAI-compatible endpoint; no prefix = OpenRouter
PROVIDERS = {"opencodego": ("https://opencode.ai/zen/go/v1/chat/completions",
                            "OPENCODEGO_API_KEY")}


def provider(model: str) -> tuple:
    """-> (url, key variable, model id as the endpoint knows it)."""
    name, _, rest = model.partition(":")
    if rest and name in PROVIDERS:
        return (*PROVIDERS[name], rest)
    return API, "OPENROUTER_API_KEY", model

PROMPT = """\
Traduci in italiano naturale e giornalistico, senza parafrasi.
Risponci ESCLUSIVAMENTE con un oggetto JSON, senza testo attorno.

Regole:
- lascia in inglese i nomi propri: aziende, prodotti, persone, luoghi, sigle
  (es. OpenAI, DeepSeek, Hugging Face, Nvidia, Wall Street, UE);
- traduci per il senso, mai con calchi letterali: "bombshell lawsuit" = "causa
  clamorosa", "tees up" = "prepara", "caves to" = "cede a";
- istituzioni e cariche con il nome italiano: Supreme Court = Corte Suprema,
  Department of Education / Ed. Dept. = Dipartimento dell'Istruzione, Civil Code =
  Codice civile, speaker of parliament = presidente del parlamento, hospital =
  ospedale, Council of Europe = Consiglio d'Europa;
- non lasciare abbreviazioni inglesi ("Dept.", "Gov.", "Sen."): scrivile per esteso
  in italiano;
- "AI" generico si scrive "IA" ("modelli IA", "agenti IA"); resta "AI" solo dentro un nome
  proprio (Reflection AI, NordLayer AI Scout);
- nei titoli solo la prima parola e i nomi propri hanno la maiuscola;
- non aggiungere e non togliere informazione;
- se una stringa contiene gia' dell'italiano, copiala identica;
- se una stringa e' vuota, restituisci stringa vuota.

Formato: {"<id>": {"t": "<titolo>", "s": "<snippet>"}}

Frammenti da tradurre:
%s"""


# English left in a translation: such entries are redone instead of trusted.
RESIDUE = re.compile(
    r"\b(ed\. ?dept|dept\.|gov\.|speaker of|civil code|supreme court|council of europe|"
    r"european union|bombshell|causa bomba)\b", re.I)


def bad(row: dict) -> bool:
    return bool(RESIDUE.search(f"{row.get('t', '')} {row.get('s', '')}"))


def load_key(model: str = DEFAULT_MODEL) -> str:
    var = provider(model)[1]
    # ../.env wins over the shell: a stale exported key must not shadow it
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line.startswith(f"{var}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    key = os.environ.get(var, "")
    if key:
        return key
    raise SystemExit(f"Error: {var} not set (and not in ../.env).")


SESSION = str(uuid.uuid4())
# at most this many requests in flight at once, across every stage and thread
GATE = threading.BoundedSemaphore(max(1, int(os.environ.get("LLM_CONCURRENCY", "16"))))
RETRY_HTTP = (408, 429, 500, 502, 503, 504, 529)


def digest(title: str, snippet: str) -> str:
    return hashlib.sha1(f"{title}\x00{snippet}".encode()).hexdigest()[:16]


def load_cache(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def save_cache(path: str, cache: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def call(key: str, model: str, payload: str, retries: int = 3, timeout: int = 180) -> dict:
    url, _, model_id = provider(model)
    body = {"model": model_id, "temperature": 0,
            "messages": [{"role": "user", "content": payload}]}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 # OpenCode's edge rejects the default Python UA and wants a session id
                 "User-Agent": "curl/8.5.0", "x-opencode-session": SESSION,
                 "HTTP-Referer": "https://github.com/pakkio/search",
                 "X-Title": "AI Briefing"})
    for attempt in range(retries):
        try:
            # `timeout` is a per-read socket timeout; the deadline makes it a hard cap on
            # the whole attempt, so a server trickling chunks cannot stall us forever
            deadline = time.monotonic() + timeout
            with GATE, urllib.request.urlopen(req, timeout=timeout) as r:
                chunks = []
                while chunk := r.read(8192):
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"no complete reply within {timeout}s")
                reply = json.loads(b"".join(chunks))
                METER.llm(reply.get("usage"))
                return reply["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            detail = e.read()[:200]
            # a 400 "upstream failed / unavailable" is the provider's hiccup, not our request
            transient = e.code in RETRY_HTTP or (e.code == 400 and
                                                 re.search(rb"upstream|unavailable", detail, re.I))
            if transient and attempt < retries - 1:
                METER.note(f"http{e.code}")
                print(f"  LLM HTTP {e.code}, retry {attempt + 2}/{retries}...", file=sys.stderr)
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"LLM HTTP {e.code}: {detail[:120]!r}")
        except Exception as e:
            if attempt < retries - 1:
                METER.note("retry")
                print(f"  LLM call failed ({type(e).__name__}: {e}), retry {attempt + 2}/{retries}...",
                      file=sys.stderr)
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return "{}"


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in a fence or prose; be forgiving."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {}


def translate(pairs: list, model: str = DEFAULT_MODEL, batch: int = 10,
              cache_path: str = None, quiet: bool = False, meter: bool = True) -> tuple:
    """pairs: [{"title":..., "snippet":...}] -> (cache dict, stats dict).
    Batches run in parallel (TRANSLATE_WORKERS); meter=False when another stage
    is already driving the progress bar."""
    key = load_key(model)
    cache_path = cache_path or os.path.join(HERE, "ai-news.it.json")
    cache = load_cache(cache_path)
    todo, seen = [], set()
    for p in pairs:
        d = digest(p["title"], p.get("snippet", ""))
        if (d not in cache or bad(cache[d])) and d not in seen:
            seen.add(d)
            todo.append((d, p))
    stats = {"cached": len(pairs) - len(todo), "translated": 0, "failed": 0}
    if meter:
        METER.set_total(len(todo))

    def run(chunk):
        numbered = "\n".join(
            f'{j}. "t": {json.dumps(p["title"], ensure_ascii=False)},'
            f' "s": {json.dumps(p.get("snippet", ""), ensure_ascii=False)}'
            for j, (_, p) in enumerate(chunk))
        return extract_json(call(key, model, PROMPT % numbered))

    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    workers = max(1, int(os.environ.get("TRANSLATE_WORKERS", "6")))
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(run, ch): ch for ch in chunks}
        for f in as_completed(futures):
            chunk = futures[f]
            if meter:
                METER.tick(len(chunk))
            try:
                got = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"  ! batch failed: {str(e)[:100]}", file=sys.stderr)
                stats["failed"] += len(chunk)
                continue
            for j, (d, p) in enumerate(chunk):
                row = got.get(str(j)) or got.get(j)
                if not row:
                    stats["failed"] += 1
                    continue
                cache[d] = {"t": row.get("t") or p["title"],
                            "s": row.get("s") or p.get("snippet", "")}
                stats["translated"] += 1
            save_cache(cache_path, cache)
            done += len(chunk)
            if not quiet:
                print(f"  tradotte {done}/{len(todo)} "
                      f"(cache: {stats['cached']}, fallite: {stats['failed']})")
    return cache, stats


def lookup(cache: dict, title: str, snippet: str) -> dict:
    row = cache.get(digest(title, snippet or ""))
    if not row:
        return {}
    return {"title": row.get("t", ""), "snippet": row.get("s", "")}

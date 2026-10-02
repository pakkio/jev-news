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
import time
import urllib.error
import urllib.request
import uuid

from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "openrouter/free")
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
    key = os.environ.get(var, "")
    if key:
        return key
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line.startswith(f"{var}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"Error: {var} not set (and no ../.env).")


SESSION = str(uuid.uuid4())


def digest(title: str, snippet: str) -> str:
    return hashlib.sha1(f"{title}\x00{snippet}".encode()).hexdigest()[:16]


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


def call(key: str, model: str, payload: str, retries: int = 3, timeout: int = 90) -> dict:
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
            with urllib.request.urlopen(req, timeout=timeout) as r:
                reply = json.loads(r.read())
                METER.llm(reply.get("usage"))
                return reply["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"OpenRouter HTTP {e.code}: {e.read()[:120]!r}")
        except Exception:
            if attempt < retries - 1:
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
              cache_path: str = None, quiet: bool = False) -> tuple:
    """pairs: [{"title":..., "snippet":...}] -> (cache dict, stats dict)."""
    key = load_key(model)
    cache_path = cache_path or os.path.join(HERE, "ai-news.it.json")
    cache = load_cache(cache_path)
    todo = []
    for p in pairs:
        d = digest(p["title"], p.get("snippet", ""))
        if d not in cache or bad(cache[d]):
            todo.append((d, p))
    stats = {"cached": len(pairs) - len(todo), "translated": 0, "failed": 0}

    METER.set_total(len(todo))
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        METER.tick(len(chunk))
        numbered = "\n".join(
            f'{j}. "t": {json.dumps(p["title"], ensure_ascii=False)},'
            f' "s": {json.dumps(p.get("snippet", ""), ensure_ascii=False)}'
            for j, (_, p) in enumerate(chunk))
        try:
            got = extract_json(call(key, model, PROMPT % numbered))
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
        if not quiet:
            done = min(i + batch, len(todo))
            print(f"  tradotte {done}/{len(todo)} "
                  f"(cache: {stats['cached']}, fallite: {stats['failed']})")
    return cache, stats


def lookup(cache: dict, title: str, snippet: str) -> dict:
    row = cache.get(digest(title, snippet or ""))
    if not row:
        return {}
    return {"title": row.get("t", ""), "snippet": row.get("s", "")}

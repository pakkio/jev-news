#!/usr/bin/env python3
"""Italian summaries of the articles behind each card, via Jina + an LLM.

The page is static, so summaries are built ahead of time: for every story the
article is fetched (first source that yields real text), summarised in Italian,
then *checked against that same text* before it is allowed on the page.
Paywalled or unreachable pages yield no summary; the page then falls back to
the translated snippet.

Three layers stand between the model and the reader:

* the prompt: attribute contested claims, no unsourced identity labels,
  translate common nouns, finish every sentence;
* lint(): cheap checks that need no model (truncated sentence, English left
  over, too short);
* verdict(): a stronger model compares summary and article on entities, numbers,
  negations and attribution. One repair attempt with the problems fed back,
  then the summary is dropped. A summary that was never verified is not shown.
"""

import hashlib
import html
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import main as M
import translate as TR

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_CHARS = 600          # below this the page is a paywall stub or a cookie wall
MAX_CHARS = 9000         # enough article for a summary, cheap to send

# A fast model gives up on pages that are mostly menu, and the checker should be
# stronger than the writer: both use this one.
STRONG = os.environ.get("LLM_MODEL_STRONG", "opencodego:deepseek-v4-pro")

PROMPT = """\
Riassumi in italiano, in 3-5 frasi (max 90 parole), l'articolo qui sotto.
Stile giornalistico e neutro. Regole:
- usa solo fatti presenti nel testo, senza opinioni e senza aggiungere nulla;
- attribuisci le affermazioni controverse, le accuse e le versioni di parte a chi
  le fa ("secondo la polizia", "sostiene l'accusa", "scrive il quotidiano"); nei
  casi non accertati usa il condizionale;
- non indicare origine, etnia, religione o identita' di una persona se non e' un
  fatto accertato e rilevante; se compare solo nell'articolo, attribuiscilo alla fonte;
- traduci in italiano corrente ogni nome comune e istituzione nota (ospedale,
  Consiglio d'Europa, Unione europea, Corte Suprema); lascia in inglese solo i nomi
  propri di persone, aziende e prodotti e le sigle;
- se una sigla poco nota non e' spiegata nel testo, sostituiscila con una descrizione
  generica fedele al testo oppure omettila; non inventare il suo significato;
- chiudi sempre le frasi: niente testo troncato;
- se il testo non e' un articolo (paywall, errore, cookie), rispondi esattamente: NONE
Rispondi solo con il riassunto, senza titoli ne' elenchi.

Titolo: %s

Testo:
%s"""

VERIFY_PROMPT = """\
Sei un controllore di fatti. Confronta il RIASSUNTO con il TESTO dell'articolo.
Segnala solo problemi reali, ciascuno in una riga breve:
- fatti, nomi, numeri, date o luoghi del riassunto assenti o diversi nel testo;
- negazioni o rapporti di causa invertiti;
- accuse, ipotesi o versioni di parte riportate come fatti accertati, o attribuite
  alla persona sbagliata;
- caratteristiche di una persona (origine, identita') dichiarate senza attribuzione;
- frasi senza senso, troncate, oppure parole inglesi non tradotte.
Se il riassunto e' fedele rispondi {"ok": true, "problemi": []}.
Rispondi ESCLUSIVAMENTE con JSON: {"ok": true|false, "problemi": ["..."]}

RIASSUNTO:
%s

TESTO:
%s"""

# Institutions and titles that must read in Italian; the check runs on every summary
# written and on ones verified before a term was added to this list.
RESIDUE = re.compile(
    r"\b(speaker of|civil code|supreme court|council of europe|european union|"
    r"ed\. ?dept|dept\.|department of|bombshell)\b", re.I)
ENGLISH = re.compile(r"\b(the|and|with|from|that|this|which|their|have|were|will|hospital)\b",
                     re.I)
DANGLING = re.compile(r"\b(sia|che|di|e|la|il|lo|le|con|ma|per|da|in|un|una|del|della|"
                      r"sebbene|anche|come)\s*[.!?]?\s*$", re.I)


MID_DANGLING = re.compile(r"\b(sia|che|di|e|la|il|lo|le|con|ma|per|da|in|un|una|del|della|"
                          r"sebbene|anche|come)\.\s+[A-Z\u00c0-\u00de]")


def digest(title: str) -> str:
    return hashlib.sha1(title.encode()).hexdigest()[:16]


def vkey(summary: str) -> str:
    """Identity of a verified text: change a word and it must be checked again."""
    return hashlib.sha1(summary.encode()).hexdigest()[:12]


def lint(summary: str) -> list:
    """Checks that need no model. Returns the problems found."""
    s, out = summary.strip(), []
    if len(s) < 80:
        out.append("troppo corto")
    if not re.search(r"[.!?»”\"]\s*$", s) or DANGLING.search(s) or MID_DANGLING.search(s):
        out.append("ultima frase troncata")
    if len(ENGLISH.findall(s)) >= 2:
        out.append("parole inglesi non tradotte")
    found = RESIDUE.search(s)
    if found:
        out.append(f"termine inglese da tradurre: {found.group(0)!r}")
    return out


def body_only(text: str) -> str:
    """Jina markdown -> prose: drop images, unwrap links, and start at the first
    real paragraph so cookie banners and menus do not look like the article."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    lines = [l.strip() for l in text.split("\n")]
    start = next((i for i, l in enumerate(lines) if len(l) >= 100), 0)
    return "\n".join(l for l in lines[start:] if l)


def direct_read(link: str) -> str:
    """Plain GET with a browser UA, tags stripped: for sites Jina cannot reach."""
    req = urllib.request.Request(link, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "Chrome/124 Safari/537.36",
        "Accept-Language": "en,it;q=0.8"})
    try:
        page = urllib.request.urlopen(req, timeout=20).read().decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    page = re.sub(r"(?s)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def article_text(links: list, log=print) -> str:
    for link in links[:3]:
        ok, text = M.jina_read(link)
        text = body_only(M.clean_content(text)) if ok else ""
        log(f"      jina {'ok' if len(text) >= MIN_CHARS else ('KO' if not ok else 'corto')} "
            f"{len(text)} car  {link[:70]}")
        if len(text) < MIN_CHARS:
            text = direct_read(link)
            log(f"      diretto {'ok' if len(text) >= MIN_CHARS else 'KO'} {len(text)} car")
        if len(text) >= MIN_CHARS:
            return text[:MAX_CHARS]
    return ""


def ask(key: str, model: str, title: str, text: str, quiet: bool,
        tries: int = 4, feedback: list = None, log=print):
    """The free router sometimes lands on a safety classifier that answers
    'User Safety: safe'; anything too short to be a summary is retried."""
    prompt = PROMPT % (title, text)
    if feedback:
        prompt += ("\n\nUna versione precedente aveva questi problemi, evitali:\n"
                   + "\n".join(f"- {p}" for p in feedback))
    for t in range(tries):
        try:
            out = TR.call(key, model, prompt).strip()
        except Exception as e:  # noqa: BLE001
            print(f"      ! errore: {str(e)[:100]}", file=sys.stderr)
            continue
        if out.upper().startswith("NONE"):
            if t < 1:                         # one retry: models sometimes say NONE to a good page
                if not quiet:
                    log(f"      NONE, riprovo {t + 1}/{tries}")
                continue
            return out
        if len(out) >= 80:
            return out
        if not quiet:
            log(f"      risposta scartata ({out[:30]!r}), riprovo {t + 1}/{tries}")
    return None


def verdict(key: str, text: str, summary: str):
    """-> list of problems ([] = faithful), or None when the checker failed."""
    for _ in range(3):
        try:
            got = TR.extract_json(TR.call(key, STRONG, VERIFY_PROMPT % (summary, text)))
        except Exception:  # noqa: BLE001
            continue
        if "ok" in got:
            if got["ok"]:
                return []
            return [str(p) for p in got.get("problemi", [])] or ["non fedele al testo"]
    return None


def vet(key: str, title: str, text: str, summary: str, quiet: bool, log=print):
    """Lint + fact check, with up to two regenerations that get the problems fed back.

    The checker is noisy: five summaries it rejected after one repair all passed
    first time when regenerated, so a single failed round must not be final.
    Returns the summary that passed, "" after three failed rounds, or None when
    the checker itself was unavailable (nothing is decided, retry next run)."""
    for attempt in range(3):
        problems = lint(summary)
        if not problems:
            found = verdict(key, text, summary)
            if found is None:
                return None
            problems = found
        if not problems:
            return summary
        if not quiet:
            log(f"      controllo: {'; '.join(problems)[:150]}")
        if attempt == 2:
            break
        fixed = ask(key, STRONG, title, text, quiet, tries=2, feedback=problems, log=log)
        if not fixed or fixed.upper().startswith("NONE"):
            return ""
        summary = fixed
    return ""


def summarize(stories: list, model: str = TR.DEFAULT_MODEL,
              cache_path: str = None, quiet: bool = False, verify: bool = True) -> tuple:
    """stories: [{"title":..., "links":[...]}] -> (verified cache, stats).

    Files: <cache>.json holds every summary written, <cache>.ver.json the
    fingerprint of the ones that passed the check. The returned dict only
    contains what is safe to publish."""
    key = TR.load_key(model)
    cache_path = cache_path or os.path.join(HERE, "ai-news.sum.json")
    ver_path = cache_path.replace(".json", ".ver.json")
    cache, ver = TR.load_cache(cache_path), TR.load_cache(ver_path)

    todo = []
    for s in stories:
        d = digest(s["title"])
        have = cache.get(d)
        if have is None or (verify and have and (ver.get(d) != vkey(have) or lint(have))):
            todo.append(s)
    stats = {"cached": len(stories) - len(todo), "done": 0, "empty": 0,
             "checked": 0, "rejected": 0}

    def one(n: int, s: dict):
        """Everything for one story. Runs in a worker thread, so it only reads
        shared state and returns what to record; output is returned as a block
        so lines from different stories do not interleave."""
        lines = []
        log = (lambda m: None) if quiet else lines.append
        log(f"  [{n}/{len(todo)}] {s['title'][:80]}")
        d = digest(s["title"])
        text = article_text(s["links"], log=log)
        fresh = d not in cache
        summary = "" if fresh else cache[d]
        if fresh and text:
            out = ask(key, model, s["title"], text, quiet, log=log)
            if out is None:
                return d, None, None, False, lines       # not cached: retried next run
            if out.upper().startswith("NONE") and model != STRONG:
                log(f"      NONE, provo {STRONG}")
                out = ask(key, STRONG, s["title"], text, quiet, tries=2, log=log) or out
            summary = "" if out.upper().startswith("NONE") else out
        mark = None
        if summary and verify:
            if not text:
                return d, None, None, False, lines       # cannot check: stays hidden
            vetted = vet(key, s["title"], text, summary, quiet, log=log)
            if vetted is None:
                return d, None, None, False, lines
            summary, mark = (vetted, vkey(vetted)) if vetted else ("", "rejected")
        log(f"      -> {'riassunto ' + str(len(summary)) + ' car' if summary else 'non disponibile'}")
        return d, summary, mark, bool(text), lines

    workers = max(1, int(os.environ.get("SUMMARY_WORKERS", "4")))
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(one, n, s) for n, s in enumerate(todo, 1)]
        for f in as_completed(futures):
            try:
                d, summary, mark, had_text, lines = f.result()
            except Exception as e:  # noqa: BLE001
                print(f"  ! errore su un articolo: {str(e)[:100]}", file=sys.stderr)
                continue
            for line in lines:
                print(line)
            if summary is None:
                continue
            if mark:
                ver[d] = mark
                stats["checked"] += 1
                stats["rejected"] += mark == "rejected"
            if summary or not had_text or mark == "rejected":     # model NONE is retried
                cache[d] = summary
                TR.save_cache(cache_path, cache)
                if verify:
                    TR.save_cache(ver_path, ver)
            stats["done" if summary else "empty"] += 1
    public = {d: v for d, v in cache.items()
              if not v or not verify or ver.get(d) == vkey(v)}
    return public, stats


def lookup(cache: dict, title: str) -> str:
    return cache.get(digest(title), "")

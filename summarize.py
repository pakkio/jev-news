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
import time
import threading
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import decide as D
import main as M
from meter import METER
import translate as TR

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_CHARS = 600          # below this the page is a paywall stub or a cookie wall
MAX_CHARS = 9000         # enough article for a summary, cheap to send

# A fast model gives up on pages that are mostly menu, and the checker should be
# stronger than the writer: both use this one.
# The writer. Jev used to filter the page furniture out of every article paragraph by
# paragraph, which was most of the Jev bill (each paragraph carried the full criteria);
# the prompt already tells the model to ignore menus and answer NONE for a non-article,
# so the default is the cheap heuristic (body_only). SUMMARY_PROSE=jev brings Jev back.
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "opencodego:deepseek-v4.1-flash")
SUMMARY_PROSE = os.environ.get("SUMMARY_PROSE", "heuristic")

STRONG = os.environ.get("LLM_MODEL_STRONG", "opencodego:deepseek-v4-pro")
# The reviewer: the fast model reads every summary; the strong one is called only to
# confirm a problem before it costs a regeneration, so most reviews never touch it.
REVIEW = os.environ.get("REVIEW_MODEL", SUMMARY_MODEL)
FETCH_TIMEOUT = 12       # seconds per page read; a slow site must not hold a worker for minutes

PROMPT = """\
Scrivi in italiano il titolo e il riassunto (3-5 frasi, max 90 parole, piu' l'eventuale frase sull'Europa) dell'articolo
qui sotto. Stile giornalistico e neutro, chiaro per chi non conosce l'argomento. Regole:
- il titolo dice quello che l'articolo dice davvero, senza enfasi ne' dettagli assenti
  dal testo, al massimo 100 caratteri, solo la prima parola e i nomi propri maiuscoli;
- "AI" generico si scrive "IA"; resta "AI" solo dentro un nome proprio;
- usa solo fatti presenti nel testo, senza opinioni e senza aggiungere nulla;
- attribuisci le affermazioni controverse, le accuse e le versioni di parte a chi
  le fa ("secondo la polizia", "sostiene l'accusa", "scrive il quotidiano"); nei
  casi non accertati usa il condizionale;
- non indicare origine, etnia, religione o identita' di una persona se non e' un
  fatto accertato e rilevante; se compare solo nell'articolo, attribuiscilo alla fonte;
- traduci in italiano corrente ogni nome comune e istituzione nota (ospedale,
  Consiglio d'Europa, Unione europea, Corte Suprema); lascia in inglese solo i nomi
  propri di persone, aziende e prodotti e le sigle;
- sigle e termini tecnici non banali per un lettore italiano (GPU, LLM, RAG, IPO, FTC,
  SEC, DOJ, AGI...): alla prima comparsa aggiungi tra parentesi una glossa breve e corretta
  ("GPU, i chip per il calcolo grafico e l'IA", "FTC, l'autorita' antitrust statunitense");
  quelle di uso comune (IA, PC, USA, UE, CEO) restano com'e';
  se non conosci con certezza il significato di una sigla, descrivila in modo generico o
  omettila: mai indovinare;
- mercato europeo: se l'articolo lo consente, chiudi con una frase che inizia con
  "Per l'Europa:" (max 25 parole) su cosa cambia o conta per Europa e Italia: regole UE (AI
  Act, GDPR, DMA), disponibilita' nell'UE, aziende o investimenti europei, prezzi in euro.
  Usa solo fatti presenti nel testo o conoscenze consolidate e certe; niente previsioni ne'
  ipotesi. Se non c'e' un legame concreto, ometti la frase;
- chiudi sempre le frasi: niente testo troncato;
- se il testo non e' un articolo (paywall, errore, cookie), rispondi esattamente: NONE
Formato della risposta, esattamente (se non e' un articolo, solo NONE):
TITOLO: <titolo>

<riassunto>
Niente elenchi, niente altro testo.

Titolo originale: %s

Testo:
%s"""

VERIFY_PROMPT = """\
Sei il redattore che fa la revisione finale. Confronta TITOLO e RIASSUNTO con il TESTO
dell'articolo. Segnala solo problemi reali, ciascuno in una riga breve:
- il titolo afferma piu' di quanto dica il testo, o lo contraddice;
- fatti, nomi, numeri, date o luoghi del riassunto assenti o diversi nel testo;
- negazioni o rapporti di causa invertiti;
- accuse, ipotesi o versioni di parte riportate come fatti accertati, o attribuite
  alla persona sbagliata;
- caratteristiche di una persona (origine, identita') dichiarate senza attribuzione;
- frasi senza senso, troncate, oppure parole inglesi non tradotte;
- una glossa di sigla sbagliata o inventata;
- la frase "Per l'Europa:" se contiene previsioni, ipotesi o fatti che non sono nel testo
  e non sono conoscenza consolidata (una glossa o un richiamo a norme UE reali va bene).
Se titolo e riassunto sono fedeli "ok" e' true e "problemi" e' vuoto.
Poi giudica tre cose, indipendenti dai problemi:
- "ia": true solo se il tema centrale dell'articolo e' l'intelligenza artificiale (modelli,
  prodotti, aziende, chip e data center per l'IA, ricerca, regole sull'IA, suo impatto);
  false se l'IA e' solo citata di passaggio o non c'entra;
- "area": la sezione piu' adatta, scegliendo una chiave tra:
%s
- "rilievo": "su" se per un lettore informato di IA la notizia pesa molto piu' del
  normale (lancio importante, accordo o decisione di grande portata); "giu" se e'
  minore, locale, promozionale o un aggiornamento marginale; altrimenti "ok".

Rispondi ESCLUSIVAMENTE con JSON:
{"ok": true|false, "problemi": ["..."], "ia": true|false, "area": "<chiave>", "rilievo": "su|ok|giu"}

TITOLO:
%s

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


def split_out(out: str) -> tuple:
    """"TITOLO: x", blank line, summary -> (title, summary); title "" if the line is missing."""
    m = re.match(r"\s*TITOLO:\s*(.+?)\s*\n+(.*)\Z", out, re.S | re.I)
    if not m:
        return "", out.strip()
    return m.group(1).strip().strip('"'), m.group(2).strip()


def body_only(text: str) -> str:
    """Jina markdown -> prose: drop images, unwrap links, and start at the first
    real paragraph so cookie banners and menus do not look like the article."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    lines = [l.strip() for l in text.split("\n")]
    start = next((i for i, l in enumerate(lines) if len(l) >= 100), 0)
    return "\n".join(l for l in lines[start:] if l)


def direct_read(link: str) -> str:
    """Plain GET with a browser UA, tags stripped but paragraph breaks kept: for sites
    Jina cannot reach."""
    req = urllib.request.Request(link, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "Chrome/124 Safari/537.36",
        "Accept-Language": "en,it;q=0.8"})
    try:
        page = urllib.request.urlopen(req, timeout=20).read().decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    page = re.sub(r"(?s)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>|<br\s*/?>", "\n", page)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", ln))).strip()
             for ln in page.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def usable_text(raw: str, para_cache: dict, log) -> tuple:
    """(text to summarise, whether it is enough). Jev keeps the paragraphs that are
    article prose and drops menus, banners and paywall prompts; if Jev is not
    available the old heuristic (start at the first long line) is used."""
    if para_cache is not None:
        try:
            prose = D.article_prose(raw, para_cache)
            return prose, len(prose) >= D.MIN_PROSE
        except (SystemExit, Exception) as e:  # noqa: BLE001 - no key, service down
            log(f"      Jev non disponibile ({str(e)[:40]}): euristica")
    text = body_only(M.clean_content(raw)) if raw else ""
    return text, len(text) >= MIN_CHARS


ART = {}                 # link -> article text, persisted next to the summaries
ART_LOCK = threading.Lock()


def read_link(link: str, log, para_cache: dict) -> str:
    """One link: Jina first, then a plain GET. "" when neither gives enough prose."""
    t0 = time.monotonic()
    ok, raw = M.jina_read(link, timeout=FETCH_TIMEOUT)
    text, enough = usable_text(raw if ok else "", para_cache, log)
    log(f"      jina {'ok' if enough else ('KO' if not ok else 'corto')} "
        f"{len(text)} car di prosa  {link[:70]}")
    if not enough:
        text, enough = usable_text(direct_read(link), para_cache, log)
        log(f"      diretto {'ok' if enough else 'KO'} {len(text)} car di prosa")
    METER.lap("lettura", time.monotonic() - t0)
    return text[:MAX_CHARS] if enough else ""


def article_text(links: list, log=print, para_cache: dict = None) -> str:
    """The first usable article among the first three links, tried together: the
    fastest good page wins, a dead or slow site no longer delays the others."""
    links = links[:3]
    with ART_LOCK:
        for link in links:
            if ART.get(link):
                METER.note("articoli_cache")
                log(f"      testo dalla cache ({len(ART[link])} car)  {link[:70]}")
                return ART[link]
    pool = ThreadPoolExecutor(max(1, len(links)))
    try:
        futures = {pool.submit(read_link, ln, log, para_cache): ln for ln in links}
        for f in as_completed(futures):
            text = f.result()
            if text:
                with ART_LOCK:
                    ART[futures[f]] = text
                return text
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return ""


def ask(key: str, model: str, title: str, text: str, quiet: bool,
        tries: int = 4, feedback: list = None, log=print):
    """-> "TITOLO: ...\\n\\nriassunto", "NONE", or None after `tries` failures.
    Anything too short to be a summary, or without a title line, is retried."""
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
        head, body = split_out(out)
        if head and len(body) >= 80:
            return out
        if not quiet:
            log(f"      risposta scartata ({out[:30]!r}), riprovo {t + 1}/{tries}")
    return None


def verdict(key: str, text: str, title: str, summary: str, areas: list, model: str = STRONG):
    """Final revision. -> {"problems": [...], "ia": bool, "area": key, "rilievo": "su|ok|giu"},
    or None when the checker failed. Problems empty = title and summary are faithful."""
    menu = "\n".join(f"  {k}: {what}" for k, what in areas)
    keys = {k for k, _ in areas}
    for _ in range(3):
        try:
            got = TR.extract_json(TR.call(
                key, model, VERIFY_PROMPT % (menu, title, summary, text)))
        except Exception:  # noqa: BLE001
            continue
        if "ok" in got:
            problems = [] if got["ok"] else (
                [str(p) for p in got.get("problemi", [])] or ["non fedele al testo"])
            rilievo = got.get("rilievo") if got.get("rilievo") in ("su", "ok", "giu") else "ok"
            return {"problems": problems, "ia": got.get("ia") is not False,
                    "area": got.get("area") if got.get("area") in keys else None,
                    "rilievo": rilievo}
    return None


def vet(key: str, source: str, text: str, title: str, summary: str, areas: list,
        quiet: bool, log=print):
    """Lint + final revision, with up to two regenerations that get the problems fed back.

    The checker is noisy: five summaries it rejected after one repair all passed
    first time when regenerated, so a single failed round must not be final.
    Returns (title, summary, review) that passed, ("", "", None) after three failed
    rounds, or None when the checker itself was unavailable (nothing is decided,
    retry next run)."""
    for attempt in range(3):
        problems, review = lint(summary), None
        if not problems:
            t0 = time.monotonic()
            review = verdict(key, text, title, summary, areas, REVIEW)
            if review and review["problems"] and REVIEW != STRONG:
                if not quiet:
                    log(f"      {REVIEW.split(':')[-1]} segnala: {'; '.join(review['problems'])[:120]}"
                        f" -> conferma con {STRONG.split(':')[-1]}")
                METER.note("conferme_pro")
                review = verdict(key, text, title, summary, areas, STRONG) or review
            METER.lap("revisione", time.monotonic() - t0)
            if review is None:
                return None
            problems = review["problems"]
        if not problems:
            return title, summary, review
        if not quiet:
            log(f"      controllo: {'; '.join(problems)[:150]}")
        if attempt == 2:
            break
        fixed = ask(key, STRONG, source, text, quiet, tries=2, feedback=problems, log=log)
        if not fixed or fixed.upper().startswith("NONE"):
            return "", "", None
        title, summary = split_out(fixed)
    return "", "", None


def summarize(stories: list, model: str = SUMMARY_MODEL, areas: list = (),
              cache_path: str = None, quiet: bool = False, verify: bool = True) -> tuple:
    """stories: [{"title":..., "links":[...]}] -> (verified cache, stats).

    areas: [(key, description)] of the sections, for the placement check.
    Files: <cache>.json holds every summary written, <cache>.tit.json the Italian
    titles, <cache>.rev.json the final revision (AI-relevance, section, prominence),
    <cache>.ver.json the fingerprint of the ones that passed. The returned dict only
    contains what is safe to publish; stats["titles"] and stats["review"] are keyed
    by digest, like it."""
    key = TR.load_key(model)
    cache_path = cache_path or os.path.join(HERE, "ai-news.sum.json")
    side = lambda tag: cache_path.replace(".json", f".{tag}.json")  # noqa: E731
    ver_path, tit_path, rev_path = side("ver"), side("tit"), side("rev")
    cache, ver = TR.load_cache(cache_path), TR.load_cache(ver_path)
    titles, rev = TR.load_cache(tit_path), TR.load_cache(rev_path)
    para_path = side("para")
    art_path = side("art")
    with ART_LOCK:
        ART.update(TR.load_cache(art_path))
    para_cache = TR.load_cache(para_path) if SUMMARY_PROSE == "jev" else None

    def stale(d: str) -> bool:
        have = cache.get(d)
        if have is None:
            return True
        return bool(have) and verify and (
            ver.get(d) != vkey(titles.get(d, "") + "\n" + have)
            or d not in titles or d not in rev or bool(lint(have)))

    todo = [s for s in stories if stale(digest(s["title"]))]
    stats = {"cached": len(stories) - len(todo), "done": 0, "empty": 0,
             "checked": 0, "rejected": 0}
    if not quiet:
        print(f"  riassunti: {len(stories)} schede, {stats['cached']} gia' pronte e rivedute, "
              f"{len(todo)} da fare con {os.environ.get('SUMMARY_WORKERS', '12')} lavori in parallelo")
        print(f"  scrittore {model}, revisore {REVIEW} (conferma con {STRONG})")

    def one(n: int, s: dict):
        """Everything for one story. Runs in a worker thread, so it only reads
        shared state and returns what to record; output is returned as a block
        so lines from different stories do not interleave."""
        lines = []
        log = (lambda m: None) if quiet else lines.append
        log(f"  [{n}/{len(todo)}] {s['title'][:80]}")
        d = digest(s["title"])
        log(f"      fase 1/3 lettura: {len(s['links'][:3])} fonti da provare")
        text = article_text(s["links"], log=log, para_cache=para_cache)
        log(f"      fase 1/3 lettura: {'ok, ' + str(len(text)) + ' car' if text else 'nessun testo utilizzabile'}")
        fresh = d not in cache or (bool(cache[d]) and d not in titles)
        title, summary = ("", "") if fresh else (titles.get(d, ""), cache[d])
        if fresh and text:
            log(f"      fase 2/3 scrittura: titolo + riassunto con {model.split(':')[-1]}")
            t0 = time.monotonic()
            out = ask(key, model, s["title"], text, quiet, log=log)
            METER.lap("scrittura", time.monotonic() - t0)
            if out is None:
                return d, None, None, False, lines, "", None   # not cached: retried next run
            if out.upper().startswith("NONE") and model != STRONG:
                log(f"      NONE, provo {STRONG}")
                out = ask(key, STRONG, s["title"], text, quiet, tries=2, log=log) or out
            title, summary = ("", "") if out.upper().startswith("NONE") else split_out(out)
            log(f"      fase 2/3 scrittura: {'titolo «' + title[:70] + '», ' + str(len(summary)) + ' car' if summary else 'scartato (non e un articolo)'}")
        mark, review = None, None
        if summary and verify:
            if not text:
                return d, None, None, False, lines, "", None   # cannot check: stays hidden
            log(f"      fase 3/3 revisione finale con {REVIEW.split(':')[-1]}: fedelta, IA, sezione, rilievo")
            vetted = vet(key, s["title"], text, title, summary, list(areas), quiet, log=log)
            if vetted is None:
                return d, None, None, False, lines, "", None
            title, summary, review = vetted
            mark = vkey(title + "\n" + summary) if summary else "rejected"
        shown = f"riassunto {len(summary)} car" if summary else "non disponibile"
        if review:
            shown += (f" | ia={review['ia']} area={review['area']} rilievo={review['rilievo']}")
        log(f"      -> {shown}")
        return d, summary, mark, bool(text), lines, title, review

    workers = max(1, int(os.environ.get("SUMMARY_WORKERS", "12")))
    METER.set_total(len(todo))
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(one, n, s) for n, s in enumerate(todo, 1)]
        for f in as_completed(futures):
            METER.tick()
            try:
                d, summary, mark, had_text, lines, title, review = f.result()
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
                if summary:
                    titles[d] = title
                if review:
                    rev[d] = review
                TR.save_cache(cache_path, cache)
                TR.save_cache(tit_path, titles)
                TR.save_cache(rev_path, rev)
                if verify:
                    TR.save_cache(ver_path, ver)
            stats["done" if summary else "empty"] += 1
    if para_cache is not None:
        TR.save_cache(para_path, para_cache)
    with ART_LOCK:
        TR.save_cache(art_path, ART)
    if not quiet:
        print(f"  riassunti finiti: {stats['done']} scritti, {stats['empty']} senza testo, "
              f"{stats['checked']} rivisti ({stats['rejected']} respinti)")
    public = {d: v for d, v in cache.items()
              if not v or not verify or ver.get(d) == vkey(titles.get(d, "") + "\n" + v)}
    stats["titles"] = {d: titles[d] for d in public if public[d] and d in titles}
    stats["review"] = {d: rev[d] for d in public if public[d] and d in rev}
    return public, stats


def lookup(cache: dict, title: str) -> str:
    return cache.get(digest(title), "")

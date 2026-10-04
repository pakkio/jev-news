#!/usr/bin/env python3
"""Decisions made by Jev (see jev.py) that used to be regexes and word counts.

* classify_areas: which area a story belongs to, with the probability of every
  area. Below a floor of confidence the story goes to the catch-all area.
* merge_clusters: do two stories report the same occurrence? Jev judges every
  candidate pair; a generative model only settles the uncertain band.
* article_prose: which paragraphs of a fetched page are article and which are
  menu, cookie banner, paywall or footer.

Tried and rejected: using Jev as a gate on summary sentences ("is this supported
by the article?"). On 17 real summaries with one injected error each it flagged
10 of 17 correct summaries to catch all the errors: a summary sentence blends
several paragraphs, and Jev's calibration does not survive that synthesis. The
slower verifier in summarize.py stays.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor

import jev as J
from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))
AREA_VERSION = "a1"


def classify_areas(items: list, areas: list, cache_path: str = None, workers: int = 4,
                   min_p: float = 0.45, quiet: bool = False) -> dict:
    """Sets it["area"], it["color"], it["area_p"] (probabilities) on every item.

    `areas` is the list of area dicts, each with a "jev" criteria entry; the LAST
    one is the catch-all. Returns {"new": n, "cached": n, "failed": n}; items that
    could not be classified keep their existing area (the regex one)."""
    cache_path = cache_path or os.path.join(HERE, "ai-news.area.json")
    cache = J.load_cache(cache_path)
    options = {a["key"]: a["jev"] for a in areas}
    sig = J.digest(AREA_VERSION, *sorted(f"{k}:{v['what']}:{v.get('not_for', '')}" for k, v in options.items()))
    catch_all = areas[-1]["key"]
    colors = {a["key"]: a["color"] for a in areas}
    question = {"area": J.choice_question(
        "Which area does the story belong to? Judge by `headline` and `snippet`.", options)}
    stats = {"new": 0, "cached": 0, "failed": 0}
    todo = []

    def put(it, e):
        top = max(e["p"], key=e["p"].get)
        it["area_p"] = e["p"]
        it["area"] = top if e["p"][top] >= min_p else catch_all
        it["color"] = colors[it["area"]]

    for it in items:
        d = J.digest(sig, it["title"], it.get("snippet", ""))
        if d in cache:
            put(it, cache[d])
            stats["cached"] += 1
        else:
            todo.append((d, it))
    if todo:
        key = J.load_key()

        def one(x):
            d, it = x
            ans = J.call(key, {"headline": it["title"], "snippet": (it.get("snippet") or "")[:500]}, question)
            return d, it, ({"p": ans["area"]["probabilities"]} if ans else None)

        METER.set_total(len(todo))
        with ThreadPoolExecutor(workers) as pool:
            for d, it, e in pool.map(one, todo):
                METER.tick()
                if e is None:
                    stats["failed"] += 1
                    continue
                cache[d] = e
                put(it, e)
                stats["new"] += 1
        J.save_cache(cache_path, cache)
    if not quiet:
        print(f"  aree con Jev: nuove {stats['new']}, in cache {stats['cached']}, fallite {stats['failed']}")
    return stats


# ------------------------------------------------------------ same event -----
PAIR_VERSION = "p2"
AUTO_P = 0.80          # Jev alone merges above this
UNSURE_P = 0.30        # between UNSURE_P and AUTO_P a generative model is asked, once, about all of them

# Leaner than the criteria Jev would like (one contrastive example each, short wording): they
# are repeated for every candidate in a request, so each character is paid up to 10 times.
SAME_YES = {"what": "One and the same occurrence, even from another angle, in other words or another language.",
            "examples": ["Court tosses lawsuit / Giudice respinge la causa"]}
SAME_NO = {"what": "Different occurrences: same kind of event on another day, place or with other victims; "
                   "or same topic or company with a different development.",
           "examples": ["An attack in Bologna on Tuesday vs another on Friday"]}


def _card(c: dict) -> dict:
    return {"headline": c["title"], "snippet": (c.get("snippet") or "")[:300]}


def candidate_pairs(clusters: list, max_gap_h: float = 120, max_per: int = 15) -> list:
    """Pairs worth asking about: published within five days of each other and sharing a
    name or two words. Cheap and permissive; Jev does the judging."""
    import format_news as FN
    toks = [FN.tokens(c["title"]) | FN.tokens(c.get("snippet", "")[:160]) for c in clusters]
    df = {}
    for t in toks:
        for w in t:
            df[w] = df.get(w, 0) + 1
    hrs = [FN.hours(c["date"]) for c in clusters]
    out = []
    for i in range(len(clusters)):
        found = []
        for j in range(i + 1, len(clusters)):
            if hrs[i] < 9999 and hrs[j] < 9999 and abs(hrs[i] - hrs[j]) > max_gap_h:
                continue
            shared = toks[i] & toks[j]
            rare = [w for w in shared if df[w] <= 6]
            if rare or len(shared) >= 2:
                found.append((len(shared), j))
        for _, j in sorted(found, reverse=True)[:max_per]:
            out.append((i, j))
    return out


def _pair_key(clusters: list, i: int, j: int) -> str:
    a, b = _card(clusters[i]), _card(clusters[j])
    return J.digest(PAIR_VERSION, a["headline"], a["snippet"], b["headline"], b["snippet"])


def judge_pairs(clusters: list, pairs: list, cache_path: str = None, workers: int = 6) -> dict:
    """{(i, j): probability that the two stories report the same occurrence}.
    One request per story, with its candidates as a list of questions answered together."""
    cache_path = cache_path or os.path.join(HERE, "ai-news.pair.json")
    cache = J.load_cache(cache_path)
    out, todo = {}, {}

    def k(i, j):
        return _pair_key(clusters, i, j)

    for i, j in pairs:
        key = k(i, j)
        if key in cache:
            out[(i, j)] = cache[key]
        else:
            todo.setdefault(i, []).append(j)
    jobs = [(i, js[n:n + 10]) for i, js in todo.items() for n in range(0, len(js), 10)]
    if jobs:
        apikey = J.load_key()

        def one(job):
            i, js = job
            qs = {f"s{n}": J.noul_question(
                f"Do `a` and `candidates[{n}]` report the very same occurrence: the same incident, ruling, "
                f"announcement, lawsuit, vote or event, at the same moment?", SAME_YES, SAME_NO)
                for n in range(len(js))}
            ans = J.call(apikey, {"a": _card(clusters[i]), "candidates": [_card(clusters[j]) for j in js]}, qs)
            return i, js, ans

        METER.set_total(len(jobs))
        with ThreadPoolExecutor(workers) as pool:
            for i, js, ans in pool.map(one, jobs):
                METER.tick()
                if not ans:
                    continue
                for n, j in enumerate(js):
                    p = ans[f"s{n}"]["noul"]
                    cache[k(i, j)] = p
                    out[(i, j)] = p
        J.save_cache(cache_path, cache)
    return out


ADJUDICATE_PROMPT = """\
Per ciascuna coppia di titoli di giornale dimmi se riportano LO STESSO IDENTICO FATTO (stesso episodio,
stessa causa, stessa udienza, stessa manifestazione, nello stesso momento). Stesso tema o stesso luogo
non bastano; due episodi dello stesso tipo in giorni o con vittime diverse sono fatti diversi.
Rispondi ESCLUSIVAMENTE con JSON: {"stesso": [numeri delle coppie che sono lo stesso fatto]}

Coppie:
%s"""


def adjudicate(clusters: list, unsure: list, model: str = None):
    """Asks a generative model about the uncertain pairs only, all in one small request.
    Returns the set of (i, j) it confirms, or None when it could not answer."""
    import translate as TR
    if not unsure:
        return set()
    model = model or TR.DEFAULT_MODEL
    lines = [f"{n}. A: {clusters[i]['title']} ({clusters[i]['date']}) - {clusters[i].get('snippet', '')[:100]}\n"
             f"   B: {clusters[j]['title']} ({clusters[j]['date']}) - {clusters[j].get('snippet', '')[:100]}"
             for n, (i, j, _) in enumerate(unsure, 1)]
    key = TR.load_key(model)
    for _ in range(2):
        try:
            got = TR.extract_json(TR.call(key, model, ADJUDICATE_PROMPT % "\n".join(lines),
                                          retries=1, timeout=200))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(got.get("stesso"), list):
            return {(unsure[n - 1][0], unsure[n - 1][1]) for n in got["stesso"]
                    if isinstance(n, int) and 1 <= n <= len(unsure)}
    return None


def merge_clusters(clusters: list, cache_path: str = None, model: str = None,
                   quiet: bool = False, max_group: int = 6) -> dict:
    """Merges clusters that report the same occurrence, in place. Jev judges every
    candidate pair; above AUTO_P it decides alone, in the uncertain band a generative
    model is asked about those pairs only. Each merged card keeps its evidence in
    c["merged"] (title, outlet, probability, who decided) for the page's explanation."""
    import format_news as FN
    pairs = candidate_pairs(clusters)
    probs = judge_pairs(clusters, pairs, cache_path)
    auto = {(i, j): p for (i, j), p in probs.items() if p >= AUTO_P}
    unsure = [(i, j, p) for (i, j), p in probs.items() if UNSURE_P <= p < AUTO_P]
    # the generative model's verdicts are cached per pair: a rerun is instant and gives the same answer
    cache_file = cache_path or os.path.join(HERE, "ai-news.pair.json")
    cache = J.load_cache(cache_file)
    by_llm, fresh = set(), []
    for i, j, p in unsure:
        ck = "llm:" + _pair_key(clusters, i, j)
        if ck in cache:
            if cache[ck]:
                by_llm.add((i, j))
        else:
            fresh.append((i, j, p))
    answered = adjudicate(clusters, fresh, model)
    if answered is not None:
        for i, j, p in fresh:
            cache["llm:" + _pair_key(clusters, i, j)] = (i, j) in answered
        by_llm |= answered
        J.save_cache(cache_file, cache)
    chosen = {**auto, **{(i, j): probs[(i, j)] for i, j in by_llm}}
    how = {pair: ("jev" if pair in auto else "llm") for pair in chosen}

    parent = list(range(len(clusters)))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    size = [1] * len(clusters)
    for (i, j) in sorted(chosen, key=lambda p: -chosen[p]):
        ri, rj = root(i), root(j)
        if ri != rj and size[ri] + size[rj] <= max_group:
            parent[rj] = ri
            size[ri] += size[rj]
    groups = {}
    for i in range(len(clusters)):
        groups.setdefault(root(i), []).append(i)
    absorbed, gone = 0, set()
    for idx in groups.values():
        if len(idx) < 2:
            continue
        base = max((clusters[i] for i in idx), key=lambda c: len(c["members"]))
        evidence = base.setdefault("merged", [])
        for i in idx:
            c = clusters[i]
            if c is base:
                continue
            ps = [chosen[pr] for pr in chosen if i in pr and (clusters[pr[0]] is base or clusters[pr[1]] is base
                                                              or pr[0] in idx and pr[1] in idx)]
            who = next((how[pr] for pr in how if i in pr), "jev")
            evidence.append({"title": c["title"], "source": c["sources"][0]["name"],
                             "p": round(max(ps) if ps else 0.0, 2), "by": who})
            base["members"] += c["members"]
            if len(c.get("snippet", "")) > len(base.get("snippet", "")):
                base["snippet"] = c["snippet"]
            if FN.hours(c["date"]) < FN.hours(base["date"]):
                base["date"] = c["date"]
            gone.add(id(c))
            absorbed += 1
            if not quiet:
                print(f"  unisco [{who} {evidence[-1]['p']:.2f}]: {c['title'][:56]!r} -> {base['title'][:56]!r}")
        FN.finalize(base)
    clusters[:] = [c for c in clusters if id(c) not in gone]
    clusters.sort(key=lambda c: FN.hours(c["date"]))
    return {"pairs": len(pairs), "auto": len(auto), "unsure": len(unsure), "llm_yes": len(by_llm),
            "absorbed": absorbed}


# ------------------------------------------------------- article body --------
PROSE_VERSION = "b1"
MIN_PROSE = 500        # characters of real prose below which a page is treated as "no article"
FURNITURE_LINE = re.compile(
    r"^(title|url source|published time|markdown content|warning|author|updated|image \d+)\s*:", re.I)
PROSE_OPTIONS = {
    "prose": {"what": "A paragraph of the article itself: sentences of prose that report facts, quote people or "
                      "explain the story.",
              "not_for": "A menu, a cookie or subscription notice, a caption, an author line, a related-links list "
                         "or a footer.",
              "examples": ["The Senate voted on Tuesday to pass the bill after years of debate.",
                           "Il tribunale ha respinto il ricorso presentato dal Comune di Bologna."]},
    "furniture": {"what": "Web-page furniture rather than article: navigation, cookie or consent notice, paywall or "
                          "login prompt, share buttons, newsletter sign-up, related links, footer, author or date "
                          "line, image caption.",
                  "examples": ["Abbonati 1 anno a 12€ Scarica l'app", "Accept All Cookies | Manage preferences",
                               "Related articles: Trump signs order", "By Jane Doe | Updated Oct 1"]},
}


def split_paragraphs(text: str) -> list:
    """Lines of at least 40 characters, markdown stripped, Jina's own header lines dropped."""
    out = []
    for ln in text.split("\n"):
        ln = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ln)
        ln = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", ln).strip(" *#>-\t")
        if len(ln) >= 40 and not FURNITURE_LINE.match(ln):
            out.append(ln[:400])
    return out


def article_prose(text: str, cache: dict) -> str:
    """Only the paragraphs Jev reads as article prose, in order. `cache` maps a
    paragraph to its probability of being prose, so a page seen twice costs nothing.
    Raises when Jev is unavailable; the caller falls back on the old heuristic."""
    pars = split_paragraphs(text)
    todo = [p for p in pars if J.digest(PROSE_VERSION, p) not in cache]
    if todo:
        key = J.load_key()
        for n in range(0, len(todo), 20):
            chunk = todo[n:n + 20]
            qs = {f"p{k}": J.choice_question(
                f"Is `paragraphs[{k}]` article prose or web-page furniture?", PROSE_OPTIONS)
                for k in range(len(chunk))}
            ans = J.call(key, {"paragraphs": chunk}, qs)
            if not ans:
                raise RuntimeError("Jev unavailable")
            for k, par in enumerate(chunk):
                cache[J.digest(PROSE_VERSION, par)] = ans[f"p{k}"]["probabilities"]["prose"]
    return "\n".join(p for p in pars if cache[J.digest(PROSE_VERSION, p)] >= 0.5)

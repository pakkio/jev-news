#!/usr/bin/env python3
"""Editorial rating of stories with Jev (see jev.py): meaning, not keywords.

The first ranking rewarded a list of English words ("exclusive", "lawsuit",
"rolls out"): a French, German, Spanish or Italian headline could never earn the
bonus, and every new topic needed a new list. Here each story gets, in ONE
request, seven independent questions:

* four ratings that feed the score (impact, new development, substance,
  exclusive), each normalised to 0-1 and combined with weights that live in this
  file and are published on the page (TypeSafe's "composite scoring" pattern);
* the type of piece (report, opinion, press release, explainer, roundup, not an
  article): opinion takes points off, and the type is shown on the card;
* whether the story is really about the topic of the digest.

Cached by model, topic and text, so a rerun costs nothing. If the key is missing
or the service fails the caller falls back on freshness, sources and snippet.
"""

import os
from concurrent.futures import ThreadPoolExecutor

import jev as J
from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "v5"           # bump to invalidate every cached rating
MAX_POINTS = 22          # what a perfect story adds to the ranking score
OPINION_PENALTY = 6      # points taken off a piece that is mostly opinion

# id -> (weight in the composite, labels it/en shown on the page, one-line meaning it/en)
DIMENSIONS = {
    "impact": (0.40, ("Impatto", "Impact"),
               ("quanto sono ampie le conseguenze di ciò che si riporta, da locali a storiche",
                "how far-reaching the consequences are, from local to landmark")),
    "development": (0.25, ("Novità", "New development"),
                    ("si riporta un fatto, una decisione o un annuncio appena avvenuti",
                     "it reports an event, decision or announcement that just happened")),
    "substance": (0.20, ("Sostanza", "Substance"),
                  ("contiene fatti concreti: chi, cosa, dove, quanto",
                   "it carries concrete facts: who, what, where, how much")),
    "exclusive": (0.15, ("Esclusiva", "Exclusive"),
                  ("presenta informazioni inedite, esclusive o trapelate",
                   "it presents new, exclusive or leaked information")),
}
OPINION = ("Opinione", "Opinion",
           ("articolo di commento o presa di posizione, non cronaca: toglie punti",
            "commentary or advocacy rather than reporting: takes points off"))

# piece type -> (label it, label en)
PIECE_TYPES = {
    "report": ("Cronaca", "Report"),
    "opinion": ("Commento", "Opinion"),
    "press_release": ("Comunicato", "Press release"),
    "explainer": ("Guida", "Explainer"),
    "roundup": ("Rassegna", "Roundup"),
    "non_article": ("Non è un articolo", "Not an article"),
}


def piece_options() -> dict:
    return {
        "report": {
            "what": "News reporting of a specific event, decision, incident or development.",
            "not_for": "Commentary that merely mentions an event, or an organisation announcing its own news.",
            "examples": ["Court upholds state law", "Aggredita una coppia gay a Bologna",
                         "Le Sénat adopte la loi sur le mariage", "Gericht kippt das Gesetz"],
        },
        "opinion": {
            "what": "An opinion column, editorial, op-ed or analysis that argues a position, or an advocacy appeal.",
            "not_for": "A news report that quotes people who hold strong opinions.",
            "examples": ["Opinion | Why the ban is wrong", "Tribune : pourquoi il faut agir",
                         "Kommentar: Die Regierung versagt", "Editorial: el Gobierno se equivoca"],
        },
        "press_release": {
            "what": "An announcement written by the organisation it concerns: a company, ministry, city hall, "
                    "NGO or event organiser.",
            "not_for": "Independent reporting by a newspaper about the same organisation.",
            "examples": ["EC-Council Releases ADG 2.0 and Offers Its Crosswalks Free",
                         "FVG Pride a Udine il 3 ottobre, con il patrocinio del Comune",
                         "Pressemitteilung des Ministeriums"],
        },
        "explainer": {
            "what": "An explainer, guide, how-to, listicle, FAQ or evergreen background piece with no new event.",
            "not_for": "A report of a new event that adds background.",
            "examples": ["7 best stocks to buy now", "What the new law means for you",
                         "Cómo funciona la inteligencia artificial"],
        },
        "roundup": {
            "what": "A live blog, newsletter, daily digest, programme or schedule covering several items.",
            "not_for": "A single story with several quotes.",
            "examples": ["Morning briefing: five things to know", "Pride in Italia: tutte le date",
                         "Live: les dernières informations"],
        },
        "non_article": {
            "what": "Not an article: a photo or video catalogue entry, a gallery, a social post, a podcast or "
                    "show introduction, or a page title with no content.",
            "examples": ["Licensable picture: the parade kicked off from the auditorium",
                         "Look at this quilt #Pride #wrestling", "It's Wednesday. I'm Albert Mohler, and this is The Briefing"],
        },
    }


def questions(topic: str) -> dict:
    """The seven questions, following TypeSafe's advice: say what a yes and a no mean,
    with a `not_for` that rules out the near miss, and contrastive examples."""
    return {
        "impact": J.score_question(
            "How far-reaching are the consequences of what `headline`, `snippet` and, when present, `other_headlines` and `lead` report?", [
                "Personal or local interest only: one person, one business or one town, with no wider consequence.",
                "Notable within a sector, region or community: affects a group or a market but changes nothing lasting.",
                "Significant: changes rules, rights, markets or safety for many people (a national law, a major court "
                "decision, large funding, a major product or policy decision).",
                "Landmark: far-reaching or precedent-setting for a whole country or the world (a supreme-court ruling, "
                "constitutional change, major crisis or conflict, an industry-defining event).",
            ]),
        "development": J.noul_question(
            "Does `headline`, `snippet` or `lead` report a specific new event, decision, announcement, ruling, vote, deal "
            "or incident that has just happened?",
            {"what": "Reports something concrete and new: it was announced, ruled, voted, signed, launched, attacked, "
                     "acquired or discovered.",
             "examples": ["Senate passes bill banning workplace discrimination", "Le tribunal annule la loi",
                          "Nvidia kündigt neuen KI-Chip an", "Aggredita una coppia gay a Bologna"]},
            {"what": "Explainer, retrospective, guide, listicle, promotional or evergreen content, or commentary "
                     "with no new event.",
             "not_for": "A report of a new event that also contains opinion or background.",
             "examples": ["7 best stocks to buy now", "What the new law means for you",
                          "Cómo funciona la inteligencia artificial"]}),
        "substance": J.score_question("How many concrete facts do `headline`, `snippet` and, when present, `other_headlines` and `lead` give?", [
            "No concrete facts: a teaser, promotional text, a bare title, or a string of keywords.",
            "Some concrete facts but incomplete: says roughly who or what, without figures, places or dates.",
            "Specific: names who did what, where or how much, with figures, dates or quotes.",
        ]),
        "exclusive": J.noul_question(
            "Does the text present itself as an exclusive, a scoop or a leak, meaning information other outlets "
            "did not have?",
            {"what": "Explicitly framed as exclusive, a first report, leaked documents or a revelation from unnamed sources.",
             "examples": ["Exclusive | Company X agrees to buy Y", "Leaked document shows plans",
                          "Exclusif : le gouvernement prépare", "Exclusiva: según fuentes del ministerio"]},
            {"what": "Ordinary reporting of public events, press releases, or reactions to news.",
             "not_for": "A headline that merely says 'breaking' or 'latest' without new information.",
             "examples": ["Mayor attends Pride parade", "Court hears two lawsuits", "Pressemitteilung des Ministeriums"]}),
        "piece": J.choice_question("What kind of piece is this, judging by `headline`, `snippet` and, when present, `other_headlines` and `lead`?", piece_options()),
        "relevant": J.noul_question(
            f"Is the story mainly about {topic}? Use `headline`, `snippet`, `other_headlines`, `lead` and `url` (the address often names the subject).",
            {"what": f"The central subject of the story is {topic}.",
             "examples": []},
            {"what": f"{topic[:1].upper() + topic[1:]} is only mentioned in passing, as one item in a list or as "
                     f"background, or the story is about something else.",
             "examples": []}),
    }


def normalise(answers: dict) -> dict:
    """Answers -> {"v": five 0-1 values, "type": the piece type, "rel": relevance}."""
    probs = answers["piece"]["probabilities"]
    return {
        "v": {"impact": answers["impact"]["score"] / 3,
              "development": answers["development"]["noul"],
              "substance": answers["substance"]["score"] / 2,
              "exclusive": answers["exclusive"]["noul"],
              "opinion": probs.get("opinion", 0.0)},
        "type": {"choice": answers["piece"]["choice"], "p": probs,
                 "conf": answers["piece"].get("confidence")},
        "rel": answers["relevant"]["noul"],
    }


def breakdown(r: dict) -> dict:
    """Points contributed by each part, for the page's explanation."""
    parts = {k: MAX_POINTS * w * r[k] for k, (w, _, _) in DIMENSIONS.items()}
    parts["opinion"] = -OPINION_PENALTY * r["opinion"]
    return parts


def points(r: dict) -> float:
    """Composite score of one story, in ranking points. Weights are right here."""
    return sum(breakdown(r).values())


LEAD_CHARS = 700         # opening of the article handed to the deep questions
OTHERS = 4               # headlines of the other outlets that told the same event


def others(c: dict) -> list:
    """Headlines the same event got elsewhere: free context, the cluster already has them."""
    seen, out = {c["title"]}, []
    for m in c.get("members") or []:
        t = m["title"]
        if t not in seen:
            seen.add(t)
            out.append(t[:140])
    return out[:OTHERS]


def fetch_lead(c: dict, cache: dict) -> str:
    """Opening paragraphs of the article (Jina Reader, no Serper credit), cached by link."""
    import main as M
    import summarize as SM
    for link in [s["link"] for s in c["sources"][:3]]:
        if link in cache:
            if cache[link]:
                return cache[link]
            continue
        ok, raw = M.jina_read(link)
        text = SM.body_only(M.clean_content(raw)) if ok else ""
        cache[link] = text[:LEAD_CHARS] if len(text) >= 200 else ""
        if cache[link]:
            return cache[link]
    return ""


def _state(c: dict) -> dict:
    st = {"headline": c["title"], "snippet": (c.get("snippet") or "")[:500],
          "url": c["sources"][0]["link"][:200] if c.get("sources") else ""}
    if others(c):
        st["other_headlines"] = others(c)
    if c.get("lead"):
        st["lead"] = c["lead"]
    return st


def rate(clusters: list, cache_path: str = None, topic: str = "artificial intelligence",
         workers: int = 4, quiet: bool = False, deep=None, rel_min: float = 0.0) -> dict:
    """Sets c["jev"] (five 0-1 values), c["ptype"] and c["relevance"] on every cluster that
    could be rated. Returns {"new": n, "cached": n, "failed": n, "light": n}.

    Jev bills input tokens and the questions are 94% of them, so asking all six of every
    story is the expensive path. With `deep`, a callable(clusters) -> the clusters worth a
    full rating, every uncached story first gets only the relevance question (about a
    twelfth of the tokens); the other five go to the stories `deep` picks among those with
    relevance >= rel_min. The rest keep relevance and are ranked by freshness and sources."""
    cache_path = cache_path or os.path.join(HERE, "ai-news.jev.json")
    cache = J.load_cache(cache_path)
    stats = {"new": 0, "cached": 0, "failed": 0, "light": 0}
    todo = []

    def put(c, e):
        c["jev"], c["ptype"], c["relevance"] = e["v"], e["type"], e["rel"]

    def digests(c):
        d = J.digest(VERSION, topic, c["title"], c.get("snippet", ""), *others(c))
        return d, "r" + d

    for c in clusters:
        d, dr = digests(c)
        if d in cache:
            put(c, cache[d])
            stats["cached"] += 1
        else:
            todo.append((d, c))
    key = qs = None
    if todo:
        key = J.load_key()
        qs = questions(topic)

    def run(items, asked, handle):
        METER.set_total(len(items))
        with ThreadPoolExecutor(workers) as pool:
            for item, ans in pool.map(lambda it: (it, J.call(key, _state(it[1]), asked)), items):
                METER.tick()
                handle(item, ans)

    if todo and deep:
        only = {"relevant": qs["relevant"]}

        def light(item, ans):
            d, c = item
            if not ans:
                return
            cache["r" + d] = c["relevance"] = ans["relevant"]["noul"]
            stats["light"] += 1

        ask = []
        for d, c in todo:
            if "r" + d in cache:
                c["relevance"] = cache["r" + d]
            else:
                ask.append((d, c))
        run(ask, only, light)
        pool = [c for c in clusters if (c.get("relevance") is None or c["relevance"] >= rel_min)]
        picked = {id(c) for c in deep(pool)}
        todo = [(d, c) for d, c in todo if id(c) in picked]

    def full(item, ans):
        d, c = item
        e = normalise(ans) if ans else None
        if e is None:
            stats["failed"] += 1
            return
        cache[d] = e
        put(c, e)
        stats["new"] += 1

    if todo and deep:                                  # the deep questions also read the article's opening
        lead_path = cache_path.replace(".jev.json", ".lead.json")
        leads = J.load_cache(lead_path)
        with ThreadPoolExecutor(workers) as pool:
            for (d, c), text in zip(todo, pool.map(lambda it: fetch_lead(it[1], leads), todo)):
                if text:
                    c["lead"] = text
        J.save_cache(lead_path, leads)
        stats["leads"] = sum(1 for _, c in todo if c.get("lead"))
    if todo:
        run(todo, qs, full)
    if key:
        J.save_cache(cache_path, cache)
    if not quiet:
        print(f"  valutazione Jev: complete {stats['new']}, in cache {stats['cached']}, "
              f"solo pertinenza {stats['light']}, fallite {stats['failed']}, con testo dell'articolo {stats.get('leads', 0)}")
    return stats

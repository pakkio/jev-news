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
VERSION = "v4"           # bump to invalidate every cached rating
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
            "How far-reaching are the consequences of what `headline` and `snippet` report?", [
                "Personal or local interest only: one person, one business or one town, with no wider consequence.",
                "Notable within a sector, region or community: affects a group or a market but changes nothing lasting.",
                "Significant: changes rules, rights, markets or safety for many people (a national law, a major court "
                "decision, large funding, a major product or policy decision).",
                "Landmark: far-reaching or precedent-setting for a whole country or the world (a supreme-court ruling, "
                "constitutional change, major crisis or conflict, an industry-defining event).",
            ]),
        "development": J.noul_question(
            "Does `headline` or `snippet` report a specific new event, decision, announcement, ruling, vote, deal "
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
        "substance": J.score_question("How many concrete facts do `headline` and `snippet` give?", [
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
        "piece": J.choice_question("What kind of piece is this, judging by `headline` and `snippet`?", piece_options()),
        "relevant": J.noul_question(
            f"Is the story mainly about {topic}? Use `headline`, `snippet` and `url` (the address often names the subject).",
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


def rate(clusters: list, cache_path: str = None, topic: str = "artificial intelligence",
         workers: int = 4, quiet: bool = False) -> dict:
    """Sets c["jev"] (five 0-1 values), c["ptype"] and c["relevance"] on every cluster that
    could be rated. Returns {"new": n, "cached": n, "failed": n}."""
    cache_path = cache_path or os.path.join(HERE, "ai-news.jev.json")
    cache = J.load_cache(cache_path)
    stats = {"new": 0, "cached": 0, "failed": 0}
    todo = []

    def put(c, e):
        c["jev"], c["ptype"], c["relevance"] = e["v"], e["type"], e["rel"]

    for c in clusters:
        d = J.digest(VERSION, topic, c["title"], c.get("snippet", ""))
        if d in cache:
            put(c, cache[d])
            stats["cached"] += 1
        else:
            todo.append((d, c))
    if todo:
        key = J.load_key()
        qs = questions(topic)

        def one(item):
            d, c = item
            ans = J.call(key, {"headline": c["title"], "snippet": (c.get("snippet") or "")[:500],
                               "url": c["sources"][0]["link"][:200] if c.get("sources") else ""}, qs)
            return d, c, normalise(ans) if ans else None

        METER.set_total(len(todo))
        with ThreadPoolExecutor(workers) as pool:
            for d, c, e in pool.map(one, todo):
                METER.tick()
                if e is None:
                    stats["failed"] += 1
                    continue
                cache[d] = e
                put(c, e)
                stats["new"] += 1
        J.save_cache(cache_path, cache)
    if not quiet:
        print(f"  valutazione Jev: nuove {stats['new']}, in cache {stats['cached']}, "
              f"fallite {stats['failed']}")
    return stats

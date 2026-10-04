#!/usr/bin/env python3
"""Render a Serper-News stories JSON into a themed digest.

Groups stories into thematic areas, clusters near-duplicate coverage of the
same event, and writes three views: a styled HTML page, a markdown digest and
a colourised terminal digest.

Usage:
  python3 format_news.py [stories.json] [--lang it|en] [--max 6] [--intro "..."]
"""

import html
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime

import rate as RATE
from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- aree ------
# L'ordine è editoriale: dall'area che pesa di più a quella di servizio.
# Ogni area ha nome bilingue, colore d'accento e una nota che spiega perché esiste.
AREAS = [
    dict(
        key="policy", color="#f4a261",
        jev={"what": "Government and public-sector action about AI: laws, regulation, courts and lawsuits, "
                     "antitrust, export controls, safety and ethics governance, official statements on rules.",
             "not_for": "A company's product or funding news, or research results.",
             "examples": ["Senate passes AI bill", "Trump signs accord with tech companies",
                          "L'UE adopte l'AI Act", "OpenAI wegen Hackerangriff verklagt"]},
        it=("Politica, sicurezza e governance",
            "Chi detta le regole: leggi, organismi, antitrust e dichiarazioni al vertice."),
        en=("Policy, Safety & Governance",
            "Who sets the rules: legislation, agencies, antitrust and executive statements."),
        pat=r"\b(regulat|policy|policymaker|senate|congress|parliament|white house|administration|"
            r"law|lawsuit|laws|bill|ban|banned|court|ruling|sue|suing|jail|criminal|"
            r"safety|alignment|guardrail|red.?team|governance|antitrust|doj|ftc|eu ai act|"
            r"ai act|commission|self.?regulat|openai and the government|executive order)\b",
    ),
    dict(
        key="models", color="#7aa2f7",
        jev={"what": "New AI models and products from AI labs, research results, benchmarks, techniques, "
                     "agents and open-source releases.",
             "not_for": "Funding and acquisitions, chips and data centres, or government rules.",
             "examples": ["OpenAI releases a new reasoning model", "DeepSeek veröffentlicht neues Modell",
                          "Nuevo benchmark de razonamiento", "Nuovo modello open source"]},
        it=("Modelli e ricerca",
            "Cosa viene effettivamente costruito: rilasci, benchmark, paper e tecniche."),
        en=("Models & Research",
            "What is actually being built: releases, benchmarks, papers and techniques."),
        pat=r"\b(release|releases|released|launch|launches|unveil|debut|model|models|llm|llms|"
            r"benchmark|gpt|chatgpt|gemini|claude|llama|mistral|deepseek|qwen|grok|kimi|"
            r"reasoning|reasoner|inference|training|fine.?tun|context window|open.?weight|"
            r"open.?sourc|multimodal|diffusion|transformer|paper|arxiv|research|researchers|"
            r"fine.?tuning|dataset|token|agent|agents|agi|superintellig)\b",
    ),
    dict(
        key="chips", color="#e0af68",
        jev={"what": "Semiconductors, GPUs and accelerators, memory, data centres, compute capacity, power "
                     "and energy for AI, supply chains.",
             "not_for": "A model launch, or a funding round that only mentions a chip maker.",
             "examples": ["Nvidia unveils new GPU", "TSMC erweitert die Fertigung", "Centros de datos de IA en España",
                          "AMD acquisisce un produttore di chip"]},
        it=("Chip, calcolo e infrastrutture",
            "Il piano fisico sotto il software: silicio, data center e costi di calcolo."),
        en=("Chips, Compute & Infrastructure",
            "The physical layer under the software: silicon, data centres and compute cost."),
        pat=r"\b(chip|chips|chipmaker|semiconductor|nvidia|gpu|gpus|tpu|accelerator|huawei|ascend|"
            r"cannond|brocade|data.?cent(er|re|ri)|cluster|compute|computing|cuda|blackwell|"
            r"tsmc|asml|arm holdi|wafer|fab|foundry|capex|cost per token|inference cost|power grid)\b",
    ),
    dict(
        key="business", color="#9ece6a",
        jev={"what": "Money and deals: funding rounds, valuations, acquisitions, mergers, earnings, IPOs, "
                     "stock-picking, layoffs and market moves involving AI companies.",
             "not_for": "A product or model launch with no financial angle.",
             "examples": ["AI startup raises $350 million", "Anthropic files for IPO", "Levée de fonds record pour une start-up IA",
                          "Nvidia-Aktie steigt nach Quartalszahlen"]},
        it=("Business, capitali e accordi",
            "Soldi e potere: finanziamenti, valutazioni, partnership e acquisizioni."),
        en=("Business, Capital & Deals",
            "Money and power: funding rounds, valuations, partnerships and acquisitions."),
        pat=r"\b(funding|funding round|raise|raised|valuation|revenue|revenues|ipo|prospectus|"
            r"acqui(red|res|sition|sition)|merger|partnership|partner|partners|invest|"
            r"invests|investor|venture|series [a-e]\b|billion|million|earnings|revenue|"
            r"layoff|layoffs|profit|market share|bankrupt|spin.?off|ipo)\b",
    ),
    dict(
        key="industry", color="#bb9af7",
        jev={"what": "Everything else about AI: its use in a sector (health, education, jobs, media, energy, "
                     "public services), its social and cultural impact, explainers and opinion.",
             "examples": ["AI in schools: what teachers say", "L'IA nella sanità", "KI und Arbeitsplätze",
                          "La inteligencia artificial en la educación"]},
        it=("Settore, persone e impatti",
            "Tutto il resto: applicazioni verticali, lavoro, cultura e vita quotidiana."),
        en=("Industry, People & Impact",
            "Everything else: vertical applications, jobs, culture and everyday life."),
        pat=r".*",  # fallback
    ),
]

# ------------------------------------------------------------------ testi ----
UI = {
    "it": dict(
        kicker="Segnale · settimana del", title="Briefing IA",
        sub="{n} storie raccolte da Google News via Serper negli ultimi 7 giorni. "
            "Raggruppate per area, deduplicate: quando più testate coprono lo stesso "
            "evento diventano una sola scheda con più fonti.",
        focus="In evidenza", others="Altre storie", all="Tutte le storie",
        main_label="Notizie principali", sub_label="Secondarie",
        more_note="altre in quest'area",
        sources="fonti", sources_one="fonte", read="Riassunto",
        flip_tip="Gira la scheda: riassunto in italiano", back_label="Torna alla notizia",
        why_btn="Perché?", why_tip="Perché questa notizia è qui e con questo punteggio",
        why_title="Perché è qui?", why_where="Dove si trova",
        why_role_main="Scheda principale dell'area «{a}»", why_role_brief="Riga secondaria dell'area «{a}»",
        why_role_thread="Dentro {kind} «{t}»", why_kind_storia="la storia", why_kind_tema="il tema",
        why_area="Area", why_type="Tipo di pezzo", why_jev="Valutazione di Jev",
        why_score="Come si arriva al punteggio", why_fresh="Freschezza", why_sources="Fonti",
        why_snippet="Anteprima", why_jevpts="Valutazione di Jev",
        why_penalty="Penalità: fonte unica non confermata", why_total="Totale",
        why_merged="Unita a queste notizie sullo stesso fatto", why_rel="Pertinenza al tema",
        why_by_jev="giudizio di Jev", why_by_llm="deciso da un modello generativo",
        why_by_rx="regole a parole (Jev non disponibile)", why_alt="alternative", why_none="Nessuna valutazione disponibile.",
        why_close="Chiudi (Esc)", why_pts="pt",
        edition="Costo di questa edizione: circa {mins} min di elaborazione · Jev ${jev} · "
                "Serper {n} crediti (~${serper}) · totale ~${total}",
        summary="Riassunto", orig="Leggi l'articolo originale", close="Chiudi",
        no_sum="Riassunto non disponibile (articolo non accessibile): ecco l'anteprima.",
        other_src="Altre fonti",
        window="Finestra", areas="aree", clusters="eventi unici",
        beat="area più coperta", generated="Generato il", source="fonte",
        more="e altre", footer_note="Chiavi mai incluse in questo file.",
        legend="la composizione della settimana", no_intro="",
        thread_k="Storia in evoluzione", thread_n="episodi", thread_generic="Episodi collegati",
        thread_tema="Tema ricorrente", thread_n_tema="storie", single="fonte unica",
        unverified="fonte unica, non confermata",
        score_tip="Punteggio di rilevanza: freschezza + numero di fonti + valutazione del contenuto con Jev (vedi Fonti e metodo)",
        weights_h="Come si calcola l'ordine",
        weights_none=("Punteggio = freschezza (fino a 30 punti, in calo nell'arco di circa 5 giorni) "
                      "+ 5 punti per ogni fonte (fino a 5) + fino a 6 punti per uno snippet informativo. "
                      "Nessuna valutazione di contenuto e nessuna parola chiave."),
        weights_note=(
            "Punteggio = freschezza (fino a 30 punti, in calo nell'arco di circa 5 giorni) "
            "+ 5 punti per ogni fonte (fino a 5) + fino a 6 punti per uno snippet informativo "
            "+ fino a {max} punti da una valutazione del modello Jev (TypeSafe) su cinque domande "
            "indipendenti, normalizzate da 0 a 1 e combinate con i pesi qui sotto. Il modello "
            "giudica il significato, non le parole, quindi vale per ogni lingua; i pesi sono nel "
            "codice, uguali per ogni tema."),
        weights_op="toglie fino a {n} punti",
        method_h="Fonti e metodo",
        method=("Gli articoli arrivano da Google News (tramite Serper) negli ultimi 7 giorni, "
                "con ricerche per area. Le notizie sullo stesso evento sono unite in una scheda "
                "per somiglianza dei titoli; gli episodi collegati formano una storia in evoluzione.",
                "L'ordine nasce da una formula con pesi scelti da noi (dettagli sotto): non è "
                "una misura neutra ma un giudizio editoriale scritto in codice.",
                "Le {outlets} testate sono quelle che Google News restituisce: agenzie, quotidiani, "
                "siti specializzati, comunicati istituzionali, blog e commenti di parte. "
                "Comparire qui non significa che ne condividiamo la linea.",
                "Titoli, anteprime e riassunti sono tradotti e scritti da un modello di IA e possono "
                "contenere errori: fa fede l'articolo originale, linkato in ogni scheda."),
        stats=(("storie", "{n}"), ("eventi unici", "{c}"), ("aree", "{a}"),
               ("eventi per area (min–max)", "{g}"), ("finestra", "{w}")),
    ),
    "en": dict(
        kicker="Signal · week of", title="AI Briefing",
        sub="{n} stories pulled from Google News via Serper over the last 7 days. "
            "Grouped by area and deduplicated: when several outlets cover the same "
            "event it becomes one card with multiple sources.",
        focus="In focus", others="More stories", all="All stories",
        main_label="Lead stories", sub_label="Briefs",
        more_note="more in this area",
        sources="sources", sources_one="source", read="Summary",
        flip_tip="Flip the card for the summary", back_label="Back to the story",
        why_btn="Why?", why_tip="Why this story is here, with this score",
        why_title="Why is it here?", why_where="Where it sits",
        why_role_main="Main card of the “{a}” area", why_role_brief="Secondary row of the “{a}” area",
        why_role_thread="Inside {kind} “{t}”", why_kind_storia="the story", why_kind_tema="the theme",
        why_area="Area", why_type="Type of piece", why_jev="Jev's rating",
        why_score="How the score adds up", why_fresh="Freshness", why_sources="Sources",
        why_snippet="Preview", why_jevpts="Jev's rating",
        why_penalty="Penalty: single source, unconfirmed", why_total="Total",
        why_merged="Merged with these reports of the same event", why_rel="Relevance to the topic",
        why_by_jev="Jev's judgement", why_by_llm="settled by a generative model",
        why_by_rx="keyword rules (Jev unavailable)", why_alt="alternatives", why_none="No rating available.",
        why_close="Close (Esc)", why_pts="pt",
        edition="Cost of this edition: about {mins} min of processing · Jev ${jev} · "
                "Serper {n} credits (~${serper}) · total ~${total}",
        summary="Summary", orig="Read the original article", close="Close",
        no_sum="Summary unavailable (article not accessible): here is the preview.",
        other_src="Other sources",
        window="Window", areas="areas", clusters="unique events",
        beat="most-covered beat", generated="Generated", source="source",
        more="more", footer_note="No key material in this file.",
        legend="how the week broke down", no_intro="",
        thread_k="Developing story", thread_n="episodes", thread_generic="Related episodes",
        thread_tema="Recurring theme", thread_n_tema="stories", single="single source",
        unverified="single source, unconfirmed",
        score_tip="Relevance score: freshness + number of sources + content rating by Jev (see Sources & method)",
        weights_h="How the order is computed",
        weights_none=("Score = freshness (up to 30 points, decaying over about 5 days) + 5 points "
                      "per source (up to 5) + up to 6 points for an informative snippet. No content "
                      "rating and no keywords."),
        weights_note=(
            "Score = freshness (up to 30 points, decaying over about 5 days) + 5 points per "
            "source (up to 5) + up to 6 points for an informative snippet + up to {max} points "
            "from a rating by the Jev model (TypeSafe) on five independent questions, normalised "
            "to 0-1 and combined with the weights below. The model judges meaning, not words, so "
            "it works in any language; the weights live in the code and are the same for every topic."),
        weights_op="takes off up to {n} points",
        method_h="Sources & method",
        method=("Articles come from Google News (via Serper) over the last 7 days, searched "
                "per area. Reports of the same event are merged into one card by headline "
                "similarity; linked episodes form a developing story.",
                "The order comes from a formula with weights we chose (details below): it is "
                "not a neutral measure but an editorial judgement written as code.",
                "The {outlets} outlets are whatever Google News returns: wires, dailies, trade "
                "sites, official releases, blogs and partisan commentary. Appearing here does "
                "not mean we endorse a line.",
                "Headlines, previews and summaries are translated and written by an AI model "
                "and may contain errors: the original article, linked on every card, prevails."),
        stats=(("stories", "{n}"), ("unique events", "{c}"), ("areas", "{a}"),
               ("events per area (min–max)", "{g}"), ("window", "{w}")),
    ),
}

STOP = set("""the a an and or but for with from that this these those into over under
about after before while has have had was were is are be been being it its it's his her
their our your my they them he she we you i not no new now says say said will would can
could may might just how why what when where who which than then out up down off more
most other some such only own same too very s t don now ai artificial intelligence
technology tech company companies report reports according told according_x""".split())


def tokens(title: str) -> set:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return {w for w in t.split() if len(w) > 3 and w not in STOP}


# ------------------------------------------------------------- ranking -----
# Importance of a story = freshness + breadth of coverage + snippet + an editorial
# rating by Jev (see rate.py), which judges meaning in any language. There used to
# be a list of English words here; it could not work for French, German or Spanish.


def score(c: dict) -> float:
    """Importance: freshness, breadth of coverage, snippet, and Jev's rating when present."""
    s = max(0.0, 30 - hours(c["date"]) / 4)          # decays over ~5 days
    s += min(len(c["sources"]), 5) * 5                # several outlets = bigger
    s += min(len(c["snippet"]) / 45, 6)               # informative snippet
    if c.get("jev"):
        s += RATE.points(c["jev"])                    # meaning, not keywords
    return s - c.get("penalty", 0)       # e.g. a single source nobody could confirm


MESI_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")
MESI_EN = ("January February March April May June July August September "
           "October November December").split()
AGGI = {"minute": ("minuto", "minuti"), "hour": ("ora", "ore"),
        "day": ("giorno", "giorni"), "week": ("settimana", "settimane"),
        "month": ("mese", "mesi")}


# ----------------------------------------------------------------- hero -----
# Verified to return 200 on images.unsplash.com. Attribution for these reads
# "Unsplash Contributor" rather than a named photographer, so the page credits
# Unsplash and links out instead of guessing a name.
HEROES = {
    "earth":   ("photo-1451187580459-43490279c0fa", "la Terra di notte"),
    "circuit": ("photo-1550751827-4bd374c3f58b", "un circuito in vetro"),
    "code":    ("photo-1526374965328-7f61d4dc18c5", "codice su schermo"),
    "robot":   ("photo-1620712943543-bcc4688e7485", "un robot umanoide"),
    "laptop":  ("photo-1531297484001-80022131f5a1", "un portatile al buio"),
}
DEFAULT_HERO = "earth"
EMBED_IMAGES = True      # inline the pictures so the page does not depend on 40 CDNs
COST = None             # {"jev","serper","total","pieces",...} set by the caller (costs.attach)
CURRENT_LANG = "it"      # language of the page being rendered (set by render_html)
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
           "viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' "
           "y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%237aa2f7'/%3E"
           "%3Cstop offset='1' stop-color='%23f4a261'/%3E%3C/linearGradient%3E"
           "%3C/defs%3E%3Crect width='32' height='32' rx='8' fill='%2308090d'/%3E"
           "%3Ccircle cx='16' cy='16' r='7' fill='url(%23g)'/%3E%3C/svg%3E")


def hero_html(hero: str, lang: str) -> str:
    """Banner image, or nothing. The gradient underneath it means a blocked or
    dead image degrades to the plain header rather than a broken icon."""
    if not hero or hero.lower() == "off":
        return ""
    label = {"it": "Foto", "en": "Photo"}[lang]
    esc = html.escape
    if hero.lower() in HEROES:
        pid, alt = HEROES[hero.lower()]
        src = (f"https://images.unsplash.com/{pid}"
               f"?w=2000&q=70&fm=jpg&fit=crop&auto=format")
        href = ("https://unsplash.com/?utm_source=ai_briefing"
                "&utm_medium=referral")
    else:
        src, href, alt = hero, hero, "hero"
    if EMBED_IMAGES:
        import images as IMG
        cache_path = os.path.join(HERE, "ai-news.img.data.json")
        cache = {}
        try:
            cache = json.load(open(cache_path))
        except (OSError, ValueError):
            pass
        src = IMG.data_uri(src, 1600, cache)
        try:
            json.dump(cache, open(cache_path, "w"))
        except OSError:
            pass
    return f"""
    <figure class="hero">
      <img src="{esc(src)}" alt="{esc(alt)}" loading="eager"
           decoding="async" referrerpolicy="no-referrer">
      <figcaption class="credit">{esc(label)}: <a href="{esc(href)}"
        target="_blank" rel="noopener nofollow">Unsplash</a></figcaption>
    </figure>"""


CRUMB = re.compile(r"((?:\s+/\s+[^/|]{1,40}){1,})$")
SUFFIX = re.compile(r"\s+[|»\-–—:]\s+([^|»\-–—:]{2,40})$")


def clean_title(title: str, source: str = "") -> str:
    """Strips what a site adds to a headline: a breadcrumb ("/ Comunicati / Novita'
    / Homepage") or its own name ("... - Reuters"). A trailing "| Opinion" or a
    "Nvidia / AMD" inside the sentence are content and stay."""
    t = title
    m = CRUMB.search(t)
    if m:
        segs = [x for x in m.group(1).split("/") if x.strip()]
        if len(segs) >= 2 or segs[-1].strip().lower() in ("homepage", "home"):
            t = t[:m.start()]
    m = SUFFIX.search(t)
    if m and source:
        tail, src = m.group(1).strip().lower(), source.lower()
        if tail in src or src in tail:
            t = t[:m.start()]
    return t.strip() or title


def show(c: dict, field: str = "title") -> str:
    """Translated text when available, English otherwise; headlines are cleaned
    of site furniture."""
    text = c.get(f"{field}_it") or c[field]
    if field == "title" and c.get("sources"):
        return clean_title(text, c["sources"][0]["name"])
    return text


# ----------------------------------------------------------- filler items ----
DATE_TITLE = re.compile(
    r"^(?:it'?s |it is |today is |oggi [eè] )?"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day|luned[iì]|marted[iì]|mercoled[iì]|"
    r"gioved[iì]|venerd[iì]|sabato|domenica)?[, ]*\w+ \d{1,2},? \d{4}\.?$", re.I)
CATALOG = ("reutersconnect.com", "gettyimages", "alamy.com", "shutterstock",
           "apimages.com", "istockphoto")


def drop_filler(items: list) -> tuple:
    """Removes entries that are not news and returns (kept, [(reason, title)]).

    Three kinds so far: photo-catalogue listings (Reuters Connect "Licensable
    picture: ..."), a headline that is only a date (a show's page whose real
    title is missing; rewriting it from the snippet just yields the show's
    intro), and a headline of three words or fewer over a snippet with hashtags,
    which is a social post and not an article."""
    kept, dropped = [], []
    for it in items:
        title, snip = it["title"].strip(), (it.get("snippet") or "").strip()
        link = it.get("link", "").lower()
        if any(d in link for d in CATALOG) or re.match(
                r"^(licensable (picture|photo)|stock photo)", title, re.I):
            dropped.append(("catalogo fotografico", title))
            continue
        if DATE_TITLE.match(title):
            dropped.append(("il titolo e' solo una data", title))
            continue
        if re.search(r"#\w+", snip) and len(title.split()) <= 3:
            dropped.append(("post social: titolo breve e hashtag", title))
            continue
        kept.append(it)
    return kept, dropped


def fmt_date(d: str, lang: str) -> str:
    """Serper emits English relative dates; an Italian page should not."""
    d = (d or "").strip()
    if lang == "en":
        return d
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", d, re.I)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()
        sing, plur = AGGI[u]
        return f"{n} {sing if n == 1 else plur} fa"
    for i, name in enumerate(MESI_EN, 1):
        d = re.sub(rf"\b{name[:3]}[a-z]*\b", MESI_IT[i - 1], d, flags=re.I)
    return d


def by_rank(rows: list) -> list:
    return sorted(rows, key=score, reverse=True)


def split_areas(clusters: list, n_main: int, n_more: int,
                skip_threaded: bool = False) -> list:
    """Per area: (area, lead, main, secondary, hidden) with a display cap.

    The cap keeps every section the same visual size even when one area has
    twice the coverage of another; the badge still reports the true total.
    With skip_threaded, stories already shown inside an evolving-story block
    are left out of the lists, so nothing appears twice (unless that would
    leave the area empty).
    """
    out = []
    for a in AREAS:
        rows = by_rank([c for c in clusters if c["area"] == a["key"]])
        if skip_threaded:
            rows = [c for c in rows if c.get("thread") is None] or rows
        if not rows:
            continue
        # unconfirmed stories (a penalty set by the caller) leave the main cards but
        # stay visible: first among the secondary rows, never hidden behind the cap
        good = [c for c in rows if not c.get("penalty")]
        weak = [c for c in rows if c.get("penalty")]
        rows = good[:n_main] + weak + good[n_main:]
        out.append((a, rows[0], rows[:n_main], rows[n_main:n_main + n_more],
                    max(0, len(rows) - n_main - n_more)))
    return out


# ------------------------------------------------------------ clustering ----
GENERIC_TITLE = set("""exclusive breaking update updates opinion analysis explainer
watch live video videos photos podcast newsletter review reviews sponsored
recap highlights fullscreen""".split())


def entity_terms(items: list) -> set:
    """Tokens that behave like proper nouns in this corpus.

    Two ways to qualify:

    * an unambiguous brand shape anywhere in the headline - ALLCAPS like HBM,
      or PascalCase like DeepSeek, which cannot be an ordinary word whatever
      its position, so it counts even at position 0;
    * a plain capitalised word inside a headline, never seen in lowercase in
      one. That covers Nvidia, Huawei, Anthropic.

    Tokens written in lowercase inside a headline are excluded outright, which
    is the line that keeps 'faces' and 'lawsuit' - ordinary words that happen
    to be rare in a 146-title corpus - from merging two unrelated lawsuits.
    """
    strong, mild, soft = set(), set(), set()
    for it in items:
        for i, w in enumerate(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.\\-]*", it["title"])):
            core = w.strip(".,'’-").lower()
            if len(core) <= 3 or core in GENERIC_TITLE:
                continue
            if w.isupper() or any(c.isupper() for c in w[1:]):
                strong.add(core)                 # HBM, DeepSeek, McKinsey
            elif w[0].isupper():
                if i > 0:
                    mild.add(core)                # Nvidia, Huawei
            else:
                soft.add(core)                    # written lowercase in a title
    return (strong | mild) - soft


def cluster(items: list, min_sim: float) -> list:
    """Greedy clustering: one card per real-world event.

    Two rules, and the second one is what makes the difference:

    * plain token containment, for the same story told twice;
    * shared *hard* anchors - terms so rare in this corpus that they name an
      entity rather than a topic. "DeepSeek" and "Huawei" appear in a handful
      of titles each, so two headlines carrying both are the same event even
      when almost no other word matches. A topic word like "lawsuit" appears
      in too many titles to count, so it never merges anything on its own.

    Every candidate is compared against each member individually rather than
    against the union of a cluster's tokens: union comparison snowballs, and
    a cluster that started broad ends up absorbing unrelated stories.
    """
    df: dict = {}
    for it in items:
        for t in tokens(it["title"]):
            df[t] = df.get(t, 0) + 1
    hard_thr = max(2, int(0.03 * len(items)))
    idf = {t: math.log(len(items) / n) for t, n in df.items()}
    ents = entity_terms(items)

    def prep(it):
        tk = tokens(it["title"])
        return tk, {t for t in tk if df.get(t, 0) <= hard_thr and t in ents}

    clusters = []
    for it in items:
        tk, anchor = prep(it)
        it["_tk"], it["_anchor"] = tk, anchor
        best, best_score = None, 0.0
        for c in clusters:
            for m in c["members"]:
                s = affinity(tk, m["_tk"], anchor, m["_anchor"], idf)
                if s > best_score:
                    best, best_score = c, s
        if best is not None and best_score >= min_sim:
            best["members"].append(it)
            # keep the most informative title/snippet, but the freshest date
            if len(it["title"]) > len(best["title"]) * 1.15:
                best["title"] = it["title"]
            if len(it["snippet"]) > len(best["snippet"]):
                best["snippet"] = it["snippet"]
            if hours(it["date"]) < hours(best["date"]):
                best["date"] = it["date"]
        else:
            clusters.append({"title": it["title"], "link": it["link"],
                             "snippet": it["snippet"], "date": it["date"],
                             "tokens": set(tk), "anchor": set(anchor),
                             "members": [it]})
    # the greedy pass cannot rejoin two clusters that became similar only after
    # later members arrived; one sweep over cluster pairs does (best member pair)
    i = 0 if MERGE_SWEEP else len(clusters)
    while i < len(clusters):
        j = i + 1
        while j < len(clusters):
            best = max((affinity(x["_tk"], y["_tk"], x["_anchor"], y["_anchor"], idf)
                        for x in clusters[i]["members"] for y in clusters[j]["members"]),
                       default=0.0)
            if best >= min_sim:
                clusters[i]["members"] += clusters[j].pop("members")
                clusters[i]["snippet"] = max(clusters[i]["snippet"], clusters[j]["snippet"], key=len)
                if hours(clusters[j]["date"]) < hours(clusters[i]["date"]):
                    clusters[i]["date"] = clusters[j]["date"]
                del clusters[j]
                j = i + 1                      # members changed: compare again
            else:
                j += 1
        i += 1
    for c in clusters:
        finalize(c)
    clusters.sort(key=lambda c: hours(c["date"]))
    return clusters


def finalize(c: dict) -> None:
    """Derived fields of a cluster; re-run after members are added to it."""
    # The card speaks with the voice of the FIRST outlet to report the story: the
    # oldest member, whatever its political colour or its size. Headline, snippet,
    # link and outlet all come from that one article; the others follow, freshest
    # first, as "other sources". An unparsable date never counts as the oldest.
    def age(m):
        h = hours(m["date"])
        return (-1.0 if h >= 9999 else h, len(m["title"]))

    rep = max(c["members"], key=age)
    c["title"] = rep["title"]
    order = [rep] + sorted((m for m in c["members"] if m is not rep),
                           key=lambda m: hours(m["date"]))
    seen, sources = set(), []
    for m in order:
        key = (m["source"] or "").lower() or m["link"]
        if key in seen:
            continue
        seen.add(key)
        sources.append({"name": m["source"] or "fonte", "link": m["link"]})
    c["sources"] = sources
    if (rep.get("snippet") or "").strip():
        c["snippet"] = rep["snippet"]
    c["area"] = rep["area"]                      # the area its voice was classified into
    c["color"] = rep["color"]
    if rep.get("area_p"):
        c["area_p"] = rep["area_p"]
    c["folded"] = [m["title"] for m in c["members"][1:]]
    # free Google News thumbnail, kept as the fallback illustration
    c["thumb"] = next((m["imageUrl"] for m in c["members"]
                       if m.get("imageUrl")), "")


IT_STOP = set("""della dello delle degli dopo come anche sono nella nelle nello negli alla
alle allo agli dalla dalle questo questa quello quella ancora ultimo ultima nuova nuovo
contro sulla sulle dello cosa dove perche quando tutti tutte solo piu fra tra per con
senza sopra sotto""".split())


def stem(w: str) -> str:
    """Crude Italian/English stem: enough to match omofoba/omofobe/omofobi."""
    return re.sub(r"[aeio]+$", "", w) if len(w) > 4 else w


def find_threads(clusters: list, min_size: int = 3) -> list:
    """Groups of distinct events that are one evolving story.

    The same-event matcher keeps "an assault on Tuesday" and "an assault on
    Friday" apart, rightly: they are different events. A thread links such
    events when their headlines share at least three rare stems (say "bologna",
    "omofob", "aggression"). Rare means it appears in a handful of headlines, so
    a lone topic word never links anything; two stems were tried and chained
    unrelated stories together.

    Returns [{"members": [cluster, ...], "area": key, "kind": "storia"|"tema"}],
    newest member first;
    every member also gets c["thread"] = index.
    """
    ents = {stem(e) for e in entity_terms([{"title": c["title"]} for c in clusters])}
    stems = []
    for c in clusters:
        stems.append({stem(t) for t in tokens(c["title"]) if t not in IT_STOP})
    df: dict = {}
    for st in stems:
        for t in st:
            df[t] = df.get(t, 0) + 1
    cap = max(6, int(0.08 * len(clusters)))
    rare = [{t for t in st if 2 <= df[t] <= cap} for st in stems]

    parent = list(range(len(clusters)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            shared = rare[i] & rare[j]
            if len(shared) >= 3:
                parent[root(i)] = root(j)

    groups: dict = {}
    for i in range(len(clusters)):
        groups.setdefault(root(i), []).append(i)
    threads = []
    for idx in groups.values():
        if len(idx) < min_size:
            continue
        members = sorted((clusters[i] for i in idx), key=lambda c: hours(c["date"]))
        area = Counter(c["area"] for c in members).most_common(1)[0][0]
        # "storia" when most episodes share one place or name, "tema" when they
        # only share a subject across different places (three states, three
        # referendums on the same topic are a theme, not one story)
        seen = Counter(t for i in idx for t in rare[i] if t in ents)
        kind = "storia" if seen and max(seen.values()) >= 0.6 * len(idx) else "tema"
        threads.append({"members": members, "area": area, "kind": kind})
    threads.sort(key=lambda t: -len(t["members"]))
    for n, t in enumerate(threads):
        for c in t["members"]:
            c["thread"] = n
    return threads


THREAD_PROMPT = """\
Questi titoli di giornale raccontano la stessa vicenda in evoluzione.
Scrivi un titolo breve (max 9 parole) e una sintesi di 1-2 frasi (max 45 parole)
in italiano, solo con fatti presenti nei titoli, senza opinioni.
Rispondi ESCLUSIVAMENTE con JSON: {"titolo": "...", "sintesi": "..."}

Titoli (dal piu' recente):
%s"""


def label_threads(threads: list, model: str = None, cache_path: str = None) -> None:
    """Adds t["title"] / t["summary"] to every thread. Cached by member titles;
    on failure the thread keeps no label and is shown with a generic heading."""
    import hashlib
    import translate as TR
    model = model or TR.DEFAULT_MODEL
    cache_path = cache_path or os.path.join(HERE, "ai-news.thr.json")
    cache = TR.load_cache(cache_path)
    key = None
    for t in threads:
        if t.get("title"):
            continue                       # already labelled (find_threads_llm)
        titles = [show(c) for c in t["members"]]
        d = hashlib.sha1("\x00".join(sorted(titles)).encode()).hexdigest()[:16]
        row = cache.get(d)
        if not row:
            key = key or TR.load_key(model)
            for _ in range(3):
                try:
                    got = TR.extract_json(TR.call(
                        key, model, THREAD_PROMPT % "\n".join(f"- {x}" for x in titles)))
                except Exception:  # noqa: BLE001
                    continue
                if got.get("titolo") and got.get("sintesi"):
                    row = {"t": got["titolo"], "s": got["sintesi"]}
                    cache[d] = row
                    TR.save_cache(cache_path, cache)
                    break
        if row:
            t["title"], t["summary"] = row["t"], row["s"]


REL_DROP = 0.06          # below this probability a story is not about the digest's topic
TOPIC_AI = ("artificial intelligence: AI models, products and companies, chips and data centres, "
            "regulation, or the effects of AI on society and work")


def jev_classify(raw: list, prefix: str, quiet: bool = False) -> None:
    """Area of each story by meaning (Jev), with the probability of every area. The area
    assigned by the regexes stays as the fallback if Jev is not available."""
    try:
        import decide as D
        D.classify_areas(raw, AREAS, cache_path=f"{prefix}.area.json", min_p=0.34, quiet=quiet)
    except (SystemExit, Exception) as e:  # noqa: BLE001 - missing key or service down
        print(f"  aree con Jev non disponibili ({str(e)[:60]}): resto sulle regex")


DEEP_PER_AREA = 20       # stories per area that get the full rating; the page shows ~10


def deep_candidates(clusters: list) -> list:
    """The stories worth the full (expensive) Jev rating: the top DEEP_PER_AREA of each
    area by what is known before it, freshness, sources and snippet."""
    out = []
    for a in AREAS:
        rows = sorted((c for c in clusters if c["area"] == a["key"]), key=score, reverse=True)
        out += rows[:DEEP_PER_AREA]
    return out


def jev_judge(clusters: list, prefix: str, topic: str) -> list:
    """Same-event merging, editorial rating, piece type and relevance, all by Jev.
    Removes what is plainly not about the topic or not an article, and returns it."""
    METER.stage("fusioni")
    try:
        import decide as D
        st = D.merge_clusters(clusters, cache_path=f"{prefix}.pair.json", quiet=True)
        print(f"  fusioni con Jev: {st['absorbed']} schede unite "
              f"({st['pairs']} coppie, {st['auto']} sicure, {st['unsure']} incerte, {st['llm_yes']} confermate)")
    except (SystemExit, Exception) as e:  # noqa: BLE001
        print(f"  fusioni con Jev non disponibili ({str(e)[:60]}): resto sul confronto di parole")
    METER.stage("aree")          # after merging: one area question per event, not per headline
    jev_classify(clusters, prefix, quiet=True)
    METER.stage("valutazione")
    try:
        RATE.rate(clusters, cache_path=f"{prefix}.jev.json", topic=topic,
                  deep=deep_candidates, rel_min=REL_DROP)
    except (SystemExit, Exception) as e:  # noqa: BLE001
        print(f"  valutazione Jev non disponibile ({str(e)[:60]}): ordine senza valutazione di contenuto")
    out = []
    for c in clusters:
        rel, pt = c.get("relevance"), c.get("ptype")
        if rel is not None and rel < REL_DROP:
            out.append((c, f"non riguarda il tema ({rel:.0%})"))
        elif pt and pt["choice"] == "non_article" and pt["p"].get("non_article", 0) >= 0.7 \
                and len(c["sources"]) == 1:
            out.append((c, f"non e' un articolo ({pt['p']['non_article']:.0%})"))
    for c, why in out:
        print(f"  scartata ({why}): {c['title'][:70]}")
    gone = {id(c) for c, _ in out}
    clusters[:] = [c for c in clusters if id(c) not in gone]
    return [c for c, _ in out]


MIN_SHARED_IDF = 0.0   # set by callers that want it: see affinity()
MERGE_SWEEP = False    # same for the cluster re-merge sweep: see cluster()


THREADS_PROMPT = """\
Sono titoli di giornale della stessa settimana, numerati, con area e data.
Trova i FILI: gruppi di 3-8 titoli che raccontano la stessa vicenda che evolve,
oppure lo stesso tema specifico in luoghi o casi diversi (per esempio tre
referendum sugli atleti trans in tre Stati, o una serie di aggressioni nella
stessa citta'). I titoli di un filo possono stare in aree diverse.
Regole:
- al massimo 5 fili, i piu' solidi: meglio pochi fili precisi che molti generici;
- un filo ha almeno 3 titoli e un fatto comune preciso (una vicenda, un luogo, una
  causa, un voto); niente fili-contenitore ("Pride 2026", "matrimonio egualitario",
  "persone trans", "diritti LGBT");
- ogni titolo sta in un solo filo;
- "tipo": "storia" se riguardano lo stesso luogo o la stessa vicenda, "tema" se
  riguardano lo stesso argomento in luoghi o casi diversi;
- "titolo": massimo 9 parole, deve dire il fatto o il tema; mai un elenco di nomi
  separati da virgole o da due punti;
- "sintesi": 1-2 frasi (massimo 45 parole), solo fatti presenti nei titoli.
Rispondi ESCLUSIVAMENTE con JSON:
{"fili": [{"tipo": "storia", "titolo": "...", "sintesi": "...", "numeri": [1, 5, 8]}]}
({"fili": []} se non ce ne sono).

Titoli:
%s"""


def find_threads_llm(clusters: list, model: str = None, cache_path: str = None,
                     quiet: bool = False, max_threads: int = 5) -> list:
    """Threads chosen by a model, with their title and synopsis.

    The word-overlap version (find_threads) cannot see that an insurer's refusal,
    a federal report and a state fund are one fight over the same care, or that
    two polls are one poll with two questions. A model reading all headlines at
    once can; the guard rails are on the output: 3-8 titles per thread, each
    cluster in at most one, a title and synopsis present. The result is cached by
    the exact list of headlines. Returns [] when the model is unavailable, so
    the caller can fall back on find_threads."""
    import hashlib
    import translate as TR
    model = model or TR.DEFAULT_MODEL
    cache_path = cache_path or os.path.join(HERE, "ai-news.thr2.json")
    cache = TR.load_cache(cache_path)
    order = sorted(range(len(clusters)), key=lambda i: clusters[i]["title"])
    lines = [f"{n}. [{clusters[i]['area']}] {clusters[i]['title']} ({clusters[i]['date']})"
             for n, i in enumerate(order, 1)]
    d = hashlib.sha1("\n".join(clusters[i]["title"] for i in order).encode()).hexdigest()[:16]
    found = cache.get(d)
    if found is None:
        key = TR.load_key(model)
        for _ in range(3):
            try:
                got = TR.extract_json(TR.call(key, model, THREADS_PROMPT % "\n".join(lines),
                                              retries=1, timeout=400))   # ~2-3 minutes for 100 titles
            except Exception:  # noqa: BLE001
                continue
            if isinstance(got.get("fili"), list):
                found = got["fili"]
                cache[d] = found
                TR.save_cache(cache_path, cache)
                break
    threads, used = [], set()
    for f in found or []:
        idx = [order[n - 1] for n in dict.fromkeys(f.get("numeri", []))
               if isinstance(n, int) and 1 <= n <= len(order)]
        idx = [i for i in idx if i not in used]
        if not 3 <= len(idx) <= 8 or not f.get("titolo") or not f.get("sintesi"):
            continue
        used.update(idx)
        members = sorted((clusters[i] for i in idx), key=lambda c: hours(c["date"]))
        threads.append({"members": members,
                        "area": Counter(c["area"] for c in members).most_common(1)[0][0],
                        "kind": "storia" if f.get("tipo") == "storia" else "tema",
                        "title": str(f["titolo"]).strip(), "summary": str(f["sintesi"]).strip()})
    # stories before themes, then by size; the page shows a few, not every grouping
    threads.sort(key=lambda t: (t["kind"] != "storia", -len(t["members"])))
    threads = threads[:max_threads]
    for n, t in enumerate(threads):
        for c in t["members"]:
            c["thread"] = n
        if not quiet:
            print(f"  thread [{t['kind']}] {t['title']}: " +
                  " | ".join(c["title"][:34] for c in t["members"]))
    return threads


def affinity(a: set, b: set, a_anchor: set, b_anchor: set, idf: dict = None) -> float:
    """Same-event score for two headlines.

    Containment normally, but a headline reduced to one or two meaningful words
    ("AI and education do not mix" carries only 'education') would score 1.00
    against anything else mentioning education. Below the size floor, Jaccard
    is used instead, which cannot be gamed by a short title.
    """
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    if min(len(a), len(b)) >= 3 and inter >= 2:
        contain = inter / min(len(a), len(b))
    else:
        contain = inter / len(a | b)
    shared = a_anchor & b_anchor
    if len(shared) >= 2 and contain >= 0.3:
        return max(contain, 0.85)      # two rare entities is conclusive
    if idf and MIN_SHARED_IDF and sum(idf.get(t, 0.0) for t in a & b) < MIN_SHARED_IDF:
        # short headlines sharing only common words ("Boise Pride Parade" vs
        # "Annapolis Pride Parade ...") score high on containment: require the
        # shared words to carry real information (IDF mass), not just count
        return 0.0
    return contain


def hours(d: str) -> float:
    d = (d or "").strip()
    m = re.match(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", d, re.I)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()
        return n * {"minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720}[u]
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(d, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return (datetime.now() - dt).total_seconds() / 3600
        except ValueError:
            continue
    return 9999.0


def classify(title: str, snippet: str) -> tuple:
    blob = f"{title} {snippet}".lower()
    for a in AREAS:
        if a["key"] != "industry" and re.search(a["pat"], blob, re.I):
            return a["key"], a["color"]
    return "industry", AREAS[-1]["color"]


PAGE_JS = r'''(function () {
  var data = JSON.parse(document.getElementById('stories').textContent);
  var ui = JSON.parse(document.getElementById('whyui').textContent);
  var $ = function (i) { return document.getElementById(i); };
  var d = $('pop'), w = $('why');
  var calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function pct(v) { return Math.round(v * 100) + '%'; }

  // FLIP zoom: the window is laid out at its final place, then animated from (or back to)
  // the rectangle of the element that opened it
  function zoom(dlg, opening, done) {
    var origin = dlg._origin;
    if (calm || !origin || !dlg.animate) { if (done) done(); return; }
    var r = origin.getBoundingClientRect(), p = dlg.getBoundingClientRect();
    var dx = (r.left + r.width / 2) - (p.left + p.width / 2);
    var dy = (r.top + r.height / 2) - (p.top + p.height / 2);
    var sc = Math.max(0.12, Math.min(r.width / p.width, 0.7));
    var small = 'translate(' + dx + 'px,' + dy + 'px) scale(' + sc + ')';
    var frames = opening ? [{ transform: small, opacity: 0 }, { transform: 'none', opacity: 1 }]
                         : [{ transform: 'none', opacity: 1 }, { transform: small, opacity: 0 }];
    var a = dlg.animate(frames, { duration: opening ? 420 : 280, easing: 'cubic-bezier(.2,.8,.2,1)' });
    a.onfinish = function () { if (done) done(); };
  }
  function openDlg(dlg, origin) { dlg._origin = origin || null; dlg.showModal(); zoom(dlg, true); }
  function closeDlg(dlg) {
    if (!dlg.open) return;
    var shut = false;
    function fin() { if (!shut) { shut = true; if (dlg.open) dlg.close(); } }
    zoom(dlg, false, fin);
    setTimeout(fin, 450);            // never leave it open if the animation does not report back
  }

  function openSummary(sid, color, from) {
    var s = data[sid]; if (!s) return;
    d.style.setProperty('--c', color);
    $('pop-t').textContent = s.title;
    $('pop-m').textContent = s.meta;
    $('pop-b').textContent = s.text;
    $('pop-n').hidden = s.ok;
    $('pop-a').href = s.link;
    var box = $('pop-sl'); box.textContent = '';
    s.others.forEach(function (o) {
      var a = document.createElement('a'); a.href = o[1]; a.textContent = o[0];
      a.target = '_blank'; a.rel = 'noopener'; box.appendChild(a);
    });
    $('pop-s').hidden = !s.others.length;
    openDlg(d, from);
  }

  function section(title) { var s = el('div', 'why-sec'); s.appendChild(el('h4', null, title)); return s; }
  function openWhy(sid, color, from) {
    var s = data[sid]; if (!s || !s.why) return;
    var y = s.why, b = $('why-body'), sec;
    b.textContent = '';
    w.style.setProperty('--c', color);
    $('why-t').textContent = s.title;
    sec = section(ui.why_where); sec.appendChild(el('p', null, y.role)); b.appendChild(sec);
    if (y.area) {
      sec = section(ui.why_area);
      sec.appendChild(el('p', null, y.area.name + (y.area.p != null ? ' · ' + pct(y.area.p) : '')));
      if (y.area.alt.length)
        sec.appendChild(el('p', 'why-w', ui.why_alt + ': ' + y.area.alt.map(function (a) { return a[0] + ' ' + pct(a[1]); }).join(', ')));
      sec.appendChild(el('p', 'why-w', ui[y.area.by === 'jev' ? 'why_by_jev' : 'why_by_rx']));
      b.appendChild(sec);
    }
    if (y.type) { sec = section(ui.why_type); sec.appendChild(el('p', null, y.type.name + ' · ' + pct(y.type.p))); b.appendChild(sec); }
    if (y.dims.length) {
      sec = section(ui.why_jev);
      y.dims.forEach(function (r) {
        var row = el('div', 'why-row'), bar = el('span', 'why-bar' + (r.w < 0 ? ' neg' : '')), fill = el('i');
        row.appendChild(el('span', 'why-l', r.label + (r.w > 0 ? ' (' + Math.round(r.w * 100) + '%)' : '')));
        fill.style.width = pct(r.v); bar.appendChild(fill); row.appendChild(bar);
        row.appendChild(el('span', 'why-v', pct(r.v) + '  ' + (r.pts >= 0 ? '+' : '−') + Math.abs(r.pts) + ' ' + ui.why_pts));
        sec.appendChild(row); sec.appendChild(el('p', 'why-w', r.what));
      });
      b.appendChild(sec);
    }
    sec = section(ui.why_score);
    var p = y.parts;
    function line(label, text, cls) {
      var row = el('div', 'why-sum' + (cls ? ' ' + cls : '')); row.appendChild(el('span', null, label)); row.appendChild(el('b', null, text)); sec.appendChild(row);
    }
    line(ui.why_fresh, '+' + p.fresh); line(ui.why_sources, '+' + p.sources); line(ui.why_snippet, '+' + p.snippet);
    if (p.jev != null) line(ui.why_jevpts, (p.jev >= 0 ? '+' : '−') + Math.abs(p.jev));
    if (p.penalty) line(ui.why_penalty, '−' + p.penalty);
    line(ui.why_total, '★ ' + Math.round(p.total), 'tot');
    b.appendChild(sec);
    if (y.merged.length) {
      sec = section(ui.why_merged);
      y.merged.forEach(function (m) {
        sec.appendChild(el('p', 'why-w', '• ' + m[0] + ' — ' + m[1] + ' (' + pct(m[2]) + ', ' + ui[m[3] === 'jev' ? 'why_by_jev' : 'why_by_llm'] + ')'));
      });
      b.appendChild(sec);
    }
    if (y.rel != null) { sec = section(ui.why_rel); sec.appendChild(el('p', null, pct(y.rel))); b.appendChild(sec); }
    openDlg(w, from);
  }

  function flip(card, on) {
    card.classList.toggle('flipped', on);
    var front = card.querySelector('.front'), back = card.querySelector('.back');
    front.inert = on; back.inert = !on;      // the hidden face must not take focus
    front.setAttribute('aria-hidden', on ? 'true' : 'false');
    back.setAttribute('aria-hidden', on ? 'false' : 'true');
    var row = card.parentNode;                       // while a tile is open its neighbours keep
    row.classList.toggle('has-flip', !!row.querySelector('.card.flipped'));   // their own height
    var inner = card.querySelector('.card-inner');   // grow only if the summary needs room
    inner.style.minHeight = on ? Math.max(inner.offsetHeight, back.scrollHeight + 2) + 'px' : '';
    var next = card.querySelector(on ? '.back-x' : '.go');
    if (next) next.focus({ preventScroll: true });
  }

  function colorOf(node) {
    var holder = node.closest('.card, .brief') || node;
    return getComputedStyle(holder).getPropertyValue('--c');
  }
  document.addEventListener('click', function (e) {
    var why = e.target.closest('.why-btn[data-sid]');
    if (why) { e.preventDefault(); e.stopPropagation(); openWhy(why.dataset.sid, colorOf(why), why); return; }
    var card = e.target.closest('.card');
    if (card && e.target.closest('.back-x')) { flip(card, false); return; }
    if (card && e.target.closest('.front')) {
      // the whole tile opens the summary; the headline link keeps its href only
      // as a no-JS fallback. Links to other sources still navigate.
      var link = e.target.closest('a');
      if (!link || link.closest('h3')) {
        if (link) e.preventDefault();
        flip(card, true);
        return;
      }
    }
    var b = e.target.closest('.brief[data-sid]');
    if (b) {
      e.preventDefault();                      // brief rows are real links: no-JS fallback
      openSummary(b.dataset.sid, colorOf(b), b);
    }
    else if (e.target === d) closeDlg(d);      // click on a backdrop
    else if (e.target === w) closeDlg(w);
  });
  document.addEventListener('keydown', function (e) {
    var why = e.target.closest && e.target.closest('.why-btn.sm');
    if (why && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); why.click(); return; }
    if (e.key === 'Escape' && !d.open && !w.open)
      document.querySelectorAll('.card.flipped').forEach(function (c) { flip(c, false); });
  });
  $('pop-x').addEventListener('click', function () { closeDlg(d); });
  $('why-x').addEventListener('click', function () { closeDlg(w); });
  d.addEventListener('cancel', function (e) { e.preventDefault(); closeDlg(d); });   // Esc
  w.addEventListener('cancel', function (e) { e.preventDefault(); closeDlg(w); });
})();'''


# ------------------------------------------------------------------ html ----
def render_html(clusters, meta, lang, intro, generated, n_main=4, n_more=6,
                hero=DEFAULT_HERO, images="all", threads=None) -> str:
    global CURRENT_LANG
    CURRENT_LANG = lang
    t = UI[lang]
    threads = threads or []
    esc = html.escape
    L = (lambda i: i) if lang == "it" else (lambda i: i)

    def name(a, i):
        return a[i][0]

    # legenda + statistiche
    counts = Counter(c["area"] for c in clusters)
    legend = "".join(
        f'<a class="chip" href="#area-{k}" style="--c:{a["color"]}">'
        f'{esc(name(a, lang))} <b>{counts.get(k, 0)}</b></a>'
        for k, a in ((a["key"], a) for a in AREAS)
    )
    stats = "".join(
        f'<div class="stat"><span class="sv">{v.format(**meta)}</span>'
        f'<span class="sl">{esc(lbl)}</span></div>'
        for lbl, v in t["stats"]
    )

    sections, spotlight = [], []
    for a, lead, main_rows, brief_rows, hidden in split_areas(
            clusters, n_main, n_more, skip_threaded=bool(threads)):
        spotlight.append(spot_html(lead, a["color"], lang, t))
        cards = [card_html(r, lang, featured=(i == 0), t=t,
                           image=(images != "off"))
                 for i, r in enumerate(main_rows)]
        briefs = "".join(brief_html(r, lang, t=t, image=(images == "all"))
                         for r in brief_rows)
        more = (f'<p class="more-note">+ {hidden} {esc(t["more_note"])}</p>'
                if hidden else "")
        thread_html = "".join(thread_block(th, lang, t) for th in threads
                              if th["area"] == a["key"])
        sections.append(f"""
      <section id="area-{a['key']}" class="area" style="--c:{a['color']}">
        <header class="area-h">
          <h2>{esc(name(a, lang))}</h2>
          <span class="count">{counts[a['key']]}</span>
        </header>
        <p class="area-note">{esc(a[lang][1])}</p>
        {thread_html}
        <h3 class="sub-label">{esc(t['main_label'])}</h3>
        <div class="grid">{"".join(cards)}</div>
        {f'<h3 class="sub-label">{esc(t["sub_label"])}</h3><div class="briefer">{briefs}</div>' if brief_rows else ''}
        {more}
      </section>""")

    payload = {}
    shown_rows = [c for _, _, m, b, _ in split_areas(
        clusters, n_main, n_more, skip_threaded=bool(threads))
                  for c in m + b]
    shown_rows += [c for th in threads for c in th["members"]]
    roles = {}
    for a, _, m, b, _ in split_areas(clusters, n_main, n_more, skip_threaded=bool(threads)):
        roles.update({id(c): ("main", None) for c in m})
        roles.update({id(c): ("brief", None) for c in b})
    for th in threads:
        roles.update({id(c): ("thread", th) for c in th["members"]})
    for c in shown_rows:
        first = c["sources"][0]
        payload[c["sid"]] = dict(why=why_data(c, lang, roles.get(id(c), ("brief", None)), t),
            title=show(c),
            meta=f"{first['name']} · {fmt_date(c['date'], lang)}"
                 + (f" · {t['single']}" if len(c["sources"]) == 1
                    else f" · {len(c['sources'])} {t['sources']}")
                 + f" · \u2605 {round(score(c))}",
            text=c.get("summary") or show(c, "snippet"),
            ok=bool(c.get("summary")) or lang != "it", link=first["link"],
            others=[[x["name"], x["link"]] for x in c["sources"][1:6]])
    outlets = len({x["name"] for c in clusters for x in c["sources"]})
    method_html = "".join(f"<li>{esc(m.format(outlets=outlets))}</li>"
                          for m in t["method"])
    weights_html = weights_block(t, any(c.get("jev") for c in clusters), lang)
    edition_html = ""
    if METER.t0 is not None:                       # what this edition cost to build
        m = METER.summary()
        edition_html = "<br>" + esc(t["edition"].format(
            mins=max(1, round(m["seconds"] / 60)), jev=f"{m['jev']:.4f}", n=m["serper_credits"],
            serper=f"{m['serper_usd']:.3f}", total=f"{m['total']:.3f}"))
    if COST:                                       # estimated cost of every piece, summed
        edition_html += ("<br>" + esc(f"Costo stimato: {COST['pieces']} pezzi · Jev ${COST['jev']:.4f} · "
                         f"Serper {COST['serper_credits']:.0f} crediti (${COST['serper']:.3f}) · "
                         f"LLM $0 · totale ${COST['total']:.4f}"))
    stories_json = json.dumps(payload).replace("</", "<\\/")
    why_ui = json.dumps({k: t[k] for k in t if k.startswith("why_")}).replace("</", "<\\/")

    intro_html = (
        f'<p class="intro">{esc(intro)}</p>' if intro else ""
    )

    page = f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(t['title'])}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Inter:wght@400;500;600&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400&display=swap');
  *,*::before,*::after {{ box-sizing: border-box; }}
  :root {{ --bg:#08090d; --fg:#e6e8ef; --mute:#9aa1b2; --faint:#5b6377; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin:0; padding:0 0 90px; background:var(--bg); color:var(--fg);
    font-family: Inter, system-ui, sans-serif; -webkit-font-smoothing:antialiased;
  }}
  body::before {{
    content:""; position:fixed; inset:0; z-index:-1;
    background:
      radial-gradient(900px 520px at 10% -10%, rgba(122,162,247,.20), transparent 60%),
      radial-gradient(820px 500px at 90% -4%, rgba(244,162,97,.15), transparent 62%),
      radial-gradient(760px 620px at 50% 112%, rgba(158,206,106,.10), transparent 60%);
  }}
  .wrap {{ max-width:1120px; margin:0 auto; padding:0 28px; }}
  a {{ color:inherit; }}

  header.top {{ padding:0 0 30px; border-bottom:1px solid rgba(255,255,255,.08); }}
  header.top.no-hero {{ padding-top:78px; }}
  .hero {{
    position:relative; height:236px; margin:0 -28px; overflow:hidden;
    background:linear-gradient(170deg,#111726 0%,#0c0e15 55%,#08090d 100%);
  }}
  .hero img {{
    width:100%; height:100%; object-fit:cover; display:block; opacity:.6;
    -webkit-mask-image:linear-gradient(180deg,#000 42%,transparent 99%);
            mask-image:linear-gradient(180deg,#000 42%,transparent 99%);
  }}
  .hero::after {{
    content:""; position:absolute; inset:0; pointer-events:none;
    background:linear-gradient(180deg,rgba(8,9,13,.46) 0%,rgba(8,9,13,.10) 38%,rgba(8,9,13,.88) 88%);
  }}
  .hero .credit {{
    position:absolute; right:28px; bottom:13px; z-index:2; margin:0;
    font:500 10px/1 Inter; letter-spacing:.16em; text-transform:uppercase;
    color:rgba(255,255,255,.42);
  }}
  .hero .credit a {{ color:inherit; text-decoration:none; border-bottom:1px solid rgba(255,255,255,.2); }}
  .hero .credit a:hover {{ color:#fff; }}
  .kicker {{ margin-top:0; }}
  .kicker.lead {{ margin-top:38px; }}
  .kicker {{ font:600 11px/1 Inter; letter-spacing:.28em; text-transform:uppercase; color:#7aa2f7; }}
  h1 {{
    font:700 clamp(40px,7.5vw,78px)/1.02 "Space Grotesk"; margin:16px 0 0;
    letter-spacing:-.035em;
    background:linear-gradient(100deg,#fff 8%,#7aa2f7 46%,#f4a261 92%);
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  .sub {{ color:var(--mute); margin:20px 0 0; max-width:66ch; font-size:16px; line-height:1.65; }}
  .intro {{
    margin:26px 0 0; max-width:66ch; font:400 19px/1.68 "Newsreader", Georgia, serif;
    color:#cdd2de; border-left:2px solid rgba(122,162,247,.5); padding-left:18px;
  }}
  .nav {{ display:flex; flex-wrap:wrap; gap:9px; margin-top:30px; }}
  .chip {{
    display:inline-flex; align-items:center; gap:8px; padding:6px 13px; border-radius:999px;
    font:500 12px/1 Inter; text-decoration:none; color:var(--c);
    border:1px solid color-mix(in srgb, var(--c) 34%, transparent);
    background:color-mix(in srgb, var(--c) 10%, transparent);
    transition:background .2s ease, transform .2s ease;
  }}
  .chip:hover {{ background:color-mix(in srgb, var(--c) 20%, transparent); transform:translateY(-1px); }}
  .chip b {{ color:#fff; font-weight:600; }}

  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1px;
            margin:44px 0 6px; background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.07);
            border-radius:14px; overflow:hidden; }}
  .stat {{ background:#0b0d13; padding:20px 22px; display:flex; flex-direction:column; gap:6px; }}
  .sv {{ font:700 27px/1 "Space Grotesk"; color:#fff; }}
  .sl {{ font:500 10.5px/1 Inter; letter-spacing:.16em; text-transform:uppercase; color:var(--faint); }}

  h2.sec {{
    font:600 12px/1 Inter; letter-spacing:.24em; text-transform:uppercase; color:var(--faint);
    margin:62px 0 16px; display:flex; align-items:center; gap:14px;
  }}
  h2.sec::after {{ content:""; flex:1; height:1px;
    background:linear-gradient(90deg, rgba(255,255,255,.14), transparent); }}
  .spot-grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); }}
  .card.spotcard .face {{ padding:18px 18px 14px; border-radius:14px; }}
  .card.spotcard .face.front {{ display:flex; flex-direction:column; }}
  .card.spotcard h4 {{ font:600 15px/1.32 "Space Grotesk"; margin:0 0 8px; }}
  .card.spotcard .spot-s {{ font:500 11.5px/1 Inter; color:var(--faint); margin-bottom:14px; }}
  .card.spotcard footer {{ margin-top:auto; }}
  .card.spotcard .back-t {{ font-size:15px; }}
  .card.spotcard .back-b {{ font-size:14px; line-height:1.55; }}

  section.area {{ margin:64px 0 0; scroll-margin-top:24px; }}
  .area-h {{ display:flex; align-items:baseline; gap:14px; }}
  .area-h h2 {{
    font:600 13px/1 Inter; letter-spacing:.22em; text-transform:uppercase; color:var(--c); margin:0;
  }}
  .area-h .count {{
    font:700 13px/1 "Space Grotesk"; color:var(--faint);
    padding:3px 9px; border-radius:999px; border:1px solid rgba(255,255,255,.1);
  }}
  .area-note {{
    font:400 16px/1.6 "Newsreader", Georgia, serif; color:#a6adbe; margin:10px 0 20px;
    max-width:62ch; font-style:italic;
  }}
  .grid {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }}
  /* The tile is a two-faced card: the news on the front, the summary on the back.
     The back sits on top of the front, so a closed tile keeps the front's height;
     when flipped, the script grows it only if the summary needs more room. */
  .card {{ position:relative; perspective:1500px; transition:transform .22s ease; }}
  .card:hover {{ transform:translateY(-4px); }}
  .card-inner {{
    position:relative; height:100%; transform-style:preserve-3d;
    transition:transform .8s cubic-bezier(.2,.75,.2,1), min-height .6s ease;
  }}
  .card.flipped .card-inner {{ transform:rotateY(180deg); }}
  .face {{
    position:relative; overflow:hidden; padding:22px 22px 17px; border-radius:15px;
    border:1px solid rgba(255,255,255,.07);
    background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.014));
    -webkit-backface-visibility:hidden; backface-visibility:hidden;
    transition:border-color .22s ease, box-shadow .22s ease;
  }}
  .face::before {{ content:""; position:absolute; top:0; bottom:0; left:0; width:3px; background:var(--c); }}
  .card:hover .face {{
    border-color:color-mix(in srgb,var(--c) 45%, transparent);
    box-shadow:0 20px 42px -24px color-mix(in srgb,var(--c) 65%, transparent);
  }}
  .has-flip {{ align-items:start; }}
  .card.featured {{ grid-column:span 2; }}
  .card.featured .face {{
    padding:28px;
    background:linear-gradient(135deg, color-mix(in srgb,var(--c) 12%, transparent), rgba(255,255,255,.014) 62%);
  }}
  .face.front {{ height:100%; box-sizing:border-box; cursor:pointer; }}
  .face.front .more-srcs a {{ cursor:alias; }}
  .face.back, .card.featured .face.back {{
    position:absolute; inset:0; overflow:auto; box-sizing:border-box;
    transform:rotateY(180deg); display:flex; flex-direction:column;
    background:linear-gradient(160deg, color-mix(in srgb,var(--c) 18%, #0b0d13), #0b0d13 72%);
  }}
  .card .back-k {{ font:600 10.5px/1 Inter; letter-spacing:.22em; text-transform:uppercase; color:var(--c); }}
  .card .back-t {{ font:600 17px/1.3 "Space Grotesk"; margin:10px 0 4px; letter-spacing:-.01em; }}
  .card.featured .back-t {{ font-size:21px; }}
  .card .back-m {{ font:500 11.5px/1 Inter; color:var(--faint); margin:0 0 14px; }}
  .card .back-b {{ font:400 15px/1.62 "Newsreader", Georgia, serif; color:#d3d8e4; margin:0;
                   flex:none; /* never shrink: the tile grows to fit the summary instead */ }}
  .card .back-n {{ font:italic 400 12.5px/1.5 Inter; color:var(--faint); margin:10px 0 0; }}
  .card .back-srcs {{ margin:12px 0 0; font:500 11.5px/1.8 Inter; color:var(--faint); }}
  .card .back-srcs a {{ color:var(--mute); margin-right:10px; }}
  .card .back-act {{ display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:auto; padding-top:16px; }}
  .card .back-x {{ margin-left:auto; display:inline-flex; align-items:center; gap:6px; color:#e6e8ef;
                   font:600 12.5px/1 Inter; cursor:pointer; padding:8px 12px; border-radius:999px;
                   border:1px solid rgba(255,255,255,.2); background:rgba(255,255,255,.05); }}
  .card .back-x:hover {{ background:rgba(255,255,255,.12); }}
  .rv {{ opacity:0; transform:translateY(8px); transition:opacity .45s ease, transform .45s ease; }}
  .card.flipped .rv {{ opacity:1; transform:none; }}
  .card.flipped .rv.d1 {{ transition-delay:.35s; }}
  .card.flipped .rv.d2 {{ transition-delay:.47s; }}
  .card.flipped .rv.d3 {{ transition-delay:.59s; }}
  @media (prefers-reduced-motion:reduce) {{
    .card, .card-inner, .face, .rv, .cyc {{ transition:none !important; }}
    dialog.pop[open]::backdrop {{ animation:none; }}
  }}
  .card h3 {{ font:600 18px/1.3 "Space Grotesk"; margin:0 42px 10px 0; letter-spacing:-.01em; }}
  .card.featured h3 {{ font-size:23px; }}
  .card h3 a {{ color:#fff; text-decoration:none; }}
  .card h3 a:hover {{ color:var(--c); }}
  .card p {{ margin:0 0 15px; color:var(--mute); font-size:14px; line-height:1.58; }}
  .card footer {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; font-size:12px; color:var(--faint); }}
  .src {{ color:#cfd4e0; font-weight:500; }}
  /* the flip control: a pill with a cycle sign, so it reads as "turns the card" */
  .go {{ margin-left:auto; display:inline-flex; align-items:center; gap:6px; color:#fff;
         font:600 12px/1 Inter; padding:6px 11px; border-radius:999px; cursor:pointer;
         border:1px solid color-mix(in srgb,var(--c) 55%, transparent);
         background:color-mix(in srgb,var(--c) 16%, transparent); transition:background .2s ease; }}
  .go:hover {{ background:color-mix(in srgb,var(--c) 32%, transparent); }}
  .cyc {{ display:inline-block; font-size:15px; line-height:1; transition:transform .5s ease; }}
  .go:hover .cyc, .back-x:hover .cyc {{ transform:rotate(180deg); }}
  .more-srcs {{
    display:inline-flex; gap:6px; margin:0 0 13px; padding:0; list-style:none; flex-wrap:wrap;
  }}
  .more-srcs a {{
    font:500 10.5px/1 Inter; letter-spacing:.1em; text-transform:uppercase; color:var(--c);
    text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--c) 40%, transparent);
    padding-bottom:2px;
  }}
  .more-srcs a:hover {{ filter:brightness(1.3); }}
  .badge {{ font:600 10.5px/1 Inter; letter-spacing:.06em; padding:4px 8px; border-radius:999px;
            color:#e6e8ef; border:1px solid color-mix(in srgb,var(--c) 45%, transparent);
            background:color-mix(in srgb,var(--c) 14%, transparent); white-space:nowrap; }}
  .badge b {{ color:#fff; font-weight:700; }}
  .badge.cost {{ color:#7fd6a4; border-color:rgba(127,214,164,.3); background:transparent; cursor:help; }}
  .badge.score {{ color:var(--mute); border-color:rgba(255,255,255,.14); background:transparent; cursor:help; }}
  .thumb {{
    position:relative; margin:-22px -22px 16px; aspect-ratio:16/9; overflow:hidden;
    background:#0b0d13; border-bottom:1px solid rgba(255,255,255,.07);
  }}
  .thumb.big {{ margin:-28px -28px 20px; aspect-ratio:21/9; }}
  .thumb img {{ width:100%; height:100%; object-fit:cover; display:block; opacity:.92; }}
  .thumb::after {{
    content:""; position:absolute; inset:0; pointer-events:none;
    background:linear-gradient(180deg,rgba(8,9,13,.16) 0%,transparent 42%,rgba(8,9,13,.5) 100%);
  }}
  .thumb .photo-credit {{
    position:absolute; right:9px; bottom:7px; z-index:2; max-width:62%;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
    font:500 9.5px/1 Inter; letter-spacing:.1em; text-transform:uppercase;
    color:rgba(255,255,255,.62); background:rgba(8,9,13,.62);
    padding:4px 7px; border-radius:5px;
  }}
  .mini {{ float:right; width:72px; height:72px; object-fit:cover; border-radius:10px; margin:0 0 8px 12px;
           background:#0b0d13; border:1px solid rgba(255,255,255,.08); }}
  .b-thumb {{
    flex:none; width:44px; height:32px; border-radius:5px; object-fit:cover;
    background:#0b0d13; align-self:center;
  }}

  h3.sub-label {{
    font:600 10.5px/1 Inter; letter-spacing:.22em; text-transform:uppercase;
    color:var(--faint); margin:30px 0 14px; display:flex; align-items:center; gap:12px;
  }}
  h3.sub-label::after {{ content:""; flex:1; height:1px; background:linear-gradient(90deg, rgba(255,255,255,.1), transparent); }}
  .briefer {{ display:grid; gap:0 26px; grid-template-columns:repeat(auto-fill,minmax(min(430px,100%),1fr)); }}
  .brief {{
    display:flex; align-items:center; gap:11px; padding:11px 4px; text-decoration:none;
    border-bottom:1px solid rgba(255,255,255,.055);
  }}
  .briefer .brief:hover {{ background:color-mix(in srgb,var(--c) 7%, transparent); }}
  .b-dot {{ flex:none; width:6px; height:6px; border-radius:50%; background:var(--c); opacity:.75; }}
  .brief:hover .b-dot {{ opacity:1; box-shadow:0 0 0 4px color-mix(in srgb,var(--c) 22%, transparent); }}
  .b-main {{ flex:1; min-width:0; display:flex; flex-direction:column; gap:7px; }}
  .b-txt {{ font:500 14.5px/1.45 Inter; color:#c9cfdd; }}
  .brief:hover .b-txt {{ color:#fff; }}
  .b-sub {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; font:500 11.5px/1.4 Inter;
            color:var(--faint); overflow-wrap:anywhere; }}
  .go.sm {{ flex:none; margin-left:0; padding:5px 10px; font-size:11px; }}
  .go.sm .cyc {{ font-size:13px; }}
  .brief:hover .go {{ background:color-mix(in srgb,var(--c) 32%, transparent); }}
  .brief:hover .cyc {{ transform:rotate(180deg); }}
  .more-note {{ font:400 13px/1 Inter; color:var(--faint); margin:20px 0 0; font-style:italic; }}
  @media (max-width:760px) {{ .card.featured {{ grid-column:span 1; }} .card.featured .face {{ padding:22px; }} }}
  @media (max-width:560px) {{
    /* phones: the control drops under the headline instead of squeezing it */
    .brief {{ flex-wrap:wrap; }}
    .b-main {{ flex-basis:calc(100% - 20px); }}
    .go.sm {{ margin-left:17px; }}
    .wrap {{ padding:0 18px; }}
    .hero {{ margin:0 -18px; }}
  }}
  dialog.pop {{
    width:min(620px,calc(100vw - 32px)); max-height:calc(100vh - 48px); padding:0; border-radius:16px;
    color:var(--fg); background:#0e1018; border:1px solid color-mix(in srgb,var(--c,#7aa2f7) 40%, transparent);
    box-shadow:0 30px 80px -20px rgba(0,0,0,.8);
  }}
  dialog.pop::backdrop {{ background:rgba(4,5,9,.72); backdrop-filter:blur(3px); }}
  dialog.pop[open]::backdrop {{ animation:fade .35s ease; }}
  @keyframes fade {{ from {{ opacity:0; }} }}
  .pop-in {{ padding:28px 28px 24px; border-top:3px solid var(--c,#7aa2f7); }}
  .pop-k {{ font:600 10.5px/1 Inter; letter-spacing:.22em; text-transform:uppercase; color:var(--c,#7aa2f7); }}
  .pop h3 {{ font:600 21px/1.3 "Space Grotesk"; margin:12px 0 4px; }}
  .pop-meta {{ font:500 12px/1 Inter; color:var(--faint); margin:0 0 18px; }}
  .pop-body {{ font:400 17px/1.7 "Newsreader", Georgia, serif; color:#d3d8e4; margin:0; }}
  .pop-note {{ font:italic 400 13px/1.5 Inter; color:var(--faint); margin:14px 0 0; }}
  .pop-act {{ display:flex; flex-wrap:wrap; align-items:center; gap:14px; margin-top:24px; }}
  .pop-link {{
    padding:10px 16px; border-radius:10px; text-decoration:none; font:600 13px/1 Inter;
    color:#08090d; background:var(--c,#7aa2f7);
  }}
  .pop-link:hover {{ filter:brightness(1.12); }}
  .pop-close {{ margin-left:auto; background:none; border:0; color:var(--mute); font:500 13px/1 Inter; cursor:pointer; }}
  .pop-close:hover {{ color:#fff; }}
  .pop-srcs {{ margin:18px 0 0; font:500 12px/1.9 Inter; color:var(--faint); }}
  .pop-srcs a {{ color:var(--mute); margin-right:12px; }}
  .thread {{
    margin:6px 0 30px; padding:22px 24px 10px; border-radius:15px;
    border:1px solid color-mix(in srgb,var(--c) 32%, transparent);
    background:linear-gradient(135deg, color-mix(in srgb,var(--c) 11%, transparent), rgba(255,255,255,.012) 70%);
  }}
  .thread-k {{ font:600 10.5px/1 Inter; letter-spacing:.22em; text-transform:uppercase; color:var(--c); }}
  .thread-t {{ font:600 21px/1.3 "Space Grotesk"; margin:12px 0 6px; }}
  .thread-s {{ font:400 16px/1.6 "Newsreader", Georgia, serif; color:#c3c9d8; margin:0 0 14px; max-width:70ch; }}
  .thread .briefer {{ grid-template-columns:1fr; }}
  .solo {{ font:600 9.5px/1 Inter; letter-spacing:.14em; text-transform:uppercase; color:var(--faint);
           border:1px solid rgba(255,255,255,.14); border-radius:999px; padding:3px 8px; }}
  .more-n {{ font:600 10.5px/1 Inter; color:var(--faint); align-self:center; }}
  details.weights {{ margin-top:18px; color:var(--mute); font-size:13px; line-height:1.65; max-width:80ch; }}
  details.weights summary {{ cursor:pointer; font-weight:600; color:#cfd4e0; }}
  details.weights ul {{ margin:8px 0 0; padding-left:18px; }}
  details.weights b {{ color:var(--c,#7aa2f7); margin-right:6px; }}
  section.method {{ margin-top:70px; }}
  section.method ul {{ margin:0; padding:0 0 0 18px; color:var(--mute); font-size:13.5px; line-height:1.7; max-width:80ch; }}
  section.method li {{ margin-bottom:6px; }}
  .why-btn {{ display:inline-flex; align-items:center; gap:5px; font:600 11.5px/1 Inter; color:var(--mute);
              background:none; border:1px solid rgba(255,255,255,.16); border-radius:999px; padding:5px 10px;
              cursor:pointer; white-space:nowrap; }}
  .why-btn:hover {{ color:#fff; border-color:color-mix(in srgb,var(--c) 60%, transparent); }}
  .why-btn.sm {{ padding:2px 7px; font-size:13px; line-height:1.2; }}
  .ptype {{ font:600 9.5px/1 Inter; letter-spacing:.12em; text-transform:uppercase; color:var(--c);
            border:1px solid color-mix(in srgb,var(--c) 40%, transparent); border-radius:999px; padding:3px 8px; }}
  dialog.why {{ width:min(700px,calc(100vw - 32px)); }}
  .why-sec {{ margin:18px 0 0; }}
  .why-sec h4 {{ font:600 10.5px/1 Inter; letter-spacing:.2em; text-transform:uppercase; color:var(--c,#7aa2f7); margin:0 0 9px; }}
  .why-sec p {{ margin:0 0 5px; font:400 14.5px/1.5 Inter; color:#d3d8e4; }}
  .why-sec p.why-w {{ font-size:12.5px; color:var(--faint); }}
  .why-row {{ display:grid; grid-template-columns:170px 1fr 120px; gap:10px; align-items:center;
              font:500 12.5px/1.3 Inter; color:#cfd4e0; margin-top:9px; }}
  .why-bar {{ height:8px; border-radius:99px; background:rgba(255,255,255,.08); overflow:hidden; }}
  .why-bar i {{ display:block; height:100%; border-radius:99px; background:var(--c,#7aa2f7); }}
  .why-bar.neg i {{ background:#f7768e; }}
  .why-v {{ text-align:right; color:var(--mute); white-space:nowrap; }}
  .why-sum {{ display:flex; justify-content:space-between; font:500 13.5px/1.3 Inter; color:#cfd4e0;
              padding:6px 0; border-bottom:1px solid rgba(255,255,255,.06); }}
  .why-sum.tot {{ border:0; font-size:15px; color:#fff; }}
  @media (max-width:560px) {{ .why-row {{ grid-template-columns:1fr; gap:4px; }} .why-v {{ text-align:left; }} }}
  footer.page {{
    margin-top:78px; padding-top:26px; border-top:1px solid rgba(255,255,255,.08);
    color:var(--faint); font-size:12px; line-height:1.9; text-align:center;
  }}
  footer.page code {{ color:#7aa2f7; }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="top{'' if hero and hero.lower() != 'off' else ' no-hero'}">
      {hero_html(hero, lang)}
      <div class="kicker{' lead' if hero and hero.lower() != 'off' else ''}">{esc(t['kicker'])} {generated}</div>
      <h1>{esc(t['title'])}</h1>
      <p class="sub">{esc(t['sub'].format(n=meta['n']))}</p>
      {intro_html}
      <nav class="nav">{legend}</nav>
    </header>

    <div class="stats">{stats}</div>

    <h2 class="sec">{esc(t['focus'])}</h2>
    <div class="spot-grid">{"".join(spotlight)}</div>

    {"".join(sections)}

    <section class="method">
      <h2 class="sec">{esc(t['method_h'])}</h2>
      <ul>{method_html}</ul>
      {weights_html}
    </section>

    <footer class="page">
      {esc(t['generated'])} {generated} &middot; {esc(t['source'])} <code>serper.dev/news?tbs=qdr:w</code><br>
      {esc(t['footer_note'])}{edition_html}
    </footer>
  </div>
  <dialog class="pop" id="pop" aria-labelledby="pop-t">
    <div class="pop-in">
      <div class="pop-k">{esc(t['summary'])}</div>
      <h3 id="pop-t"></h3>
      <p class="pop-meta" id="pop-m"></p>
      <p class="pop-body" id="pop-b"></p>
      <p class="pop-note" id="pop-n" hidden>{esc(t['no_sum'])}</p>
      <p class="pop-srcs" id="pop-s" hidden>{esc(t['other_src'])}: <span id="pop-sl"></span></p>
      <div class="pop-act">
        <a class="pop-link" id="pop-a" target="_blank" rel="noopener">{esc(t['orig'])} &rarr;</a>
        <button type="button" class="pop-close" id="pop-x">{esc(t['close'])} (Esc)</button>
      </div>
    </div>
  </dialog>
  <dialog class="pop why" id="why" aria-labelledby="why-t">
    <div class="pop-in">
      <div class="pop-k">{esc(t['why_title'])}</div>
      <h3 id="why-t"></h3>
      <div id="why-body"></div>
      <div class="pop-act">
        <button type="button" class="pop-close" id="why-x">{esc(t['why_close'])}</button>
      </div>
    </div>
  </dialog>
  <script type="application/json" id="stories">{stories_json}</script>
  <script type="application/json" id="whyui">{why_ui}</script>
  <script>{PAGE_JS}</script>
</body>
</html>
"""
    # Some hosts normalise curly quotes to straight ones, which breaks attributes
    # and the JSON above; numeric entities cannot be rewritten.
    return page.encode("ascii", "xmlcharrefreplace").decode("ascii")


def thumb_html(c: dict, lang: str, big: bool = False) -> str:
    """Story illustration, or nothing. Self-hiding: publisher CDNs sometimes
    refuse a hotlink, and a card with a torn-off image looks worse than a card
    without one."""
    row = c.get("img") or {}
    if not row.get("url"):
        return ""
    esc = html.escape
    if row.get("lowres") or "gstatic.com" in row["url"]:
        # a 92x92 News thumbnail: shown at its own size, never stretched into a banner
        return f"""
        <img class="mini" src="{esc(row['url'])}" alt="" loading="lazy" decoding="async"
             referrerpolicy="no-referrer" onerror="this.remove()">"""
    return f"""
        <div class="thumb{' big' if big else ''}">
          <img src="{esc(row['url'])}" alt="{esc(c['title'])}" loading="lazy"
               decoding="async" referrerpolicy="no-referrer"
               onerror="this.parentNode.remove()">
          <span class="photo-credit">{esc(row.get('credit') or 'foto')}</span>
        </div>"""


def thread_block(th: dict, lang: str, t: dict) -> str:
    """An evolving story: label, one-line synopsis, then every episode as a row
    that opens the same summary popup as the cards."""
    esc = html.escape
    members = th["members"]
    color = next((a["color"] for a in AREAS if a["key"] == th["area"]), members[0]["color"])
    tema = th.get("kind") == "tema"
    kicker = t["thread_tema" if tema else "thread_k"]
    noun = t["thread_n_tema" if tema else "thread_n"]
    rows = "".join(brief_html(c, lang, t=t, image=False) for c in members)
    span = f"{esc(fmt_date(members[-1]['date'], lang))} → {esc(fmt_date(members[0]['date'], lang))}"
    return f"""
        <div class="thread" style="--c:{color}">
          <div class="thread-k">{esc(kicker)} · {len(members)} {esc(noun)} · {span}</div>
          <h3 class="thread-t">{esc(th.get('title') or t['thread_generic'])}</h3>
          {f'<p class="thread-s">{esc(th["summary"])}</p>' if th.get('summary') else ''}
          <div class="briefer">{rows}</div>
        </div>"""


def weights_block(t: dict, rated: bool, lang: str) -> str:
    """How the order is computed, published: freshness, sources, snippet and, when the
    stories were rated, each Jev question with its weight."""
    esc = html.escape
    head = f'<details class="weights"><summary>{esc(t["weights_h"])}</summary>'
    if not rated:
        return f'{head}<p>{esc(t["weights_none"])}</p></details>'
    k = 0 if lang == "it" else 1
    rows = [f'<li><b>{round(w * 100)}%</b> {esc(labels[k])}: {esc(what[k])}</li>'
            for w, labels, what in RATE.DIMENSIONS.values()]
    op = RATE.OPINION
    rows.append(f'<li><b>&minus;</b> {esc(op[k])}: {esc(op[2][k])} '
                f'({esc(t["weights_op"].format(n=RATE.OPINION_PENALTY))})</li>')
    return (f'{head}<p>{esc(t["weights_note"].format(max=RATE.MAX_POINTS))}</p>'
            f'<ul>{"".join(rows)}</ul></details>')


def back_face(c: dict, lang: str, t: dict) -> str:
    """The reverse of a card: the summary, other sources, the link to the original."""
    esc = html.escape
    src = c["sources"]
    first = src[0]
    body = c.get("summary") or show(c, "snippet")
    has_summary = bool(c.get("summary")) or lang != "it"
    others = "".join(
        f'<a href="{esc(x["link"])}" target="_blank" rel="noopener">{esc(x["name"])}</a>'
        for x in src[1:6])
    single = f" &middot; {esc(t['single'])}" if len(src) == 1 else ""
    note = "" if has_summary else f'<p class="back-n rv d1">{esc(t["no_sum"])}</p>'
    srcs = (f'<div class="back-srcs rv d2">{esc(t["other_src"])}: {others}</div>'
            if others else "")
    back = f"""<div class="face back" inert aria-hidden="true">
          <div class="back-k">{esc(t['summary'])}</div>
          <h3 class="back-t">{esc(show(c))}</h3>
          <div class="back-m">{esc(first['name'])} &middot; {esc(fmt_date(c['date'], lang))}{single}</div>
          <p class="back-b rv d1">{esc(body)}</p>
          {note}
          {srcs}
          <div class="back-act rv d3">
            <a class="pop-link" href="{esc(first['link'])}" target="_blank" rel="noopener">{esc(t['orig'])} &rarr;</a>
            <button type="button" class="back-x"><span class="cyc" aria-hidden="true">&#8635;</span> {esc(t['back_label'])}</button>
          </div>
        </div>"""
    return back


def why_data(c: dict, lang: str, role: tuple, t: dict) -> dict:
    """Everything the "Why?" window shows about one story, localised: where it sits, which
    area and why (probabilities), the type of piece, every Jev question with its weight and
    points, how the score adds up, and which reports were merged into it."""
    k = 0 if lang == "it" else 1
    names = {a["key"]: a[lang][0] for a in AREAS}
    ap = c.get("area_p") or {}
    area = {"name": names.get(c["area"], c["area"]),
            "p": round(ap[c["area"]], 2) if c["area"] in ap else None,
            "alt": [[names.get(key, key), round(p, 2)] for key, p in sorted(ap.items(), key=lambda x: -x[1])
                    if key != c["area"] and p >= 0.1][:2],
            "by": "jev" if ap else "rx"}
    kind, ref = role
    if kind == "thread":
        role_text = t["why_role_thread"].format(
            kind=t["why_kind_storia" if ref.get("kind") == "storia" else "why_kind_tema"],
            t=ref.get("title") or "")
    else:
        role_text = t["why_role_main" if kind == "main" else "why_role_brief"].format(a=area["name"])
    hrs = hours(c["date"])
    parts = {"fresh": round(max(0.0, 30 - hrs / 4), 1) if hrs < 9999 else 0.0,
             "sources": min(len(c["sources"]), 5) * 5,
             "snippet": round(min(len(c["snippet"]) / 45, 6), 1),
             "jev": None, "penalty": c.get("penalty", 0), "total": round(score(c), 1)}
    dims = []
    jev = c.get("jev")
    if jev:
        bd = RATE.breakdown(jev)
        for key, (w, labels, what) in RATE.DIMENSIONS.items():
            dims.append({"label": labels[k], "w": w, "v": round(jev[key], 2), "pts": round(bd[key], 1),
                         "what": what[k]})
        op = RATE.OPINION
        dims.append({"label": op[k], "w": -1, "v": round(jev["opinion"], 2), "pts": round(bd["opinion"], 1),
                     "what": op[2][k]})
        parts["jev"] = round(sum(bd.values()), 1)
    pt = c.get("ptype")
    return {"role": role_text, "area": area,
            "type": ({"name": RATE.PIECE_TYPES[pt["choice"]][k], "p": round(pt["p"][pt["choice"]], 2)}
                     if pt else None),
            "dims": dims, "parts": parts,
            "merged": [[m["title"], m["source"], m["p"], m["by"]] for m in c.get("merged", [])],
            "rel": round(c["relevance"], 2) if c.get("relevance") is not None else None}


def cost_chip(c: dict) -> str:
    """Estimated cost of the piece (Jev questions + its share of the Serper searches)."""
    if c.get("cost") is None:
        return ""
    return (f'<span class="badge cost" title="Costo stimato del pezzo: domande a Jev + quota delle ricerche Serper">'
            f'${c["cost"]:.4f}</span>')


def ptype_chip(c: dict) -> str:
    """A small label for anything that is not plain reporting (opinion, press release, ...)."""
    pt = c.get("ptype")
    if not pt or pt["choice"] == "report" or pt["p"].get(pt["choice"], 0) < 0.5:
        return ""
    return f'<span class="ptype">{html.escape(RATE.PIECE_TYPES[pt["choice"]][0 if CURRENT_LANG == "it" else 1])}</span>'


def why_button(c: dict, t: dict, small: bool = False) -> str:
    esc = html.escape
    if small:
        return (f'<span class="why-btn sm" role="button" tabindex="0" data-sid="{esc(c.get("sid", ""))}" '
                f'title="{esc(t["why_tip"])}" aria-label="{esc(t["why_btn"])}">&#9432;</span>')
    return (f'<button type="button" class="why-btn" data-sid="{esc(c.get("sid", ""))}" '
            f'title="{esc(t["why_tip"])}">&#9432; {esc(t["why_btn"])}</button>')


def spot_html(c: dict, color: str, lang: str, t: dict) -> str:
    """An "In evidenza" tile. Same two-faced card as the grid, same badges, same
    flip: clicking it shows the summary, it no longer jumps to the article."""
    esc = html.escape
    src = c["sources"]
    chip = (f'<span class="solo">{esc(t["unverified" if c.get("penalty") else "single"])}</span>'
            if len(src) == 1 else f'<span class="badge"><b>{len(src)}</b> {esc(t["sources"])}</span>')
    return f"""
        <article class="card spotcard" style="--c:{color}">
          <div class="card-inner">
            <div class="face front">
              <h4>{esc(show(c))}</h4>
              <div class="spot-s">{esc(src[0]['name'])} &middot; {esc(fmt_date(c['date'], lang))}</div>
              <footer>
                {chip}
                <span class="badge score" title="{esc(t['score_tip'])}">&#9733; {round(score(c))}</span>
                {cost_chip(c)}
                {ptype_chip(c)}
                {why_button(c, t)}
                <button type="button" class="go" data-sid="{esc(c.get('sid', ''))}" title="{esc(t['flip_tip'])}"><span class="cyc" aria-hidden="true">&#8635;</span> {esc(t['read'])}</button>
              </footer>
            </div>
            {back_face(c, lang, t)}
          </div>
        </article>"""


def card_html(c, lang, featured=False, t=None, image=True) -> str:
    esc = html.escape
    src = c["sources"]
    first = src[0]
    extra = ""
    if len(src) > 1:
        links = "".join(
            f'<li><a href="{esc(s["link"])}" target="_blank" rel="noopener">{esc(s["name"])}</a></li>'
            for s in src[1:6]
        )
        rest = len(src) - 6
        more = f'<li class="more-n">+{rest}</li>' if rest > 0 else ""
        extra = f'<ul class="more-srcs">{links}{more}</ul>'
    back = back_face(c, lang, t)
    return f"""
      <article class="card{' featured' if featured else ''}" style="--c:{c['color']}">
        <div class="card-inner">
        <div class="face front">
        {thumb_html(c, lang, big=featured) if image else ''}
        {extra}
        <h3><a href="{esc(first['link'])}" target="_blank" rel="noopener" title="{esc(c['title'])}">{esc(show(c))}</a></h3>
        <p>{esc(show(c, 'snippet'))}</p>
        <footer>
          <span class="src">{esc(first['name'])}</span>
          <span>&middot;</span><span>{esc(fmt_date(c['date'], lang))}</span>
          {f'<span class="solo">{esc(t["unverified" if c.get("penalty") else "single"])}</span>' if len(src) == 1 else ''}
          {f'<span class="badge"><b>{len(src)}</b> {esc(t["sources"])}</span>' if len(src) > 1 else ''}
          <span class="badge score" title="{esc(t['score_tip'])}">&#9733; {round(score(c))}</span>
          {cost_chip(c)}
          {ptype_chip(c)}
          {why_button(c, t)}
          <button type="button" class="go" data-sid="{esc(c.get('sid', ''))}" title="{esc(t['flip_tip'])}"><span class="cyc" aria-hidden="true">&#8635;</span> {esc(t['read'])}</button>
        </footer>
        </div>
        {back}
        </div>
      </article>"""


def brief_html(c, lang, t=None, image=True) -> str:
    """Compact row for secondary stories: headline, outlet and date, the same badges
    as the cards (sources, score) and a "Summary" control. Clicking opens the
    summary with a zoom that grows out of the row."""
    esc = html.escape
    src = c["sources"]
    first = src[0]
    chip = (f'<span class="solo">{esc(t["unverified" if c.get("penalty") else "single"])}</span>'
            if len(src) == 1 else f'<span class="badge"><b>{len(src)}</b> {esc(t["sources"])}</span>')
    row = c.get("img") or {}
    pic = (f'<img class="b-thumb" src="{esc(row["url"])}" alt="" loading="lazy"'
           f' decoding="async" referrerpolicy="no-referrer"'
           f' onerror="this.remove()">') if (image and row.get("url")) else ""
    return f"""
        <a class="brief" href="{esc(first['link'])}" target="_blank" rel="noopener" data-sid="{esc(c.get('sid', ''))}" title="{esc(t['flip_tip'])}">
          <span class="b-dot"></span>
          {pic}
          <span class="b-main">
            <span class="b-txt" title="{esc(c['title'])}">{esc(show(c))}</span>
            <span class="b-sub">
              <span class="b-src">{esc(first['name'])} &middot; {esc(fmt_date(c['date'], lang))}</span>
              {chip}
              <span class="badge score" title="{esc(t['score_tip'])}">&#9733; {round(score(c))}</span>
              {cost_chip(c)}
              {ptype_chip(c)}
              {why_button(c, t, small=True)}
            </span>
          </span>
          <span class="go sm"><span class="cyc" aria-hidden="true">&#8635;</span> {esc(t['read'])}</span>
        </a>"""


# ------------------------------------------------------------- markdown -----
def render_md(clusters, meta, lang, generated, intro, n_main=4, n_more=6) -> str:
    t = UI[lang]
    out = [f"# {t['title']} — {generated}", ""]
    if intro:
        out += [f"_{intro}_", ""]
    for a, lead, main_rows, brief_rows, hidden in split_areas(clusters, n_main, n_more):
        total = sum(1 for c in clusters if c["area"] == a["key"])
        out += [f"## {a[lang][0]} ({total})", "", f"_{a[lang][1]}_", "",
                f"### {t['main_label']}", ""]
        for c in main_rows:
            names = " · ".join(s["name"] for s in c["sources"])
            out += [f"- [**{show(c)}**]({c['sources'][0]['link']})  ",
                    f"  {names} · {fmt_date(c['date'], lang)}  ", f"  {show(c, 'snippet')}", ""]
        if brief_rows:
            out += [f"### {t['sub_label']}", ""]
            for c in brief_rows:
                first = c["sources"][0]
                out.append(f"- [{show(c)}]({first['link']}) — {first['name']} · {fmt_date(c['date'], lang)}")
            out.append("")
        if hidden:
            out += [f"_{'+' + str(hidden)} {t['more_note']}_", ""]
    return "\n".join(out)


# ------------------------------------------------------------- terminal -----
def render_term(clusters, lang, generated, intro, n_main=4, n_more=6) -> str:
    t = UI[lang]
    w = 76
    dim, bold, reset = "\033[38;5;243m", "\033[1m", "\033[0m"
    lines = [f"{dim}╭{'─' * w}╮{reset}",
             f"  \033[38;5;255m{bold}{t['title']}{reset}  {dim}{generated}{reset}",
             f"{dim}╰{'─' * w}╯{reset}"]
    if intro:
        cur = ""
        for word in intro.split():
            if len(cur) + len(word) + 1 <= w - 4:
                cur = f"{cur} {word}".strip()
            else:
                lines.append(f"  {dim}{cur}{reset}")
                cur = word
        if cur:
            lines.append(f"  {dim}{cur}{reset}")
        lines.append("")
    for a, lead, main_rows, brief_rows, hidden in split_areas(clusters, n_main, n_more):
        total = sum(1 for c in clusters if c["area"] == a["key"])
        ansi = lambda h: f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
        lines.append(f"{ansi(a['color'])}{bold}{a[lang][0].upper()}{reset} "
                     f"{dim}({total}){reset}")
        for c in main_rows:
            names = " · ".join(s["name"] for s in c["sources"][:3])
            lines.append(f"  {ansi(a['color'])}●{reset} \033[38;5;255m{bold}"
                         f"{show(c)[:w-4]}{reset}")
            lines.append(f"    {dim}{names} — {c['date']}{reset}")
        if brief_rows:
            lines.append(f"  {dim}{t['sub_label']}{reset}")
            for c in brief_rows:
                first = c["sources"][0]
                extra = f" +{len(c['sources']) - 1}" if len(c["sources"]) > 1 else ""
                lines.append(f"    {ansi(a['color'])}·{reset} {dim}"
                             f"{show(c)[:w - 26]}{reset}")
                lines.append(f"      {dim}{first['name']}{extra} — {c['date']}{reset}")
        if hidden:
            lines.append(f"  {dim}+{hidden} {t['more_note']}{reset}")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------- main -----
def main() -> None:
    global EMBED_IMAGES
    argv = sys.argv[1:]
    path, lang, intro = None, "it", None
    n_main, n_more = 4, 6
    min_sim, show_folded = 0.62, False
    do_translate, model, hero = False, "openrouter/free", DEFAULT_HERO
    images, summaries, do_jev = "all", False, True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--lang", "-l"):
            lang = argv[i + 1]; i += 2
        elif a == "--intro":
            intro = argv[i + 1]; i += 2
        elif a == "--main":
            n_main = int(argv[i + 1]); i += 2
        elif a == "--more":
            n_more = int(argv[i + 1]); i += 2
        elif a == "--sim":
            min_sim = float(argv[i + 1]); i += 2
        elif a == "--anchors":
            print("Error: --anchors is now fixed at 2; use --sim to tune")
            sys.exit(1)
        elif a == "--folded":
            show_folded = not show_folded; i += 1
        elif a in ("--translate", "--tr"):
            do_translate = not do_translate; i += 1
        elif a == "--model":
            model = argv[i + 1]; i += 2
        elif a == "--hero":
            hero = argv[i + 1]; i += 2
        elif a == "--summaries":
            summaries = not summaries; i += 1
        elif a == "--no-jev":
            do_jev = False; i += 1
        elif a == "--no-embed":
            EMBED_IMAGES = False; i += 1
        elif a == "--images":
            images = argv[i + 1]; i += 2
        elif a.startswith("--"):
            i += 2
        else:
            path = a; i += 1
    if lang not in UI:
        print(f"Error: --lang must be one of {sorted(UI)}")
        sys.exit(1)
    if n_main < 1 or n_more < 0:
        print("Error: --main must be >= 1 and --more >= 0")
        sys.exit(1)

    METER.start("ai", {"carico": 3, "aree": 25, "cluster": 1, "fusioni": 80, "valutazione": 20,
                       "immagini": 40, "traduzione": 30, "riassunti": 300, "pagina": 15})
    METER.stage("carico")
    path = path or os.path.join(HERE, "ai-news.json")
    raw = json.load(open(path))
    raw, dropped = drop_filler(raw)
    for why, title in dropped:
        print(f"  scartata ({why}): {title[:70]}")
    for it in raw:
        it["area"], it["color"] = classify(it["title"], it.get("snippet", ""))
    prefix = os.path.join(HERE, "ai-news")
    if do_jev:
        METER.stage("aree")
        raw.sort(key=lambda x: hours(x["date"]))

    METER.stage("cluster")
    clusters = cluster(raw, min_sim)

    if do_jev:
        jev_judge(clusters, prefix, TOPIC_AI)

    METER.stage("immagini")
    if images != "off":
        import images as IMG
        # Serper Images (1 credit each) only for the stories that get a card;
        # the rest reuse a cached result if there is one, else the News thumbnail
        featured = [c for _, _, rows, _, _ in split_areas(clusters, n_main, n_more)
                    for c in rows]
        print("  immagini per le storie in evidenza (Serper Images)...")
        cache, ist = IMG.fetch([c["title"] for c in featured],
                               lang=lang, quiet=(lang == "en"),
                               aliases={c["title"]: [m["title"] for m in c["members"]]
                                        for c in featured},
                               want={c["title"]: [s["link"] for s in c["sources"]]
                                     for c in featured})
        for c in clusters:
            row = IMG.lookup(cache, c["title"])
            if row and not IMG.same_publisher(row, [s["link"] for s in c["sources"]]):
                row = {}
            if not row and c.get("thumb"):
                row = {"url": c["thumb"], "credit": c["sources"][0]["name"]}
            if row:
                c["img"] = row
        n_og = IMG.upgrade(featured, cache)           # the article's own header photo
        print(f"  foto dall'articolo (og:image): {n_og}")
        if EMBED_IMAGES:
            others = [c for _, _, m, o, _ in split_areas(clusters, n_main, n_more)
                      for c in m + o if c not in featured]
            n_emb, n_bytes = IMG.embed(featured, others)
            print(f"  foto incorporate nell'HTML: {n_emb} ({n_bytes // 1024} KB)")
        print(f"  trovate {ist['found']}, dalla cache {ist['cached']}, "
              f"nessuna {ist['empty']}\n")

    METER.stage("traduzione")
    if do_translate:
        import translate as TR
        print("  traduzione in italiano (dopo il clustering, mai prima)...")
        cache, st = TR.translate(
            [{"title": c["title"], "snippet": c["snippet"]} for c in clusters],
            model=model, quiet=lang == "en")
        for c in clusters:
            row = TR.lookup(cache, c["title"], c["snippet"])
            if row:
                c["title_it"] = row["title"]
                c["snippet_it"] = row["snippet"]
        print(f"  tradotte {st['translated']}, dalla cache {st['cached']}, "
              f"fallite {st['failed']}\n")

    for n, c in enumerate(clusters):
        c["sid"] = f"s{n}"
    METER.stage("riassunti")
    if summaries and lang == "it":
        import summarize as SM
        print("  riassunti degli articoli (Jina + OpenRouter)...")
        shown = [c for _, _, rows, more, _ in split_areas(clusters, n_main, n_more)
                 for c in rows + more]
        cache, sst = SM.summarize(
            [{"title": c["title"], "links": [x["link"] for x in c["sources"]]}
             for c in shown], model=model, quiet=(lang == "en"))
        for c in shown:
            c["summary"] = SM.lookup(cache, c["title"])
        print(f"  nuovi {sst['done']}, dalla cache {sst['cached']}, "
              f"non disponibili {sst['empty']}\n")

    per_area = {a["key"]: sum(1 for c in clusters if c["area"] == a["key"])
                for a in AREAS}
    live = [v for v in per_area.values() if v]
    spread = f"{min(live)}–{max(live)}" if live else "—"
    dates = [c["date"] for c in clusters if hours(c["date"]) < 9999]
    window = (f"{fmt_date(max(dates, key=hours), lang)} → "
              f"{fmt_date(min(dates, key=hours), lang)}") if dates else "—"

    meta = dict(n=len(raw), c=len(clusters), a=len(live), g=spread, w=window)
    if lang == "it":
        now = datetime.now()
        generated = f"{now.day} {MESI_IT[now.month - 1]} {now.year}"
    else:
        generated = datetime.now().strftime("%d %B %Y")

    print(render_term(clusters, lang, generated, intro, n_main, n_more))
    folded = [c for c in clusters if len(c["sources"]) > 1]
    print(f"  {len(raw)} storie -> {len(clusters)} eventi unici "
          f"(unificati: {len(folded)})")
    print(f"  per area: {spread} | mostra {n_main} principali + {n_more} "
          f"secondarie per area\n")
    if show_folded:
        print("  fusioni:\n")
        for c in sorted(folded, key=lambda x: -len(x["sources"])):
            names = ", ".join(s["name"] for s in c["sources"])
            print(f"   * [{len(c['sources'])}] {c['title'][:82]}")
            print(f"       fonti: {names}")
            for f in c["folded"]:
                print(f"       piegato: {f[:80]}")
        print()

    suffix = "" if lang == "it" else f".{lang}"
    hp = os.path.join(HERE, f"ai-news{suffix}.html")
    mp = os.path.join(HERE, f"ai-news{suffix}.md")
    if hero.lower() != "off" and hero.lower() not in HEROES \
            and not hero.startswith(("http://", "https://")):
        print(f"Error: --hero must be one of {sorted(HEROES)}, 'off', or a URL")
        sys.exit(1)
    if images not in ("all", "main", "off"):
        print("Error: --images must be one of all, main, off")
        sys.exit(1)

    open(hp, "w").write(
        render_html(clusters, meta, lang, intro, generated, n_main, n_more,
                    hero, images))
    open(mp, "w").write(
        render_md(clusters, meta, lang, intro, generated, n_main, n_more))
    print(f"  scritto {hp}\n  scritto {mp}\n")
    METER.finish()


if __name__ == "__main__":
    main()

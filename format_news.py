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
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- aree ------
# L'ordine è editoriale: dall'area che pesa di più a quella di servizio.
# Ogni area ha nome bilingue, colore d'accento e una nota che spiega perché esiste.
AREAS = [
    dict(
        key="policy", color="#f4a261",
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
        sources="fonti", sources_one="fonte", read="leggi",
        window="Finestra", areas="aree", clusters="eventi unici",
        beat="area più coperta", generated="Generato il", source="fonte",
        more="e altre", footer_note="Chiavi mai incluse in questo file.",
        legend="la composizione della settimana", no_intro="",
        stats=(("storie", "{n}"), ("eventi unici", "{c}"), ("aree", "{a}"),
               ("area più coperta", "{b}"), ("finestra", "{w}")),
    ),
    "en": dict(
        kicker="Signal · week of", title="AI Briefing",
        sub="{n} stories pulled from Google News via Serper over the last 7 days. "
            "Grouped by area and deduplicated: when several outlets cover the same "
            "event it becomes one card with multiple sources.",
        focus="In focus", others="More stories", all="All stories",
        sources="sources", sources_one="source", read="read",
        window="Window", areas="areas", clusters="unique events",
        beat="most-covered beat", generated="Generated", source="source",
        more="more", footer_note="No key material in this file.",
        legend="how the week broke down", no_intro="",
        stats=(("stories", "{n}"), ("unique events", "{c}"), ("areas", "{a}"),
               ("most-covered beat", "{b}"), ("window", "{w}")),
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


# ------------------------------------------------------------ clustering ----
def cluster(items: list, min_sim: float) -> list:
    """Greedy containment clustering: one card per real-world event."""
    clusters = []
    for it in items:
        tk = tokens(it["title"])
        best, best_score = None, 0.0
        for c in clusters:
            inter = len(tk & c["tokens"])
            if not inter:
                continue
            score = inter / min(len(tk), len(c["tokens"]))
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score >= min_sim:
            best["members"].append(it)
            best["tokens"] |= tk
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
                             "tokens": set(tk), "members": [it]})
    for c in clusters:
        seen, sources = set(), []
        for m in sorted(c["members"], key=lambda m: hours(m["date"])):
            key = (m["source"] or "").lower() or m["link"]
            if key in seen:
                continue
            seen.add(key)
            sources.append({"name": m["source"] or "fonte", "link": m["link"]})
        c["sources"] = sources
        c["area"] = c["members"][0]["area"]
        c["color"] = c["members"][0]["color"]
    clusters.sort(key=lambda c: hours(c["date"]))
    return clusters


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


# ------------------------------------------------------------------ html ----
def render_html(clusters, meta, lang, intro, generated) -> str:
    t = UI[lang]
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
    for a in AREAS:
        rows = [c for c in clusters if c["area"] == a["key"]]
        if not rows:
            continue
        lead, rest = rows[0], rows[1:]
        spotlight.append(f"""
        <a class="spot" href="{esc(lead['link'])}" target="_blank" rel="noopener" style="--c:{a['color']}">
          <span class="spot-n">{counts[a['key']]:02d}</span>
          <h4>{esc(lead['title'])}</h4>
          <span class="spot-s">{esc(lead['sources'][0]['name'])} · {esc(lead['date'])}</span>
        </a>""")
        cards = [card_html(lead, lang, featured=True, t=t)]
        cards += [card_html(c, lang, t=t) for c in rest]
        sections.append(f"""
      <section id="area-{a['key']}" class="area" style="--c:{a['color']}">
        <header class="area-h">
          <h2>{esc(name(a, lang))}</h2>
          <span class="count">{counts[a['key']]}</span>
        </header>
        <p class="area-note">{esc(a[lang][1])}</p>
        <div class="grid">{"".join(cards)}</div>
      </section>""")

    intro_html = (
        f'<p class="intro">{esc(intro)}</p>' if intro else ""
    )

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
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

  header.top {{ padding:78px 0 30px; border-bottom:1px solid rgba(255,255,255,.08); }}
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
  .spot {{
    position:relative; text-decoration:none; padding:20px; border-radius:14px; overflow:hidden;
    border:1px solid rgba(255,255,255,.07);
    background:linear-gradient(150deg, color-mix(in srgb,var(--c) 13%, transparent), rgba(255,255,255,.012) 65%);
    transition:transform .2s ease, border-color .2s ease;
  }}
  .spot:hover {{ transform:translateY(-3px); border-color:color-mix(in srgb,var(--c) 50%, transparent); }}
  .spot-n {{ position:absolute; top:14px; right:18px; font:700 22px/1 "Space Grotesk"; color:rgba(255,255,255,.09); }}
  .spot h4 {{ font:600 15px/1.32 "Space Grotesk"; margin:0 30px 10px 0; }}
  .spot-s {{ font:500 11.5px/1 Inter; color:var(--faint); }}

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
  .card {{
    position:relative; overflow:hidden; padding:22px 22px 17px; border-radius:15px;
    border:1px solid rgba(255,255,255,.07);
    background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.014));
    transition:transform .22s ease, border-color .22s ease, box-shadow .22s ease;
  }}
  .card::before {{ content:""; position:absolute; top:0; bottom:0; left:0; width:3px; background:var(--c); }}
  .card:hover {{
    transform:translateY(-4px);
    border-color:color-mix(in srgb,var(--c) 45%, transparent);
    box-shadow:0 20px 42px -24px color-mix(in srgb,var(--c) 65%, transparent);
  }}
  .card.featured {{
    grid-column:span 2; padding:28px;
    background:linear-gradient(135deg, color-mix(in srgb,var(--c) 12%, transparent), rgba(255,255,255,.014) 62%);
  }}
  .card h3 {{ font:600 18px/1.3 "Space Grotesk"; margin:0 42px 10px 0; letter-spacing:-.01em; }}
  .card.featured h3 {{ font-size:23px; }}
  .card h3 a {{ color:#fff; text-decoration:none; }}
  .card h3 a:hover {{ color:var(--c); }}
  .card p {{ margin:0 0 15px; color:var(--mute); font-size:14px; line-height:1.58; }}
  .card footer {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; font-size:12px; color:var(--faint); }}
  .src {{ color:#cfd4e0; font-weight:500; }}
  .go {{ margin-left:auto; color:var(--c); text-decoration:none; font-weight:600; }}
  .go:hover {{ filter:brightness(1.25); }}
  .more-srcs {{
    display:inline-flex; gap:6px; margin:0 0 13px; padding:0; list-style:none; flex-wrap:wrap;
  }}
  .more-srcs a {{
    font:500 10.5px/1 Inter; letter-spacing:.1em; text-transform:uppercase; color:var(--c);
    text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--c) 40%, transparent);
    padding-bottom:2px;
  }}
  .more-srcs a:hover {{ filter:brightness(1.3); }}
  .rank {{ position:absolute; top:15px; right:19px; font:700 25px/1 "Space Grotesk"; color:rgba(255,255,255,.07); }}
  .rank.feat {{ top:20px; right:24px; font-size:34px; }}
  @media (max-width:760px) {{ .card.featured {{ grid-column:span 1; padding:22px; }} }}
  footer.page {{
    margin-top:78px; padding-top:26px; border-top:1px solid rgba(255,255,255,.08);
    color:var(--faint); font-size:12px; line-height:1.9; text-align:center;
  }}
  footer.page code {{ color:#7aa2f7; }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <div class="kicker">{esc(t['kicker'])} {generated}</div>
      <h1>{esc(t['title'])}</h1>
      <p class="sub">{esc(t['sub'].format(n=meta['n']))}</p>
      {intro_html}
      <nav class="nav">{legend}</nav>
    </header>

    <div class="stats">{stats}</div>

    <h2 class="sec">{esc(t['focus'])}</h2>
    <div class="spot-grid">{"".join(spotlight)}</div>

    {"".join(sections)}

    <footer class="page">
      {esc(t['generated'])} {generated} &middot; {esc(t['source'])} <code>serper.dev/news?tbs=qdr:w</code><br>
      {esc(t['footer_note'])}
    </footer>
  </div>
</body>
</html>
"""


def card_html(c, lang, featured=False, t=None) -> str:
    esc = html.escape
    src = c["sources"]
    first = src[0]
    extra = ""
    if len(src) > 1:
        links = "".join(
            f'<li><a href="{esc(s["link"])}" target="_blank" rel="noopener">{esc(s["name"])}</a></li>'
            for s in src[1:6]
        )
        extra = f'<ul class="more-srcs">{links}</ul>'
    return f"""
      <article class="card{' featured' if featured else ''}" style="--c:{c['color']}">
        <div class="rank{' feat' if featured else ''}">{len(c['sources']) if len(c['sources']) > 1 else ''}</div>
        {extra}
        <h3><a href="{esc(first['link'])}" target="_blank" rel="noopener">{esc(c['title'])}</a></h3>
        <p>{esc(c['snippet'])}</p>
        <footer>
          <span class="src">{esc(first['name'])}</span>
          <span>&middot;</span><span>{esc(c['date'])}</span>
          <a class="go" href="{esc(first['link'])}" target="_blank" rel="noopener">{esc(t['read'])} &rarr;</a>
        </footer>
      </article>"""


# ------------------------------------------------------------- markdown -----
def render_md(clusters, meta, lang, generated, intro) -> str:
    t = UI[lang]
    out = [f"# {t['title']} — {generated}", ""]
    if intro:
        out += [f"_{intro}_", ""]
    for a in AREAS:
        rows = [c for c in clusters if c["area"] == a["key"]]
        if not rows:
            continue
        out += [f"## {a[lang][0]} ({len(rows)})", "", f"_{a[lang][1]}_", ""]
        for c in rows:
            names = " · ".join(s["name"] for s in c["sources"])
            out += [f"- [{c['title']}]({c['sources'][0]['link']})  ",
                    f"  {names} · {c['date']}  ",
                    f"  {c['snippet']}", ""]
    return "\n".join(out)


# ------------------------------------------------------------- terminal -----
def render_term(clusters, lang, generated, intro) -> str:
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
    for a in AREAS:
        rows = [c for c in clusters if c["area"] == a["key"]]
        if not rows:
            continue
        c0 = None
        ansi = lambda h: f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
        lines.append(f"{ansi(a['color'])}{bold}{a[lang][0].upper()}{reset} "
                     f"{dim}({len(rows)}){reset}")
        for c in rows:
            names = " · ".join(s["name"] for s in c["sources"][:3])
            lines.append(f"  {ansi(a['color'])}●{reset} \033[38;5;255m{bold}"
                         f"{c['title'][:w-4]}{reset}")
            lines.append(f"    {dim}{names} — {c['date']}{reset}")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------- main -----
def main() -> None:
    argv = sys.argv[1:]
    path, lang, intro = None, "it", None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--lang", "-l"):
            lang = argv[i + 1]; i += 2
        elif a == "--intro":
            intro = argv[i + 1]; i += 2
        elif a == "--title":
            i += 2
        elif a.startswith("--"):
            i += 2
        else:
            path = a; i += 1
    if lang not in UI:
        print(f"Error: --lang must be one of {sorted(UI)}")
        sys.exit(1)

    path = path or os.path.join(HERE, "ai-news.json")
    raw = json.load(open(path))
    for it in raw:
        it["area"], it["color"] = classify(it["title"], it.get("snippet", ""))
    raw.sort(key=lambda x: hours(x["date"]))

    min_sim = 0.62
    clusters = cluster(raw, min_sim)

    top_area = Counter(c["area"] for c in clusters).most_common(1)[0][0]
    top_name = next(a for a in AREAS if a["key"] == top_area)[lang][0]
    dates = [c["date"] for c in clusters if hours(c["date"]) < 9999]
    window = f"{min(dates, key=hours)} → {max(dates, key=hours)}" if dates else "—"

    meta = dict(n=len(raw), c=len(clusters), a=sum(1 for a in AREAS
              if any(c["area"] == a["key"] for c in clusters)),
                b=top_name, w=window)
    if lang == "it":
        mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
                "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
        now = datetime.now()
        generated = f"{now.day} {mesi[now.month - 1]} {now.year}"
    else:
        generated = datetime.now().strftime("%d %B %Y")

    print(render_term(clusters, lang, generated, intro))
    print(f"  {len(raw)} stories -> {len(clusters)} eventi unici "
          f"(similarita' >= {min_sim}), {meta['a']} aree\n")

    suffix = "" if lang == "it" else f".{lang}"
    hp = os.path.join(HERE, f"ai-news{suffix}.html")
    mp = os.path.join(HERE, f"ai-news{suffix}.md")
    open(hp, "w").write(render_html(clusters, meta, lang, intro, generated))
    open(mp, "w").write(render_md(clusters, meta, lang, intro, generated))
    print(f"  scritto {hp}\n  scritto {mp}\n")


if __name__ == "__main__":
    main()

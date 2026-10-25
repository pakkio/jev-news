#!/usr/bin/env python3
"""One self-contained HTML page explaining the six sections: python explain_sections.py [out.html]

Built from format_news.AREAS, so it never drifts from what the classifier does."""

import sys
from html import escape as e

from format_news import AREAS

NOTES = {
    "frontiers": "Area di ripiego: tutto ciò che riguarda l'IA e non rientra altrove finisce qui.",
}


WHY = {
    "business": "Verde: il colore del denaro e dei mercati in crescita.",
    "frontiers": "Ciano: freddo e tecnico, richiama laboratori, schermi e ricerca di punta.",
    "design": "Magenta: il colore della creatività, l'unico tra i sei che parla di estetica.",
    "regulation": "Rosso: segnale di divieto e attenzione, come semafori e cartelli; è la sezione dei limiti.",
    "fun": "Giallo: leggerezza e sorriso, il più luminoso, per staccare dalle sezioni serie.",
    "models": "Viola: il colore del prodotto vero e proprio, dei modelli e delle voci sul prossimo arrivo.",
}


def card(n: int, a: dict) -> str:
    j = a["jev"]
    name, desc = a["it"]
    en_name = a["en"][0]
    not_for = (f'<h3>Non è per</h3><p>{e(j["not_for"])}</p>' if j.get("not_for") else "")
    ex = "".join(f"<li>{e(x)}</li>" for x in j["examples"])
    kw = ("" if a["pat"] == r".*" else
          f'<details><summary>Parole chiave (regole di ripiego)</summary><code>{e(a["pat"])}</code></details>')
    note = f'<p class="note">{e(NOTES[a["key"]])}</p>' if a["key"] in NOTES else ""
    return f"""<section id="{a['key']}" style="--c:{a['color']}">
  <header><span class="n">{n:02d}</span><h2>{e(name)}</h2><span class="en">{e(en_name)}</span></header>
  <p class="lead">{e(desc)}</p>{note}
  <h3>Cosa contiene</h3><p>{e(j['what'])}</p>
  {not_for}
  <h3>Esempi di titoli</h3><ul class="ex">{ex}</ul>
  <p class="why"><i class="sw"></i>{e(WHY[a['key']])} <code class="hex">{a['color']}</code></p>
  {kw}
</section>"""


def page() -> str:
    nav = "".join(f'<a href="#{a["key"]}" style="--c:{a["color"]}">{e(a["it"][0])}</a>' for a in AREAS)
    cards = "\n".join(card(i, a) for i, a in enumerate(AREAS, 1))
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Le sezioni del Briefing IA</title>
<style>
:root{{--bg:#fff;--fg:#1c1f26;--mut:#5b6270;--card:#f5f6f8;--line:#e2e5ea}}
@media(prefers-color-scheme:dark){{:root{{--bg:#12141a;--fg:#e6e8ee;--mut:#98a0b0;--card:#1b1e27;--line:#2b303c}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:1.9rem;margin:0 0 8px}}
.sub{{color:var(--mut);margin:0 0 20px}}
nav{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:28px}}
nav a{{padding:5px 12px;border-radius:99px;border:1px solid var(--c);color:var(--fg);text-decoration:none;font-size:.9rem}}
nav a:hover{{background:var(--c);color:#111}}
section{{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--c);
  border-radius:10px;padding:18px 20px;margin-bottom:18px}}
section header{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}}
.n{{color:var(--c);font-weight:700;font-variant-numeric:tabular-nums}}
h2{{margin:0;font-size:1.4rem}}
.en{{color:var(--mut);font-size:.9rem}}
.lead{{font-size:1.05rem;margin:8px 0 4px}}
h3{{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:16px 0 4px}}
p{{margin:0}} .note{{color:var(--mut);font-style:italic;margin-top:6px}}
.ex{{margin:0;padding-left:20px}} .ex li{{margin:2px 0}}
p.why{{margin-top:16px;color:var(--mut);font-size:.9rem;display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.sw{{width:14px;height:14px;border-radius:50%;background:var(--c);flex:none}}
code.hex{{display:inline;margin:0}}
details{{margin-top:14px;color:var(--mut)}} code{{display:block;margin-top:6px;white-space:pre-wrap;
  word-break:break-word;font-size:.8rem}}
</style></head><body><main>
<h1>Le sezioni del Briefing IA</h1>
<p class="sub">I sei colori stanno a tinte ben distanti (verde, ciano, viola, magenta, rosso, giallo) e con luminosità simile,
così nessuna coppia si confonde, neppure su sfondo scuro. Ogni notizia viene assegnata a una sola sezione: prima dal giudizio di Jev sul significato
(in qualsiasi lingua), poi, se Jev non è disponibile, da regole a parole chiave.</p>
<nav>{nav}</nav>
{cards}
</main></body></html>"""


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sezioni.html"
    open(out, "w", encoding="utf-8").write(page())
    print(f"scritto {out}")

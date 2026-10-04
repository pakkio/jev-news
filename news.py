#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Rassegna su un tema qualsiasi: stesso motore di provs/lgbt_news.py.

Usage:
  uv run news.py "<tema>" [lang] [giorni] [opzioni di lgbt_news.py]
  uv run news.py "energia nucleare" it 7
  uv run news.py "formula 1" en 3 --no-images

Al primo giro un modello scrive le 4-5 aree tematiche, le query di ricerca e i
testi della pagina per il tema (provs/<slug>.spec.json, modificabile a mano e
riusato ai giri successivi). Poi parte la pipeline di lgbt_news.py: raccolta
Serper negli ultimi N giorni, cluster, Jev, immagini, riassunti, pagina.
Output in provs/<slug>.html.
"""

import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "provs"))

import lgbt_news as L          # noqa: E402
import translate as TR         # noqa: E402

COLORS = ["#f4a261", "#f7768e", "#7aa2f7", "#bb9af7", "#9ece6a"]

PROMPT = """\
Devi preparare la configurazione di una rassegna stampa settimanale sul tema: "{topic}".
Lingua dell'utente: {lang}. Rispondi ESCLUSIVAMENTE con JSON di questa forma:

{{"title": "titolo breve della rassegna",
  "kicker": "etichetta sopra il titolo, es. 'Rassegna · settimana del'",
  "sub": "frase di presentazione che contiene {{n}} (numero storie) e {{window}} (periodo, es. 'negli ultimi 7 giorni'), nella lingua dell'utente",
  "topic": "il tema in una frase inglese: 'X: its a, b, c or d' (serve a un classificatore)",
  "areas": [
    {{"key": "slug_ascii",
      "what": "English: what belongs in this area",
      "not_for": "English: what does NOT belong (optional, may be empty)",
      "examples": ["4 realistic news headlines, in different languages"],
      "name": "nome dell'area nella lingua dell'utente",
      "note": "una riga che spiega cosa contiene, nella lingua dell'utente",
      "pat": "regex Python (case-insensitive, con \\\\b) di parole chiave in inglese e nella lingua dell'utente",
      "queries": [["search query", null], ["query nella lingua del mercato locale", "it"]]}}
  ]}}

Regole: da 4 a 5 aree che coprono tutto il tema senza sovrapporsi; l'ULTIMA area e'
quella residuale (pat ".*"). Per ogni area 4 query per Google News: la maggior
parte in inglese (gl null), alcune nella lingua dell'utente con gl = codice
paese ("it", "us", "gb"...). Niente testo fuori dal JSON."""


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "tema"


def make_spec(topic: str, lang: str) -> dict:
    model = TR.DEFAULT_MODEL
    key = TR.load_key(model)
    last = None
    for _ in range(3):
        spec = TR.extract_json(TR.call(key, model, PROMPT.format(topic=topic, lang=lang)))
        areas = spec.get("areas") or []
        if 3 <= len(areas) <= 6 and all(a.get("queries") and a.get("key") for a in areas):
            areas[-1]["pat"] = ".*"
            return spec
        last = spec
    raise SystemExit(f"Il modello non ha prodotto una configurazione valida: {str(last)[:200]}")


def install(spec: dict, topic: str, lang: str, slug: str) -> None:
    other = "en" if lang == "it" else "it"
    areas, queries = [], {}
    for n, a in enumerate(spec["areas"]):
        name, note = a.get("name", a["key"]), a.get("note", "")
        jev = {"what": a["what"], "examples": a.get("examples", [])}
        if a.get("not_for"):
            jev["not_for"] = a["not_for"]
        areas.append({"key": a["key"], "color": COLORS[n % len(COLORS)], "jev": jev,
                      "it": (name, note), "en": (name, note), "pat": a.get("pat") or ".*"})
        queries[a["key"]] = [tuple(q) if isinstance(q, list) else (q, None) for q in a["queries"]]
    L.AREAS, L.QUERIES, L.TOPIC = areas, queries, spec.get("topic") or topic
    L.OUT = os.path.join(L.HERE, slug)
    L.METER_NAME = slug
    text = dict(kicker=spec.get("kicker") or "Rassegna · settimana del",
                title=spec.get("title") or topic.capitalize(),
                sub=spec.get("sub") or "{n} storie {window}.")
    if "{n}" not in text["sub"]:
        text["sub"] += " ({n} storie)"
    L.TEXT_IT, L.TEXT_EN = text, text


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        raise SystemExit(__doc__)
    topic, rest = args[0], args[1:]
    lang = rest.pop(0) if rest and rest[0] in ("it", "en") else "it"
    days = rest.pop(0) if rest and rest[0].isdigit() else "7"
    slug = slugify(topic)
    path = os.path.join(HERE, "provs", f"{slug}.spec.json")
    if os.path.exists(path):
        spec = json.load(open(path))
        print(f"  configurazione: {os.path.relpath(path, HERE)} (riusata)")
    else:
        print(f"  genero la configurazione per {topic!r}...")
        spec = make_spec(topic, lang)
        json.dump(spec, open(path, "w"), indent=1, ensure_ascii=False)
        print(f"  salvata in {os.path.relpath(path, HERE)}: "
              f"{[a['key'] for a in spec['areas']]}")
    install(spec, topic, lang, slug)
    sys.argv = [sys.argv[0], "--days", days, "--lang", lang, *rest]
    L.main()


if __name__ == "__main__":
    main()

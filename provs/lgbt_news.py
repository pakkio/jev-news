#!/usr/bin/env python3
"""Rassegna settimanale LGBT+: stesso motore del briefing IA, altro tema.

Riusa clustering, immagini, traduzione, riassunti e pagina di format_news.py
sostituendo solo le aree tematiche e i testi. Tutti i file prodotti (e le
cache) stanno qui in provs/ con prefisso lgbt-news.

Usage:
  python3 provs/lgbt_news.py [--max-credits 30] [--reuse] [--no-summaries] [--no-images]
                             [--corroborate]

--corroborate spends Serper credits (1 per main card that rests on a single
unreadable source, at most 6) to look for a second outlet. Off by default: without
it those cards are simply demoted and marked "single source, unconfirmed".
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import collect_news as CN          # noqa: E402
import format_news as FN           # noqa: E402

OUT = os.path.join(HERE, "lgbt-news")

# L'ordine conta: classify() assegna la prima area che combacia.
AREAS = [
    dict(key="rights", color="#f4a261",
         it=("Diritti, leggi e tribunali",
             "Matrimonio, famiglie, riconoscimento giuridico: cosa cambia per legge."),
         en=("Rights, Law & Courts", "Marriage, families, legal recognition."),
         pat=r"\b(law|laws|bill|ban|bans|banned|court|courts|ruling|supreme|legislat\w*|"
             r"marriage|same.?sex|civil union|adoption|rights|equality|congress|senate|"
             r"parliament|government|policy|legge|leggi|ddl|diritti|matrimonio|nozze|"
             r"unioni civili|sentenza|tribunale|corte|governo|parlamento|senato|camera|"
             r"adozion\w*|riconoscimento|figli)\b"),
    dict(key="safety", color="#f7768e",
         it=("Violenza, odio e discriminazione",
             "Aggressioni, hate crime, bullismo e le risposte di istituzioni e comunità."),
         en=("Violence, Hate & Discrimination", "Attacks, hate crimes, bullying."),
         pat=r"\b(attack\w*|hate|violence|violent|discriminat\w*|harass\w*|murder\w*|"
             r"bully\w*|bullying|homophob\w*|transphob\w*|assault\w*|threat\w*|killed|"
             r"aggress\w*|violenz\w*|odio|omofob\w*|transfob\w*|bullismo|aggredit\w*|"
             r"insult\w*|minacc\w*|omicidio|molest\w*)\b"),
    dict(key="health", color="#7aa2f7",
         it=("Salute, identità e persone trans",
             "Cure, percorsi di affermazione di genere, HIV e benessere psicologico."),
         en=("Health, Identity & Trans Lives", "Care, gender-affirming health, HIV."),
         pat=r"\b(trans|transgender|nonbinary|non.?binary|gender|gender.?affirming|"
             r"health|healthcare|hiv|prep|puberty|therapy|mental|medical|hospital|"
             r"identity|salute|sanitari\w*|ospedale|identita|identità|genere|"
             r"disforia|ormon\w*|psicolog\w*|persone trans)\b"),
    dict(key="culture", color="#bb9af7",
         it=("Cultura, media, sport e Pride",
             "Film, serie, musica, atleti e le piazze: la visibilità raccontata."),
         en=("Culture, Media, Sport & Pride", "Film, TV, music, athletes and Pride."),
         pat=r"\b(pride|parade|festival|film|movie|series|show|tv|music|song|album|"
             r"artist|actor|actress|book|novel|drag|queen|athlete|sport|olympic\w*|"
             r"football|calcio|corteo|parata|serie|musica|cantante|attore|attrice|"
             r"libro|romanzo|atleta|sportiv\w*|mostra|teatro|cinema)\b"),
    dict(key="society", color="#9ece6a",
         it=("Società, lavoro e comunità",
             "Tutto il resto: aziende, scuole, religioni, associazioni e vita quotidiana."),
         en=("Society, Work & Community", "Everything else."),
         pat=r".*"),
]

# (query, gl) — internazionali in inglese, più alcune italiane.
QUERIES = {
    "rights": [("LGBT rights law", None), ("gay marriage ruling", None),
               ("diritti LGBT legge", "it"), ("coppie gay lesbiche unioni civili", "it")],
    "safety": [("anti-LGBT hate crime", None), ("gay lesbian attack homophobia", None),
               ("aggressione omofoba gay", "it"), ("transfobia violenza trans", "it")],
    "health": [("transgender health care", None), ("trans youth gender-affirming care", None),
               ("persone trans salute", "it"), ("gay men HIV PrEP", None)],
    "culture": [("Pride parade", None), ("gay lesbian film series", None),
                ("Pride corteo", "it"), ("trans athlete sport", None)],
    "society": [("LGBT workplace inclusion", None), ("lesbian gay community news", None),
                ("LGBT trans lavoro scuola", "it"), ("LGBT church religion", None)],
}

TEXT_IT = dict(
    kicker="Rassegna · settimana del", title="Rassegna LGBT+",
    sub="{n} storie raccolte da Google News via Serper negli ultimi 7 giorni, "
        "internazionali e italiane. Raggruppate per area e deduplicate: quando più "
        "testate coprono lo stesso evento diventano una sola scheda con più fonti.",
)


def serper_news(key: str, q: str, gl: str, num: int = 10) -> list:
    body = {"q": q, "num": num, "tbs": "qdr:w"}
    if gl:
        # hl=en tiene le date in "N days ago", l'unico formato che hours() legge
        body.update(gl=gl, hl="en")
    req = urllib.request.Request(
        "https://google.serper.dev/news", data=json.dumps(body).encode(),
        headers={"X-API-KEY": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("news") or []
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  ! {q!r}: {str(e)[:70]}", file=sys.stderr)
        return []


def collect(max_credits: int) -> list:
    key, seen, items, spent = CN.load_key(), set(), [], 0
    # un giro per area alla volta: se i crediti finiscono, nessuna area resta a secco
    rounds = max(len(v) for v in QUERIES.values())
    for r in range(rounds):
        for a in AREAS:
            qs = QUERIES[a["key"]]
            if r >= len(qs) or spent >= max_credits:
                continue
            q, gl = qs[r]
            spent += 1
            new = 0
            for n in serper_news(key, q, gl):
                link = n.get("link", "")
                c = CN.canonical(link) if link else ""
                if not c or c in seen:
                    continue
                seen.add(c)
                area, color = FN.classify(n.get("title", ""), n.get("snippet", ""))
                items.append({
                    "title": n.get("title", ""), "link": link, "url": c,
                    "snippet": n.get("snippet", ""), "source": n.get("source", ""),
                    "date": n.get("date", ""), "area": area, "color": color,
                    "imageUrl": n.get("imageUrl", ""), "q": a["key"]})
                new += 1
            print(f"  [{spent:2}] {q[:40]:40} +{new}")
    return items


JUDGE = """\
Titolo di riferimento: %s

Candidati, numerati:
%s

Quali candidati riportano LA STESSA NOTIZIA (stesso fatto, stesso momento)? Lo stesso
tema non basta.
Rispondi ESCLUSIVAMENTE con JSON: {"stessa": [1, 3]}  ({"stessa": []} se nessuno)."""


def corroborate(cands: list, max_checks: int = 6) -> tuple:
    """A main card with one outlet and no summary is a claim nobody could check
    (T24's closure came from a single site Jina could not read). One Serper
    search by headline per card, then a model says which hits report the very
    same news; those become extra sources. Returns (cards that gained sources,
    Serper credits spent)."""
    import images as IMG
    import translate as TR
    key, model = CN.load_key(), TR.DEFAULT_MODEL
    mkey, gained, spent = TR.load_key(model), 0, 0
    for c in cands[:max_checks]:
        title = c["members"][0]["title"]
        mine = {IMG.registrable(m["link"]) for m in c["members"]}
        spent += 1
        hits = [h for h in serper_news(key, title[:110], None, num=8)
                if h.get("link") and IMG.registrable(h["link"]) not in mine][:6]
        same = []
        if hits:
            listing = "\n".join(f"{i}. {h.get('title', '')} - {h.get('source', '')}"
                                for i, h in enumerate(hits, 1))
            try:
                got = TR.extract_json(TR.call(mkey, model, JUDGE % (title, listing)))
            except Exception:  # noqa: BLE001
                got = {}
            same = [hits[n - 1] for n in got.get("stessa", [])
                    if isinstance(n, int) and 1 <= n <= len(hits)]
        for h in same:
            c["members"].append({
                "title": h.get("title", ""), "link": h["link"], "url": CN.canonical(h["link"]),
                "snippet": h.get("snippet", ""), "source": h.get("source", ""),
                "date": h.get("date", ""), "area": c["area"], "color": c["color"],
                "imageUrl": h.get("imageUrl", ""), "q": "conferma"})
        if same:
            FN.finalize(c)
            gained += 1
        print(f"  conferma {title[:56]!r}: {'+%d fonti' % len(same) if same else 'nessuna conferma'}")
    return gained, spent


def main() -> None:
    argv = sys.argv[1:]
    max_credits = int(argv[argv.index("--max-credits") + 1]) if "--max-credits" in argv else 30
    do_sum, do_img = "--no-summaries" not in argv, "--no-images" not in argv
    do_corr = "--corroborate" in argv
    n_main, n_more, lang = 4, 6, "it"

    # stesso motore, altro tema
    FN.AREAS[:] = AREAS
    FN.MIN_SHARED_IDF = 7.0      # two shared words must carry real information
    FN.USE_BOOST = False         # the ranking lexicon is English tech/finance: not used here
    FN.MERGE_SWEEP = True        # rejoin clusters that converged after the greedy pass
    FN.UI[lang].update(TEXT_IT)

    if "--reuse" in argv and os.path.exists(f"{OUT}.json"):
        raw = json.load(open(f"{OUT}.json"))       # nessun credito Serper per le news
        print(f"  riuso {len(raw)} storie da {os.path.basename(OUT)}.json")
    else:
        raw = collect(max_credits)
    if not raw:
        raise SystemExit("Nessuna storia raccolta.")
    raw, dropped = FN.drop_filler(raw)
    for why, title in dropped:
        print(f"  scartata ({why}): {title[:70]}")
    for it in raw:
        it["area"], it["color"] = FN.classify(it["title"], it["snippet"])
    raw.sort(key=lambda x: FN.hours(x["date"]))
    json.dump(raw, open(f"{OUT}.json", "w"), indent=1, ensure_ascii=False)

    clusters = FN.cluster(raw, 0.62)
    print(f"  cluster per parole: {len(clusters)}")
    merged = FN.llm_merge(clusters, cache_path=f"{OUT}.mrg.json")
    print(f"  unite per parafrasi: {merged} -> {len(clusters)} eventi")
    for n, c in enumerate(clusters):
        c["sid"] = f"s{n}"
    threads = (FN.find_threads_llm(clusters, cache_path=f"{OUT}.thr2.json")
               or FN.find_threads(clusters))
    rows = [(c, kind) for _, _, main_rows, more, _ in
            FN.split_areas(clusters, n_main, n_more, skip_threaded=True)
            for c, kind in [(x, "main") for x in main_rows] + [(x, "more") for x in more]]
    shown = [c for c, _ in rows]
    shown += [c for th in threads for c in th["members"] if c not in shown]
    featured = [c for c, k in rows if k == "main"]

    if do_img:
        import images as IMG
        print("  immagini per le storie in evidenza...")
        cache, st = IMG.fetch([c["title"] for c in featured], lang=lang,
                              cache_path=f"{OUT}.img.json",
                              aliases={c["title"]: [m["title"] for m in c["members"]]
                                       for c in featured},
                              want={c["title"]: [s["link"] for s in c["sources"]]
                                    for c in featured})
        for c in clusters:
            row = IMG.lookup(cache, c["title"])
            if row and not IMG.same_publisher(row, [s["link"] for s in c["sources"]]):
                row = {}                     # lookalike from another site: not trusted
            if not row and c.get("thumb"):
                row = {"url": c["thumb"], "credit": c["sources"][0]["name"]}
            if row:
                c["img"] = row
        n_og = IMG.upgrade(featured, cache, f"{OUT}.img.json")   # the article's own header photo
        print(f"  trovate {st['found']}, cache {st['cached']}, nessuna {st['empty']}; "
              f"foto dall'articolo (og:image): {n_og}")
        n_emb, n_bytes = IMG.embed(featured, [c for c, k in rows if k == "more"], f"{OUT}.img.json")
        print(f"  foto incorporate nell'HTML: {n_emb} ({n_bytes // 1024} KB)")

    import translate as TR
    print("  traduzione...")
    cache, st = TR.translate([{"title": c["title"], "snippet": c["snippet"]}
                              for c in clusters], cache_path=f"{OUT}.it.json")
    for c in clusters:
        row = TR.lookup(cache, c["title"], c["snippet"])
        if row:
            c["title_it"], c["snippet_it"] = row["title"], row["snippet"]
    print(f"  tradotte {st['translated']}, cache {st['cached']}, fallite {st['failed']}")

    if do_sum:
        import summarize as SM
        print("  riassunti degli articoli...")
        cache, st = SM.summarize(
            [{"title": c["title"], "links": [x["link"] for x in c["sources"]]}
             for c in shown], cache_path=f"{OUT}.sum.json")
        for c in shown:
            c["summary"] = SM.lookup(cache, c["title"])
        print(f"  nuovi {st['done']}, cache {st['cached']}, non disponibili {st['empty']}, "
              f"controllati {st['checked']}, scartati {st['rejected']}")

    if do_sum:
        # main cards resting on one unreadable source: look for a second outlet, retry
        # the summary with it, and demote what stays unconfirmed
        weak = [c for c in featured if len(c["sources"]) == 1 and not c.get("summary")]
        if weak:
            print(f"  fonte unica senza riassunto tra le principali: {len(weak)}")
            if do_corr:
                gained, spent = corroborate(weak)
                print(f"  conferme trovate: {gained} | crediti Serper spesi: {spent}")
                sp, vp = f"{OUT}.sum.json", f"{OUT}.sum.ver.json"
                sc, vc = TR.load_cache(sp), TR.load_cache(vp)
                for c in weak:
                    sc.pop(SM.digest(c["title"]), None)
                    vc.pop(SM.digest(c["title"]), None)
                TR.save_cache(sp, sc)
                TR.save_cache(vp, vc)
                cache, st = SM.summarize(
                    [{"title": c["title"], "links": [x["link"] for x in c["sources"]]}
                     for c in weak], cache_path=sp)
                for c in weak:
                    c["summary"] = SM.lookup(cache, c["title"])
            for c in weak:
                if len(c["sources"]) == 1 and not c.get("summary"):
                    c["penalty"] = 40            # unconfirmed: leaves the main cards
    FN.label_threads(threads, cache_path=f"{OUT}.thr.json")
    print(f"  fili: {[(th['area'], len(th['members']), th.get('title')) for th in threads]}")

    per_area = Counter(c["area"] for c in clusters)
    live = [v for v in per_area.values() if v]
    dates = [c["date"] for c in clusters if FN.hours(c["date"]) < 9999]
    window = (f"{FN.fmt_date(max(dates, key=FN.hours), lang)} → "
              f"{FN.fmt_date(min(dates, key=FN.hours), lang)}") if dates else "—"
    meta = dict(n=len(raw), c=len(clusters), a=len(live),
                g=f"{min(live)}–{max(live)}", w=window)
    now = datetime.now()
    generated = f"{now.day} {FN.MESI_IT[now.month - 1]} {now.year}"

    open(f"{OUT}.html", "w").write(FN.render_html(
        clusters, meta, lang, None, generated, n_main, n_more,
        hero="off", images="all" if do_img else "off", threads=threads))
    open(f"{OUT}.md", "w").write(FN.render_md(
        clusters, meta, lang, generated, None, n_main, n_more))
    print(f"\n  {len(raw)} storie -> {len(clusters)} eventi unici, aree: "
          f"{dict(per_area)}\n  scritto {OUT}.html")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Marks on a digest page what changed from an earlier run of the same stories.

  uv run diff_runs.py provs/ai-3-days-c.html provs/run4.html

Both pages carry their stories as JSON (<script id="stories">); a story is the same
when its first link is. On the NEW page each changed story gets a badge next to its
"Perche'?" (impact, piece type, place on the page, score), and a box under the
numbers lists the stories that came in and the ones that dropped out. The page is
written in place; running it again replaces the marks.
"""

import html
import json
import re
import sys

MARK = "<!--diff-runs-->"
CSS = """<style>
.diff { font:600 10.5px/1.3 Inter; padding:3px 8px; border-radius:6px; color:#ffd479;
        border:1px dashed rgba(255,212,121,.55); background:rgba(255,212,121,.08); cursor:help; }
.diff.new { color:#7fd6a4; border-color:rgba(127,214,164,.55); background:rgba(127,214,164,.08); }
.diffbox { margin:18px 0; padding:14px 18px; border:1px dashed rgba(255,212,121,.55); border-radius:10px;
           background:rgba(255,212,121,.05); font:14px/1.5 Inter; color:#e6e8ef; }
.diffbox h3 { margin:0 0 6px; font-size:15px; color:#ffd479; }
.diffbox ul { margin:4px 0 8px 18px; padding:0; }
.diffbox .s { color:#9aa3b5; }
</style>"""


def stories(path: str) -> dict:
    page = open(path, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="stories"[^>]*>(.*?)</script>', page, re.S)
    out = {}
    for sid, v in json.loads(m.group(1)).items():
        w = v["why"]
        dims = {d["label"]: d["v"] for d in w["dims"]}
        role = w["role"]
        out[v["link"]] = dict(sid=sid, title=v["title"], type=(w["type"] or {}).get("name"),
                              impact=dims.get("Impatto", dims.get("Impact")), score=w["parts"]["total"],
                              place="principale" if role.startswith(("Scheda principale", "Main card"))
                              else "secondaria" if role.startswith(("Riga secondaria", "Brief"))
                              else "filo")
    return out


def changes(a: dict, b: dict) -> list:
    out = []
    if a["impact"] is not None and b["impact"] is not None and abs(a["impact"] - b["impact"]) >= 0.05:
        out.append(f"impatto {a['impact'] * 3:.1f}→{b['impact'] * 3:.1f}")
    if a["type"] != b["type"]:
        out.append(f"{a['type']}→{b['type']}")
    if a["place"] != b["place"]:
        out.append(f"{a['place']}→{b['place']}")
    return out


def main(old_path: str, new_path: str) -> None:
    old, new = stories(old_path), stories(new_path)
    page = open(new_path, encoding="utf-8").read()
    page = re.sub(re.escape(MARK) + r".*?" + re.escape(MARK + "end"), "", page, flags=re.S)

    badges, n_changed = {}, 0
    for link, b in new.items():
        a = old.get(link)
        if a is None:
            badges[b["sid"]] = (f'<span class="diff new" title="Non era in pagina nel run precedente">'
                                f'nuova in pagina</span> ')
            continue
        ch = changes(a, b)
        if ch:
            n_changed += 1
            tip = f"Run precedente: punteggio {a['score']}, ora {b['score']}"
            badges[b["sid"]] = f'<span class="diff" title="{html.escape(tip)}">Δ {html.escape(" · ".join(ch))}</span> '
    for sid, badge in badges.items():           # before the first "Perche'?" of the story
        page = re.sub(r'(<(?:button|span)[^>]*class="why-btn[^"]*"[^>]*data-sid="%s")' % sid,
                      lambda m: MARK + badge + MARK + "end" + m.group(1), page, count=1)

    came = [b for k, b in new.items() if k not in old]
    went = [a for k, a in old.items() if k not in new]

    def li(rows):
        return "".join(f'<li>{html.escape(r["title"])} <span class="s">({r["type"]}, ★ {round(r["score"])})</span></li>'
                       for r in rows)
    box = (f'{MARK}<div class="diffbox"><h3>Differenze dal run precedente ({html.escape(old_path.split("/")[-1])})</h3>'
           f'{n_changed} storie con impatto, tipo o posizione diversi (segnate con Δ), '
           f'{len(came)} entrate, {len(went)} uscite.'
           + (f'<div>Entrate:</div><ul>{li(came)}</ul>' if came else "")
           + (f'<div>Uscite:</div><ul>{li(went)}</ul>' if went else "")
           + f'</div>{MARK}end')
    page = page.replace("</head>", MARK + CSS + MARK + "end</head>", 1)
    page = page.replace('<h2 class="sec">', box + '<h2 class="sec">', 1)
    open(new_path, "w", encoding="utf-8").write(page)
    print(f"  {n_changed} cambiate, {len(came)} entrate, {len(went)} uscite -> {new_path}")


if __name__ == "__main__":
    main(*sys.argv[1:3]) if len(sys.argv) > 2 else sys.exit(__doc__)

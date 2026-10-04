#!/usr/bin/env python3
"""Hand-labelling sheet to check Jev against a human, and the agreement score.

  sheet(clusters, path)   writes <slug>.labels.csv: 60 events (area, relevance, impact, piece type)
                          and every merged event with up to 40 (were the headlines really one occurrence?).
                          Jev's answer is in the "jev_*" columns: label first, look at them after.
  uv run labels.py provs/ai-3-days-c.labels.csv   -> agreement, per question

Fill the empty columns: ok_area y/n, relevant y/n, impact 0-3 (0 local .. 3 landmark), ptype
(report/opinion/press_release/explainer/roundup/non_article), merge_ok y/n.
"""

import csv
import random
import sys

FIELDS = ["id", "kind", "title", "snippet", "others", "jev_area", "jev_relevant", "jev_impact",
          "jev_ptype", "ok_area", "relevant", "impact", "ptype", "merge_ok"]


def sheet(clusters: list, path: str, n_events: int = 60, n_merges: int = 40, seed: int = 7) -> None:
    rnd = random.Random(seed)
    rated = [c for c in clusters if c.get("jev")]
    events = rnd.sample(rated, min(n_events, len(rated)))
    merged = [c for c in clusters if len(c.get("members") or []) > 1]
    merged = rnd.sample(merged, min(n_merges, len(merged)))
    rows = []
    for c in events:
        rows.append(dict(id=c["sid"], kind="event", title=c["title"], snippet=(c.get("snippet") or "")[:300],
                         jev_area=c["area"], jev_relevant=f"{c.get('relevance', 0):.2f}",
                         jev_impact=f"{c['jev']['impact'] * 3:.1f}", jev_ptype=(c.get("ptype") or {}).get("choice", "")))
    for c in merged:
        rows.append(dict(id=c["sid"], kind="merge", title=c["title"],
                         others=" || ".join(m["title"] for m in c["members"] if m["title"] != c["title"])))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def score(path: str) -> None:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))

    def rate(name, pairs):
        pairs = [(a, b) for a, b in pairs if b.strip() != ""]
        if pairs:
            hit = sum(1 for a, b in pairs if a == b)
            print(f"  {name:22} {hit}/{len(pairs)} = {hit / len(pairs):.0%}")
        else:
            print(f"  {name:22} nessuna etichetta")
    ev = [r for r in rows if r["kind"] == "event"]
    rate("area", [("y", r["ok_area"].strip().lower()) for r in ev])
    rate("pertinenza (>=0.5)", [("y" if float(r["jev_relevant"]) >= 0.5 else "n", r["relevant"].strip().lower()) for r in ev])
    rate("impatto (+-0.5)", [(1, 1) if r["impact"].strip() and abs(float(r["jev_impact"]) - float(r["impact"])) <= 0.5 else (0, 1)
                             for r in ev if r["impact"].strip()])
    rate("tipo di pezzo", [(r["jev_ptype"], r["ptype"].strip()) for r in ev])
    rate("fusioni corrette", [("y", r["merge_ok"].strip().lower()) for r in rows if r["kind"] == "merge"])


if __name__ == "__main__":
    score(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__)

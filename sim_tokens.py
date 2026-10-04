#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Simulation of the Jev input tokens of one run. No API call, no key, no cost.

It builds the real questions the pipeline sends (rate.py, decide.py, the areas of
provs/lgbt_news.py), pairs them with a synthetic story, and counts tokens as
characters / --cpt (default 4, a common estimate: check against the "Jev per fase"
line of a real run and pass the ratio you observe). Compares rating the old way (six
questions for every story) with the two-stage way (relevance for all, the rest only for
the top --deep per area), next to the stages the trick does not touch.

Usage:
  uv run sim_tokens.py [--stories 200] [--events 150] [--areas 5] [--deep 20]
                       [--keep 0.8] [--pairs 3] [--cached 0.0] [--cpt 4]
--keep    share of stories that are relevant (pass the relevance filter)
--pairs   average merge candidates per story
--cached  share of stories already cached from earlier runs (they cost nothing)
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "provs"))

import decide as D          # noqa: E402
import jev as J             # noqa: E402
import lgbt_news as L       # noqa: E402
import meter                # noqa: E402
import rate as R            # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--stories", type=int, default=200)
p.add_argument("--events", type=int, default=150, help="clusters left after word clustering")
p.add_argument("--areas", type=int, default=len(L.AREAS))
p.add_argument("--deep", type=int, default=R and 20, help="full ratings per area")
p.add_argument("--keep", type=float, default=0.8)
p.add_argument("--pairs", type=float, default=3.0)
p.add_argument("--cached", type=float, default=0.0)
p.add_argument("--cpt", type=float, default=4.0, help="characters per token")
a = p.parse_args()

SNIPPET, TITLE, URL = 200, 90, 70
state = {"headline": "x" * TITLE, "snippet": "y" * SNIPPET, "url": "u" * URL}
short = {"headline": "x" * TITLE, "snippet": "y" * SNIPPET}


def tok(obj) -> float:
    return len(json.dumps(obj)) / a.cpt


qs = R.questions("topic of the digest")
full = tok({"state": state, "questions": qs})
light = tok({"state": state, "questions": {"relevant": qs["relevant"]}})
area_q = {"area": J.choice_question("Which area does the story belong to? Judge by `headline` and `snippet`.",
                                    {x["key"]: x["jev"] for x in L.AREAS[:a.areas]})}
area = tok({"state": short, "questions": area_q})
one_pair_q = J.noul_question("Do `a` and `candidates[0]` report the very same occurrence?",
                             D.SAME_YES, D.SAME_NO)
job = lambda n: tok({"a": short, "candidates": [short] * n, "questions": {f"s{i}": one_pair_q for i in range(n)}})

new = 1 - a.cached
ev_new = a.events * new
pair_total = a.events * a.pairs * new
jobs = [10] * int(pair_total // 10 / 1) + ([int(pair_total % 10)] if pair_total % 10 else [])
pairs_t = sum(job(n) for n in jobs)

deep_n = min(ev_new * a.keep, a.areas * a.deep)
rows = [
    ("classify areas, OLD: per headline", a.stories * new, a.stories * new * area),
    ("classify areas, NEW: per event", ev_new, ev_new * area),
    ("merge judging (unchanged)", len(jobs), pairs_t),
    ("rating, OLD: 6 questions each", ev_new, ev_new * full),
    ("rating, NEW: relevance for all", ev_new, ev_new * light),
    ("rating, NEW: 5 more for top", deep_n, deep_n * full),
]
usd = lambda t: t / 1e6 * meter.JEV_IN
print(f"stories {a.stories}, events {a.events}, areas {a.areas}, deep {a.deep}/area, "
      f"keep {a.keep:.0%}, cached {a.cached:.0%}, {a.cpt} chars/token\n")
print(f"  one story: all six questions ~{full:,.0f} tok | relevance only ~{light:,.0f} tok | "
      f"area ~{area:,.0f} tok\n")
print(f"  {'stage':36}{'requests':>10}{'input tok':>12}{'USD':>9}")
for name, n, t in rows:
    print(f"  {name:36}{n:>10,.0f}{t:>12,.0f}{usd(t):>9.4f}")
old = rows[0][2] + rows[2][2] + rows[3][2]
now = rows[1][2] + rows[2][2] + rows[4][2] + rows[5][2]
print(f"\n  whole run  OLD {old:>9,.0f} tok ${usd(old):.4f}  |  NEW {now:>9,.0f} tok ${usd(now):.4f}"
      f"  |  saved {1 - now / old:.0%}")

#!/usr/bin/env python3
"""Estimated cost of each piece of a digest, from the real texts. No API call.

A rerun from cache measures nothing (the meter sees zero requests), so the cost of a piece is
rebuilt as sim_tokens.py does: the Jev questions it went through (merge pairs, area, relevance,
and the five deep ratings when it was rated in depth), counted as characters / CPT, priced at
meter.JEV_IN. The Serper credits of the run are shared equally among the pieces. Generative
model costs follow meter.LLM_* (zero unless set).
"""

import json
import os

import meter
import rate as R
import jev as J

CPT = float(os.environ.get("CPT", "4"))      # characters per token
PAIRS = 3                                    # merge candidates per headline, as in sim_tokens.py


def _tok(obj) -> float:
    return len(json.dumps(obj, ensure_ascii=False)) / CPT


def attach(clusters: list, areas: list, topic: str, serper_credits: float = 0.0) -> dict:
    """Sets c["cost"] (USD) on every cluster; returns the totals."""
    qs = R.questions(topic)
    area_q = {"area": J.choice_question("Which area does the story belong to? Judge by `headline` and `snippet`.",
                                        {a["key"]: a["jev"] for a in areas})}
    full_q, light_q = qs, {"relevant": qs["relevant"]}
    pair_q = J.noul_question("Do `a` and `candidates[0]` report the very same occurrence?", "same", "different")
    usd = lambda tokens: tokens / 1e6 * meter.JEV_IN
    jev_total = 0.0
    rows = []
    for c in clusters:
        heads = c.get("members") or [c]
        t = 0.0
        for m in heads:                              # merge judging: every headline met its candidates
            s = {"headline": m["title"], "snippet": m.get("snippet", "")}
            t += _tok({"a": s, "candidates": [s] * PAIRS, "questions": {f"s{i}": pair_q for i in range(PAIRS)}})
        s = {"headline": c["title"], "snippet": c.get("snippet", ""), "url": c["sources"][0]["link"]}
        t += _tok({"state": s, "questions": area_q})
        t += _tok({"state": s, "questions": full_q if c.get("jev") else light_q})
        rows.append(usd(t))
        jev_total += usd(t)
    share = serper_credits * meter.SERPER / len(clusters) if clusters else 0.0
    for c, j in zip(clusters, rows):
        c["cost"] = j + share
    return {"jev": jev_total, "serper_credits": serper_credits, "serper": serper_credits * meter.SERPER,
            "total": jev_total + serper_credits * meter.SERPER, "pieces": len(clusters)}

#!/usr/bin/env python3
"""Shared client for Jev, TypeSafe's System One model.

Jev answers typed questions about a piece of `state` in a single forward pass:
a probability for a yes/no (Noul), a distribution over options (Choice), a
position on an ordered scale (Score). It judges meaning, so the same question
works on an English, French, German, Spanish or Italian headline, which a list
of keywords cannot do. English is where it is most accurate: criteria are
written in English, with examples in other languages, and the story is passed
as it is.

Rules this project follows (from TypeSafe's docs):
* Jev decides, a generative model writes, code does the arithmetic and dates.
* Questions are literal and carry criteria with a `not_for` and examples.
* Many questions go in one request (60 take about 0.3 s), each with its own id.
* States stay small: filter first, send only what the question needs.
* Results are cached by model and content, so a rerun costs nothing.
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

from meter import METER

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
ANY_LANGUAGE = " The text may be written in any language; judge what it says, not the language."


def load_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if key:
        return key
    path = os.path.join(HERE, "..", ".env")
    if os.path.exists(path):
        for line in open(path):
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("TYPESAFE_API_KEY not set (and no ../.env).")


def call(key: str, state, questions: dict, retries: int = 3):
    """One request, or None on failure. Retries rate limits and 5xx with backoff."""
    body = json.dumps({"state": state, "model": MODEL, "questions": questions}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API, data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "User-Agent": "curl/8.5.0"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                reply = json.loads(r.read())
                METER.jev(reply.get("usage"))
                return reply["answers"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            print(f"  ! Jev HTTP {e.code}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            print(f"  ! Jev {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
            return None
    return None


def digest(*parts) -> str:
    """Cache key: the model and every part of the question's content."""
    return hashlib.sha1("\x00".join([MODEL, *map(str, parts)]).encode()).hexdigest()[:16]


def load_cache(path: str) -> dict:
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def save_cache(path: str, cache: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(cache, fh)
    os.replace(tmp, path)


def choice_question(instructions: str, options: dict) -> dict:
    """options: {id: {"what": ..., "not_for": ..., "examples": [...]}}"""
    return {"type": "choice", "instructions": instructions + ANY_LANGUAGE, "criteria": options}


def noul_question(instructions: str, yes: dict, no: dict) -> dict:
    return {"type": "noul", "instructions": instructions + ANY_LANGUAGE,
            "criteria": {"true": yes, "false": no}}


def score_question(instructions: str, levels: list) -> dict:
    return {"type": "score", "instructions": instructions + ANY_LANGUAGE, "criteria": levels}

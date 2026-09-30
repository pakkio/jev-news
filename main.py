#!/usr/bin/env python3
"""Search Google through Serper, then read and clean the top pages via Jina Reader.

Usage:  python3 main.py "search query"
        python3 main.py --news --days 7 "AI"
Needs:  export SERPER_API_KEY=...
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
WIDTH = 70          # inner width of the boxes (borders add 2)
PREVIEW_LINES = 15  # content lines shown per result
ELLIPSIS = "…"
DECORATION = re.compile(r"^[-*_=~+|]{3,}$")
DAYS_TO_TBS = {1: "qdr:d", 7: "qdr:w", 30: "qdr:m"}


# --------------------------------------------------------------------------- #
# network
# --------------------------------------------------------------------------- #
def serper_search(
    query: str, num: int = 10, news: bool = False, tbs: str | None = None
) -> list[dict]:
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY environment variable is not set.")

    endpoint = "news" if news else "search"
    payload: dict = {"q": query, "num": num}
    if tbs:
        payload["tbs"] = tbs
    req = urllib.request.Request(
        f"https://google.serper.dev/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = " - " + e.read().decode(errors="replace")[:120]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Serper API returned {e.code} {e.reason}{detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Serper: {e.reason}") from e
    return data.get("news" if news else "organic") or []


def jina_read(url: str) -> tuple[bool, str]:
    """Fetch a page as markdown. Returns (ok, text); on failure text is the error."""
    req = urllib.request.Request(
        f"https://r.jina.ai/{url}",
        headers={"Accept": "text/markdown"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return True, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"blocked ({e.code} {e.reason})"
    except Exception as e:  # noqa: BLE001 - jina can fail in many ways
        return False, str(e)


# --------------------------------------------------------------------------- #
# text cleaning
# --------------------------------------------------------------------------- #
SKIP_PATTERNS = (
    "sign in", "log in", "subscribe", "newsletter", "cookie",
    "privacy policy", "terms of", "all rights reserved",
    "advertisement", "sponsored", "related articles",
    "share this", "follow us", "© 20", "read more",
    "table of contents", "edit page", "view source",
    "accedi", "registrati", "iscriviti", "cookie",
    "informativa sulla privacy", "termini e condizioni",
    "tutti i diritti riservati", "pubblicità", "sponsorizzato",
    "articoli correlati", "condividi", "seguici", "leggi anche",
    "indice dei contenuti", "modifica pagina", "visualizza sorgente",
)


def clean_content(text: str) -> str:
    cleaned: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if DECORATION.match(stripped):
            continue
        if any(p in stripped.lower() for p in SKIP_PATTERNS):
            continue
        if len(stripped) < 3 and not stripped.startswith("#"):
            continue
        cleaned.append(stripped)
    return "\n".join(cleaned).strip()


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #
def clip(text: str, width: int) -> str:
    """Collapse whitespace, truncate with an ellipsis, pad to exactly `width`."""
    text = " ".join(str(text).split())
    if width <= 0:
        return ""
    if len(text) > width:
        text = text[: width - 1] + ELLIPSIS
    return text.ljust(width)


def row(text: str = "", indent: int = 2) -> str:
    """One `  │ ... │` line of exactly WIDTH + 2 characters."""
    return "  │" + " " * indent + clip(text, WIDTH - 2 - indent) + "│"


def rule(left: str, mid: str, right: str) -> str:
    """A horizontal box rule, aligned with `row()` output."""
    return "  " + left + "─" * (WIDTH - 2) + right


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
USAGE = (
    'Usage: python3 main.py [options] "search query"\n'
    "Options:\n"
    "  --news        use the Serper News endpoint instead of web search\n"
    "  --days N      restrict to the last N days (1, 7, 30 -> qdr:d/w/m)\n"
    "  --num N       number of results (default 10)\n"
    "Env:   SERPER_API_KEY=<your key>"
)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    news, tbs, num, days = False, None, 10, None
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if flag == "--news":
            news = True
        elif flag == "--days":
            if not args:
                print("Error: --days needs a value (1, 7 or 30).")
                sys.exit(1)
            days = int(args.pop(0))
            if days not in DAYS_TO_TBS:
                print(f"Error: --days must be one of {sorted(DAYS_TO_TBS)}.")
                sys.exit(1)
            tbs = DAYS_TO_TBS[days]
        elif flag == "--num":
            num = int(args.pop(0))
        else:
            print(f"Error: unknown option {flag}\n\n{USAGE}")
            sys.exit(1)

    if not args:
        print(USAGE)
        sys.exit(1)
    if not SERPER_API_KEY:
        print("Error: SERPER_API_KEY environment variable is not set.")
        sys.exit(1)

    query = " ".join(args)

    print()
    print("╔" + "═" * WIDTH + "╗")
    print("║" + " SERPER + JINA SEARCH".center(WIDTH) + "║")
    print("╠" + "═" * WIDTH + "╣")
    print("║" + clip(f" Query: {query}", WIDTH) + "║")
    scope = "Google News" if news else "Google Web"
    if days is not None:
        scope += f" · last {days} day(s)"
    print("║" + clip(f" Source: {scope}", WIDTH) + "║")
    print("╚" + "═" * WIDTH + "╝")
    print()

    try:
        results = serper_search(query, num=num, news=news, tbs=tbs)
    except RuntimeError as e:
        print(f"  Error: {e}")
        sys.exit(1)

    if not results:
        print("  No results found.")
        return

    print(f"  Found {len(results)} result(s)\n")

    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("link", "")
        snippet = r.get("snippet", "")
        meta = " · ".join(
            x for x in (r.get("source", ""), r.get("date", "")) if x
        )

        print(rule("┌", "─", "┐"))
        print(row(f"[{i}/{len(results)}]  {title}"))
        print(rule("├", "─", "┤"))
        print(row(f"URL: {url}"))
        if meta:
            print(row(f"Via: {meta}"))
        print(rule("├", "─", "┤"))

        if snippet:
            wrap_at = WIDTH - 6
            for j in range(0, len(snippet), wrap_at):
                print(row(snippet[j : j + wrap_at], indent=4))
            print(rule("├", "─", "┤"))

        ok, content = jina_read(url)
        if ok:
            lines = [ln for ln in clean_content(content).split("\n") if ln.strip()]
        else:
            lines = []

        if not ok:
            print(row(f"(page not fetched: {content})", indent=4))
        elif not lines:
            print(row("(no content extracted)", indent=4))
        else:
            for line in lines[:PREVIEW_LINES]:
                print(row(line, indent=4))
            remaining = len(lines) - PREVIEW_LINES
            if remaining > 0:
                print(row(f"{ELLIPSIS} ({remaining} more lines)", indent=4))

        print(rule("└", "─", "┘"))
        print()

    print("─" * (WIDTH + 2))
    print(f"  Done — {len(results)} result(s) for: {query}")
    print("─" * (WIDTH + 2))
    print()


if __name__ == "__main__":
    main()

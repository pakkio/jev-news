import os
import sys
import json
import urllib.request
import urllib.error

SERPER_API_KEY = os.environ.get("SERPER_API_KEY")


def serper_search(query: str, num: int = 10) -> list[dict]:
    payload = json.dumps({"q": query, "num": num}).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=payload,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return data.get("organic", [])


def jina_read(url: str) -> str:
    req = urllib.request.Request(
        f"https://r.jina.ai/{url}",
        headers={"Accept": "text/markdown"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return f"[Blocked: {e.code} {e.reason}]"
    except Exception as e:
        return f"[Error: {e}]"


def clean_content(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    skip_patterns = [
        "sign in", "log in", "subscribe", "newsletter", "cookie",
        "privacy policy", "terms of", "all rights reserved",
        "advertisement", "sponsored", "related articles",
        "share this", "Follow us", "© 20", "Read more",
        "Table of contents", "Edit page", "View source",
        "accedi", "registrati", "iscriviti", "newsletter", "cookie",
        "informativa sulla privacy", "termini e condizioni",
        "tutti i diritti riservati", "pubblicità", "sponsorizzato",
        "articoli correlati", "condividi", "seguici", "leggi anche",
        "indice dei contenuti", "modifica pagina", "visualizza sorgente",
    ]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        lower = stripped.lower()
        if any(p in lower for p in skip_patterns):
            continue
        if len(stripped) < 3 and not stripped.startswith("#"):
            continue
        cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def main():
    if not SERPER_API_KEY:
        print("Error: SERPER_API_KEY environment variable is not set.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print('Usage: python3 main.py "search query"')
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    width = 70

    print()
    print("╔" + "═" * width + "╗")
    print("║" + " SERPER + JINA SEARCH".center(width) + "║")
    print("╠" + "═" * width + "╣")
    print("║" + f" Query: {query}".ljust(width) + "║")
    print("╚" + "═" * width + "╝")
    print()

    results = serper_search(query)
    if not results:
        print("  No results found.")
        return

    print(f"  Found {len(results)} result(s)\n")

    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("link", "")
        snippet = r.get("snippet", "")

        print("  ┌" + "─" * (width - 4) + "┐")
        print(f"  │  [{i}/{len(results)}]  {title}".ljust(width - 2) + "│")
        print("  ├" + "─" * (width - 4) + "┤")
        print(f"  │  URL: {url}".ljust(width - 2) + "│")
        print("  ├" + "─" * (width - 4) + "┤")

        snippet_lines = [snippet[j:j:width - 8] for j in range(0, len(snippet), width - 8)] or [""]
        for line in snippet_lines:
            print(f"  │    {line}".ljust(width - 2) + "│")

        print("  ├" + "─" * (width - 4) + "┤")

        content = jina_read(url)
        if not content.startswith("["):
            content = clean_content(content)
        preview = content[:1500]

        content_lines = preview.split("\n")
        shown = 0
        for line in content_lines:
            if shown >= 15:
                remaining = len(content_lines) - shown
                print(f"  │    ... ({remaining} more lines)".ljust(width - 2) + "│")
                break
            if not line.strip():
                continue
            truncated = line[:width - 8]
            print(f"  │    {truncated}".ljust(width - 2) + "│")
            shown += 1

        print("  └" + "─" * (width - 4) + "┘")
        print()

    print("─" * width)
    print(f"  Done — {len(results)} result(s) for: {query}")
    print("─" * width)
    print()


if __name__ == "__main__":
    main()

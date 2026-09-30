#!/usr/bin/env python3
"""Render ai-news.json (from Serper News) into a pretty HTML page, a markdown
digest, and a colourised terminal digest.

Usage:  python3 format_news.py [stories.json] [--top N]
"""

import html
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- palette ----
INK = "\033[38;5;255m"
DIM = "\033[38;5;243m"
BOLD = "\033[1m"
RESET = "\033[0m"


def hex_to_ansi(hex_color: str) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return f"\033[38;2;{r};{g};{b}m"


def visible_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def rule(width: int, left: str = "╭", right: str = "╮") -> str:
    return f"{DIM}{left}{'─' * width}{right}{RESET}"


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ data ----
def load(path: str) -> list[dict]:
    with open(path) as fh:
        return json.load(fh)


def tier(item: dict, index: int) -> str:
    """lead / brief based on how fresh and how central the story is."""
    return "lead" if index < 6 else "brief"


# ------------------------------------------------------------------ html ----
def render_html(items: list[dict], top: int, generated: str) -> str:
    esc = html.escape
    counts: dict[str, int] = {}
    for it in items:
        counts[it["cat"]] = counts.get(it["cat"], 0) + 1

    legend = "".join(
        f'<span class="chip" style="--c:{c}">{esc(k)} <b>{v}</b></span>'
        for k, c, v in sorted({i["cat"]: (i["color"], 0) for i in items}.keys())
        and [
            (k, next(i["color"] for i in items if i["cat"] == k), counts[k])
            for k in counts
        ]
    )

    leads, briefs = [], []
    for idx, it in enumerate(items):
        card = f"""
      <article class="card {tier(it, idx)}" style="--c:{it['color']}">
        <div class="rank">{idx + 1:02d}</div>
        <span class="tag">{esc(it['cat'])}</span>
        <h3><a href="{esc(it['link'])}" target="_blank" rel="noopener">{esc(it['title'])}</a></h3>
        <p>{esc(it['snippet'])}</p>
        <footer>
          <span class="src">{esc(it['source'] or 'unknown')}</span>
          <span class="dot">&middot;</span>
          <span class="when">{esc(it['date'] or 'undated')}</span>
          <a class="go" href="{esc(it['link'])}" target="_blank" rel="noopener">read &rarr;</a>
        </footer>
      </article>"""
        (leads if tier(it, idx) == "lead" else briefs).append(card)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Briefing &middot; last 7 days</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Inter:wght@400;500;600&display=swap');
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 0 80px; color: #e6e8ef;
    background: #08090d;
    font-family: Inter, system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  body::before {{
    content: ""; position: fixed; inset: 0; z-index: -1;
    background:
      radial-gradient(900px 500px at 12% -8%, rgba(122,162,247,.22), transparent 60%),
      radial-gradient(800px 480px at 88% 0%, rgba(244,162,97,.16), transparent 62%),
      radial-gradient(700px 600px at 50% 110%, rgba(158,206,106,.12), transparent 60%);
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 0 28px; }}
  header {{ padding: 76px 0 34px; border-bottom: 1px solid rgba(255,255,255,.08); }}
  .kicker {{
    font: 600 11px/1 Inter, sans-serif; letter-spacing: .28em; text-transform: uppercase;
    color: #7aa2f7;
  }}
  h1 {{
    font: 700 clamp(38px, 7vw, 74px)/1.02 "Space Grotesk", sans-serif;
    margin: 16px 0 0; letter-spacing: -.03em;
    background: linear-gradient(100deg, #fff 10%, #7aa2f7 45%, #f4a261 90%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .sub {{ color: #9aa1b2; margin: 18px 0 0; max-width: 60ch; font-size: 16px; line-height: 1.6; }}
  .meta {{ margin-top: 26px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
  .chip {{
    display: inline-flex; align-items: center; gap: 7px; padding: 5px 12px;
    border-radius: 999px; font: 500 12px/1 Inter, sans-serif;
    color: var(--c); border: 1px solid color-mix(in srgb, var(--c) 35%, transparent);
    background: color-mix(in srgb, var(--c) 10%, transparent);
  }}
  .chip b {{ color: #fff; font-weight: 600; }}
  h2 {{
    font: 600 13px/1 Inter, sans-serif; letter-spacing: .22em; text-transform: uppercase;
    color: #6b7280; margin: 54px 0 18px; display: flex; align-items: center; gap: 14px;
  }}
  h2::after {{ content: ""; flex: 1; height: 1px; background: linear-gradient(90deg, rgba(255,255,255,.12), transparent); }}
  .grid {{ display: grid; gap: 18px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }}
  .card {{
    position: relative; overflow: hidden; padding: 22px 22px 18px;
    border-radius: 16px; border: 1px solid rgba(255,255,255,.07);
    background: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.015));
    transition: transform .22s ease, border-color .22s ease, box-shadow .22s ease;
  }}
  .card::before {{
    content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--c);
  }}
  .card:hover {{
    transform: translateY(-4px); border-color: color-mix(in srgb, var(--c) 45%, transparent);
    box-shadow: 0 18px 40px -22px color-mix(in srgb, var(--c) 60%, transparent);
  }}
  .rank {{
    position: absolute; top: 16px; right: 20px; font: 700 26px/1 "Space Grotesk", sans-serif;
    color: rgba(255,255,255,.07);
  }}
  .tag {{
    display: inline-block; margin-bottom: 12px; padding: 3px 9px; border-radius: 6px;
    font: 600 10px/1.5 Inter, sans-serif; letter-spacing: .12em; text-transform: uppercase;
    color: var(--c); background: color-mix(in srgb, var(--c) 13%, transparent);
  }}
  .card h3 {{
    font: 600 18px/1.28 "Space Grotesk", sans-serif; margin: 0 44px 10px 0;
    letter-spacing: -.01em; padding-right: 4px;
  }}
  .card h3 a {{ color: #fff; text-decoration: none; }}
  .card h3 a:hover {{ color: var(--c); }}
  .card p {{ margin: 0 0 16px; color: #9aa1b2; font-size: 14px; line-height: 1.58; }}
  .card footer {{ display: flex; align-items: center; gap: 8px; font-size: 12px; color: #6b7280; }}
  .src {{ color: #cfd4e0; font-weight: 500; }}
  .go {{ margin-left: auto; color: var(--c); text-decoration: none; font-weight: 600; }}
  .go:hover {{ filter: brightness(1.25); }}
  .card.lead h3 {{ font-size: 21px; }}
  .card.lead {{ grid-column: span 2; background: linear-gradient(135deg, color-mix(in srgb, var(--c) 11%, transparent), rgba(255,255,255,.015) 60%); }}
  .card.lead p {{ font-size: 15px; }}
  @media (max-width: 720px) {{ .card.lead {{ grid-column: span 1; }} }}
  footer.page {{
    margin-top: 60px; color: #4b5563; font-size: 12px; text-align: center; line-height: 1.8;
  }}
  footer.page code {{ color: #7aa2f7; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="kicker">Signal &middot; week of {generated}</div>
      <h1>AI Briefing</h1>
      <p class="sub">{len(items)} stories across the last 7 days, pulled from Google News
         via Serper and deduped, newest first, grouped by beat. Chips show the mix.</p>
      <div class="meta">{legend}</div>
    </header>

    <h2>Lead stories</h2>
    <div class="grid">{"".join(leads)}</div>

    <h2>Everything else</h2>
    <div class="grid">{"".join(briefs)}</div>

    <footer class="page">
      generated {generated} &middot; source <code>serper.dev/news?tbs=qdr:w</code>
      &middot; no key material in this file
    </footer>
  </div>
</body>
</html>
"""


# -------------------------------------------------------------- markdown ----
def render_md(items: list[dict], top: int, generated: str) -> str:
    out = [
        f"# AI Briefing - week of {generated}",
        "",
        f"_{len(items)} stories, last 7 days. Newest first._",
        "",
    ]
    for idx, it in enumerate(items):
        mark = "**" if idx < top else "-"
        out.append(f"{mark} [{it['title']}]({it['link']})  ")
        out.append(f"  {it['source'] or 'unknown'} | {it['date']} | _{it['cat']}_  ")
        out.append(f"  {it['snippet']}\n")
    return "\n".join(out)


# --------------------------------------------------------------- terminal ----
def render_term(items: list[dict], top: int, generated: str) -> str:
    w = 74
    lines = [rule(w), f"{BOLD}{INK}  AI BRIEFING{RESET}  {DIM}week of {generated}{RESET}", rule(w)]
    inner = w - 2
    for idx, it in enumerate(items):
        c = hex_to_ansi(it["color"])
        lines.append("")
        head = f"  {c}{BOLD}{idx + 1:02d}{RESET}  {INK}{BOLD}{it['title'][:inner - 8]}{RESET}"
        lines.append(head)
        tag = f"  {c}{it['cat'].upper():<17}{RESET}"
        lines.append(f"{tag} {DIM}{it['source'] or 'unknown'} - {it['date'] or 'undated'}{RESET}")
        for ln in wrap(it["snippet"], inner - 4)[:3]:
            lines.append(f"     {DIM}{ln}{RESET}")
        lines.append(f"     {DIM}{it['link'][:inner - 4]}{RESET}")
    lines.append("")
    lines.append(rule(w, "╰", "╯"))
    return "\n".join(lines)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 6
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
        args = [a for a in args if a != str(top)]
    path = args[0] if args else os.path.join(HERE, "ai-news.json")

    items = load(path)
    generated = datetime.now().strftime("%d %b %Y")

    html_path = os.path.join(HERE, "ai-news.html")
    md_path = os.path.join(HERE, "ai-news.md")
    with open(html_path, "w") as fh:
        fh.write(render_html(items, top, generated))
    with open(md_path, "w") as fh:
        fh.write(render_md(items, top, generated))

    print(render_term(items, top, generated))
    print(f"\n  wrote {html_path}\n  wrote {md_path}\n")


if __name__ == "__main__":
    main()

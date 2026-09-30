# search

Small CLI scripts for web search via third-party APIs.

## Scripts

### `main.py` — Serper + Jina search

Searches with the [Serper](https://serper.dev) API and fetches readable
page content for each result via [Jina Reader](https://jina.ai/reader/).

```bash
export SERPER_API_KEY="your-serper-api-key"
python3 main.py "your search query"
```

| Flag | Effect |
| --- | --- |
| `--news` | use Serper's News endpoint instead of web search (adds `Via: <source> · <date>`) |
| `--days N` | restrict to the last `N` days — `1` → `qdr:d`, `7` → `qdr:w`, `30` → `qdr:m` |
| `--num N` | number of results (default 10) |

```bash
python3 main.py --news --days 7 "artificial intelligence"
```

### `collect_news.py` — balanced collection

A single generic sweep skews hard towards model launches: one area can swallow
a third of the page while another gets two cards. This walks a per-area query
list instead, stopping early for areas that already have enough coverage and
spending extra queries only on the ones that are still short.

```bash
python3 collect_news.py --target 20        # 20 stories per area
python3 collect_news.py --target 25 --max-credits 60
```

Writes `ai-news.json`, and prints a per-area count with `OK`/`LOW` so an
unbalanced run is visible immediately.

### `format_news.py` — digest renderer

Reads a stories JSON file and writes three views: a styled HTML page, a
markdown digest, and a colourised terminal digest.

```bash
python3 format_news.py --lang it --intro "..." [--main 4] [--more 6] [--folded]
```

| Flag | Effect |
| --- | --- |
| `--lang it\|en` | output language, including relative dates (`2 ore fa`) |
| `--intro` | lead paragraph under the title |
| `--main N` | lead cards shown per area (default 4) |
| `--more N` | secondary cards shown per area (default 6) |
| `--sim F` | same-event merge threshold (default 0.62) |
| `--folded` | list every merge, with its folded titles - check the clustering |
| `--translate` | translate titles and snippets into Italian (see below) |
| `--model M` | model used for translation (default `openai/gpt-4o-mini`) |
| `--hero N` | banner image: `earth` (default), `circuit`, `code`, `robot`, `laptop`, `off`, or any URL |

Grouped into five thematic areas (Politica e sicurezza, Modelli e ricerca,
Chip e infrastrutture, Business, Settore), each with its own accent colour and
a note explaining what belongs there.

### How stories get unified

Two headlines about the same event become one card with several sources. Two
rules decide it:

- **token containment** for the same story told twice;
- **shared rare entities** for the case containment misses. The NYT writes
  *"DeepSeek and Huawei Target a Key Source of Nvidia's A.I. Dominance"*, the
  wire writes *"DeepSeek partners with Huawei to develop chip programming
  tools"* - two words overlap and nothing else, but both name the same
  companies.

An entity is a term that is ALLCAPS (`HBM`) or PascalCase (`DeepSeek`) in the
headline, or capitalised inside one and never written in lowercase inside one.
That last part is what stops `'faces'` and `'lawsuit'` - ordinary words that
are merely rare in a few hundred headlines - from merging two unrelated
lawsuits.

A headline reduced to one meaningful word (`AI and education do not mix` keeps
only *education*) would otherwise score 1.00 against anything else mentioning
education, so below three tokens and two shared words the score falls back to
Jaccard, which a short title cannot game.

Run `--folded` to see every merge and its sources before trusting it.

### The hero image

A 236px banner from Unsplash, hotlinked free, masked so it dissolves into the
page background instead of ending on a hard edge, with the credit bottom
right. Five images are catalogued and each was verified to return HTTP 200:

```bash
python3 format_news.py --hero earth      # la Terra di notte (default)
python3 format_news.py --hero circuit    # un circuito in vetro
python3 format_news.py --hero off        # nessuna immagine
python3 format_news.py --hero https://example.com/photo.jpg
```

The figure carries its own gradient background, so a blocked or dead image
degrades to the plain header rather than a broken-icon box. Attribution for
these reads "Unsplash Contributor" rather than a named photographer, so the
page credits Unsplash and links out instead of guessing a name.

### Italian titles and snippets

The page chrome is Italian out of the box; headlines and snippets come from
the sources in English. `--translate` sends them through OpenRouter
(`openai/gpt-4o-mini`, about $0.03 for 130 stories) and keeps proper nouns
untouched, so *OpenAI*, *DeepSeek* and *Hugging Face* stay in English inside
Italian sentences.

Translation runs **after** clustering, never before: the same-event matcher
reads the English titles, and a translated headline loses both the proper
nouns and the word overlap it depends on.

Results are cached in `ai-news.it.json`, keyed by a hash of the English source,
so only genuinely new stories cost anything. A second run over an unchanged
corpus takes 0.3s and spends nothing. The original English title stays in the
`title` attribute of every link, so hovering a headline shows the source text.

Display is capped per area (`--main` + `--more`) so every section comes out
the same size whatever the raw counts; the badge and the `+N` note still
report the true totals. Lead cards are ranked by a freshness / breadth /
headline-weight heuristic.

### `demo.py` — Tavily search demo

Minimal example of querying the [Tavily](https://tavily.com) search API.

```bash
export TAVILY_API_KEY="your-tavily-api-key"
python3 demo.py
```

## Setup

```bash
pip install requests
```

Both scripts read their API key from an environment variable — no keys are
stored in the code.

## Keys

Keep real keys in a `.env` file one level up (already gitignored) and load
them into the environment:

```bash
set -a && . ../.env && set +a
python3 main.py --news --days 7 "AI"
```

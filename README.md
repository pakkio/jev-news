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
python3 format_news.py --lang it --intro "..." [--main 4] [--more 6]
```

| Flag | Effect |
| --- | --- |
| `--lang it\|en` | output language, including relative dates (`2 ore fa`) |
| `--intro` | lead paragraph under the title |
| `--main N` | lead cards shown per area (default 4) |
| `--more N` | secondary cards shown per area (default 6) |

Grouped into five thematic areas (Politica e sicurezza, Modelli e ricerca,
Chip e infrastrutture, Business, Settore), each with its own accent colour and
a note explaining what belongs there. Near-duplicate coverage of one event is
clustered into a single card with several sources.

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

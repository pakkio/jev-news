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

### `format_news.py` — news digest renderer

Reads a stories JSON file and writes three views: a styled HTML page, a
markdown digest, and a colourised terminal digest. Works with the JSON that
a `--news` sweep produces.

```bash
python3 main.py --news --days 7 "AI" > ai-news.json   # collect
python3 format_news.py --top 6 ai-news.json            # render
```

Output: `ai-news.html`, `ai-news.md`, plus the digest on stdout. Stories are
sorted newest-first and tagged into five beats (Policy & Safety, Models &
Research, Business & Deals, Chips & Compute, Industry), each with its own
accent colour.

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

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

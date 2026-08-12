# 🕸️ Web Scraping AI Agent

Describe what you want from a web page in plain English. The agent fetches the page, works out a schema for your request, and hands back structured records — JSON or CSV, no selectors, no XPath, no per-site code.

```bash
$ scrape-agent https://books.toscrape.com "every book with its title, price and stock availability" \
      --provider ollama --model qwen2.5:3b --csv books.csv

  · Fetching https://books.toscrape.com
  · Cleaning HTML into markdown
  · Designing extraction schema
  · Schema: book (many) -> title:string, price:number, stock_availability:string
  · Extracting from section 1/1
wrote books.csv

20 record(s) · ollama/qwen2.5:3b · 2 call(s) · 5130 tokens · 23.5s
title                                     price  stock_availability
----------------------------------------  -----  ------------------
A Light in the Attic                      51.77  In stock
Tipping the Velvet                        53.74  In stock
Soumission                                50.1   In stock
Sharp Objects                             47.82  In stock
Sapiens: A Brief History of Humankind     54.23  In stock
…
```

No selectors were written for that page. Point it at a different site with a different sentence and it works the same way.

Inspired by the [Web Scraping AI Agent](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/starter_ai_agents/web_scraping_ai_agent) in [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps), rebuilt as a standalone package with its own extraction pipeline (no ScrapeGraphAI dependency), a CLI, a local-model option and a test suite.

---

## Why not just paste the HTML into an LLM?

Because raw HTML is ~85% markup. The agent runs a pipeline instead:

```
URL
 │
 ├─ fetch ......... plain HTTP; escalates to headless Chromium only when the
 │                  page comes back as an empty JavaScript shell
 ├─ clean ......... HTML → markdown, keeping headings, lists, tables and link
 │                  targets, resolving every URL to absolute
 ├─ plan .......... one LLM call turns your sentence into a typed JSON Schema
 │                  ("product: name:string, price:number, url:string")
 ├─ extract ....... page split at block boundaries, each section extracted
 │                  against that schema with structured-output decoding
 └─ merge ......... sections recombined, overlap duplicates dropped, every
                    record given identical keys
     │
     └─ JSON / CSV / DataFrame
```

Measured on live pages, with `qwen2.5:3b` running locally on an M-series Mac:

| Page | Raw HTML | Cleaned markdown | LLM calls | Result |
|---|---|---|---|---|
| books.toscrape.com | 51,274 chars | 7,539 chars (15%) | 2 | 20/20 books, 23s |
| news.ycombinator.com | 34,791 chars | 17,125 chars (49%) | 4 | 24 stories + 1 junk row, 50s |

Hacker News compresses less because it is built from nested layout tables and is almost entirely links. Those tables are detected and unwrapped rather than rendered as markdown grids — without that, the same page cleaned to 33,058 chars (95% of raw) and cost twice the tokens.

The schema step is what keeps results stable: every record has the same keys in the same order, so chunk 7 of a long page cannot invent a different shape from chunk 1, and CSV columns always line up.

---

## Install

```bash
git clone https://github.com/muhammad-sheri/web-scraping-ai-agent.git
cd web-scraping-ai-agent

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then add your key, or switch to a local model
```

Optional, for JavaScript-heavy pages:

```bash
pip install playwright && playwright install chromium
```

---

## Choosing a model

**The OpenAI API is pay-as-you-go and is *not* the free ChatGPT tier.** A key with no credit returns a quota error. In practice this agent costs roughly **$0.001–0.01 per page** with `gpt-4o-mini`, because the cleaning step keeps the token count small — but it is cents, not free.

If you want genuinely free, run a local model with [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:3b        # ~1.9GB, decent at structured JSON
ollama pull qwen2.5:7b        # ~4.7GB, noticeably more accurate

scrape-agent example.com "product names and prices" --provider ollama --model qwen2.5:3b
```

| | `--provider openai` | `--provider ollama` |
|---|---|---|
| Cost | ~$0.001–0.01 per page | free |
| Needs a key | yes | no |
| Data leaves your machine | yes | no |
| Accuracy on messy pages | high | good, model-dependent |
| Speed | seconds | seconds to minutes on CPU |

Set your default once in `.env` with `SCRAPER_PROVIDER=ollama`.

---

## Usage

### CLI

```bash
# basics
scrape-agent https://news.ycombinator.com "top story titles, points and links"

# save results
scrape-agent example.com/pricing "plan names and monthly prices" --csv plans.csv --json run.json

# skip schema inference and force exact column names (saves one LLM call)
scrape-agent example.com/jobs "open roles" --fields title,location,url

# a React/Vue page that renders client-side
scrape-agent app.example.com/listings "listing titles and prices" --render always

# free, local, offline
scrape-agent example.com "product names" --provider ollama --model qwen2.5:3b
```

Useful flags:

| Flag | What it does |
|---|---|
| `--provider {openai,ollama}` | Pick the backend |
| `--model NAME` | Model for that backend |
| `--render {auto,always,never}` | Headless browser policy (default `auto`) |
| `--fields a,b,c` | Fixed field names; skips the planning call |
| `--csv PATH` / `--json PATH` | Write results to disk |
| `--stdout-json` | Print JSON instead of a table |
| `--max-chunks N` | Hard cap on LLM calls per page |
| `--keep-boilerplate` | Keep nav/header/footer when your data lives there |
| `--ignore-robots` | Skip the robots.txt check |
| `--dump-markdown PATH` | Save exactly what the model was shown — the first thing to look at when results are wrong |

Exit codes: `0` records found, `3` none found, `1` fetch/provider error, `2` bad usage.

### Streamlit app

```bash
streamlit run app.py
```

URL and prompt at the top, provider/model/rendering in the sidebar, results as a sortable table with JSON and CSV download buttons. Two expanders show the schema the agent designed and the exact cleaned page text it read — which is how you debug a bad extraction.

### Python

```python
from scraper_agent import ScrapeAgent
from scraper_agent.providers import get_provider

agent = ScrapeAgent(provider=get_provider("openai", "gpt-4o-mini"))
result = agent.run("https://books.toscrape.com", "every book title and price")

print(result.count)     # 20
print(result.records)   # [{'title': 'A Light in the Attic', 'price': 51.77, ...}, ...]
print(result.usage)     # {'calls': 2, 'prompt_tokens': ..., 'total_tokens': 5130}
print(result.cost_usd)  # estimated USD for OpenAI; None for local models
```

Or the one-liner: `from scraper_agent.agent import scrape; scrape(url, prompt)`.

---

## Configuration

Everything is env-driven (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPER_PROVIDER` | `openai` | Default backend |
| `OPENAI_API_KEY` | — | Required for the OpenAI backend |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `llama3.2` / `localhost:11434` | Local backend |
| `MAX_CHUNK_CHARS` | `12000` | Page characters per LLM call (~4 chars/token) |
| `MAX_CHUNKS` | `12` | Ceiling on calls per page, so one huge page can't run up a bill |
| `RESPECT_ROBOTS` | `true` | Check robots.txt before fetching |
| `POLITENESS_DELAY` | `0.5` | Seconds between requests |

---

## Tests

```bash
pytest
```

91 tests, all offline — no network, no API keys, no cost. They cover the HTML→markdown converter (including the layout-table handling that Hacker News-style pages need), chunking and overlap, tolerant JSON parsing of model output, schema generation under OpenAI strict mode, record merging, output writers, and the full agent loop against a stubbed provider.

Several are regression tests for bugs found by running the thing against real sites rather than by reading the code — the planner guessing "one record" on a 20-item listing and truncating away 19 rows, and markdown link syntax leaking into extracted values.

---

## Limitations

- **One page per run.** No crawling or pagination yet; call it per URL.
- **The model can still be wrong.** It only ever sees text that was genuinely on the page and is told to return `null` rather than guess, but extraction from ambiguous layouts is not perfect. Spot-check before trusting a dataset.
- **Small local models produce some junk rows.** On the Hacker News run above, `qwen2.5:3b` also returned the site's own nav links as if they were stories. Larger models (`qwen2.5:7b`, `gpt-4o-mini`) don't. Filtering rows where every field but one is `null` clears most of it.
- **Small local models struggle with wide schemas.** Under ~7B parameters, keep to a handful of fields.
- **Login-walled and aggressively bot-protected pages** are out of scope.

## Responsible use

`robots.txt` is respected by default and requests are rate-limited. `--ignore-robots` exists for pages you own or are authorised to scrape — check the site's terms and applicable law before using it, and don't collect personal data you have no basis to hold.

## License

MIT

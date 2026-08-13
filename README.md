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

Three things make it more than a demo:

- **It knows when not to use AI.** For Shopify stores, `--all-products` pulls the **complete** catalogue — every variant, exact SKUs and prices — from the store's own API, with no LLM and no cost. → [E-commerce](#e-commerce-the-complete-catalogue-exactly)
- **It measures its own accuracy.** Shopify stores double as a free answer key, so extraction quality is a number, not a claim. → [The numbers](#does-it-actually-work-here-are-the-numbers)
- **It plugs into Claude.** An MCP server exposes all of it, including the accuracy check, as tools. → [MCP](#use-it-from-claude-mcp)

Inspired by the [Web Scraping AI Agent](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/starter_ai_agents/web_scraping_ai_agent) in [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps), rebuilt as a standalone package with its own extraction pipeline (no ScrapeGraphAI dependency), a CLI, a local-model option, an eval harness and a test suite.

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

## E-commerce: the complete catalogue, exactly

For online stores, AI extraction is usually the *wrong* tool — and the agent will tell you so.

Every **Shopify** storefront publishes its own catalogue as JSON at `/products.json`. That is a public endpoint by design: it is what the store's own JavaScript reads. Going there beats reading the rendered page on every axis that matters.

```bash
scrape-agent https://www.allbirds.com --all-products --csv catalogue.csv
```

|  | Reading the page with an LLM | `--all-products` |
|---|---|---|
| Coverage | the products on page 1 | the **entire** catalogue, paginated |
| Prices | whatever the page displayed | exact, plus `compare_at_price` (the discount) |
| Variants | usually missed | **every** size/colour, each with its own SKU and stock flag |
| Accuracy | inferred, can misread | the store's own records |
| Cost | tokens per page | **zero** — no LLM at all |
| Speed | ~25s per page | 291 products in ~2 seconds |

Columns you get: `product_id, title, url, vendor, product_type, tags, published_at, description, variant_title, sku, price, compare_at_price, available, grams, image`.

Scope it to one collection, or cap it while testing:

```bash
scrape-agent https://store.com/collections/mens --all-products --csv mens.csv
scrape-agent https://store.com --all-products --max-products 50
```

No key and no model are needed for this mode. If the URL is not a Shopify store, it says so and points you at the prompt-based route. And when you run a normal scrape against a store that *is* Shopify, the agent notices and suggests the better command.

### Non-Shopify stores

Use a prompt and follow the pagination:

```bash
scrape-agent https://shop.example.com/category "product name, price and link" \
    --all-pages --max-pages 10 --csv products.csv
```

It follows `rel="next"`, "Next" links and arrow glyphs, staying on the same site and refusing to revisit a page. The schema is designed once on page 1 and reused, so the columns stay identical across every page.

---

## Does it actually work? Here are the numbers

Most scraping agents ask you to take their accuracy on faith. This one measures itself, because Shopify stores hand out a free answer key: the store publishes its own records at `/products.json`, and renders those same products as HTML. So the store supplies both the question and the correct answer — **no hand-labelling, and every Shopify store on the internet is a fresh test case.**

```bash
scrape-agent-eval https://www.allbirds.com/collections/mens --model ollama:qwen2.5:3b
```

Three real Shopify stores, two local models, prompt held constant at *"every product on this page with its title and price"*:

| Store | Model | On page | Found | Recall | Precision | Hallucinated | Price accuracy | Invented numbers | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| allbirds.com | `qwen2.5:3b` | 35 | 31 | 89% | 89% | 11% | 100% | 0 | 40s |
| allbirds.com | `qwen2.5:7b` | 35 | 33 | **94%** | **97%** | 3% | 100% | 0 | 88s |
| deathwishcoffee.com | `qwen2.5:3b` | 27 | 24 | 89% | 100% | 0% | 100% | 0 | 39s |
| deathwishcoffee.com | `qwen2.5:7b` | 27 | 24 | 89% | 100% | 0% | 100% | 0 | 58s |
| drinkolipop.com | `qwen2.5:3b` | 43 | 26 | 60%* | 100% | 0% | n/a | 34 → 0 | 29s |
| drinkolipop.com | `qwen2.5:7b` | 43 | 26 | 60%* | 100% | 0% | n/a | 10 → 0 | 68s |

Raw results: [`evals/results/`](evals/results/). Doubling model size bought ~5 points of recall and 8 of precision on Allbirds, for roughly 2× the time.

\* Olipop's 43 products share only 27 distinct names — the same flavour ships as a single can, a 4-pack and a 12-pack. Title-based matching therefore tops out at 63%, and 60% is essentially that ceiling. A known limit of the eval, not a model failure.

### What the benchmark caught: silent price fabrication

Olipop's collection page **displays no prices at all** — not one `$nn.nn` in the content the model receives. The real price is $35.99. The model returned **$12.99 for all 34 products**: uniform, confident, invented, despite a prompt that explicitly says to return null when a value is absent.

That is the failure that actually hurts. Not a crash, not an empty result — plausible wrong data that looks perfectly fine in a spreadsheet. **192 offline tests had no chance of catching it.** The eval found it on the third store.

### And then it was fixed

Instructions do not reliably stop a 3B model inventing values, so the fix is a check rather than a plea. Numbers are the one field type where grounding is decidable: every number in the source text is parsed once, and any numeric answer that is not among them is replaced with `null` and counted ([`grounding.py`](scraper_agent/grounding.py)). Text is deliberately exempt — models legitimately tidy titles, and substring-checking prose would delete correct data.

The `Invented numbers` column above is that guard firing: 34 fabricated prices on Olipop, now zero reaching the output.

Two things had to be verified before trusting it:

**It does not damage good data.** Allbirds and Death Wish both display real prices — they stayed at 100% price accuracy with 0 removals. The guard is silent when the data is honest.

**It does not lose products.** Recall on Olipop appears to drop from 79% to 60%, which looked alarming. It is the opposite. Without the guard the model emitted 34 rows containing only **26 unique titles**; the 8 duplicates survived deduplication solely because each copy carried a *different* invented price. Those duplicates were then matching separate same-named truth records, so **the hallucination was inflating the recall score**. With the guard: 26 rows, 26 unique titles, no product lost, and an honest number.

A metric that improves when the model lies is a broken metric. Finding that was worth more than the fix.

**How the scoring works.** The `/products/<handle>` links in the page define which products were genuinely visible; the API then supplies their true titles and prices. Extracted records are matched to truth by normalised title (exact → contiguous containment → fuzzy ratio ≥ 0.85), each truth row consumed at most once. Metrics: **recall** (of the products on the page, how many were found), **precision**, **hallucination rate** (records matching no real product), and **price accuracy** (within 1%).

### The eval had a bug, and that is the point

The first version accepted a `--max-products` flag to keep slow local runs short. It capped the *truth set* but the model still read the *whole page*, so every correct product past the cap scored as a false positive. It reported **59% precision** for output that was essentially right — the "hallucinations" turned out to be real Allbirds colourways.

Uncapped, the same model and page scored **94%**.

An eval that is silently wrong is worse than no eval, so the option was removed rather than documented, and two regression tests now assert it cannot come back. This is the whole reason to build evals: the failure was in the measurement, and nothing but a measurement would have found it.

---

## Use it from Claude (MCP)

The agent runs as an [MCP](https://modelcontextprotocol.io) server, so Claude Desktop, Claude Code, or any MCP client can drive it directly.

```bash
pip install -e ".[mcp]"     # or: pip install fastmcp
```

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "web-scraping-ai-agent": {
      "command": "/absolute/path/to/web-scraping-ai-agent/.venv/bin/scrape-agent-mcp"
    }
  }
}
```

For Claude Code: `claude mcp add web-scraping-ai-agent -- /absolute/path/to/.venv/bin/scrape-agent-mcp`

Then just ask. Four tools are exposed:

| Tool | What it does |
|---|---|
| `check_store` | Is this Shopify? Routes you to the right tool before any tokens are spent |
| `scrape_page` | Natural-language extraction, optionally across paginated listings |
| `shopify_catalogue` | The complete, exact catalogue — no LLM, no cost |
| `evaluate_extraction` | **Reports the agent's own measured accuracy on a store** |

That last tool is the unusual one: you can ask Claude *"how accurate are you on this store?"* and get real measured numbers back, not a guess.

**On context discipline:** a catalogue can run to thousands of rows, and dumping those into a client's context is the classic agent failure. Every tool caps its inline payload, sets a `truncated` flag, writes the full dataset to disk and returns the path — a readable sample plus a pointer, never a wall of JSON. No API key is needed either: the server defaults to local Ollama unless an OpenAI key is actually present.

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
| `--all-products` | Shopify: complete catalogue from the store's JSON API, no LLM |
| `--max-products N` | Cap products in catalogue mode |
| `--all-pages` | Follow "next" links instead of stopping at page 1 |
| `--max-pages N` | Page limit for `--all-pages` (default 5) |
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

### Measuring accuracy

```bash
# one store, one model
scrape-agent-eval https://www.allbirds.com/collections/mens

# compare models on several stores, save the results
scrape-agent-eval https://store-a.com/collections/all https://store-b.com/collections/all \
    --model ollama:qwen2.5:3b --model ollama:qwen2.5:7b \
    --json evals/results/run.json --markdown evals/results/run.md
```

The prompt is held constant across every run, because comparing models only means something if they answered the same question. Any Shopify collection page works as a benchmark.

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
| `VERIFY_GROUNDING` | `true` | Null out numeric answers absent from the page text |
| `RESPECT_ROBOTS` | `true` | Check robots.txt before fetching |
| `POLITENESS_DELAY` | `0.5` | Seconds between requests |

---

## Tests

```bash
pytest
```

205 tests, all offline — no network, no API keys, no cost. They cover the HTML→markdown converter (including the layout-table handling that Hacker News-style pages need), chunking and overlap, tolerant JSON parsing of model output, schema generation under OpenAI strict mode, record merging, output writers, Shopify pagination and flattening against a mocked transport, next-link detection, title matching and metric arithmetic against hand-computed fixtures, ground-truth scoping, the MCP tool surface via an in-memory client, and the full agent loop against a stubbed provider.

Several are regression tests for bugs found by running against real sites rather than by reading the code:

- the planner guessing "one record" on a 20-item listing and truncating away 19 rows
- markdown link syntax leaking into extracted values
- a next-link matcher that missed the very common `pagination__next` class, because `_` counts as a word character in `\b`
- apostrophes splitting `Men's` into two tokens, so `Mens` failed to match exactly
- **the eval capping its own ground truth and manufacturing false positives** (see above)
- fabricated prices surviving into output, now blocked by the grounding check

---

## Limitations

- **Some big retailers block scrapers outright.** Amazon is the obvious one. That is a fetching problem every scraper shares, not something the AI layer can solve. Most stores — including essentially all Shopify ones — are fine.
- **The model can still be wrong.** It only ever sees text that was genuinely on the page and is told to return `null` rather than guess, but extraction from ambiguous layouts is not perfect. The point of the benchmark above is that you do not have to guess how wrong — run it on a store like yours.
- **Small local models produce some junk rows.** On the Hacker News run above, `qwen2.5:3b` returned the site's own nav links as if they were stories. Filtering rows where every field but one is `null` clears most of it.
- **Small local models struggle with wide schemas.** Under ~7B parameters, keep to a handful of fields.
- **The benchmark only covers Shopify stores**, since that is where free ground truth exists. Accuracy on a news site or job board is not measured by it — treat the numbers as evidence about product-listing extraction specifically, not a universal score.
- **Login-walled and aggressively bot-protected pages** are out of scope.

## Responsible use

`robots.txt` is respected by default and requests are rate-limited. `--ignore-robots` exists for pages you own or are authorised to scrape — check the site's terms and applicable law before using it, and don't collect personal data you have no basis to hold.

## License

MIT

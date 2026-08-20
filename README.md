# 🕸️ Web Scraping AI Agent

Describe what you want from a web page in plain English. The agent fetches the page, works out a schema for your request, and hands back structured records as JSON or CSV. No selectors, no XPath, no per-site code.

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

Four things make it more than a demo:

- **It knows when not to use AI.** For Shopify stores, `--all-products` pulls the **complete** catalogue (every variant, exact SKUs and prices) from the store's own API, with no LLM and no cost. → [E-commerce](#e-commerce-the-complete-catalogue-exactly)
- **It measures its own accuracy.** Shopify stores double as a free answer key, so extraction quality is a number, not a claim. → [The numbers](#does-it-actually-work-here-are-the-numbers)
- **It tells you when it starts getting worse.** Extraction pipelines fail silently; this one watches itself on a schedule and exits non-zero when it drifts. → [Drift monitoring](#catching-drift-before-your-data-does)
- **It plugs into Claude.** An MCP server exposes all of it, including the accuracy check, as tools. → [MCP](#use-it-from-claude-mcp)

Inspired by the [Web Scraping AI Agent](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/main/starter_ai_agents/web_scraping_ai_agent) in [awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps), rebuilt as a standalone package with its own extraction pipeline (no ScrapeGraphAI dependency), a CLI, a local-model option, an eval harness and a test suite.

---

## Why not just paste the HTML into an LLM?

Because raw HTML is ~85% markup. The agent runs a pipeline instead:

```
URL
 │
 ├─ fetch ......... plain HTTP; escalates to a browser TLS fingerprint if the
 │                  site blocks it, then to headless Chromium if the HTML
 │                  comes back as an empty JavaScript shell
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

Hacker News compresses less because it is built from nested layout tables and is almost entirely links. Those tables are detected and unwrapped rather than rendered as markdown grids. Without that, the same page cleaned to 33,058 chars (95% of raw) and cost twice the tokens.

The schema step is what keeps results stable: every record has the same keys in the same order, so chunk 7 of a long page cannot invent a different shape from chunk 1, and CSV columns always line up.

---

## E-commerce: the complete catalogue, exactly

For online stores, AI extraction is usually the *wrong* tool, and the agent will tell you so.

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
| Cost | tokens per page | **zero**, no LLM at all |
| Speed | ~25s per page | 291 products in ~2 seconds |

Columns you get, covering every field the store's own API publishes:

`product_id, title, url, image, vendor, product_type, variant_title, option1, option2, option3, sku, variant_id, price, compare_at_price, discount_pct, available, grams, requires_shipping, taxable, position, options, tags, handle, image_count, published_at, created_at, updated_at, variant_updated_at, description`

Two of those are derived rather than copied. `discount_pct` is the real markdown off `compare_at_price`. Plenty of stores leave that field mirroring the live price instead of clearing it, so only a positive gap counts. `options` carries the store's *names* for its variant axes ("Color / Ring size"), which live at product level; without them `option1`/`option2` are anonymous values. The currency those prices are in is not in `/products.json` at all, so `fetch_store_meta()` reads it from `/meta.json`, and returns `{}` rather than failing when a store does not serve that endpoint.

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

Most scraping agents ask you to take their accuracy on faith. This one measures itself, because Shopify stores hand out a free answer key: the store publishes its own records at `/products.json`, and renders those same products as HTML. So the store supplies both the question and the correct answer. **No hand-labelling, and every Shopify store on the internet is a fresh test case.**

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

\* Olipop's 43 products share only 27 distinct names, because the same flavour ships as a single can, a 4-pack and a 12-pack. Title-based matching therefore tops out at 63%, and 60% is essentially that ceiling. A known limit of the eval, not a model failure.

### What the benchmark caught: silent price fabrication

Olipop's collection page **displays no prices at all**, not one `$nn.nn` in the content the model receives. The real price is $35.99. The model returned **$12.99 for all 34 products**: uniform, confident, invented, despite a prompt that explicitly says to return null when a value is absent.

That is the failure that actually hurts. Not a crash, not an empty result, but plausible wrong data that looks perfectly fine in a spreadsheet. **192 offline tests had no chance of catching it.** The eval found it on the third store.

### And then it was fixed

Instructions do not reliably stop a 3B model inventing values, so the fix is a check rather than a plea. Numbers are the one field type where grounding is decidable: every number in the source text is parsed once, and any numeric answer that is not among them is replaced with `null` and counted ([`grounding.py`](scraper_agent/grounding.py)). Text is deliberately exempt, because models legitimately tidy titles, and substring-checking prose would delete correct data.

The `Invented numbers` column above is that guard firing: 34 fabricated prices on Olipop, now zero reaching the output.

Two things had to be verified before trusting it:

**It does not damage good data.** Allbirds and Death Wish both display real prices, and they stayed at 100% price accuracy with 0 removals. The guard is silent when the data is honest.

**It does not lose products.** Recall on Olipop appears to drop from 79% to 60%, which looked alarming. It is the opposite. Without the guard the model emitted 34 rows containing only **26 unique titles**; the 8 duplicates survived deduplication solely because each copy carried a *different* invented price. Those duplicates were then matching separate same-named truth records, so **the hallucination was inflating the recall score**. With the guard: 26 rows, 26 unique titles, no product lost, and an honest number.

A metric that improves when the model lies is a broken metric. Finding that was worth more than the fix.

### Getting better accuracy

In order of how much they actually move the numbers:

1. **Use Shopify mode when the site is Shopify.** `--all-products` is exact: 100%, not 94%. It is not a model reading a page, it is the store's own database. Always prefer it.
2. **Use a bigger model.** Measured, same page, same prompt: `qwen2.5:3b` → `qwen2.5:7b` took Allbirds from 89%/89% to 94%/97% recall/precision, for ~2× the time. `gpt-4o-mini` is stronger again and costs about a cent a page.
3. **Name concrete fields.** `"every product with its name, price and link"` beats `"get me the data"` by a wide margin, because the schema step has something specific to build from.
4. **Keep to 3-4 fields** on small local models. Wide schemas are where sub-7B models fall apart.
5. **Leave grounding on.** It is on by default and it is why fabricated numbers no longer reach output.

And measure rather than guess: `scrape-agent-eval <any Shopify store>` gives you the current numbers for your own model and pages.

**How the scoring works.** The `/products/<handle>` links in the page define which products were genuinely visible; the API then supplies their true titles and prices. Extracted records are matched to truth by normalised title (exact → contiguous containment → fuzzy ratio ≥ 0.85), each truth row consumed at most once. Metrics: **recall** (of the products on the page, how many were found), **precision**, **hallucination rate** (records matching no real product), and **price accuracy** (within 1%).

### The eval had a bug, and that is the point

The first version accepted a `--max-products` flag to keep slow local runs short. It capped the *truth set* but the model still read the *whole page*, so every correct product past the cap scored as a false positive. It reported **59% precision** for output that was essentially right. The "hallucinations" turned out to be real Allbirds colourways.

Uncapped, the same model and page scored **94%**.

An eval that is silently wrong is worse than no eval, so the option was removed rather than documented, and two regression tests now assert it cannot come back. This is the whole reason to build evals: the failure was in the measurement, and nothing but a measurement would have found it.

---

## Catching drift before your data does

Extraction pipelines do not fail loudly. A site redesigns its markup, recall falls from 94% to 60%, and **nothing raises**: the scraper still returns rows, the rows are still well-formed, and the wrong numbers flow into pricing decisions and dashboards. Teams find out weeks later, because a human noticed a figure looked off.

Catching that needs ground truth, and ground truth normally means hand labelling, which nobody does nightly. So `scrape-agent-monitor` runs **two detectors over one extraction pass**:

| Detector | Needs ground truth? | Catches |
|---|---|---|
| **Truth-scored canaries** | yes, Shopify pages supply their own | the **extractor** regressing: model, prompt, parsing or fetch |
| **Signal drift** | no, it compares each page to its own history | the **site** changing under you |

A canary does not have to be a page you care about. It is there because it runs through the same fetch → clean → plan → extract path as everything else, so it reports on that shared path. That is what makes the alert actionable rather than merely alarming:

```bash
scrape-agent-monitor init > watchlist.json
scrape-agent-monitor run --config watchlist.json
```

Three clean passes, then a config change that quietly truncated the page (`MAX_CHUNKS=1`). A real run against real stores:

```
FAIL  canary  https://www.deathwishcoffee.com/collections/all
          ! records fell 87% (baseline 23, now 3)
          ! 'price' empty in 100% of records (baseline 0%)
          ! recall fell 88% (88% → 0%)
          ! precision fell 100% (100% → 0%)
FAIL          https://books.toscrape.com
          ! records fell 90% (baseline 20, now 2)
          ! 'price_excluding_tax' empty in 100% of records (baseline 0%)

verdict: extractor (high confidence)
  Canaries degraded alongside ordinary pages. The fault is in the shared extraction
  path (model, prompt, parsing or fetch), so treat every page as suspect,
  including those that scored clean.
```

It correctly blamed the extractor rather than the sites, because the canary fell too. Had the canaries held while one page dropped, the verdict would read `site`. With no canary in the watchlist at all, it says so instead of guessing.

**Signals tracked per page, none of which need an answer key:** record count, schema shape, per-field null rate, numeric medians, cleaned page size. Shape breaks; content churns. So a catalogue gaining and losing products is not a finding, while a `price` column going from 0% to 70% null always is.

Baselines are the **median** of recent runs, never the previous one, so a single flaky fetch cannot become tomorrow's reference. The current run is excluded from its own baseline, or a large enough regression would partly hide from the check meant to catch it.

Exit codes make it usable from cron or CI, where nobody reads a green run:

```bash
scrape-agent-monitor run --config watchlist.json --fail-on warn   # 0 clean · 1 warn · 2 alert
scrape-agent-monitor status --config watchlist.json               # recent history, runs nothing
```

`canary` is optional in the watchlist. Whether a page can be scored against ground truth is a fact about the page, not a decision you should have to research, so leaving it unset means "find out at run time".

**What it does not do:** measure a page whose ground truth does not exist. Nothing can. The canaries are how you find out whether the extractor is healthy on pages you *can* measure, so that a drop on the ones you cannot is attributable to the site rather than a mystery.

---

## Sites that block scrapers

Many sites return 403 to `httpx` and 200 to Chrome for a page their own robots.txt allows. The block is not about permission. It keys off the TLS/HTTP2 handshake fingerprint, which every Python HTTP client shares and no browser does.

[`curl_cffi`](https://github.com/lexiforest/curl_cffi) reproduces a real browser's handshake, so the fetcher escalates to it automatically:

```
1. httpx ............ fast, light, handles most of the web
2. curl_cffi ........ when the response looks like a block (403/429/503)
3. headless Chromium  when the HTML arrives as an empty JavaScript shell
```

Nothing escalates unless the page demands it, and **robots.txt is checked before any of it**.

```bash
pip install curl_cffi          # or: pip install -e ".[bot]"
```

Measured against real sites:

| Site | plain `httpx` | with `curl_cffi` |
|---|---:|---:|
| indeed.com | 403 | **200** ✅ |
| walmart.com | 200 | 200 |
| g2.com | 403 | 403 ❌ |
| zillow.com | 403 | 403 ❌ |
| amazon.com | 404 | 404 ❌ |

**It is not a universal bypass, and this README will not pretend otherwise.** TLS impersonation defeats fingerprint-based blocking (Indeed: a real win). It does not defeat JavaScript challenges, behavioural scoring or CAPTCHA, which is what G2, Zillow and Amazon use. For those, `--render always` sometimes helps; often nothing short of a commercial proxy network will.

Control it with `--impersonate auto|always|never` (default `auto`) or `IMPERSONATION` in `.env`.

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
| `shopify_catalogue` | The complete, exact catalogue, with no LLM and no cost |
| `evaluate_extraction` | **Reports the agent's own measured accuracy on a store** |

That last tool is the unusual one: you can ask Claude *"how accurate are you on this store?"* and get real measured numbers back, not a guess.

**On context discipline:** a catalogue can run to thousands of rows, and dumping those into a client's context is the classic agent failure. Every tool caps its inline payload, sets a `truncated` flag, writes the full dataset to disk and returns the path. A readable sample plus a pointer, never a wall of JSON. No API key is needed either: the server defaults to local Ollama unless an OpenAI key is actually present.

---

## Deploy a free live demo

**Not on Vercel.** The Streamlit UI needs a persistent server process with a live WebSocket connection; Vercel is serverless functions with a 10-second timeout on the free plan. It's not that this app runs poorly there; it doesn't run at all without a full rewrite to a static frontend + API routes.

**Not with Ollama either.** No free host gives you a persistent way to keep a 2-5GB model loaded, so the "free local AI" path that powers local development can't come along to a hosted demo.

**[Streamlit Community Cloud](https://streamlit.io/cloud)** is the actual fit. It's built for exactly this, deploys straight from a GitHub repo, and is free:

1. Push this repo to GitHub (already done if you're reading it there).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick the repo, branch `main`, main file `app.py`.
3. Under **Advanced settings → Secrets**, add:
   ```toml
   PUBLIC_DEMO_MODE = "true"
   ```
4. Deploy.

`PUBLIC_DEMO_MODE` restricts the live app to the **Shopify catalogue mode only**, with no LLM, no key, exact data, and no cost no matter how much traffic a public link gets. The natural-language mode is hidden rather than left half-working, because a public link with no key configured would otherwise dangle a broken "Ask in plain language" tab, and one with a key configured would let any visitor spend it. A short per-session cooldown discourages accidental rapid-fire clicking; it is a courtesy, not real abuse protection.

Free tier: ~1GB RAM, sleeps after 12h with no traffic (reloads in a few seconds on the next visit), unlimited public apps.

To later also enable the AI mode on the same deployment, add `OPENAI_API_KEY` as a secret and remove `PUBLIC_DEMO_MODE`, but think about cost and abuse first, since a public link with a live key means anyone who finds it can spend it.

[Hugging Face Spaces](https://huggingface.co/spaces) is a solid alternative, with the same deploy shape, more generous free RAM (16GB vs ~1GB), sleeps after 48h instead of 12h.

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

**The OpenAI API is pay-as-you-go and is *not* the free ChatGPT tier.** A key with no credit returns a quota error. In practice this agent costs roughly **$0.001-0.01 per page** with `gpt-4o-mini`, because the cleaning step keeps the token count small. It is cents, not free.

If you want genuinely free, run a local model with [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:3b        # ~1.9GB, decent at structured JSON
ollama pull qwen2.5:7b        # ~4.7GB, noticeably more accurate

scrape-agent example.com "product names and prices" --provider ollama --model qwen2.5:3b
```

| | `--provider openai` | `--provider ollama` |
|---|---|---|
| Cost | ~$0.001-0.01 per page | free |
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
| `--impersonate {auto,always,never}` | Retry blocked pages with a browser TLS fingerprint |
| `--dump-markdown PATH` | Save exactly what the model was shown, the first thing to look at when results are wrong |

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

### Monitoring for drift

```bash
scrape-agent-monitor init > watchlist.json     # an example to edit
scrape-agent-monitor run --config watchlist.json
scrape-agent-monitor run --config watchlist.json --fail-on warn --json report.json
scrape-agent-monitor status --config watchlist.json
```

A watchlist is a dozen lines of JSON. `canary` is optional. Omit it and the monitor works out at run time whether the page can be scored:

```json
{
  "prompt": "every product on this page with its title and price",
  "provider": "ollama",
  "model": "qwen2.5:7b",
  "state_path": "~/.scrape-agent/monitor/history.jsonl",
  "pages": [
    {"url": "https://www.allbirds.com/collections/mens", "canary": true},
    {"url": "https://your-store.com/collections/all"}
  ]
}
```

History is append-only JSON Lines: small, greppable, diffable in git, and readable by anything. See [Drift monitoring](#catching-drift-before-your-data-does) for what is measured and why.

### Streamlit app

```bash
streamlit run app.py
```

One page, no sidebar: a scrape needs one required input, and hiding the model
settings off-canvas made the page feel like a control panel for something that
is really a search box. The URL goes in a card at the top, the model settings
live in a popover next to the input they affect, and the public demo, which
has nothing to configure, shows no settings affordance at all. Two expanders
below the results show the schema the agent designed and the exact cleaned page
text it read, which is how you debug a bad extraction.

Catalogue results get a browser rather than a dump. A 385-product store is 5381
variant rows, unreadable as a flat table, so the results section adds:

- **Filters**, in a panel that is deliberately the loudest block on the page:
  search (all terms must match), in stock / out of stock, a price range in the
  store's currency, vendor and product-type facets, discounted-only, and a cap
  on how many rows go on screen. The panel header names the filters currently
  applied, and the stat tiles above the table report the filtered set with the
  store total for context. Downloads always contain every filtered row, not
  just the visible page.
- **Collapsed variants.** One row per product showing its variant count, price
  spread and stock ("3/7 in stock"), which expands to that product's variants
  when you tick it. Streamlit has no nested rows, so row selection stands in
  for the disclosure triangle.
- **Typed columns.** `url` is a clickable link, `image` renders the photo,
  prices are numbers formatted in the store's currency (string prices sort
  lexically: "9" after "100"), stock flags are checkboxes, and every header is
  a readable label rather than a raw key.
- **Columns that earn their width.** A column that is empty, or zero all the
  way down, is dropped: a jewellery store reports `grams=0` for everything, and
  a store with no sale prices should not get a "Best off" column full of
  Streamlit's grey `None` placeholders. The column picker still exposes every
  field the API publishes.

The look is a design system in two halves. `.streamlit/config.toml` carries the
tokens (colours, fonts, radii, semantic tones) for a fully specified light
*and* dark theme, so Streamlit's own components (widgets, dataframes, badges,
alerts) come out on-theme instead of being fought with a stylesheet.
[`ui.py`](ui.py) adds only what Streamlit has no component for: the hero band,
the stat tiles, the filter panel shell, section headers and empty states, as
pure functions returning HTML strings so they can be tested without a browser.
Streamlit internals are touched only through `data-testid` attributes that are
stable across releases, and every rule degrades to "plainer" rather than
"broken" if one stops matching.

The filter logic lives in `scraper_agent/catalogue_view.py` rather than in
`app.py`, because it is the part with edge cases worth testing and Streamlit
scripts are awkward to unit-test.

**One bug worth writing down**, because it is the kind that only a browser
finds. The Reset button first cleared the filters by deleting their
`session_state` keys, which is the documented pattern, and `AppTest` confirmed it
worked. In a real browser it did not: a button click ships every widget's
current value to the server in the same message, and those values are
reapplied as the widgets re-register during the rerun, so the search box still
read "gold necklace" while the header chips said "No filters". The fix is to
version the filter widget keys and bump the generation on reset, which hands
Streamlit widgets it has never seen. Same lesson as the rest of this project:
the offline suite was green throughout.

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
| `OPENAI_API_KEY` | none | Required for the OpenAI backend |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `llama3.2` / `localhost:11434` | Local backend |
| `MAX_CHUNK_CHARS` | `12000` | Page characters per LLM call (~4 chars/token) |
| `MAX_CHUNKS` | `12` | Ceiling on calls per page, so one huge page can't run up a bill |
| `VERIFY_GROUNDING` | `true` | Null out numeric answers absent from the page text |
| `IMPERSONATION` | `auto` | Retry blocked requests via curl_cffi (`auto`/`always`/`never`) |
| `IMPERSONATE_PROFILE` | `chrome` | Browser profile curl_cffi imitates |
| `RESPECT_ROBOTS` | `true` | Check robots.txt before fetching |
| `POLITENESS_DELAY` | `0.5` | Seconds between requests |

---

## Tests

```bash
pytest
```

435 tests, all offline: no network, no API keys, no cost. They cover the HTML→markdown converter (including the layout-table handling that Hacker News-style pages need), chunking and overlap, tolerant JSON parsing of model output, schema generation under OpenAI strict mode, record merging, output writers, Shopify pagination and flattening against a mocked transport, next-link detection, title matching and metric arithmetic against hand-computed fixtures, ground-truth scoping, the MCP tool surface via an in-memory client, the drift monitor's signals, baselines, detection rules, attribution and exit codes, the catalogue filters and product grouping, the design system's HTML builders, the Streamlit layout itself via AppTest, and the full agent loop against a stubbed provider.

Several are regression tests for bugs found by running against real sites rather than by reading the code:

- the planner guessing "one record" on a 20-item listing and truncating away 19 rows
- markdown link syntax leaking into extracted values
- a next-link matcher that missed the very common `pagination__next` class, because `_` counts as a word character in `\b`
- apostrophes splitting `Men's` into two tokens, so `Mens` failed to match exactly
- **the eval capping its own ground truth and manufacturing false positives** (see above)
- fabricated prices surviving into output, now blocked by the grounding check
- a history file whose last line had been truncated by a killed process silently swallowing the *next* run too, so one interrupted write cost two runs and the second loss was invisible
- **the monitor reporting "no page degraded" on a first run**, when no page had a baseline and nothing had in fact been checked. The same failure as a metric that improves when the model lies, wearing different clothes

---

## Limitations

- **Some sites block scrapers outright.** Fingerprint-based blocks are handled (see [above](#sites-that-block-scrapers)), but JavaScript challenges and behavioural scoring (Amazon, G2, Zillow) are not, and no amount of AI changes that. Most of the web, including essentially all Shopify stores, is fine: gymshark.com and fashionnova.com both block a plain Python client on `/products.json` and both read correctly through the escalating client.
- **The model can still be wrong.** It only ever sees text that was genuinely on the page and is told to return `null` rather than guess, but extraction from ambiguous layouts is not perfect. The point of the benchmark above is that you do not have to guess how wrong. Run it on a store like yours.
- **Small local models produce some junk rows.** On the Hacker News run above, `qwen2.5:3b` returned the site's own nav links as if they were stories. Filtering rows where every field but one is `null` clears most of it.
- **Small local models struggle with wide schemas.** Under ~7B parameters, keep to a handful of fields.
- **The benchmark only covers Shopify stores**, since that is where free ground truth exists. Accuracy on a news site or job board is not measured by it, so treat the numbers as evidence about product-listing extraction specifically, not a universal score.
- **Login-walled and aggressively bot-protected pages** are out of scope.

## Responsible use

`robots.txt` is respected by default and requests are rate-limited. `--ignore-robots` exists for pages you own or are authorised to scrape. Check the site's terms and applicable law before using it, and don't collect personal data you have no basis to hold.

## License

MIT

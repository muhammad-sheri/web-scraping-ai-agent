"""MCP server — exposes the scraper to Claude Desktop, Claude Code, or any MCP client.

Run it directly (`scrape-agent-mcp`) or point a client at it; see the README for
the config snippet.

Two decisions worth knowing about:

*Token discipline.* A catalogue can be several thousand rows. Returning those
inline would bury the client's context in data it cannot use — the classic
agent failure mode. Every tool caps what it returns, flags the truncation, and
writes the complete dataset to a file whose path it hands back. The model gets
a readable sample plus a pointer, not a wall of JSON.

*No key required.* The provider defaults to local Ollama unless an OpenAI key
is actually present, so the server is useful the moment it starts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.fetch import FetchError
from scraper_agent.providers import ProviderError, get_provider
from scraper_agent.shopify import ShopifyError, is_shopify_store

mcp = FastMCP(
    "web-scraping-ai-agent",
    instructions=(
        "Extracts structured data from web pages. For online stores, call "
        "check_store first: Shopify stores should go through shopify_catalogue, "
        "which returns the store's exact records instead of AI-read values. "
        "evaluate_extraction reports this agent's measured accuracy on a store."
    ),
)

#: Rows returned inline. The rest goes to a file.
MAX_INLINE_ROWS = 40


def _output_dir() -> Path:
    path = Path(os.getenv("SCRAPER_OUTPUT_DIR", "~/.scrape-agent")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_provider() -> str:
    """Local model unless an OpenAI key is genuinely available."""
    settings = Settings.from_env()
    if settings.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        return "ollama"
    return settings.provider


def _slug(url: str) -> str:
    keep = [c if c.isalnum() else "-" for c in url.replace("https://", "").replace("http://", "")]
    return "".join(keep).strip("-")[:60] or "scrape"


def _package(records: list[dict[str, Any]], url: str, label: str) -> dict[str, Any]:
    """Cap the inline payload; write the full set to disk and return its path."""
    total = len(records)
    truncated = total > MAX_INLINE_ROWS
    payload: dict[str, Any] = {
        "total_records": total,
        "returned_inline": min(total, MAX_INLINE_ROWS),
        "truncated": truncated,
        "records": records[:MAX_INLINE_ROWS],
    }

    if truncated:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = _output_dir() / f"{label}-{_slug(url)}-{stamp}.json"
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        payload["saved_to"] = str(path)
        payload["note"] = (
            f"Showing the first {MAX_INLINE_ROWS} of {total} records. "
            f"The complete set is at {path}."
        )
    return payload


@mcp.tool
def check_store(url: str) -> dict[str, Any]:
    """Check whether a URL is a Shopify store and say which tool to use.

    Call this before scraping a shop. Shopify stores expose their real
    catalogue, so they should go through shopify_catalogue rather than being
    read by a language model — the data is exact and it costs nothing.
    """
    try:
        shopify = is_shopify_store(url)
    except Exception as exc:
        return {"url": url, "error": f"Could not check the store: {exc}"}

    if shopify:
        return {
            "url": url,
            "is_shopify": True,
            "recommended_tool": "shopify_catalogue",
            "reason": (
                "This store publishes its own catalogue as JSON. That gives exact "
                "prices, SKUs, stock flags and every size/colour variant for the "
                "whole catalogue, with no language model involved and no cost."
            ),
        }
    return {
        "url": url,
        "is_shopify": False,
        "recommended_tool": "scrape_page",
        "reason": (
            "No public product API, so the page has to be read by a model. Set "
            "all_pages=True if the listing spans several pages."
        ),
    }


@mcp.tool
def scrape_page(
    url: str,
    what_to_extract: str,
    all_pages: bool = False,
    max_pages: int = 5,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Extract structured data from a web page, described in plain language.

    Args:
        url: The page to scrape.
        what_to_extract: What you want, e.g. "every product with its name,
            price and link". Naming 3-4 concrete fields works far better than
            a vague request.
        all_pages: Follow "next" links and extract every page, not just the first.
        max_pages: Page limit when all_pages is set.
        provider: "ollama" (local, free) or "openai". Defaults to whichever is usable.
        model: Model name for that provider.

    Returns the extracted records, the schema the agent designed, and token usage.
    """
    try:
        settings = Settings.from_env()
        llm = get_provider(provider or _default_provider(), model, settings)
        agent = ScrapeAgent(provider=llm, settings=settings)
        result = (
            agent.run_pages(url, what_to_extract, max_pages=max_pages)
            if all_pages
            else agent.run(url, what_to_extract)
        )
    except (FetchError, ProviderError, ValueError) as exc:
        return {"url": url, "error": str(exc)}

    payload = _package(result.records, url, "scrape")
    payload.update(
        url=result.final_url,
        schema=result.plan,
        pages_read=result.pages,
        model=f"{result.provider}/{result.model}",
        tokens=result.usage.get("total_tokens", 0),
        estimated_cost_usd=result.cost_usd,
        elapsed_s=result.elapsed_s,
    )
    return payload


@mcp.tool
def shopify_catalogue(store_url: str, max_products: int | None = None) -> dict[str, Any]:
    """Get a Shopify store's COMPLETE catalogue as exact data — no AI, no cost.

    Reads the store's own product API, so values are the store's records rather
    than a model's reading of a page: exact prices, compare-at prices, SKUs,
    stock flags, and one row per size/colour variant, across the entire
    catalogue rather than the first page.

    Args:
        store_url: Store or collection URL. A /collections/<name> URL limits
            the result to that collection.
        max_products: Cap the number of products (each yields several variant rows).
    """
    try:
        if not is_shopify_store(store_url):
            return {
                "store_url": store_url,
                "error": (
                    "Not a Shopify store — it has no public /products.json. "
                    "Use scrape_page with a description of what you want instead."
                ),
            }
        agent = ScrapeAgent(settings=Settings.from_env())
        result = agent.run_catalogue(store_url, max_products=max_products)
    except (ShopifyError, FetchError) as exc:
        return {"store_url": store_url, "error": str(exc)}

    payload = _package(result.records, store_url, "catalogue")
    payload.update(
        store_url=store_url,
        source="the store's own product API (exact data)",
        cost_usd=0.0,
        elapsed_s=result.elapsed_s,
        note_fields=(
            "One row per variant. Columns: title, url, vendor, product_type, tags, "
            "description, variant_title, sku, price, compare_at_price, available, "
            "grams, image."
        ),
    )
    return payload


@mcp.tool
def evaluate_extraction(
    page_url: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Measure how accurate this agent's AI extraction actually is, on a real store.

    Uses a Shopify store as its own answer key: the products linked on the page
    define the scope, the store's API supplies their true titles and prices, and
    the model's output is scored against that. No hand-labelled data is needed,
    so any Shopify store works as a fresh benchmark.

    Reports recall (share of on-page products found), precision, hallucination
    rate (records matching no real product), and price accuracy.

    Args:
        page_url: A Shopify collection/listing page, e.g. https://store.com/collections/all
        provider: "ollama" or "openai". Defaults to whichever is usable.
        model: Model to evaluate.
    """
    from scraper_agent.evals.runner import evaluate_page

    run = evaluate_page(
        page_url,
        provider or _default_provider(),
        model,
        keep_predictions=False,
    )
    if not run.ok:
        return {"page_url": page_url, "error": run.error}

    metrics = run.metrics
    return {
        "page_url": run.page_url,
        "model": f"{run.provider}/{run.model}",
        "products_on_page": metrics["truth_count"],
        "products_found": metrics["matched_count"],
        "recall": metrics["recall"],
        "precision": metrics["precision"],
        "hallucination_rate": metrics["hallucination_rate"],
        "price_accuracy": metrics["price_accuracy"],
        "f1": metrics["f1"],
        "missed_examples": metrics["missed_titles"][:5],
        "hallucinated_examples": metrics["hallucinated_titles"][:5],
        "catalogue_size": run.catalogue_size,
        "tokens": run.tokens,
        "elapsed_s": run.elapsed_s,
        "method": (
            "Ground truth comes from the store's own /products.json, scoped to the "
            "product handles linked on this page."
        ),
    }


def main() -> None:
    """Console-script entry point (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()

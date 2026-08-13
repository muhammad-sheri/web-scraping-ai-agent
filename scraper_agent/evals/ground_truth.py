"""Building an answer key for one page, from the store's own API.

The whole eval hinges on one detail. A store's API returns the entire
catalogue — Allbirds has 291 products — while the page the model reads shows
maybe 24. Scoring 24 predictions against 291 answers would report ~8% recall
and mean nothing.

So the page defines the scope: every `/products/<handle>` link in the HTML says
which products were genuinely visible, and the API then supplies the true
values for exactly those. Recall becomes an honest question again — "of the
products actually on this page, how many did the model find?"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from scraper_agent.config import Settings
from scraper_agent.fetch import fetch
from scraper_agent.shopify import fetch_products

# Shopify product URLs are /products/<handle>, optionally nested under a
# collection. Handles are lowercase alphanumeric with hyphens/underscores.
_HANDLE = re.compile(r"/products/([a-z0-9][a-z0-9_-]*)", re.IGNORECASE)

# Not a product: the JSON endpoints themselves.
_NOT_HANDLES = {"json", "products"}


def product_handles_in_page(html: str) -> list[str]:
    """Product handles linked from the page, in first-seen order."""
    seen: list[str] = []
    known: set[str] = set()
    for match in _HANDLE.finditer(html or ""):
        handle = match.group(1).lower()
        if handle.endswith(".json"):
            handle = handle[: -len(".json")]
        if not handle or handle in _NOT_HANDLES or handle in known:
            continue
        known.add(handle)
        seen.append(handle)
    return seen


def product_price(product: dict[str, Any]) -> float | None:
    """Lowest variant price — what a listing page shows as "from $X"."""
    prices: list[float] = []
    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        try:
            prices.append(float(str(variant.get("price"))))
        except (TypeError, ValueError):
            continue
    return min(prices) if prices else None


def simplify(product: dict[str, Any], store_url: str) -> dict[str, Any]:
    """Reduce an API product to the fields an extraction is scored on."""
    parsed = urlparse(store_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    handle = product.get("handle", "")
    return {
        "title": product.get("title"),
        "price": product_price(product),
        "handle": handle,
        "url": urljoin(root, f"/products/{handle}") if handle else None,
        "vendor": product.get("vendor"),
    }


@dataclass
class PageTruth:
    """The answer key for a single page."""

    page_url: str
    records: list[dict[str, Any]] = field(default_factory=list)
    handles_on_page: list[str] = field(default_factory=list)
    catalogue_size: int = 0
    html: str = ""

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def unresolved_handles(self) -> int:
        """Linked on the page but absent from the API (recommendations etc.)."""
        return max(0, len(self.handles_on_page) - len(self.records))


def build_page_truth(
    page_url: str,
    settings: Settings | None = None,
    *,
    render: bool | None = None,
) -> PageTruth:
    """Fetch a store page and assemble ground truth for the products on it.

    Deliberately has no "limit" option. An earlier version could cap the truth
    set for speed, which quietly destroyed the metric: the model still read the
    whole page, so every correct product beyond the cap was scored as a false
    positive. Measured on Allbirds, a cap of 20 against a 36-product page
    reported 59% precision for output that was essentially correct. Truth is
    always the full set of products the page links.
    """
    settings = settings or Settings.from_env()

    page = fetch(page_url, settings, render=render)
    handles = product_handles_in_page(page.html)

    catalogue = fetch_products(page_url, settings)
    by_handle = {
        str(p.get("handle", "")).lower(): p for p in catalogue if p.get("handle")
    }

    records = [
        simplify(by_handle[h], page.final_url) for h in handles if h in by_handle
    ]

    return PageTruth(
        page_url=page.final_url,
        records=records,
        handles_on_page=handles,
        catalogue_size=len(catalogue),
        html=page.html,
    )

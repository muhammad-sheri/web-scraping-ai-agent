"""Complete catalogue extraction from Shopify stores — no LLM involved.

Every Shopify storefront publishes its catalogue as JSON at `/products.json`
(and per collection at `/collections/<handle>/products.json`). That endpoint is
public by design: it is what the storefront's own JavaScript reads.

This matters because it beats AI extraction on every axis that counts here.
The LLM route reads rendered text and infers values, so it sees the one price
the page happens to show and can misread it. The JSON route returns the store's
own records: exact prices, SKUs, stock flags and every size/colour variant,
for the whole catalogue rather than the first page. It is free, fast and
deterministic, so the agent uses it whenever the store is Shopify.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from scraper_agent.config import Settings

MAX_PAGE_SIZE = 250

# Fingerprints that appear in the HTML of essentially every Shopify storefront.
_HTML_MARKERS = (
    "cdn.shopify.com",
    "myshopify.com",
    "Shopify.theme",
    "shopify-section",
    "/cdn/shop/",
)

_TAG_NOISE = re.compile(r"^[a-z0-9_-]+::", re.IGNORECASE)


class ShopifyError(RuntimeError):
    """Raised when a store's catalogue cannot be read."""


def looks_like_shopify(html: str, headers: Any = None) -> bool:
    """Cheap check against already-fetched page HTML and response headers."""
    if headers:
        try:
            if any(k.lower().startswith("x-shopid") for k in headers.keys()):
                return True
            if "shopify" in str(headers.get("powered-by", "")).lower():
                return True
        except AttributeError:
            pass
    lowered = (html or "")[:200_000].lower()
    return any(marker.lower() in lowered for marker in _HTML_MARKERS)


def products_endpoint(url: str) -> str:
    """Map a store or collection URL to the JSON endpoint that serves it.

    https://shop.com/collections/mens?x=1 -> https://shop.com/collections/mens/products.json
    https://shop.com/products/a-shoe      -> https://shop.com/products.json
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    root = f"{parsed.scheme}://{parsed.netloc}"

    match = re.search(r"/collections/([^/?#]+)", parsed.path)
    # `/collections/all` is the whole catalogue anyway; use the root endpoint,
    # which is the one that reliably paginates.
    if match and match.group(1) != "all":
        return f"{root}/collections/{match.group(1)}/products.json"
    return f"{root}/products.json"


def is_shopify_store(url: str, settings: Settings | None = None) -> bool:
    """Probe the JSON endpoint directly. One cheap request, no guessing."""
    settings = settings or Settings.from_env()
    try:
        response = httpx.get(
            products_endpoint(url),
            params={"limit": 1},
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        )
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        return isinstance(response.json().get("products"), list)
    except (ValueError, AttributeError):
        return False


def _clean_tags(tags: Any) -> str:
    """Drop app-generated metafield tags like `allbirds::hue => grey`."""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if not isinstance(tags, list):
        return ""
    keep = [str(t).strip() for t in tags if t and not _TAG_NOISE.match(str(t))]
    return ", ".join(keep)


def _html_to_text(html: str) -> str:
    from scraper_agent.fetch import visible_text

    return visible_text(html or "")


def _option_names(product: dict[str, Any]) -> str:
    """The store's own names for its variant axes, e.g. "Color / Ring size".

    The values live on each variant as option1/2/3, which say nothing on their
    own; the names are only ever published here, at product level.
    """
    names = []
    for option in product.get("options") or []:
        if isinstance(option, dict):
            name = option.get("name")
        else:
            name = option
        if name and str(name).strip():
            names.append(str(name).strip())
    return " / ".join(names)


def to_price(value: Any) -> float | None:
    """Shopify sends prices as strings ("91.00"). Numeric or None, never raise."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def discount_percent(price: Any, compare_at_price: Any) -> float | None:
    """Percent off, or None when the variant is not actually discounted.

    Plenty of stores leave `compare_at_price` equal to (or below) the live
    price rather than clearing it, so only a positive gap counts as a discount.
    """
    now, was = to_price(price), to_price(compare_at_price)
    if now is None or was is None or was <= 0 or now >= was:
        return None
    return round((was - now) / was * 100, 1)


def fetch_store_meta(url: str, settings: Settings | None = None) -> dict[str, Any]:
    """Store name, country and currency from `/meta.json`. Best effort.

    Nothing else in the catalogue says what currency those prices are in, and
    a store that does not serve this endpoint is still perfectly scrapable —
    so every failure here returns {} rather than raising.
    """
    settings = settings or Settings.from_env()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    try:
        response = httpx.get(
            f"{parsed.scheme}://{parsed.netloc}/meta.json",
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        )
        if response.status_code != 200:
            return {}
        meta = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(meta, dict):
        return {}
    return {
        key: meta.get(key)
        for key in ("name", "currency", "country", "domain", "description")
        if meta.get(key)
    }


def _variant_row(
    base: dict[str, Any], variant: dict[str, Any], fallback_image: str | None
) -> dict[str, Any]:
    """Merge one variant into the product fields, in table-reading order."""
    image = variant.get("featured_image")
    if isinstance(image, dict):
        image = image.get("src")
    price = variant.get("price")
    compare_at = variant.get("compare_at_price")

    return {
        "product_id": base["product_id"],
        "title": base["title"],
        "url": base["url"],
        "image": image or fallback_image,
        "vendor": base["vendor"],
        "product_type": base["product_type"],
        "variant_title": variant.get("title"),
        "option1": variant.get("option1"),
        "option2": variant.get("option2"),
        "option3": variant.get("option3"),
        "sku": variant.get("sku"),
        "variant_id": variant.get("id"),
        "price": price,
        "compare_at_price": compare_at,
        "discount_pct": discount_percent(price, compare_at),
        "available": variant.get("available"),
        "grams": variant.get("grams"),
        "requires_shipping": variant.get("requires_shipping"),
        "taxable": variant.get("taxable"),
        "position": variant.get("position"),
        "options": base["options"],
        "tags": base["tags"],
        "handle": base["handle"],
        "image_count": base["image_count"],
        "published_at": base["published_at"],
        "created_at": base["created_at"],
        "updated_at": base["updated_at"],
        "variant_updated_at": variant.get("updated_at"),
        "description": base["description"],
    }


def flatten_product(product: dict[str, Any], store_url: str) -> list[dict[str, Any]]:
    """One row per variant — a size or colour is its own SKU, price and stock."""
    root = f"{urlparse(store_url).scheme}://{urlparse(store_url).netloc}"
    handle = product.get("handle", "")
    images = product.get("images") or []
    first_image = images[0].get("src") if images and isinstance(images[0], dict) else None

    base = {
        "product_id": product.get("id"),
        "title": product.get("title"),
        "url": urljoin(root, f"/products/{handle}") if handle else None,
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": _clean_tags(product.get("tags")),
        "handle": handle or None,
        "options": _option_names(product),
        "image_count": len(images),
        "published_at": product.get("published_at"),
        "created_at": product.get("created_at"),
        "updated_at": product.get("updated_at"),
        "description": _html_to_text(product.get("body_html", "")),
    }

    variants = [v for v in (product.get("variants") or []) if isinstance(v, dict)]
    if not variants:
        # Still worth a row: the product exists, it just has no purchasable SKU.
        return [_variant_row(base, {}, first_image)]
    return [_variant_row(base, variant, first_image) for variant in variants]


def fetch_products(
    url: str,
    settings: Settings | None = None,
    *,
    max_products: int | None = None,
    page_size: int = MAX_PAGE_SIZE,
    on_progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Page through the whole catalogue and return raw Shopify product dicts."""
    settings = settings or Settings.from_env()
    endpoint = products_endpoint(url)
    report = on_progress or (lambda _m: None)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    # Don't pull 250 products to satisfy a cap of 5.
    if max_products is not None:
        page_size = max(1, min(page_size, max_products))

    collected: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()
    page = 1

    while True:
        try:
            response = httpx.get(
                endpoint,
                params={"limit": page_size, "page": page},
                timeout=settings.request_timeout,
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ShopifyError(f"Could not read {endpoint}: {exc}") from exc

        if response.status_code == 404:
            raise ShopifyError(
                f"{endpoint} returned 404 — this does not look like a Shopify store."
            )
        if response.status_code >= 400:
            raise ShopifyError(f"{endpoint} returned HTTP {response.status_code}")

        try:
            batch = response.json().get("products") or []
        except ValueError as exc:
            raise ShopifyError(f"{endpoint} did not return JSON.") from exc

        if not batch:
            break

        # Stores that ignore ?page would otherwise loop on page 1 forever.
        fresh = [p for p in batch if p.get("id") not in seen_ids]
        if not fresh:
            break
        for product in fresh:
            seen_ids.add(product.get("id"))
        collected.extend(fresh)

        report(f"Fetched {len(collected)} products (page {page})")

        if max_products is not None and len(collected) >= max_products:
            collected = collected[:max_products]
            break
        if len(batch) < page_size:
            break

        page += 1
        if settings.politeness_delay:
            time.sleep(settings.politeness_delay)

    return collected


def scrape_catalogue(
    url: str,
    settings: Settings | None = None,
    *,
    max_products: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Full catalogue as flat rows, one per variant."""
    products = fetch_products(
        url, settings, max_products=max_products, on_progress=on_progress
    )
    rows: list[dict[str, Any]] = []
    for product in products:
        rows.extend(flatten_product(product, url))
    return rows

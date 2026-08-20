"""Filtering and product-level grouping for catalogue rows.

The catalogue arrives as one row per *variant*, which is the right shape for
export but the wrong shape for reading: a store with 700 products becomes 5000
near-identical rows. These helpers back the UI's two answers to that — narrow
the rows down (search, price, stock, vendor), and fold the survivors back up
into one row per product that can be expanded to show its variants.

Kept out of app.py deliberately: this is the part with edge cases worth
testing (missing prices, string prices, products whose variants disagree),
and Streamlit scripts are awkward to unit-test.
"""

from __future__ import annotations

from typing import Any, Iterable

from scraper_agent.shopify import to_price

Record = dict[str, Any]

ANY_STOCK = "Any"
IN_STOCK = "In stock"
OUT_OF_STOCK = "Out of stock"
STOCK_CHOICES = (ANY_STOCK, IN_STOCK, OUT_OF_STOCK)

# Fields a shopper-style search should look at. Deliberately excludes
# `description`, which is long enough that almost any term matches something.
SEARCH_FIELDS = (
    "title",
    "sku",
    "vendor",
    "product_type",
    "tags",
    "variant_title",
    "option1",
    "option2",
    "option3",
    "handle",
)


def price_bounds(records: Iterable[Record]) -> tuple[float, float]:
    """(min, max) across every priced row; (0.0, 0.0) when nothing has a price."""
    prices = [p for p in (to_price(r.get("price")) for r in records) if p is not None]
    if not prices:
        return 0.0, 0.0
    return min(prices), max(prices)


def distinct(records: Iterable[Record], field: str) -> list[str]:
    """Sorted non-empty values of a field — the options for a facet filter."""
    values = {str(r.get(field)).strip() for r in records if r.get(field)}
    return sorted(v for v in values if v and v.lower() != "none")


def _matches_search(record: Record, terms: list[str]) -> bool:
    """Every term must appear somewhere in the row (AND, not OR).

    AND is what makes a two-word query useful: "gold ring" should mean both,
    otherwise adding a word only ever widens the result set.
    """
    haystack = " ".join(
        str(record.get(field, "")) for field in SEARCH_FIELDS
    ).lower()
    return all(term in haystack for term in terms)


def filter_records(
    records: list[Record],
    *,
    search: str = "",
    stock: str = ANY_STOCK,
    price_range: tuple[float, float] | None = None,
    vendors: Iterable[str] | None = None,
    product_types: Iterable[str] | None = None,
    on_sale_only: bool = False,
) -> list[Record]:
    """Apply the visible filters. Every argument left at its default is a no-op."""
    terms = [t for t in search.lower().split() if t]
    vendor_set = {v for v in (vendors or []) if v}
    type_set = {t for t in (product_types or []) if t}

    out = []
    for record in records:
        if terms and not _matches_search(record, terms):
            continue

        available = record.get("available")
        if stock == IN_STOCK and available is not True:
            continue
        if stock == OUT_OF_STOCK and available is not False:
            continue

        if price_range is not None:
            price = to_price(record.get("price"))
            # An unpriced row can't satisfy a price range, so it drops out only
            # once the user has actually narrowed the range from full width.
            if price is None or not (price_range[0] <= price <= price_range[1]):
                continue

        if vendor_set and str(record.get("vendor") or "") not in vendor_set:
            continue
        if type_set and str(record.get("product_type") or "") not in type_set:
            continue
        if on_sale_only and record.get("discount_pct") in (None, ""):
            continue

        out.append(record)
    return out


def group_products(records: list[Record]) -> list[Record]:
    """One summary row per product, in first-seen order.

    Variants of a product disagree about price and stock, which is the whole
    reason the collapsed view exists: it reports the range and the in-stock
    count rather than picking an arbitrary variant to stand for the product.
    """
    order: list[Any] = []
    groups: dict[Any, list[Record]] = {}
    for record in records:
        key = record.get("product_id") or record.get("url") or record.get("title")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    summaries = []
    for key in order:
        rows = groups[key]
        first = rows[0]
        prices = [p for p in (to_price(r.get("price")) for r in rows) if p is not None]
        discounts = [d for d in (r.get("discount_pct") for r in rows) if d not in (None, "")]
        in_stock = sum(1 for r in rows if r.get("available") is True)
        summaries.append(
            {
                "product_id": first.get("product_id"),
                "image": next((r.get("image") for r in rows if r.get("image")), None),
                "title": first.get("title"),
                "url": first.get("url"),
                "vendor": first.get("vendor"),
                "product_type": first.get("product_type"),
                "variants": len(rows),
                "in_stock": in_stock,
                "stock": _stock_label(in_stock, len(rows)),
                "price_min": min(prices) if prices else None,
                "price_max": max(prices) if prices else None,
                "best_discount_pct": max(discounts) if discounts else None,
                "options": first.get("options"),
                "tags": first.get("tags"),
                "published_at": first.get("published_at"),
            }
        )
    return summaries


def _stock_label(in_stock: int, total: int) -> str:
    if in_stock == 0:
        return "Out of stock"
    if in_stock == total:
        return "In stock"
    return f"{in_stock}/{total} in stock"


def variants_of(records: list[Record], product_id: Any) -> list[Record]:
    """The rows belonging to one product, matched the way group_products keys them."""
    return [
        r
        for r in records
        if (r.get("product_id") or r.get("url") or r.get("title")) == product_id
    ]


def price_label(low: float | None, high: float | None, currency: str = "") -> str:
    """"$91.00" for a single price, "$91.00 – $130.00" for a spread, "—" for none."""
    if low is None or high is None:
        return "—"
    symbol = CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    left = f"{symbol}{low:,.2f}"
    if abs(high - low) < 0.005:
        return left
    return f"{left} – {symbol}{high:,.2f}"


CURRENCY_SYMBOLS = {
    "USD": "$",
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "GBP": "£",
    "EUR": "€",
    "JPY": "¥",
    "INR": "₹",
    "PKR": "₨",
    "AED": "AED ",
    "SAR": "SAR ",
}

"""Ground-truth scoping, the detail the whole eval depends on."""

import httpx
import pytest

from scraper_agent.config import Settings
from scraper_agent.evals.ground_truth import (
    build_page_truth,
    product_handles_in_page,
    product_price,
    simplify,
)

PAGE_HTML = """
<html><body>
  <nav><a href="/collections/all">All</a></nav>
  <a href="/products/wool-runner">Wool Runner</a>
  <a href="/collections/mens/products/tree-dasher-2">Tree Dasher 2</a>
  <a href="/products/wool-runner">Wool Runner (duplicate link)</a>
  <a href="/pages/about">About</a>
  <a href="/products/trail-runner-swt?variant=123">Trail Runner SWT</a>
</body></html>
"""


def _product(handle, title, prices):
    return {
        "handle": handle,
        "title": title,
        "vendor": "Allbirds",
        "variants": [{"price": str(p)} for p in prices],
    }


CATALOGUE = [
    _product("wool-runner", "Wool Runner", [98.0, 98.0]),
    _product("tree-dasher-2", "Tree Dasher 2", [135.0]),
    _product("trail-runner-swt", "Trail Runner SWT", [145.0]),
    _product("not-on-this-page", "Wool Lounger", [105.0]),
]


# --- handle extraction ----------------------------------------------------


def test_finds_product_handles():
    handles = product_handles_in_page(PAGE_HTML)
    assert handles == ["wool-runner", "tree-dasher-2", "trail-runner-swt"]


def test_handles_are_deduplicated_in_first_seen_order():
    assert product_handles_in_page(
        '<a href="/products/b">B</a><a href="/products/a">A</a><a href="/products/b">B</a>'
    ) == ["b", "a"]


def test_collection_nested_urls_are_found():
    assert product_handles_in_page('<a href="/collections/x/products/y">y</a>') == ["y"]


def test_query_strings_do_not_pollute_the_handle():
    assert product_handles_in_page('<a href="/products/abc?variant=9">x</a>') == ["abc"]


def test_non_product_links_are_ignored():
    assert product_handles_in_page('<a href="/pages/about">a</a><a href="/cart">c</a>') == []


def test_products_json_is_not_a_handle():
    assert product_handles_in_page('<script src="/products.json"></script>') == []


def test_empty_html_is_safe():
    assert product_handles_in_page("") == []
    assert product_handles_in_page(None) == []


# --- simplification -------------------------------------------------------


def test_product_price_is_the_lowest_variant():
    assert product_price(_product("h", "t", [135.0, 98.0, 120.0])) == 98.0


def test_product_price_is_none_without_variants():
    assert product_price({"variants": []}) is None
    assert product_price({"variants": [{"price": "n/a"}]}) is None


def test_simplify_builds_a_scoreable_record():
    record = simplify(CATALOGUE[0], "https://shop.com/collections/mens")
    assert record == {
        "title": "Wool Runner",
        "price": 98.0,
        "handle": "wool-runner",
        "url": "https://shop.com/products/wool-runner",
        "vendor": "Allbirds",
    }


# --- the scoping rule -----------------------------------------------------


@pytest.fixture
def stub_network(monkeypatch):
    """Serve PAGE_HTML for the page and CATALOGUE for the API."""
    from scraper_agent.evals import ground_truth as gt

    class _Page:
        html = PAGE_HTML
        final_url = "https://shop.com/collections/mens"

    monkeypatch.setattr(gt, "fetch", lambda url, settings=None, **kw: _Page())
    monkeypatch.setattr(gt, "fetch_products", lambda url, settings=None, **kw: CATALOGUE)


def test_truth_is_scoped_to_the_page_not_the_catalogue(stub_network):
    """The whole point: 3 products are on the page, 4 exist in the store.

    Scoring against all 4 would report 75% recall for a perfect extraction.
    """
    truth = build_page_truth("https://shop.com/collections/mens", Settings())

    assert truth.count == 3
    assert truth.catalogue_size == 4
    assert [r["title"] for r in truth.records] == [
        "Wool Runner",
        "Tree Dasher 2",
        "Trail Runner SWT",
    ]
    assert "Wool Lounger" not in [r["title"] for r in truth.records]


def test_truth_records_carry_exact_prices(stub_network):
    truth = build_page_truth("https://shop.com/collections/mens", Settings())
    assert truth.records[0]["price"] == 98.0
    assert truth.records[1]["price"] == 135.0


def test_page_order_is_preserved(stub_network):
    truth = build_page_truth("https://shop.com/collections/mens", Settings())
    assert [r["handle"] for r in truth.records] == truth.handles_on_page


def test_truth_is_never_capped(stub_network):
    """Regression: capping the truth set silently destroyed precision.

    An earlier version accepted max_products. The model still read the whole
    page, so every correct product beyond the cap counted as a false positive.
    Measured live on Allbirds: a cap of 20 against a 36-product page reported
    59% precision for output that was essentially right.
    """
    import inspect

    assert "max_products" not in inspect.signature(build_page_truth).parameters

    truth = build_page_truth("https://shop.com/collections/mens", Settings())
    assert truth.count == len([h for h in truth.handles_on_page])


def test_capped_truth_would_have_manufactured_false_positives():
    """Demonstrates the bug the cap caused, so it cannot quietly return."""
    from scraper_agent.evals.metrics import score_extraction

    page_products = [{"title": f"Product {i}", "price": 10.0} for i in range(6)]
    perfect_extraction = list(page_products)

    full, _ = score_extraction(perfect_extraction, page_products)
    capped, _ = score_extraction(perfect_extraction, page_products[:3])

    assert full.precision == 1.0
    assert capped.precision == 0.5  # correct answers scored as hallucinations


def test_handles_absent_from_the_api_are_reported(monkeypatch):
    from scraper_agent.evals import ground_truth as gt

    class _Page:
        html = PAGE_HTML
        final_url = "https://shop.com/x"

    monkeypatch.setattr(gt, "fetch", lambda url, settings=None, **kw: _Page())
    monkeypatch.setattr(gt, "fetch_products", lambda url, settings=None, **kw: CATALOGUE[:1])

    truth = build_page_truth("https://shop.com/x", Settings())
    assert truth.count == 1
    assert truth.unresolved_handles == 2  # linked on the page, unknown to the API

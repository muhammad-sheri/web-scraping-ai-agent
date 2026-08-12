"""Shopify catalogue extraction. Offline — the HTTP layer is stubbed."""

from __future__ import annotations

import httpx
import pytest

from scraper_agent.config import Settings
from scraper_agent.shopify import (
    ShopifyError,
    fetch_products,
    flatten_product,
    is_shopify_store,
    looks_like_shopify,
    products_endpoint,
    scrape_catalogue,
)

PRODUCT = {
    "id": 123,
    "title": "Men's Strider",
    "handle": "mens-strider",
    "body_html": "<p>Made to <b>match</b> the pace.</p><script>x()</script>",
    "vendor": "Allbirds",
    "product_type": "Shoes",
    "published_at": "2026-08-11T15:42:35-07:00",
    "tags": ["shoprunner", "allbirds::hue => grey", "sale"],
    "images": [{"src": "https://cdn.shopify.com/img/main.jpg"}],
    "variants": [
        {"title": "8", "sku": "A-080", "price": "91.00", "compare_at_price": "130.00",
         "available": False, "grams": 903},
        {"title": "9", "sku": "A-090", "price": "91.00", "compare_at_price": "130.00",
         "available": True, "grams": 947,
         "featured_image": {"src": "https://cdn.shopify.com/img/9.jpg"}},
    ],
}


# --- endpoint mapping -----------------------------------------------------


def test_store_url_maps_to_root_endpoint():
    assert products_endpoint("https://shop.com") == "https://shop.com/products.json"
    assert products_endpoint("shop.com/products/a-shoe") == "https://shop.com/products.json"


def test_collection_url_maps_to_collection_endpoint():
    assert (
        products_endpoint("https://shop.com/collections/mens?page=2")
        == "https://shop.com/collections/mens/products.json"
    )


def test_collections_all_uses_the_root_endpoint():
    # /collections/all is the whole catalogue, and the root endpoint paginates.
    assert (
        products_endpoint("https://shop.com/collections/all")
        == "https://shop.com/products.json"
    )


# --- detection ------------------------------------------------------------


def test_html_markers_detect_shopify():
    assert looks_like_shopify('<script src="https://cdn.shopify.com/x.js">')
    assert looks_like_shopify("<link href='/cdn/shop/t/1/style.css'>")
    assert not looks_like_shopify("<html><body>plain site</body></html>")


def test_headers_detect_shopify():
    assert looks_like_shopify("", {"X-ShopId": "123"})


# --- flattening -----------------------------------------------------------


def test_one_row_per_variant():
    rows = flatten_product(PRODUCT, "https://shop.com")
    assert len(rows) == 2
    assert [r["sku"] for r in rows] == ["A-080", "A-090"]


def test_rows_carry_exact_commercial_fields():
    row = flatten_product(PRODUCT, "https://shop.com")[0]
    assert row["price"] == "91.00"
    assert row["compare_at_price"] == "130.00"  # the discount is visible
    assert row["available"] is False
    assert row["vendor"] == "Allbirds"
    assert row["url"] == "https://shop.com/products/mens-strider"


def test_variant_image_wins_over_product_image():
    rows = flatten_product(PRODUCT, "https://shop.com")
    assert rows[0]["image"] == "https://cdn.shopify.com/img/main.jpg"
    assert rows[1]["image"] == "https://cdn.shopify.com/img/9.jpg"


def test_description_html_is_stripped():
    row = flatten_product(PRODUCT, "https://shop.com")[0]
    assert row["description"] == "Made to match the pace."
    assert "<b>" not in row["description"]


def test_app_generated_tags_are_dropped():
    row = flatten_product(PRODUCT, "https://shop.com")[0]
    assert row["tags"] == "shoprunner, sale"


def test_product_without_variants_still_yields_a_row():
    rows = flatten_product(dict(PRODUCT, variants=[]), "https://shop.com")
    assert len(rows) == 1
    assert rows[0]["sku"] is None


# --- pagination -----------------------------------------------------------


def _mock_transport(pages: dict[int, list[dict]], calls: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if calls is not None:
            calls.append({"page": page, "limit": int(request.url.params.get("limit", 0))})
        return httpx.Response(200, json={"products": pages.get(page, [])})

    return httpx.MockTransport(handler)


@pytest.fixture
def patch_httpx(monkeypatch):
    def _install(transport):
        real_get = httpx.get

        def fake_get(url, **kwargs):
            kwargs.pop("transport", None)
            with httpx.Client(transport=transport, follow_redirects=True) as client:
                return client.get(url, **{k: v for k, v in kwargs.items()
                                          if k in ("params", "headers", "timeout")})

        monkeypatch.setattr(httpx, "get", fake_get)
        return real_get

    return _install


def test_pagination_collects_every_page(patch_httpx):
    pages = {
        1: [dict(PRODUCT, id=i) for i in range(250)],
        2: [dict(PRODUCT, id=1000 + i) for i in range(41)],
        3: [],
    }
    patch_httpx(_mock_transport(pages))
    products = fetch_products("https://shop.com", Settings(politeness_delay=0))
    assert len(products) == 291


def test_short_page_ends_pagination_without_an_extra_request(patch_httpx):
    calls: list[dict] = []
    patch_httpx(_mock_transport({1: [dict(PRODUCT, id=i) for i in range(10)]}, calls))
    fetch_products("https://shop.com", Settings(politeness_delay=0))
    assert len(calls) == 1


def test_store_ignoring_the_page_param_does_not_loop_forever(patch_httpx):
    # Some stores serve page 1 for every page. Repeated ids must stop the loop.
    same = [dict(PRODUCT, id=i) for i in range(250)]
    patch_httpx(_mock_transport({n: same for n in range(1, 20)}))
    products = fetch_products("https://shop.com", Settings(politeness_delay=0))
    assert len(products) == 250


def test_max_products_caps_the_result_and_shrinks_the_request(patch_httpx):
    calls: list[dict] = []
    patch_httpx(_mock_transport({1: [dict(PRODUCT, id=i) for i in range(250)]}, calls))
    products = fetch_products(
        "https://shop.com", Settings(politeness_delay=0), max_products=5
    )
    assert len(products) == 5
    # Don't download 250 products to satisfy a cap of 5.
    assert calls[0]["limit"] == 5


def test_catalogue_returns_flat_variant_rows(patch_httpx):
    patch_httpx(_mock_transport({1: [PRODUCT]}))
    rows = scrape_catalogue("https://shop.com", Settings(politeness_delay=0))
    assert len(rows) == 2
    assert rows[0]["title"] == "Men's Strider"


def test_non_shopify_404_is_a_clear_error(patch_httpx):
    def handler(request):
        return httpx.Response(404, text="Not Found")

    patch_httpx(httpx.MockTransport(handler))
    with pytest.raises(ShopifyError, match="does not look like a Shopify store"):
        fetch_products("https://plain-site.com", Settings(politeness_delay=0))


def test_is_shopify_store_is_true_for_json_products(patch_httpx):
    patch_httpx(_mock_transport({1: [PRODUCT]}))
    assert is_shopify_store("https://shop.com", Settings()) is True


def test_is_shopify_store_is_false_for_html(patch_httpx):
    patch_httpx(httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")))
    assert is_shopify_store("https://plain-site.com", Settings()) is False

"""Shopify catalogue extraction, offline, with the HTTP layer stubbed."""

from __future__ import annotations

import httpx
import pytest

from scraper_agent.config import Settings
from scraper_agent.shopify import (
    ShopifyError,
    catalogue_is_readable,
    check_store,
    discount_percent,
    fetch_products,
    fetch_store_meta,
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


# --- the extra fields the API publishes -----------------------------------

RICH_PRODUCT = dict(
    PRODUCT,
    created_at="2026-07-23T13:56:33-07:00",
    updated_at="2026-08-20T00:53:02-07:00",
    options=[
        {"name": "Color", "position": 1, "values": ["Grey"]},
        {"name": "Size", "position": 2, "values": ["8", "9"]},
    ],
    variants=[
        dict(PRODUCT["variants"][0], id=999, option1="Grey", option2="8",
             position=1, requires_shipping=True, taxable=True,
             updated_at="2026-08-19T10:00:00-07:00"),
    ],
)


def test_option_names_come_from_the_product_not_the_variant():
    """option1/option2 are bare values; only the product says what they mean."""
    row = flatten_product(RICH_PRODUCT, "https://shop.com")[0]
    assert row["options"] == "Color / Size"
    assert (row["option1"], row["option2"], row["option3"]) == ("Grey", "8", None)


def test_variant_identity_and_flags_are_carried():
    row = flatten_product(RICH_PRODUCT, "https://shop.com")[0]
    assert row["variant_id"] == 999
    assert row["position"] == 1
    assert row["requires_shipping"] is True
    assert row["taxable"] is True
    assert row["variant_updated_at"] == "2026-08-19T10:00:00-07:00"


def test_product_timestamps_and_image_count_are_carried():
    row = flatten_product(RICH_PRODUCT, "https://shop.com")[0]
    assert row["created_at"] == "2026-07-23T13:56:33-07:00"
    assert row["updated_at"] == "2026-08-20T00:53:02-07:00"
    assert row["image_count"] == 1
    assert row["handle"] == "mens-strider"


def test_discount_percent_is_derived_from_the_compare_at_price():
    row = flatten_product(PRODUCT, "https://shop.com")[0]
    assert row["discount_pct"] == pytest.approx(30.0)  # 91 off a 130 list price


@pytest.mark.parametrize(
    "price, compare_at",
    [
        ("91.00", None),        # not on sale
        ("91.00", "91.00"),     # stores that mirror the price instead of clearing it
        ("91.00", "80.00"),     # compare_at below the live price is not a discount
        ("91.00", "0"),
        (None, "130.00"),
        ("free", "130.00"),
    ],
)
def test_no_discount_reported_without_a_real_markdown(price, compare_at):
    assert discount_percent(price, compare_at) is None


def test_options_survive_a_store_that_sends_plain_strings():
    product = dict(RICH_PRODUCT, options=["Color", "Size"])
    assert flatten_product(product, "https://shop.com")[0]["options"] == "Color / Size"


def test_products_with_no_options_get_an_empty_string_not_a_crash():
    assert flatten_product(dict(PRODUCT, options=None), "https://shop.com")[0]["options"] == ""


def test_column_order_puts_the_commercial_fields_before_the_metadata():
    """The table is read left to right; description last, identity first."""
    keys = list(flatten_product(RICH_PRODUCT, "https://shop.com")[0])
    assert keys[:4] == ["product_id", "title", "url", "image"]
    assert keys[-1] == "description"
    assert keys.index("price") < keys.index("published_at")


# --- store metadata -------------------------------------------------------


def test_store_meta_supplies_the_currency(patch_httpx):
    patch_httpx(httpx.MockTransport(
        lambda r: httpx.Response(200, json={"name": "David Von", "currency": "USD",
                                            "country": "US", "id": 1})
    ))
    meta = fetch_store_meta("https://shop.com", Settings())
    assert meta["currency"] == "USD"
    assert meta["name"] == "David Von"
    assert "id" not in meta  # only the fields the UI actually shows


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="Not Found"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["unexpected", "shape"]),
    ],
)
def test_missing_store_meta_is_not_an_error(patch_httpx, response):
    """A store without /meta.json is still perfectly scrapable."""
    patch_httpx(httpx.MockTransport(lambda r: response))
    assert fetch_store_meta("https://shop.com", Settings()) == {}


# --- store detection ------------------------------------------------------
# "Is this Shopify?" and "can we read its catalogue?" are two questions.
# Collapsing them told users that gymshark.com and fashionnova.com were not
# Shopify stores, which is simply false: one answers 403 to the product API
# and the other 404, and both plainly run on Shopify.

SHOPIFY_HTML = '<html><head><script src="https://cdn.shopify.com/s/x.js"></script></head></html>'


def _routes(monkeypatch, handler):
    """Stub scraper_agent.http.get, which is what shopify.py now goes through."""
    from scraper_agent import http as http_module
    from scraper_agent import shopify as shopify_module

    def fake_get(url, **kwargs):
        return handler(url, kwargs)

    monkeypatch.setattr(shopify_module.http, "get", fake_get)
    return http_module.Response


def test_a_readable_catalogue_answers_both_questions_yes(monkeypatch):
    def handler(url, kw):
        from scraper_agent.http import Response as R
        return R(text='{"products": [{"id": 1}]}', status_code=200, url=url)

    _routes(monkeypatch, handler)
    check = check_store("https://shop.com", Settings())
    assert (check.is_shopify, check.catalogue_available) == (True, True)
    assert check.status == 200


def test_a_blocked_product_api_does_not_mean_not_shopify(monkeypatch):
    """gymshark.com: 403 on /products.json, unmistakably Shopify in its HTML."""
    def handler(url, kw):
        from scraper_agent.http import Response as R
        if "products.json" in url:
            return R(text="blocked", status_code=403, url=url)
        return R(text=SHOPIFY_HTML, status_code=200, url=url)

    _routes(monkeypatch, handler)
    check = check_store("https://gymshark.com", Settings())
    assert check.is_shopify is True
    assert check.catalogue_available is False
    assert check.status == 403
    assert "Shopify store" in check.detail and "cannot be read" in check.detail


def test_a_404_product_api_does_not_mean_not_shopify_either(monkeypatch):
    """fashionnova.com: 404 rather than 403, same wrong conclusion before."""
    def handler(url, kw):
        from scraper_agent.http import Response as R
        if "products.json" in url:
            return R(text="Not Found", status_code=404, url=url)
        return R(text=SHOPIFY_HTML, status_code=200, url=url)

    _routes(monkeypatch, handler)
    assert check_store("https://fashionnova.com", Settings()).is_shopify is True


def test_a_plain_site_is_correctly_rejected(monkeypatch):
    def handler(url, kw):
        from scraper_agent.http import Response as R
        if "products.json" in url:
            return R(text="Not Found", status_code=404, url=url)
        return R(text="<html><body>a blog</body></html>", status_code=200, url=url)

    _routes(monkeypatch, handler)
    check = check_store("https://blog.com", Settings())
    assert (check.is_shopify, check.catalogue_available) == (False, False)
    assert "No Shopify fingerprints" in check.detail


def test_an_unreachable_store_is_not_claimed_to_be_shopify(monkeypatch):
    from scraper_agent.http import HttpError

    def handler(url, kw):
        raise HttpError("dns failure")

    _routes(monkeypatch, handler)
    check = check_store("https://nope.invalid", Settings())
    assert (check.is_shopify, check.catalogue_available) == (False, False)
    assert "could not be reached" in check.detail


def test_json_that_is_not_a_catalogue_is_not_a_catalogue(monkeypatch):
    """A 200 with the wrong shape must not be mistaken for products."""
    def handler(url, kw):
        from scraper_agent.http import Response as R
        if "products.json" in url:
            return R(text='{"errors": "unavailable"}', status_code=200, url=url)
        return R(text=SHOPIFY_HTML, status_code=200, url=url)

    _routes(monkeypatch, handler)
    check = check_store("https://shop.com", Settings())
    assert check.catalogue_available is False
    assert check.is_shopify is True


def test_helpers_read_the_two_fields(monkeypatch):
    def handler(url, kw):
        from scraper_agent.http import Response as R
        if "products.json" in url:
            return R(text="blocked", status_code=403, url=url)
        return R(text=SHOPIFY_HTML, status_code=200, url=url)

    _routes(monkeypatch, handler)
    assert is_shopify_store("https://gymshark.com", Settings()) is True
    assert catalogue_is_readable("https://gymshark.com", Settings()) is False

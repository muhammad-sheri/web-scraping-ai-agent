"""Filtering and product grouping behind the catalogue table. Offline."""

from __future__ import annotations

import pytest

from scraper_agent.catalogue_view import (
    IN_STOCK,
    OUT_OF_STOCK,
    distinct,
    filter_records,
    group_products,
    price_bounds,
    price_label,
    variants_of,
)

ROWS = [
    {"product_id": 1, "title": "Gold Star Ring", "vendor": "David Von", "product_type": "Rings",
     "variant_title": "14K Yellow Gold / 4", "option1": "14K Yellow Gold", "option2": "4",
     "sku": "R-4", "price": "685.00", "compare_at_price": None, "discount_pct": None,
     "available": True, "tags": "Diana", "url": "https://s.com/products/gold-star-ring",
     "image": None},
    {"product_id": 1, "title": "Gold Star Ring", "vendor": "David Von", "product_type": "Rings",
     "variant_title": "14K White Gold / 8", "option1": "14K White Gold", "option2": "8",
     "sku": "R-8", "price": "1205.00", "compare_at_price": "1400.00", "discount_pct": 13.9,
     "available": False, "tags": "Diana", "url": "https://s.com/products/gold-star-ring",
     "image": "https://cdn/img.jpg"},
    {"product_id": 2, "title": "Silver Necklace", "vendor": "Other Co", "product_type": "Necklaces",
     "variant_title": "One size", "option1": "One size", "option2": None,
     "sku": "N-1", "price": "585.00", "compare_at_price": None, "discount_pct": None,
     "available": True, "tags": "sale", "url": "https://s.com/products/silver-necklace",
     "image": None},
]


# --- search ---------------------------------------------------------------


def test_search_is_case_insensitive_across_fields():
    assert len(filter_records(ROWS, search="david von")) == 2
    assert len(filter_records(ROWS, search="R-8")) == 1


def test_search_terms_are_anded_not_ored():
    """Adding a word must narrow the result, never widen it."""
    assert len(filter_records(ROWS, search="gold")) == 2
    assert len(filter_records(ROWS, search="gold white")) == 1
    assert filter_records(ROWS, search="gold necklace") == []


def test_search_ignores_description_noise():
    rows = [dict(ROWS[0], description="hand-forged in a workshop in Antwerp")]
    assert filter_records(rows, search="antwerp") == []


# --- stock ----------------------------------------------------------------


def test_stock_filter_splits_the_rows():
    assert [r["sku"] for r in filter_records(ROWS, stock=IN_STOCK)] == ["R-4", "N-1"]
    assert [r["sku"] for r in filter_records(ROWS, stock=OUT_OF_STOCK)] == ["R-8"]


def test_unknown_availability_counts_as_neither():
    """A null flag is not evidence of stock, and not evidence of the opposite."""
    rows = [dict(ROWS[0], available=None)]
    assert filter_records(rows, stock=IN_STOCK) == []
    assert filter_records(rows, stock=OUT_OF_STOCK) == []


# --- price ----------------------------------------------------------------


def test_price_range_reads_string_prices():
    kept = filter_records(ROWS, price_range=(500.0, 700.0))
    assert [r["sku"] for r in kept] == ["R-4", "N-1"]


def test_price_bounds_span_the_catalogue():
    assert price_bounds(ROWS) == (585.0, 1205.0)


def test_price_bounds_of_unpriced_rows_do_not_raise():
    assert price_bounds([{"price": None}, {"price": "n/a"}]) == (0.0, 0.0)


def test_unpriced_rows_drop_out_of_a_narrowed_range():
    rows = ROWS + [dict(ROWS[0], sku="X", price=None)]
    assert "X" not in [r["sku"] for r in filter_records(rows, price_range=(0.0, 2000.0))]


# --- facets ---------------------------------------------------------------


def test_vendor_and_type_filters_combine():
    assert len(filter_records(ROWS, vendors=["David Von"], product_types=["Rings"])) == 2
    assert filter_records(ROWS, vendors=["David Von"], product_types=["Necklaces"]) == []


def test_discounted_only_keeps_real_markdowns():
    assert [r["sku"] for r in filter_records(ROWS, on_sale_only=True)] == ["R-8"]


def test_distinct_skips_blanks():
    assert distinct(ROWS, "vendor") == ["David Von", "Other Co"]
    assert distinct([{"vendor": ""}, {"vendor": None}], "vendor") == []


def test_no_filters_is_a_no_op():
    assert filter_records(ROWS) == ROWS


# --- grouping -------------------------------------------------------------


def test_grouping_folds_variants_into_one_row_per_product():
    groups = group_products(ROWS)
    assert [g["title"] for g in groups] == ["Gold Star Ring", "Silver Necklace"]
    assert [g["variants"] for g in groups] == [2, 1]


def test_group_reports_the_price_spread_not_one_arbitrary_variant():
    ring = group_products(ROWS)[0]
    assert (ring["price_min"], ring["price_max"]) == (685.0, 1205.0)


def test_partial_stock_is_labelled_as_partial():
    """A product with some sizes gone is neither "in stock" nor "out of stock"."""
    assert group_products(ROWS)[0]["stock"] == "1/2 in stock"
    assert group_products(ROWS[2:])[0]["stock"] == "In stock"
    assert group_products(ROWS[1:2])[0]["stock"] == "Out of stock"


def test_group_prefers_the_first_variant_image_that_exists():
    assert group_products(ROWS)[0]["image"] == "https://cdn/img.jpg"


def test_group_keeps_the_best_discount():
    assert group_products(ROWS)[0]["best_discount_pct"] == 13.9


def test_variants_of_returns_that_product_only():
    assert [r["sku"] for r in variants_of(ROWS, 1)] == ["R-4", "R-8"]


def test_grouping_falls_back_to_url_when_ids_are_missing():
    rows = [dict(r, product_id=None) for r in ROWS]
    assert [g["variants"] for g in group_products(rows)] == [2, 1]


def test_filtering_then_grouping_reports_the_filtered_counts():
    """The collapsed row must describe what survived the filter, not the store."""
    visible = filter_records(ROWS, stock=IN_STOCK)
    assert group_products(visible)[0]["variants"] == 1


# --- labels ---------------------------------------------------------------


@pytest.mark.parametrize(
    "low, high, currency, expected",
    [
        (685.0, 1205.0, "USD", "$685.00 to $1,205.00"),
        (685.0, 685.0, "USD", "$685.00"),
        (10.0, 20.0, "GBP", "£10.00 to £20.00"),
        (10.0, 20.0, "XYZ", "10.00 to 20.00"),
        (None, None, "USD", "no prices"),
    ],
)
def test_price_label(low, high, currency, expected):
    assert price_label(low, high, currency) == expected

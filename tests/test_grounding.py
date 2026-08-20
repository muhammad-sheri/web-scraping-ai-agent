"""Grounding verification: numbers must have come from the page.

Written after the eval caught qwen2.5:3b returning $12.99 for all 34 products
on a page that displayed no prices whatsoever.
"""

import pytest

from scraper_agent.grounding import (
    drop_ungrounded_numbers,
    is_grounded,
    numbers_in,
)

PAGE = """
# Gadget Shop

- [Widget Pro](https://shop.com/p/1) $49.99 — In stock
- [Widget Mini](https://shop.com/p/2) $19.50 — 3 left
- Free delivery over $75
"""


# --- reading numbers out of the source ------------------------------------


def test_finds_prices_and_plain_numbers():
    assert {49.99, 19.50, 75.0, 3.0, 1.0, 2.0} <= numbers_in(PAGE)


def test_thousands_separators_are_not_decimal_points():
    assert 1299.0 in numbers_in("Priced at $1,299.00 today")


def test_decimal_comma_is_understood():
    assert 35.99 in numbers_in("Preis: 35,99 EUR")


def test_empty_text_has_no_numbers():
    assert numbers_in("") == set()
    assert numbers_in(None) == set()


# --- the check ------------------------------------------------------------


def test_price_present_on_the_page_is_grounded():
    assert is_grounded(49.99, numbers_in(PAGE)) is True
    assert is_grounded("49.99", numbers_in(PAGE)) is True
    assert is_grounded("$49.99", numbers_in(PAGE)) is True


def test_invented_price_is_not_grounded():
    """The real failure: a plausible price that appears nowhere in the source."""
    assert is_grounded(12.99, numbers_in(PAGE)) is False


def test_text_values_are_never_rejected():
    """Models legitimately tidy titles, so prose is out of scope."""
    source = numbers_in(PAGE)
    assert is_grounded("Widget Pro", source) is True
    assert is_grounded("Some product never mentioned", source) is True
    assert is_grounded(None, source) is True
    assert is_grounded(True, source) is True


def test_strings_containing_a_number_are_not_treated_as_numeric():
    # "Model 3 Runner" is a name, not the quantity 3.
    assert is_grounded("Model 3 Runner", set()) is True


def test_integer_and_float_forms_agree():
    source = numbers_in("The price is 91 dollars")
    assert is_grounded(91, source) is True
    assert is_grounded(91.0, source) is True
    assert is_grounded("91.00", source) is True


# --- applying it to records -----------------------------------------------


def test_fabricated_prices_are_nulled_and_counted():
    records = [
        {"title": "Widget Pro", "price": 49.99},   # real
        {"title": "Widget Mini", "price": 12.99},  # invented
    ]
    cleaned, removed = drop_ungrounded_numbers(records, PAGE)

    assert removed == 1
    assert cleaned[0]["price"] == 49.99
    assert cleaned[1]["price"] is None
    assert cleaned[1]["title"] == "Widget Mini"  # the row survives, the lie does not


def test_valid_data_is_left_completely_alone():
    """The guard must not damage correct extractions."""
    records = [
        {"title": "Widget Pro", "price": 49.99, "url": "https://shop.com/p/1"},
        {"title": "Widget Mini", "price": 19.50, "url": "https://shop.com/p/2"},
    ]
    cleaned, removed = drop_ungrounded_numbers(records, PAGE)

    assert removed == 0
    assert cleaned == records


def test_the_olipop_case():
    """34 identical invented prices on a page with no prices at all."""
    page_without_prices = "# Flavors\n\n- Vintage Cola\n- Classic Root Beer\n- Crisp Apple"
    records = [{"title": t, "price": 12.99} for t in ("Vintage Cola", "Classic Root Beer", "Crisp Apple")]

    cleaned, removed = drop_ungrounded_numbers(records, page_without_prices)

    assert removed == 3
    assert all(r["price"] is None for r in cleaned)
    assert [r["title"] for r in cleaned] == ["Vintage Cola", "Classic Root Beer", "Crisp Apple"]


def test_empty_records_are_safe():
    assert drop_ungrounded_numbers([], PAGE) == ([], 0)

"""Streamlit UI rendering, via AppTest.

Layout and wiring only, with no Scrape click, since that would hit the network;
the agent pipeline itself is covered elsewhere.

Two classes of regression these exist for:

* An earlier edit left part of the sidebar re-indented outside its
  `with st.sidebar:` block, which no offline test caught because app.py is not
  otherwise exercised by pytest. AppTest runs the real script and surfaces
  exactly that.
* The results section renders from session_state rather than from the run
  block, because every filter click reruns the script with the Scrape button
  unclicked. Rendering results only inside `if run:` would wipe the table on
  the first filter interaction.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _run(monkeypatch, public_demo: bool) -> AppTest:
    if public_demo:
        monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    else:
        monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    return at


def _html(at: AppTest) -> str:
    """Everything ui.py rendered this run, concatenated."""
    return "\n".join(el.proto.body for el in at.get("html"))


# --- shell ----------------------------------------------------------------


def test_normal_mode_renders_without_exceptions(monkeypatch):
    assert not _run(monkeypatch, public_demo=False).exception


def test_public_demo_renders_without_exceptions(monkeypatch):
    assert not _run(monkeypatch, public_demo=True).exception


@pytest.mark.parametrize("public_demo", [True, False])
def test_neither_mode_renders_a_sidebar(monkeypatch, public_demo):
    """The redesign has no sidebar. A scrape needs one input, not a control panel."""
    at = _run(monkeypatch, public_demo)
    assert not at.sidebar.header
    assert not at.sidebar.markdown
    assert not at.sidebar.radio


def test_the_hero_carries_the_theme_stylesheet(monkeypatch):
    at = _run(monkeypatch, public_demo=True)
    body = _html(at)
    assert "<style>" in body
    assert "fx-hero" in body


def test_settings_live_in_a_popover_in_normal_mode(monkeypatch):
    """The model controls moved off-canvas-left to next-to-the-input."""
    at = _run(monkeypatch, public_demo=False)
    provider = next(r for r in at.radio if r.label == "Provider")
    assert set(provider.options) == {"openai", "ollama"}
    assert any(s.label == "Browser rendering" for s in at.select_slider)


def test_public_demo_has_no_model_controls_at_all(monkeypatch):
    """Nothing there for a stranger to misconfigure, and no key to spend."""
    at = _run(monkeypatch, public_demo=True)
    assert not any(r.label == "Provider" for r in at.radio)
    assert not any(t.label == "OpenAI API key" for t in at.text_input)


def test_normal_mode_offers_both_extraction_modes(monkeypatch):
    at = _run(monkeypatch, public_demo=False)
    mode = next(s for s in at.segmented_control if s.label == "Mode")
    assert set(mode.options) == {"Ask in plain language", "Full Shopify catalogue"}


def test_public_demo_skips_the_mode_choice(monkeypatch):
    """Shopify mode is forced, not offered, because there is nothing to route to."""
    at = _run(monkeypatch, public_demo=True)
    assert not any(s.label == "Mode" for s in at.segmented_control)


def test_public_demo_prefills_a_working_store_url(monkeypatch):
    at = _run(monkeypatch, public_demo=True)
    assert next(t for t in at.text_input if t.key == "shop_url").value == "https://www.allbirds.com"


def test_both_modes_show_a_scrape_button(monkeypatch):
    """Regression: a stray indent once put widgets outside their container,
    which raised at import time and would have shown no button at all."""
    for demo in (True, False):
        at = _run(monkeypatch, demo)
        assert any(b.label == "Scrape" for b in at.button)


def test_normal_mode_has_no_cooldown_state(monkeypatch):
    """The rate-limit courtesy throttle is demo-only, not a normal-use tax."""
    assert "last_run_ts" not in _run(monkeypatch, public_demo=False).session_state


# --- results --------------------------------------------------------------

CATALOGUE_ROWS = [
    {"product_id": 1, "title": "Gold Star Ring", "url": "https://s.com/products/gold-star-ring",
     "image": None, "vendor": "David Von", "product_type": "Rings",
     "variant_title": "14K Yellow Gold / 4", "option1": "14K Yellow Gold", "option2": "4",
     "option3": None, "sku": "R-4", "variant_id": 11, "price": "685.00",
     "compare_at_price": None, "discount_pct": None, "available": True, "grams": 0,
     "tags": "Diana", "options": "Color / Ring size"},
    {"product_id": 1, "title": "Gold Star Ring", "url": "https://s.com/products/gold-star-ring",
     "image": None, "vendor": "David Von", "product_type": "Rings",
     "variant_title": "14K White Gold / 8", "option1": "14K White Gold", "option2": "8",
     "option3": None, "sku": "R-8", "variant_id": 12, "price": "1205.00",
     "compare_at_price": "1400.00", "discount_pct": 13.9, "available": False, "grams": 0,
     "tags": "Diana", "options": "Color / Ring size"},
    {"product_id": 2, "title": "Silver Necklace", "url": "https://s.com/products/silver-necklace",
     "image": None, "vendor": "Other Co", "product_type": "Necklaces",
     "variant_title": "One size", "option1": "One size", "option2": None,
     "option3": None, "sku": "N-1", "variant_id": 21, "price": "585.00",
     "compare_at_price": None, "discount_pct": None, "available": True, "grams": 0,
     "tags": "sale", "options": "Title"},
]


def _with_catalogue(monkeypatch, **state) -> AppTest:
    from scraper_agent.agent import ScrapeResult

    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    at = AppTest.from_file(APP_PATH)
    at.session_state["result"] = ScrapeResult(
        url="https://s.com",
        final_url="https://s.com",
        prompt="(Shopify catalogue)",
        records=CATALOGUE_ROWS,
        provider="shopify-api",
    )
    at.session_state["result_shopify"] = True
    at.session_state["result_currency"] = "USD"
    at.session_state["result_url"] = "https://s.com"
    # Filter widget keys carry a generation number (see app.fkey), so seeding a
    # bare "flt_search" would set a key no widget reads.
    for key, value in state.items():
        at.session_state[f"{key}_0" if key.startswith("flt_") else key] = value
    at.run(timeout=30)
    assert not at.exception
    return at


def test_results_render_without_rerunning_the_scrape(monkeypatch):
    """The filters are widgets; a filter click must not wipe the table."""
    at = _with_catalogue(monkeypatch)
    assert at.dataframe, "results should render from session_state alone"


def test_the_hero_collapses_once_there_are_results(monkeypatch):
    # Match the element, not the stylesheet, since the rule is always present.
    assert '<div class="fx-hero">' in _html(_run(monkeypatch, public_demo=True))
    assert '<div class="fx-hero fx-compact">' in _html(_with_catalogue(monkeypatch))


# --- stat tiles -----------------------------------------------------------


def test_catalogue_stats_replace_the_cost_readout(monkeypatch):
    """Catalogue data has no token cost, so the interesting numbers are stock ones."""
    at = _with_catalogue(monkeypatch)
    body = _html(at)
    for label in ("Products", "Variants", "In stock", "Price range"):
        assert label in body
    assert "Est. cost" not in body
    assert "free" not in body.lower()


def test_stats_count_products_not_just_rows(monkeypatch):
    body = _html(_with_catalogue(monkeypatch))
    assert ">2<" in body                       # 2 products
    assert ">3<" in body                       # 3 variants
    assert "$585.00 to $1,205.00" in body


def test_stats_describe_the_filtered_set_with_the_total_for_context(monkeypatch):
    """Filtering must move the numbers, or the filters look broken."""
    at = _with_catalogue(monkeypatch, flt_stock="Out of stock")
    body = _html(at)
    assert "of 3 rows" in body
    assert "of 2 in the store" in body


# --- the filter panel -----------------------------------------------------


def test_filter_panel_offers_search_stock_price_and_quantity(monkeypatch):
    at = _with_catalogue(monkeypatch)
    assert any(t.label == "Search" for t in at.text_input)
    stock = next(s for s in at.segmented_control if s.label == "Availability")
    assert set(stock.options) == {"Any", "In stock", "Out of stock"}
    assert any(s.label == "Price (USD)" for s in at.slider)
    assert any(n.label == "Rows to show" for n in at.number_input)
    assert any(b.label == "Reset" for b in at.button)


def test_facets_are_offered_for_vendor_and_product_type(monkeypatch):
    at = _with_catalogue(monkeypatch)
    labels = {p.label for p in at.pills}
    assert {"Vendor", "Product type"} <= labels


def test_the_panel_header_names_the_active_filters(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_search="gold", flt_stock="In stock")
    body = _html(at)
    assert "gold" in body and "In stock" in body


def test_the_panel_header_says_so_when_nothing_is_filtering(monkeypatch):
    assert "No filters" in _html(_with_catalogue(monkeypatch))


def test_a_full_width_price_slider_does_not_count_as_a_filter(monkeypatch):
    """The slider always has a value; only a narrowed one is filtering."""
    at = _with_catalogue(monkeypatch, flt_price=(585.0, 1205.0))
    assert "No filters" in _html(at)


def test_a_narrowed_price_slider_does_count(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_price=(600.0, 900.0))
    body = _html(at)
    assert "No filters" not in body
    assert "600 to 900" in body


def test_stock_filter_narrows_the_table(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_stock="Out of stock", flt_view="Every variant")
    assert "Showing 1 of 1" in " ".join(c.value for c in at.caption)


def test_search_narrows_the_table(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_search="necklace", flt_view="Every variant")
    assert "Showing 1 of 1" in " ".join(c.value for c in at.caption)


def test_empty_filter_result_explains_itself(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_search="no such product")
    assert "Nothing matches these filters" in _html(at)


# --- grouping -------------------------------------------------------------


def test_variants_are_grouped_into_one_row_per_product_by_default(monkeypatch):
    at = _with_catalogue(monkeypatch)
    view = next(s for s in at.segmented_control if s.key == "flt_view_0")
    assert view.value == "Grouped by product"
    assert len(at.dataframe[0].value) == 2          # two products, three variants
    assert "2 product(s)" in " ".join(c.value for c in at.caption)


def test_ungrouping_shows_every_variant_row(monkeypatch):
    at = _with_catalogue(monkeypatch, flt_view="Every variant")
    assert len(at.dataframe[0].value) == 3


def test_the_column_picker_appears_only_for_the_flat_table(monkeypatch):
    """The grouped view has its own fixed summary columns, so a picker would lie."""
    flat = _with_catalogue(monkeypatch, flt_view="Every variant")
    assert any(m.label == "Columns to show" for m in flat.multiselect)
    grouped = _with_catalogue(monkeypatch)
    assert not any(m.label == "Columns to show" for m in grouped.multiselect)


def test_the_column_picker_exposes_every_scraped_field(monkeypatch):
    """The point of pulling extra fields is being able to switch them on."""
    at = _with_catalogue(monkeypatch, flt_view="Every variant")
    picker = next(m for m in at.multiselect if m.label == "Columns to show")
    assert {"variant_id", "option1", "discount_pct", "compare_at_price"} <= set(picker.options)


def test_reset_starts_a_new_widget_generation(monkeypatch):
    """Reset cannot just delete the keys.

    A button click ships every widget's current value in the same message, and
    those values are reapplied when the widgets re-register during the rerun, so
    the search box kept its text while the header chips said "No filters".
    Bumping the generation gives Streamlit widgets it has never seen. AppTest
    does not model that round trip, so this asserts the mechanism; the
    behaviour itself was verified in a browser.
    """
    at = _with_catalogue(monkeypatch, flt_search="gold", flt_stock="In stock")
    assert at.session_state["flt_search_0"] == "gold"

    next(b for b in at.button if b.label == "Reset").click().run(timeout=30)
    assert at.session_state["filter_gen"] == 1
    assert "flt_search_0" not in at.session_state
    assert "flt_stock_0" not in at.session_state
    assert "No filters" in _html(at)


def test_filter_widgets_carry_the_generation_in_their_keys(monkeypatch):
    at = _with_catalogue(monkeypatch)
    assert any(t.key == "flt_search_0" for t in at.text_input)
    assert any(s.key == "flt_stock_0" for s in at.segmented_control)


def test_downloads_are_still_offered(monkeypatch):
    at = _with_catalogue(monkeypatch)
    assert {"Download JSON", "Download CSV"} <= {b.label for b in at.download_button}


# --- column presentation --------------------------------------------------


def test_url_renders_as_a_link_and_image_as_a_picture():
    import app as app_module

    config = app_module.column_config(pd.DataFrame(CATALOGUE_ROWS), "USD")
    assert config["url"]["type_config"]["type"] == "link"
    assert config["image"]["type_config"]["type"] == "image"


def test_prices_are_formatted_in_the_store_currency():
    import app as app_module

    frame = pd.DataFrame(CATALOGUE_ROWS)
    assert app_module.column_config(frame, "USD")["price"]["type_config"]["format"] == "$%.2f"
    assert app_module.column_config(frame, "EUR")["price"]["type_config"]["format"] == "€%.2f"
    # An unknown currency code must not print a wrong symbol.
    assert app_module.column_config(frame, "ZZZ")["price"]["type_config"]["format"] == "%.2f"


def test_every_column_gets_a_readable_header():
    """No raw snake_case key should ever reach a table header."""
    import app as app_module

    frame = pd.DataFrame(CATALOGUE_ROWS)
    config = app_module.column_config(frame, "USD")
    assert set(config) == set(frame.columns)
    assert config["product_type"]["label"] == "Type"
    assert config["variant_title"]["label"] == "Variant"
    assert config["option1"]["label"] == "Option 1"
    assert config["vendor"]["label"] == "Vendor"       # derived, not hand-listed
    assert not any("_" in c["label"] for c in config.values())


def test_string_prices_become_numbers_for_display():
    """Otherwise the price column sorts lexically: "9" after "100"."""
    import app as app_module

    frame = app_module.display_frame(CATALOGUE_ROWS)
    assert list(frame["price"]) == [685.0, 1205.0, 585.0]


def test_sparse_numeric_columns_become_nan_not_the_string_none():
    """An object column of floats-and-Nones renders the word "None" per cell."""
    import app as app_module

    frame = app_module.display_frame(CATALOGUE_ROWS)
    assert str(frame["discount_pct"].dtype) == "float64"
    assert frame["discount_pct"].isna().sum() == 2


# --- which columns are worth showing --------------------------------------


def test_all_empty_columns_are_dropped_from_the_default_view():
    import app as app_module

    rows = [dict(r, option3=None) for r in CATALOGUE_ROWS]
    assert "option3" not in app_module.useful_columns(rows, ["sku", "option3"])


def test_an_all_zero_column_is_dropped_too():
    """Shopify reports grams=0 for every item in a jewellery store."""
    import app as app_module

    assert app_module.useful_columns(CATALOGUE_ROWS, ["sku", "grams"]) == ["sku"]


def test_false_is_content_because_it_means_out_of_stock():
    """False == 0 in Python; an all-out-of-stock column must still show."""
    import app as app_module

    rows = [dict(r, available=False) for r in CATALOGUE_ROWS]
    assert "available" in app_module.useful_columns(rows, ["sku", "available"])


def test_dropping_everything_falls_back_to_the_candidates():
    import app as app_module

    assert app_module.useful_columns([{"a": None}], ["a"]) == ["a"]


def test_default_columns_lead_with_the_readable_ones():
    import app as app_module

    rows = [dict(r, image="https://cdn/a.jpg") for r in CATALOGUE_ROWS]
    columns = app_module.default_columns(rows)
    assert columns[:3] == ["image", "title", "url"]
    assert "grams" not in columns


def test_a_store_with_no_photos_gets_no_photo_column():
    import app as app_module

    assert "image" not in app_module.default_columns(CATALOGUE_ROWS)

"""Streamlit UI for the web scraping agent.

Run with:  streamlit run app.py

Layout notes, because they are decisions rather than accidents:

* No sidebar. A scrape has exactly one required input, a URL. Hiding the
  model settings off-canvas made the page feel like a control panel for
  something that is really a search box. Settings now live in a popover next
  to the input they affect, and the public demo (which has nothing to
  configure) shows no settings affordance at all.
* Results are stored in session_state and rendered outside the run block.
  Filters are widgets, so every filter click reruns the script with the
  Scrape button unclicked; without this the first click would wipe the table.
* The look lives in .streamlit/config.toml (Streamlit's own components) and
  ui.py (the parts Streamlit has no component for).
"""

from __future__ import annotations

import json
import os
import time

import pandas as pd
import streamlit as st

import ui
from scraper_agent.agent import ScrapeAgent
from scraper_agent.catalogue_view import (
    ANY_STOCK,
    CURRENCY_SYMBOLS,
    IN_STOCK,
    STOCK_CHOICES,
    distinct,
    filter_records,
    group_products,
    price_bounds,
    price_label,
    variants_of,
)
from scraper_agent.config import Settings
from scraper_agent.fetch import FetchError
from scraper_agent.output import columns_for
from scraper_agent.providers.base import ProviderError
from scraper_agent.providers.ollama_provider import OllamaProvider
from scraper_agent.providers.openai_provider import OpenAIProvider
from scraper_agent.shopify import (
    ShopifyError,
    fetch_store_meta,
    check_store,
    to_price,
)

st.set_page_config(
    page_title="Catalogue Scraper",
    page_icon="🕸️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hosted free tiers (Streamlit Community Cloud, Hugging Face Spaces, ...) have
# no way to run Ollama, and a public link means anyone can spend an OpenAI key
# left configured. Setting this secret restricts the live demo to the Shopify
# catalogue mode, which needs no model and no key. Local runs are unaffected,
# since the flag defaults to off.
PUBLIC_DEMO = os.getenv("PUBLIC_DEMO_MODE", "false").strip().lower() == "true"
DEMO_COOLDOWN_S = 5  # courtesy throttle against accidental rapid-fire clicks

EXAMPLES = {
    "Hacker News": (
        "https://news.ycombinator.com",
        "every story: title, points, comment count and link",
    ),
    "Python releases": (
        "https://www.python.org/downloads/",
        "each release: version number, release date and download link",
    ),
    "Books to scrape": (
        "https://books.toscrape.com",
        "every book with its title, price and availability",
    ),
}

FILTER_KEYS = (
    "flt_search", "flt_stock", "flt_price", "flt_vendor", "flt_type",
    "flt_sale", "flt_quantity", "flt_columns", "flt_view", "product_table",
)

DEFAULT_COLUMNS = [
    "image", "title", "url", "vendor", "product_type", "variant_title", "sku",
    "price", "compare_at_price", "discount_pct", "available", "tags",
]

# Every column that reaches a table gets a human label; anything not listed
# falls back to "some_field" -> "Some field" so no raw key ever shows.
LABELS = {
    "product_id": "Product ID",
    "variant_id": "Variant ID",
    "url": "Product page",
    "image": "Photo",
    "variant_title": "Variant",
    "product_type": "Type",
    "compare_at_price": "Was",
    "discount_pct": "Off",
    "best_discount_pct": "Best off",
    "available": "In stock",
    "image_count": "Images",
    "grams": "Weight (g)",
    "price_min": "From",
    "price_max": "To",
    "sku": "SKU",
    "option1": "Option 1",
    "option2": "Option 2",
    "option3": "Option 3",
    "published_at": "Published",
    "created_at": "Created",
    "updated_at": "Updated",
    "variant_updated_at": "Variant updated",
    "requires_shipping": "Ships",
}

# Columns that must be numeric for sorting, alignment and blank-instead-of-None.
NUMERIC_COLUMNS = (
    "price", "compare_at_price", "price_min", "price_max", "discount_pct",
    "best_discount_pct", "grams", "image_count", "variants", "in_stock",
    "position", "product_id", "variant_id",
)

GROUPED, FLAT = "Grouped by product", "Every variant"


def active_theme() -> str:
    """Which theme the viewer is actually on, so ui.css can match it.

    st.context is unavailable outside a script run (and in some test
    harnesses), and light is the configured default, so that is the fallback.
    """
    try:
        return str(st.context.theme.type or "light")
    except Exception:  # noqa: BLE001 - presentation must never break the app
        return "light"


st.html(ui.css(active_theme()))

# Compact once there are results to look at. See ui.hero.
HAS_RESULT = st.session_state.get("result") is not None

if PUBLIC_DEMO:
    st.html(
        ui.hero(
            "Shopify catalogue scraper",
            "Point it at any Shopify store and get the whole catalogue as structured rows. "
            "One row per variant, carrying the store's own SKUs, prices and stock flags.",
            [
                "Exact store data",
                "Every size & colour",
                "Filter · group · export",
                ("View source ↗", ui.GITHUB_URL),
            ],
            compact=HAS_RESULT,
        )
    )
else:
    st.html(
        ui.hero(
            "Web Scraping AI Agent",
            "Describe what you want in plain language. The agent fetches the page, designs a "
            "schema for your request and returns structured records. Shopify stores skip "
            "the model entirely and come straight from the store's own product API.",
            [
                "Natural-language extraction",
                "Exact Shopify catalogues",
                "Multi-page crawling",
                ("View source ↗", ui.GITHUB_URL),
            ],
            compact=HAS_RESULT,
        )
    )

settings = Settings.from_env()

# Defaults for every control that only exists on one path, so the run block
# below never depends on which branch rendered.
provider_name, api_key, model = "openai", "", ""
max_chars, max_chunks = settings.max_chunk_chars, settings.max_chunks
render_mode, respect_robots, strip_boilerplate, fields_raw = "auto", settings.respect_robots, True, ""
prompt, follow_pages, max_pages, max_products = "", False, 1, None


# --- the ask --------------------------------------------------------------

with st.container():
    st.html(ui.anchor("card"))

    if PUBLIC_DEMO:
        shopify_mode = True
        st.html(ui.panel_head("◎", "Which store?", ["Any Shopify storefront"]))
    else:
        head, settings_col = st.columns([5, 1], vertical_alignment="bottom")
        with head:
            st.html(ui.panel_head("◎", "What should the agent scrape?"))
        with settings_col:
            with st.popover("Settings", icon=":material/tune:", width="stretch"):
                st.markdown("**Model**")
                provider_name = st.radio(
                    "Provider",
                    ["openai", "ollama"],
                    index=0 if settings.provider == "openai" else 1,
                    horizontal=True,
                    help="OpenAI is pay-per-token. Ollama runs locally.",
                )
                if provider_name == "openai":
                    api_key = st.text_input(
                        "OpenAI API key",
                        type="password",
                        value=os.getenv("OPENAI_API_KEY", ""),
                        help="Loaded from .env when present. Billed per token. This is not "
                        "the free ChatGPT tier.",
                    )
                    model = st.selectbox(
                        "Model", ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"], index=0
                    )
                else:
                    api_key = ""
                    installed = OllamaProvider(host=settings.ollama_host).available_models()
                    if installed:
                        model = st.selectbox("Local model", installed, index=0)
                    else:
                        model = st.text_input("Local model", value=settings.ollama_model)
                        st.warning("No local models found. Pull one:\n\n`ollama pull qwen2.5:7b`",
                                   icon=":material/warning:")

                st.divider()
                st.markdown("**Fetching**")
                render_mode = st.select_slider(
                    "Browser rendering",
                    options=["never", "auto", "always"],
                    value="auto",
                    help="'auto' launches a headless browser only when the page looks "
                    "empty without JavaScript.",
                )
                respect_robots = st.toggle("Respect robots.txt", value=settings.respect_robots)
                strip_boilerplate = st.toggle(
                    "Strip nav/header/footer",
                    value=True,
                    help="Turn off when the data you want lives in the site chrome.",
                )

                st.divider()
                st.markdown("**Advanced**")
                max_chars = st.number_input(
                    "Characters per LLM call", 2_000, 60_000, settings.max_chunk_chars, step=1_000
                )
                max_chunks = st.number_input("Max LLM calls per page", 1, 50, settings.max_chunks)
                fields_raw = st.text_input(
                    "Fixed fields (optional)",
                    placeholder="title,price,url",
                    help="Set these to skip schema inference and force exact column names.",
                )

        mode = st.segmented_control(
            "Mode",
            ["Ask in plain language", "Full Shopify catalogue"],
            default="Ask in plain language",
            label_visibility="collapsed",
            help="Shopify stores publish their whole catalogue as JSON: exact prices, "
            "SKUs and every size variant, with no AI involved.",
        )
        shopify_mode = mode == "Full Shopify catalogue"

    if shopify_mode:
        entry, action = st.columns([4, 1], vertical_alignment="bottom")
        with entry:
            url = st.text_input(
                "Store URL",
                value="https://www.allbirds.com" if PUBLIC_DEMO else "",
                placeholder="https://www.allbirds.com",
                key="shop_url",
            )
        with action:
            run = st.button("Scrape", type="primary", width="stretch", icon=":material/bolt:")

        scope, limit_col = st.columns([2, 3], vertical_alignment="center")
        with scope:
            limit_all = st.toggle("Fetch the entire catalogue", value=True)
        with limit_col:
            max_products = (
                None
                if limit_all
                else int(st.number_input("Product limit", 1, 5_000, 50, step=10,
                                         label_visibility="collapsed"))
            )
        st.caption(
            "One row per variant. Each size or colour has its own SKU, price and stock flag."
            + ("" if PUBLIC_DEMO else " Not a Shopify store? Switch to plain language above.")
        )
    else:
        example = st.selectbox("Start from an example", ["None"] + list(EXAMPLES))
        default_url, default_prompt = EXAMPLES.get(example, ("", ""))

        col_url, col_prompt = st.columns([1, 2])
        with col_url:
            url = st.text_input("URL", value=default_url, placeholder="example.com/products")
        with col_prompt:
            prompt = st.text_area(
                "What to extract",
                value=default_prompt,
                placeholder="every product with its name, price and link",
                height=80,
            )

        pages_col, max_col, action = st.columns([2, 2, 1], vertical_alignment="bottom")
        with pages_col:
            follow_pages = st.toggle(
                "Follow 'next' links",
                value=False,
                help="Listings usually span several pages. Turn this on to extract all of them.",
            )
        with max_col:
            max_pages = int(st.number_input("Max pages", 1, 50, 5)) if follow_pages else 1
        with action:
            run = st.button("Scrape", type="primary", width="stretch", icon=":material/bolt:")


# --- display helpers ------------------------------------------------------
# Above the run block so the results section renders on plain reruns (a filter
# change) as well as on the run that produced them.


def money_format(currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    return f"{symbol}%.2f" if symbol else "%.2f"


def display_frame(records: list[dict], columns: list[str] | None = None) -> pd.DataFrame:
    """Records as a frame with the numeric columns actually numeric.

    Two bugs live here if this is skipped. Shopify sends prices as strings,
    which sort lexically ("9" after "100") and cannot be right-aligned or
    currency-formatted. And a column of floats-and-Nones is an object column,
    which renders the word "None" in every empty cell instead of a blank.
    `to_numeric` turns those into NaN, which the grid draws as empty.

    The raw values stay in `records` for the download, so nothing is lost.
    """
    frame = pd.DataFrame(records, columns=columns or columns_for(records))
    for field in NUMERIC_COLUMNS:
        if field in frame.columns:
            frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame


def default_columns(records: list[dict]) -> list[str]:
    """The opening column set: the dozen worth reading, minus the empty ones."""
    available = columns_for(records)
    return useful_columns(records, [c for c in DEFAULT_COLUMNS if c in available]) or available


def _has_content(value: object) -> bool:
    """Whether a cell says anything at all.

    False is content, because it is the answer "out of stock". A numeric zero in
    a column that is zero all the way down is not: Shopify reports grams=0 for
    every item in a jewellery store, and a column of zeroes is just width.
    """
    if value is None or value == "" or value == []:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)) and value == 0:
        return False
    return True


def useful_columns(records: list[dict], candidates: list[str]) -> list[str]:
    """Candidates that carry content somewhere in these rows.

    An all-empty column is wasted width and, worse, a column of grey "None"s.
    That is Streamlit's null-cell indicator, not a bug in the data. A store
    with no sale prices should simply not get a "Best off" column.
    """
    kept = [c for c in candidates if any(_has_content(r.get(c)) for r in records)]
    return kept or list(candidates)


def label_for(name: str) -> str:
    return LABELS.get(name) or name.replace("_", " ").capitalize()


def column_config(frame: pd.DataFrame, currency: str) -> dict:
    """Make the columns behave like what they are: links, images, money, flags."""
    money = money_format(currency)
    typed = {
        # display_text pulls the handle out of the URL, so the cell reads
        # "the-signature-star-ring" and opens the product page when clicked.
        "url": st.column_config.LinkColumn(
            label_for("url"), display_text=r"/products/([^?#]+)", width="medium"
        ),
        "image": st.column_config.ImageColumn(label_for("image"), width="small"),
        "price": st.column_config.NumberColumn("Price", format=money),
        "compare_at_price": st.column_config.NumberColumn(label_for("compare_at_price"), format=money),
        "price_min": st.column_config.NumberColumn(label_for("price_min"), format=money),
        "price_max": st.column_config.NumberColumn(label_for("price_max"), format=money),
        "discount_pct": st.column_config.NumberColumn(label_for("discount_pct"), format="%.0f%%"),
        "best_discount_pct": st.column_config.NumberColumn(
            label_for("best_discount_pct"), format="%.0f%%"
        ),
        "available": st.column_config.CheckboxColumn(label_for("available")),
        "requires_shipping": st.column_config.CheckboxColumn(label_for("requires_shipping")),
        "taxable": st.column_config.CheckboxColumn("Taxable"),
        "product_id": st.column_config.NumberColumn(label_for("product_id"), format="%d"),
        "variant_id": st.column_config.NumberColumn(label_for("variant_id"), format="%d"),
        "grams": st.column_config.NumberColumn(label_for("grams"), format="%d"),
        "image_count": st.column_config.NumberColumn(label_for("image_count"), format="%d"),
        "variants": st.column_config.NumberColumn("Variants", format="%d"),
        "in_stock": st.column_config.NumberColumn("In stock", format="%d"),
        "position": st.column_config.NumberColumn("Position", format="%d"),
        "description": st.column_config.TextColumn("Description", width="medium"),
        "title": st.column_config.TextColumn("Title", width="medium"),
    }
    # Anything without a specific type still gets a readable header rather
    # than the raw snake_case key.
    return {
        name: typed.get(name, st.column_config.TextColumn(label_for(name)))
        for name in frame.columns
    }


def fkey(name: str) -> str:
    """Filter widget keys, carrying a generation number.

    Reset cannot just delete these from session_state. A button click ships
    every widget's current value to the server in the same message, and those
    values are reapplied as the widgets re-register during the rerun, so the
    search box still read "gold necklace" while the filter logic saw an empty
    search and the header chips said "No filters".

    Bumping the generation hands Streamlit widgets it has never seen, which
    come up at their defaults with no stale client value to restore. Found in
    a browser; AppTest does not model that round trip and reported the delete
    as working.
    """
    return f"{name}_{st.session_state.get('filter_gen', 0)}"


def reset_filters() -> None:
    generation = st.session_state.get("filter_gen", 0)
    for name in FILTER_KEYS:
        st.session_state.pop(f"{name}_{generation}", None)
    st.session_state["filter_gen"] = generation + 1


def render_filters(records: list[dict], currency: str) -> list[dict]:
    """The filter panel, the loudest block on the page. Returns surviving rows."""
    low, high = price_bounds(records)
    vendors, types = distinct(records, "vendor"), distinct(records, "product_type")
    has_price_range = high > low

    with st.container():
        st.html(ui.anchor("filter"))
        head, reset = st.columns([5, 1], vertical_alignment="center")
        with head:
            st.html(ui.panel_head("⌕", "Filters", _active_chips(low, high)))
        with reset:
            st.button(
                "Reset", width="stretch", on_click=reset_filters,
                icon=":material/restart_alt:", help="Clear every filter",
            )

        top = st.columns([3, 2], vertical_alignment="bottom")
        search = top[0].text_input(
            "Search",
            placeholder="title, SKU, tag, colour…",
            key=fkey("flt_search"),
            icon=":material/search:",
            help="All words must match, so 'gold ring' finds rows containing both.",
        )
        stock = top[1].segmented_control(
            "Availability", STOCK_CHOICES, default=ANY_STOCK, key=fkey("flt_stock"),
        ) or ANY_STOCK

        mid = st.columns([3, 1, 1], vertical_alignment="bottom")
        if has_price_range:
            price_range = mid[0].slider(
                f"Price ({currency})" if currency else "Price",
                min_value=float(low),
                max_value=float(high),
                value=(float(low), float(high)),
                key=fkey("flt_price"),
            )
            # Full width means "no price filter", so unpriced rows stay in.
            if price_range == (float(low), float(high)):
                price_range = None
        else:
            price_range = None
            mid[0].text_input(
                "Price", value=price_label(low or None, high or None, currency),
                disabled=True,
            )
        # Shopify's public catalogue publishes a stock boolean, not a count, so
        # "quantity" here means how many rows to put on screen at once.
        mid[1].number_input(
            "Rows to show", 10, 10_000, 200, step=50, key=fkey("flt_quantity"),
            help="Caps the table only. Downloads always contain every filtered row.",
        )
        on_sale = mid[2].toggle("Discounted only", key=fkey("flt_sale"))

        chosen_vendors, chosen_types = [], []
        if len(vendors) > 1 or len(types) > 1:
            facets = st.columns(2)
            if len(vendors) > 1:
                chosen_vendors = _facet(facets[0], "Vendor", vendors, "flt_vendor")
            if len(types) > 1:
                chosen_types = _facet(facets[1], "Product type", types, "flt_type")

    st.session_state["row_cap"] = int(st.session_state.get(fkey("flt_quantity"), 200))
    return filter_records(
        records,
        search=search,
        stock=stock,
        price_range=price_range,
        vendors=chosen_vendors,
        product_types=chosen_types,
        on_sale_only=on_sale,
    )


def _facet(column, label: str, options: list[str], name: str) -> list[str]:
    """Pills read better than a dropdown, but only while they fit on one line."""
    with column:
        if len(options) <= 8:
            return st.pills(label, options, selection_mode="multi", key=fkey(name)) or []
        return st.multiselect(label, options, key=fkey(name),
                              placeholder=f"Any {label.lower()}") or []


def _active_chips(low: float, high: float) -> list[str]:
    """Summarise what is currently filtering the table, for the panel header."""
    state = st.session_state
    chips = []
    if state.get(fkey("flt_search")):
        chips.append(f'\u201c{state[fkey("flt_search")]}\u201d')
    if state.get(fkey("flt_stock")) and state[fkey("flt_stock")] != ANY_STOCK:
        chips.append(str(state[fkey("flt_stock")]))
    if state.get(fkey("flt_sale")):
        chips.append("Discounted")
    for name in ("flt_vendor", "flt_type"):
        chosen = state.get(fkey(name)) or []
        if chosen:
            chips.append(", ".join(map(str, chosen[:2]))
                         + (f" +{len(chosen) - 2}" if len(chosen) > 2 else ""))
    # The slider always has a value; only a value narrower than the full
    # catalogue range is actually filtering anything.
    price = state.get(fkey("flt_price"))
    if price and (float(price[0]) > low or float(price[1]) < high):
        chips.append(f"{float(price[0]):,.0f} to {float(price[1]):,.0f}")
    return chips or ["No filters, showing everything"]


def render_catalogue_stats(visible: list[dict], total: list[dict], currency: str) -> None:
    """Stat tiles that describe the *filtered* set, with the total for context."""
    filtered = len(visible) != len(total)
    products = len({r.get("product_id") for r in visible})
    all_products = len({r.get("product_id") for r in total})
    in_stock = sum(1 for r in visible if r.get("available") is True)
    low, high = price_bounds(visible)
    share = f"{in_stock / len(visible):.0%} of shown" if visible else "nothing shown"

    st.html(ui.stat_tiles([
        {"label": "Products", "value": f"{products:,}",
         "sub": f"of {all_products:,} in the store" if filtered else "in the catalogue"},
        {"label": "Variants", "value": f"{len(visible):,}",
         "sub": f"of {len(total):,} rows" if filtered else "one row per size/colour",
         "tone": "primary"},
        {"label": "In stock", "value": f"{in_stock:,}", "sub": share,
         "tone": "green" if in_stock else "red"},
        {"label": "Price range", "value": price_label(low or None, high or None, currency),
         "sub": f"in {currency}" if currency else "store currency unknown", "tone": "orange"},
    ]))


def render_grouped(visible: list[dict], currency: str) -> None:
    """One row per product, expandable to its variants.

    Streamlit has no nested rows, so selection stands in for a disclosure
    triangle: ticking a product renders its variants underneath.
    """
    summaries = group_products(visible)
    shown = summaries[: st.session_state.get("row_cap", 200)]

    frame = display_frame(shown, useful_columns(shown, [
        "image", "title", "url", "vendor", "product_type", "variants", "stock",
        "price_min", "price_max", "best_discount_pct", "options", "tags",
    ]))
    event = st.dataframe(
        frame,
        width="stretch",
        height=560,
        hide_index=True,
        column_config=column_config(frame, currency),
        on_select="rerun",
        selection_mode="multi-row",
        key=fkey("product_table"),
    )

    selected = event.selection["rows"] if event and event.selection else []
    st.caption(
        f"{len(summaries):,} product(s) · showing {len(shown):,}"
        + ("" if selected else " · tick a row to open that product's variants")
    )

    for index in selected:
        if index >= len(shown):
            continue
        summary = shown[index]
        rows = variants_of(visible, summary["product_id"])
        in_stock = sum(1 for r in rows if r.get("available") is True)
        with st.container():
            st.html(ui.anchor("variant"))
            st.html(ui.product_header(
                str(summary.get("title") or "Untitled"),
                f"{len(rows)} variant(s) · {in_stock} in stock"
                + (f" · {summary['options']}" if summary.get("options") else ""),
                summary.get("url"),
                summary.get("image"),
            ))
            variant_frame = display_frame(rows, useful_columns(rows, [
                "variant_title", "option1", "option2", "option3", "sku",
                "price", "compare_at_price", "discount_pct", "available", "grams",
            ]))
            st.dataframe(
                variant_frame,
                width="stretch",
                hide_index=True,
                column_config=column_config(variant_frame, currency),
            )


def render_flat(visible: list[dict], currency: str) -> None:
    columns = st.session_state.get(fkey("flt_columns")) or default_columns(visible)

    frame = display_frame(visible[: st.session_state.get("row_cap", 200)], columns)
    st.dataframe(
        frame,
        width="stretch",
        height=560,
        hide_index=True,
        column_config=column_config(frame, currency),
    )
    st.caption(f"Showing {len(frame):,} of {len(visible):,} matching variant row(s).")


def render_downloads(records: list[dict], label: str) -> None:
    frame = pd.DataFrame(records, columns=columns_for(records))
    left, right, note = st.columns([1, 1, 2], vertical_alignment="center")
    left.download_button(
        "Download JSON",
        json.dumps(records, indent=2, ensure_ascii=False),
        file_name="scraped.json",
        mime="application/json",
        width="stretch",
        icon=":material/data_object:",
    )
    right.download_button(
        "Download CSV",
        frame.to_csv(index=False),
        file_name="scraped.csv",
        mime="text/csv",
        width="stretch",
        icon=":material/table:",
    )
    note.caption(label)


# --- run ------------------------------------------------------------------

if run:
    if not url.strip():
        st.error("A URL is required.", icon=":material/error:")
        st.stop()
    if not shopify_mode and not prompt.strip():
        st.error("Describe what you want extracted.", icon=":material/error:")
        st.stop()

    if PUBLIC_DEMO:
        last_run = st.session_state.get("last_run_ts", 0.0)
        elapsed = time.time() - last_run
        if elapsed < DEMO_COOLDOWN_S:
            st.warning(f"Please wait {DEMO_COOLDOWN_S - elapsed:.0f}s before scraping again.")
            st.stop()
        st.session_state["last_run_ts"] = time.time()

    run_settings = Settings(
        **{
            **settings.__dict__,
            "max_chunk_chars": int(max_chars),
            "max_chunks": int(max_chunks),
        }
    )

    provider = None
    if not shopify_mode:  # the Shopify path needs no model and no key
        try:
            if provider_name == "openai":
                provider = OpenAIProvider(model=model, api_key=api_key or None)
            else:
                provider = OllamaProvider(model=model, host=run_settings.ollama_host)
        except ProviderError as exc:
            st.error(str(exc), icon=":material/error:")
            st.stop()

    status = st.status("Working…", expanded=True)
    agent = ScrapeAgent(
        provider=provider, settings=run_settings, on_progress=lambda m: status.write(m)
    )

    currency = ""
    try:
        if shopify_mode:
            check = check_store(url, run_settings)
            if not check.catalogue_available:
                # "Not Shopify" and "Shopify, but the API refused" are different
                # answers and the user can act on only one of them.
                label = ("Catalogue not readable" if check.is_shopify
                         else "Not a Shopify store")
                status.update(label=label, state="error")
                st.error(
                    f"{check.detail}"
                    + (
                        ""
                        if PUBLIC_DEMO or check.is_shopify
                        else " Switch to 'Ask in plain language' and describe what you want instead."
                    ),
                    icon=":material/error:",
                )
                st.stop()
            currency = str(fetch_store_meta(url, run_settings).get("currency") or "")
            result = agent.run_catalogue(url, max_products=max_products)
        elif follow_pages:
            result = agent.run_pages(
                url,
                prompt,
                max_pages=max_pages,
                render={"never": False, "auto": None, "always": True}[render_mode],
                fields=[f for f in fields_raw.split(",") if f.strip()] if fields_raw else None,
                respect_robots=respect_robots,
                strip_boilerplate=strip_boilerplate,
            )
        else:
            result = agent.run(
                url,
                prompt,
                render={"never": False, "auto": None, "always": True}[render_mode],
                fields=[f for f in fields_raw.split(",") if f.strip()] if fields_raw else None,
                respect_robots=respect_robots,
                strip_boilerplate=strip_boilerplate,
                keep_markdown=True,
            )
    except (FetchError, ProviderError, ShopifyError) as exc:
        status.update(label="Failed", state="error")
        st.error(str(exc), icon=":material/error:")
        st.stop()
    except Exception as exc:  # surface unexpected errors in the UI, not the console
        status.update(label="Failed", state="error")
        st.exception(exc)
        st.stop()

    status.update(label=f"Found {result.count:,} record(s)", state="complete", expanded=False)

    st.session_state["result"] = result
    st.session_state["result_shopify"] = shopify_mode
    st.session_state["result_currency"] = currency
    st.session_state["result_cost"] = (
        None if shopify_mode or provider_name == "ollama" else result.cost_usd
    )
    st.session_state["result_local"] = provider_name == "ollama"
    st.session_state["result_url"] = url
    reset_filters()  # a new store deserves a clean filter bar


# --- results --------------------------------------------------------------

result = st.session_state.get("result")
if result is not None:
    is_catalogue = st.session_state.get("result_shopify", False)
    currency = st.session_state.get("result_currency", "")
    records = result.records
    store = st.session_state.get("result_url", "")

    st.html(ui.section("Results", store))

    if not records:
        st.html(ui.empty_state(
            "🕳️",
            "No records found",
            "The page may be JavaScript-rendered, in which case try 'always' rendering in "
            "Settings. Or the data may sit in the nav or footer, so turn off boilerplate "
            "stripping and scrape again.",
        ))
    elif is_catalogue:
        visible = render_filters(records, currency)
        render_catalogue_stats(visible, records, currency)

        if not visible:
            st.html(ui.empty_state(
                "🔍",
                "Nothing matches these filters",
                "Widen the price range, clear the search, or switch availability back to Any.",
            ))
        else:
            view_col, cols_col = st.columns([3, 1], vertical_alignment="bottom")
            with view_col:
                view = st.segmented_control(
                    "View", [GROUPED, FLAT], default=GROUPED, key=fkey("flt_view"),
                    label_visibility="collapsed",
                    help="Grouped collapses each product to one row you can open.",
                ) or GROUPED
            # Only meaningful for the flat table; the grouped view has its own
            # fixed summary columns, so offering a picker there would lie.
            if view == FLAT:
                with cols_col:
                    with st.popover("Columns", icon=":material/view_column:", width="stretch"):
                        available = columns_for(visible)
                        st.multiselect(
                            "Columns to show",
                            available,
                            default=default_columns(visible),
                            key=fkey("flt_columns"),
                            help="Every field the store's API publishes is available here.",
                        )

            if view == GROUPED:
                render_grouped(visible, currency)
            else:
                render_flat(visible, currency)

        st.html('<hr class="fx-rule">')
        render_downloads(
            visible or records,
            f"{len(visible or records):,} row(s). Every row matching the filters, "
            "not just the ones on screen.",
        )
    else:
        cost = st.session_state.get("result_cost")
        st.html(ui.stat_tiles([
            {"label": "Records", "value": f"{result.count:,}", "sub": "extracted rows"},
            {"label": "Pages" if result.pages > 1 else "LLM calls",
             "value": f"{result.pages if result.pages > 1 else result.usage.get('calls', 0):,}",
             "sub": "crawled" if result.pages > 1 else "chunks sent to the model"},
            {"label": "Tokens", "value": f"{result.usage.get('total_tokens', 0):,}",
             "sub": "prompt + completion", "tone": "orange"},
            {"label": "Est. cost",
             "value": "local model" if st.session_state.get("result_local")
             else (f"${cost:.4f}" if cost is not None else "n/a"),
             "sub": result.model or "no model", "tone": "green"},
        ]))

        frame = display_frame(records)
        st.dataframe(
            frame,
            width="stretch",
            hide_index=True,
            column_config=column_config(frame, currency),
        )
        st.html('<hr class="fx-rule">')
        render_downloads(records, f"{len(records):,} extracted record(s).")

        with st.expander("Schema the agent designed"):
            st.json(result.plan)
        with st.expander(
            f"Cleaned page content sent to the model ({result.markdown_chars:,} chars)"
        ):
            st.code(result.markdown[:20_000] or "(empty)", language="markdown")

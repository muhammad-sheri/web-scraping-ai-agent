"""Streamlit UI for the web scraping agent.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.fetch import FetchError
from scraper_agent.output import columns_for
from scraper_agent.providers.base import ProviderError
from scraper_agent.providers.ollama_provider import OllamaProvider
from scraper_agent.providers.openai_provider import OpenAIProvider
from scraper_agent.shopify import ShopifyError, is_shopify_store

st.set_page_config(page_title="Web Scraping AI Agent", page_icon="🕸️", layout="wide")

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

st.title("🕸️ Web Scraping AI Agent")
st.caption(
    "Describe what you want in plain language — the agent fetches the page, "
    "designs a schema for your request, and returns structured records."
)

settings = Settings.from_env()

with st.sidebar:
    st.header("Model")
    provider_name = st.radio(
        "Provider",
        ["openai", "ollama"],
        index=0 if settings.provider == "openai" else 1,
        help="OpenAI is pay-per-token. Ollama runs locally and is free.",
    )

    if provider_name == "openai":
        env_key = os.getenv("OPENAI_API_KEY", "")
        api_key = st.text_input(
            "OpenAI API key",
            type="password",
            value=env_key,
            help="Loaded from .env when present. Billed per token — this is not the free ChatGPT tier.",
        )
        model = st.selectbox(
            "Model",
            ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o", "gpt-4.1"],
            index=0,
            help="gpt-4o-mini is the cheapest and handles most pages.",
        )
    else:
        api_key = ""
        installed = OllamaProvider(host=settings.ollama_host).available_models()
        if installed:
            model = st.selectbox("Local model", installed, index=0)
        else:
            model = st.text_input("Local model", value=settings.ollama_model)
            st.warning(
                "No local models found. Pull one first:\n\n"
                "`ollama pull qwen2.5:7b`",
                icon="⚠️",
            )

    st.header("Fetching")
    render_mode = st.select_slider(
        "Browser rendering",
        options=["never", "auto", "always"],
        value="auto",
        help="'auto' launches a headless browser only when the page looks empty without JavaScript.",
    )
    respect_robots = st.toggle("Respect robots.txt", value=settings.respect_robots)
    strip_boilerplate = st.toggle(
        "Strip nav/header/footer",
        value=True,
        help="Turn off when the data you want lives in the site chrome.",
    )

    with st.expander("Advanced"):
        max_chars = st.number_input(
            "Characters per LLM call", 2_000, 60_000, settings.max_chunk_chars, step=1_000
        )
        max_chunks = st.number_input("Max LLM calls per page", 1, 50, settings.max_chunks)
        fields_raw = st.text_input(
            "Fixed fields (optional)",
            placeholder="title,price,url",
            help="Set these to skip schema inference and force exact column names.",
        )

mode = st.radio(
    "Mode",
    ["Ask in plain language", "Full Shopify catalogue"],
    horizontal=True,
    help="Shopify stores publish their whole catalogue as JSON — exact prices, SKUs and "
    "every size variant, with no AI involved and no cost.",
)
shopify_mode = mode == "Full Shopify catalogue"

if shopify_mode:
    st.subheader("Which store?")
    url = st.text_input(
        "Store URL", placeholder="https://www.allbirds.com", key="shop_url"
    )
    prompt = ""
    limit_all = st.toggle("Fetch the entire catalogue", value=True)
    max_products = (
        None if limit_all else int(st.number_input("Product limit", 1, 5_000, 50, step=10))
    )
    st.caption(
        "Works on any Shopify store. Returns one row per variant (each size/colour has "
        "its own SKU, price and stock flag). Not a Shopify store? Use the other mode."
    )
else:
    st.subheader("What should the agent scrape?")
    example = st.selectbox("Start from an example", ["—"] + list(EXAMPLES))
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

    follow_pages = st.toggle(
        "Follow 'next' links",
        value=False,
        help="Listings usually span several pages. Turn this on to extract all of them.",
    )
    max_pages = (
        int(st.number_input("Max pages", 1, 50, 5)) if follow_pages else 1
    )

run = st.button("Scrape", type="primary", use_container_width=True)

if run:
    if not url.strip():
        st.error("A URL is required.")
        st.stop()
    if not shopify_mode and not prompt.strip():
        st.error("Describe what you want extracted.")
        st.stop()

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
            st.error(str(exc))
            st.stop()

    status = st.status("Working…", expanded=True)
    agent = ScrapeAgent(
        provider=provider, settings=run_settings, on_progress=lambda m: status.write(m)
    )

    try:
        if shopify_mode:
            if not is_shopify_store(url, run_settings):
                status.update(label="Not a Shopify store", state="error")
                st.error(
                    f"{url} has no public /products.json, so it is not a Shopify store. "
                    "Switch to 'Ask in plain language' and describe what you want instead."
                )
                st.stop()
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
        st.error(str(exc))
        st.stop()
    except Exception as exc:  # surface unexpected errors in the UI, not the console
        status.update(label="Failed", state="error")
        st.exception(exc)
        st.stop()

    status.update(label=f"Done — {result.count} record(s)", state="complete", expanded=False)

    metrics = st.columns(4)
    metrics[0].metric("Records", result.count)
    metrics[1].metric("Pages" if result.pages > 1 else "LLM calls",
                      result.pages if result.pages > 1 else result.usage.get("calls", 0))
    metrics[2].metric("Tokens", f"{result.usage.get('total_tokens', 0):,}")
    if shopify_mode:
        cost = "free (store API)"
    elif provider_name == "ollama":
        cost = "free (local)"
    else:
        cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "—"
    metrics[3].metric("Est. cost", cost)

    if result.records:
        frame = pd.DataFrame(result.records, columns=columns_for(result.records))
        st.dataframe(frame, use_container_width=True, hide_index=True)

        downloads = st.columns(2)
        downloads[0].download_button(
            "Download JSON",
            json.dumps(result.records, indent=2, ensure_ascii=False),
            file_name="scraped.json",
            mime="application/json",
            use_container_width=True,
        )
        downloads[1].download_button(
            "Download CSV",
            frame.to_csv(index=False),
            file_name="scraped.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.warning(
            "No records found. The page may be JavaScript-rendered (try 'always' "
            "rendering), or the data may sit in nav/footer (turn off boilerplate "
            "stripping)."
        )

    if not shopify_mode:
        with st.expander("Schema the agent designed"):
            st.json(result.plan)
        with st.expander(
            f"Cleaned page content sent to the model ({result.markdown_chars:,} chars)"
        ):
            st.code(result.markdown[:20_000] or "(empty)", language="markdown")

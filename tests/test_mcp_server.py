"""MCP server wiring, exercised through an in-memory FastMCP client."""

from __future__ import annotations

import asyncio
import json

import pytest

from fastmcp import Client

from scraper_agent import mcp_server

EXPECTED_TOOLS = {"check_store", "scrape_page", "shopify_catalogue", "evaluate_extraction"}


def call(tool: str, args: dict):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(go()).data


def list_tools():
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.list_tools()

    return asyncio.run(go())


# --- registration ---------------------------------------------------------


def test_all_four_tools_are_registered():
    assert {t.name for t in list_tools()} == EXPECTED_TOOLS


def test_tools_carry_descriptions_for_the_model():
    for tool in list_tools():
        assert tool.description and len(tool.description) > 40, tool.name


def test_schemas_expose_the_expected_arguments():
    schemas = {t.name: set((t.inputSchema or {}).get("properties", {})) for t in list_tools()}
    assert schemas["check_store"] == {"url"}
    assert {"url", "what_to_extract", "all_pages", "max_pages"} <= schemas["scrape_page"]
    assert {"store_url", "max_products"} <= schemas["shopify_catalogue"]
    assert {"page_url", "model"} <= schemas["evaluate_extraction"]


# --- routing --------------------------------------------------------------


def test_check_store_recommends_the_catalogue_tool_for_shopify(monkeypatch):
    monkeypatch.setattr(mcp_server, "is_shopify_store", lambda url: True)
    result = call("check_store", {"url": "https://shop.com"})
    assert result["is_shopify"] is True
    assert result["recommended_tool"] == "shopify_catalogue"


def test_check_store_falls_back_to_the_llm_path(monkeypatch):
    monkeypatch.setattr(mcp_server, "is_shopify_store", lambda url: False)
    result = call("check_store", {"url": "https://blog.com"})
    assert result["is_shopify"] is False
    assert result["recommended_tool"] == "scrape_page"


def test_catalogue_refuses_a_non_shopify_url(monkeypatch):
    monkeypatch.setattr(mcp_server, "is_shopify_store", lambda url: False)
    result = call("shopify_catalogue", {"store_url": "https://blog.com"})
    assert "error" in result and "Not a Shopify store" in result["error"]


# --- errors are returned, not raised --------------------------------------


def test_fetch_errors_come_back_as_data(monkeypatch):
    from scraper_agent.fetch import FetchError

    def boom(*a, **k):
        raise FetchError("host unreachable")

    monkeypatch.setattr(mcp_server.ScrapeAgent, "run", boom)
    result = call("scrape_page", {"url": "https://x.com", "what_to_extract": "things"})
    assert result["error"] == "host unreachable"


# --- token discipline -----------------------------------------------------


def test_small_results_are_returned_inline_without_a_file():
    records = [{"i": i} for i in range(5)]
    payload = mcp_server._package(records, "https://x.com", "test")
    assert payload["truncated"] is False
    assert payload["total_records"] == 5
    assert len(payload["records"]) == 5
    assert "saved_to" not in payload


def test_large_results_are_capped_and_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPER_OUTPUT_DIR", str(tmp_path))
    records = [{"i": i} for i in range(500)]

    payload = mcp_server._package(records, "https://shop.com/collections/all", "catalogue")

    assert payload["truncated"] is True
    assert payload["total_records"] == 500
    assert len(payload["records"]) == mcp_server.MAX_INLINE_ROWS
    # The full set is on disk, so nothing is lost by capping the context.
    saved = json.loads((tmp_path / payload["saved_to"].split("/")[-1]).read_text())
    assert len(saved) == 500


def test_provider_defaults_to_local_when_no_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SCRAPER_PROVIDER", "openai")
    assert mcp_server._default_provider() == "ollama"


def test_explicit_provider_setting_is_respected(monkeypatch):
    monkeypatch.setenv("SCRAPER_PROVIDER", "ollama")
    assert mcp_server._default_provider() == "ollama"

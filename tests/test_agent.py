"""End-to-end agent behaviour with the network and the LLM stubbed out."""

from __future__ import annotations

import pytest

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.fetch import FetchResult
from tests.conftest import FakeProvider

PLAN = {
    "item_name": "product",
    "multiple": True,
    "fields": [
        {"name": "name", "type": "string", "description": "product name"},
        {"name": "price", "type": "number", "description": "price"},
    ],
}

BATCH = {"items": [{"name": "Widget Pro", "price": 49.99}, {"name": "Widget Mini", "price": 19.5}]}


@pytest.fixture
def stub_fetch(monkeypatch, shop_html):
    def _install(html: str = shop_html, rendered: bool = False):
        def fake_fetch(url, settings=None, *, render=None, respect_robots=None):
            return FetchResult(
                url="https://shop.example.com",
                final_url="https://shop.example.com",
                html=html,
                status_code=200,
                rendered=rendered,
            )

        monkeypatch.setattr("scraper_agent.agent.fetch", fake_fetch)

    return _install


def test_run_returns_records_and_metadata(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=PLAN, batches=[BATCH])
    result = ScrapeAgent(provider=provider, settings=Settings()).run(
        "shop.example.com", "all products with prices"
    )

    assert result.count == 2
    assert result.records[0] == {"name": "Widget Pro", "price": 49.99}
    assert result.plan["item_name"] == "product"
    assert result.provider == "fake"
    assert result.usage["calls"] == 2  # one plan call + one extraction call
    assert result.markdown_chars > 0
    assert result.elapsed_s >= 0


def test_page_content_reaches_the_model(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=PLAN, batches=[BATCH])
    ScrapeAgent(provider=provider, settings=Settings()).run("shop.example.com", "products")

    extraction_call = provider.calls[-1]
    assert "Widget Pro" in extraction_call["user"]
    assert "https://shop.example.com/p/widget-pro" in extraction_call["user"]
    # The schema is passed so the backend can constrain decoding.
    assert extraction_call["schema"]["properties"]["items"]["type"] == "array"


def test_long_pages_are_chunked_into_several_calls(stub_fetch):
    body = "".join(f"<p>Item {i} costs ${i}.00 and is a fine product indeed.</p>" for i in range(400))
    stub_fetch(html=f"<html><head><title>Big</title></head><body><main>{body}</main></body></html>")

    provider = FakeProvider(plan=PLAN, batches=[BATCH, BATCH, BATCH, BATCH, BATCH, BATCH])
    result = ScrapeAgent(
        provider=provider, settings=Settings(max_chunk_chars=2_000, chunk_overlap_chars=100)
    ).run("shop.example.com", "products")

    assert result.chunks > 1
    # Same records returned per chunk collapse to one set.
    assert result.count == 2


def test_max_chunks_caps_spend(stub_fetch):
    body = "".join(f"<p>Item {i} costs ${i}.00 and is a fine product indeed.</p>" for i in range(400))
    stub_fetch(html=f"<html><head><title>Big</title></head><body><main>{body}</main></body></html>")

    provider = FakeProvider(plan=PLAN, batches=[BATCH] * 10)
    result = ScrapeAgent(
        provider=provider, settings=Settings(max_chunk_chars=1_000, max_chunks=2)
    ).run("shop.example.com", "products")

    assert result.chunks == 2
    assert provider.usage.calls == 3  # plan + 2 extractions


def test_explicit_fields_skip_the_planning_call(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=None, batches=[BATCH])
    result = ScrapeAgent(provider=provider, settings=Settings()).run(
        "shop.example.com", "products", fields=["name", "price"]
    )

    assert result.usage["calls"] == 1
    assert [f["name"] for f in result.plan["fields"]] == ["name", "price"]


def test_records_are_ordered_by_schema(stub_fetch):
    stub_fetch()
    scrambled = {"items": [{"price": 10, "name": "A", "extra": "kept"}]}
    provider = FakeProvider(plan=PLAN, batches=[scrambled])
    result = ScrapeAgent(provider=provider, settings=Settings()).run("shop.example.com", "products")

    assert list(result.records[0]) == ["name", "price", "extra"]


def test_missing_fields_become_null(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=PLAN, batches=[{"items": [{"name": "Only a name"}]}])
    result = ScrapeAgent(provider=provider, settings=Settings()).run("shop.example.com", "products")

    assert result.records[0] == {"name": "Only a name", "price": None}


def test_single_record_plan_never_discards_extracted_rows(stub_fetch):
    """Regression: a planner guessing multiple=false used to truncate to one row.

    Seen for real — qwen2.5:3b answered multiple=false for "every book with its
    title and price" on a 20-book page, and 19 real records were dropped.
    """
    stub_fetch()
    provider = FakeProvider(plan=dict(PLAN, multiple=False), batches=[BATCH])
    result = ScrapeAgent(provider=provider, settings=Settings()).run(
        "shop.example.com", "the product"
    )

    assert result.count == 2
    assert result.plan["multiple"] is False  # the hint is still reported


def test_plural_wording_overrides_a_wrong_plurality_guess(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=dict(PLAN, multiple=False), batches=[BATCH])
    result = ScrapeAgent(provider=provider, settings=Settings()).run(
        "shop.example.com", "every product with its price"
    )

    assert result.plan["multiple"] is True
    assert "do not stop after the first one" in provider.calls[-1]["user"]


def test_empty_prompt_is_rejected(stub_fetch):
    stub_fetch()
    with pytest.raises(ValueError):
        ScrapeAgent(provider=FakeProvider(plan=PLAN), settings=Settings()).run("x.com", "   ")


def test_no_records_is_not_an_error(stub_fetch):
    stub_fetch()
    provider = FakeProvider(plan=PLAN, batches=[{"items": []}])
    result = ScrapeAgent(provider=provider, settings=Settings()).run("shop.example.com", "unicorns")

    assert result.records == []
    assert result.count == 0


def test_progress_messages_are_emitted(stub_fetch):
    stub_fetch()
    seen: list[str] = []
    agent = ScrapeAgent(
        provider=FakeProvider(plan=PLAN, batches=[BATCH]),
        settings=Settings(),
        on_progress=seen.append,
    )
    agent.run("shop.example.com", "products")

    assert any("Fetching" in m for m in seen)
    assert any("Extracting" in m for m in seen)

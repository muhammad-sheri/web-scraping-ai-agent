"""Watchlist parsing. Bad config must fail loudly, at load, with the index."""

from __future__ import annotations

import json

import pytest

from scraper_agent.monitor.config import (
    DEFAULT_PROMPT,
    EXAMPLE_WATCHLIST,
    WatchlistError,
    load_watchlist,
    parse_watchlist,
)


def test_bare_url_strings_are_accepted():
    watchlist = parse_watchlist({"pages": ["https://a.com", "https://b.com"]})
    assert watchlist.urls == ["https://a.com", "https://b.com"]
    assert all(p.canary is None for p in watchlist.pages)


def test_page_objects_carry_canary_and_prompt():
    watchlist = parse_watchlist(
        {"pages": [{"url": " https://a.com ", "canary": True, "prompt": "just titles"}]}
    )
    page = watchlist.pages[0]
    assert (page.url, page.canary, page.prompt) == ("https://a.com", True, "just titles")


def test_page_prompt_overrides_the_watchlist_prompt():
    watchlist = parse_watchlist({"prompt": "shared", "pages": [{"url": "https://a.com"}]})
    assert watchlist.pages[0].question(watchlist.prompt) == "shared"

    watchlist = parse_watchlist(
        {"prompt": "shared", "pages": [{"url": "https://a.com", "prompt": "mine"}]}
    )
    assert watchlist.pages[0].question(watchlist.prompt) == "mine"


def test_defaults_are_filled_in():
    watchlist = parse_watchlist({"pages": ["https://a.com"]})
    assert watchlist.prompt == DEFAULT_PROMPT
    assert watchlist.provider is None
    assert watchlist.state_path.endswith(".jsonl")


@pytest.mark.parametrize(
    "data, message",
    [
        ({}, "pages"),
        ({"pages": []}, "pages"),
        ({"pages": [123]}, "pages[0]"),
        ({"pages": [{}]}, "url"),
        ({"pages": [{"url": "  "}]}, "url"),
        ({"pages": [{"url": "https://a.com", "canary": "yes"}]}, "canary"),
        ({"pages": [{"url": "https://a.com", "prompt": 5}]}, "prompt"),
    ],
)
def test_invalid_watchlists_are_rejected(data, message):
    with pytest.raises(WatchlistError) as exc:
        parse_watchlist(data)
    assert message in str(exc.value)


def test_a_non_object_watchlist_is_rejected():
    with pytest.raises(WatchlistError):
        parse_watchlist(["https://a.com"])


def test_load_reports_a_missing_file(tmp_path):
    with pytest.raises(WatchlistError, match="No watchlist"):
        load_watchlist(tmp_path / "nope.json")


def test_load_reports_broken_json(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WatchlistError, match="not valid JSON"):
        load_watchlist(path)


def test_the_shipped_example_is_valid(tmp_path):
    """`scrape-agent-monitor init` must emit something that actually loads."""
    path = tmp_path / "w.json"
    path.write_text(json.dumps(EXAMPLE_WATCHLIST), encoding="utf-8")
    watchlist = load_watchlist(path)
    assert len(watchlist.pages) == 3
    assert any(p.canary for p in watchlist.pages)

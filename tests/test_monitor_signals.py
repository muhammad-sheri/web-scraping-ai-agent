"""Signals must describe shape, not content — and must not confuse 0 with missing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scraper_agent.monitor.signals import Signals, extract_signals


@dataclass
class FakeResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    markdown_chars: int = 0
    chunks: int = 0
    rendered: bool = False
    ungrounded_removed: int = 0


def test_empty_extraction_has_no_schema():
    signals = extract_signals(FakeResult())
    assert signals.record_count == 0
    assert signals.schema_keys == []
    assert signals.null_rate == {}


def test_schema_keys_are_the_sorted_union():
    result = FakeResult(records=[{"b": 1, "a": 2}, {"c": 3}])
    assert extract_signals(result).schema_keys == ["a", "b", "c"]


def test_null_rate_counts_missing_fields():
    result = FakeResult(records=[{"price": 1.0}, {"price": None}, {"price": ""}, {"price": 4.0}])
    assert extract_signals(result).null_rate["price"] == 0.5


def test_zero_and_false_are_values_not_holes():
    """A price of 0 and an out-of-stock flag are answers, not missing data."""
    result = FakeResult(records=[{"price": 0, "available": False}])
    signals = extract_signals(result)
    assert signals.null_rate["price"] == 0.0
    assert signals.null_rate["available"] == 0.0


def test_missing_key_counts_as_null():
    result = FakeResult(records=[{"title": "a", "price": 1.0}, {"title": "b"}])
    assert extract_signals(result).null_rate["price"] == 0.5


def test_numeric_median_resists_one_wild_value():
    """Median, not mean: a single fabricated 9999 must not move the baseline."""
    result = FakeResult(records=[{"p": 10.0}, {"p": 12.0}, {"p": 11.0}, {"p": 9999.0}])
    assert extract_signals(result).numeric_median["p"] == 11.5


def test_numeric_median_ignores_text_and_booleans():
    result = FakeResult(records=[{"p": "from $10"}, {"p": True}, {"p": 20.0}])
    assert extract_signals(result).numeric_median["p"] == 20.0


def test_text_only_field_gets_no_numeric_median():
    result = FakeResult(records=[{"title": "Wool Runner"}])
    assert "title" not in extract_signals(result).numeric_median


def test_carries_through_page_level_facts():
    result = FakeResult(
        records=[{"a": 1}], markdown_chars=7539, chunks=2, rendered=True, ungrounded_removed=3
    )
    signals = extract_signals(result)
    assert (signals.markdown_chars, signals.chunks, signals.rendered) == (7539, 2, True)
    assert signals.ungrounded_removed == 3


def test_roundtrips_through_dict_and_ignores_unknown_keys():
    original = extract_signals(FakeResult(records=[{"a": 1}], markdown_chars=10))
    restored = Signals.from_dict({**original.to_dict(), "from_a_future_version": 1})
    assert restored == original

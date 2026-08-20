"""History is append-only and must survive being written to badly."""

from __future__ import annotations

from scraper_agent.monitor.history import (
    MIN_BASELINE_RUNS,
    History,
    Observation,
    build_baseline,
)


def _observation(url: str = "https://shop.com/c", count: int = 30, **kwargs) -> Observation:
    signals = {
        "record_count": count,
        "schema_keys": ["price", "title"],
        "null_rate": {"price": 0.0, "title": 0.0},
        "numeric_median": {"price": 50.0},
        "markdown_chars": 8000,
    }
    signals.update(kwargs.pop("signals", {}))
    return Observation(page_url=url, signals=signals, **kwargs)


def test_append_and_read_back(tmp_path):
    history = History(tmp_path / "h.jsonl")
    history.append(_observation(count=10))
    history.append(_observation(count=20))
    assert [o.signals["record_count"] for o in history] == [10, 20]


def test_creates_parent_directories(tmp_path):
    history = History(tmp_path / "nested" / "deeper" / "h.jsonl")
    history.append(_observation())
    assert history.path.exists()


def test_missing_file_reads_as_empty(tmp_path):
    assert list(History(tmp_path / "absent.jsonl")) == []


def test_a_truncated_line_does_not_poison_the_file(tmp_path):
    """Killed mid-write must cost one run, not the whole history."""
    path = tmp_path / "h.jsonl"
    history = History(path)
    history.append(_observation(count=10))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"page_url": "https://shop.com/c", "signal')  # cut off
    history.append(_observation(count=20))
    assert [o.signals["record_count"] for o in history] == [10, 20]


def test_for_page_filters_and_limits(tmp_path):
    history = History(tmp_path / "h.jsonl")
    for i in range(5):
        history.append(_observation(url="https://a.com", count=i))
    history.append(_observation(url="https://b.com", count=99))

    assert len(history.for_page("https://a.com")) == 5
    assert [o.signals["record_count"] for o in history.for_page("https://a.com", limit=2)] == [3, 4]
    assert len(history.for_page("https://b.com")) == 1


def test_pages_lists_each_url_once(tmp_path):
    history = History(tmp_path / "h.jsonl")
    history.append(_observation(url="https://a.com"))
    history.append(_observation(url="https://b.com"))
    history.append(_observation(url="https://a.com"))
    assert history.pages() == ["https://a.com", "https://b.com"]


def test_baseline_needs_a_minimum_of_runs():
    assert not build_baseline([]).usable
    assert not build_baseline([_observation()]).usable
    assert build_baseline([_observation() for _ in range(MIN_BASELINE_RUNS)]).usable


def test_baseline_is_a_median_not_the_last_run():
    """One flaky run must not become tomorrow's reference."""
    runs = [_observation(count=30), _observation(count=31), _observation(count=2)]
    assert build_baseline(runs).record_count == 30


def test_baseline_ignores_failed_runs():
    runs = [
        _observation(count=30),
        _observation(count=30),
        Observation(page_url="https://shop.com/c", ok=False, error="boom"),
    ]
    baseline = build_baseline(runs)
    assert baseline.runs == 2
    assert baseline.record_count == 30


def test_baseline_unions_schema_keys_across_runs():
    """A field absent from one page of a catalogue is not a schema change."""
    runs = [
        _observation(signals={"schema_keys": ["price", "title"]}),
        _observation(signals={"schema_keys": ["title"]}),
    ]
    assert build_baseline(runs).schema_keys == ["price", "title"]


def test_baseline_carries_scored_accuracy():
    runs = [
        _observation(metrics={"recall": 0.9, "precision": 1.0}),
        _observation(metrics={"recall": 0.94, "precision": 0.98}),
    ]
    baseline = build_baseline(runs)
    assert baseline.recall == 0.92
    assert baseline.precision == 0.99


def test_baseline_recall_is_none_without_any_scored_run():
    assert build_baseline([_observation(), _observation()]).recall is None


def test_history_baseline_reads_only_stored_runs(tmp_path):
    """Baseline is taken before the current run is appended."""
    history = History(tmp_path / "h.jsonl")
    history.append(_observation(count=30))
    history.append(_observation(count=30))
    assert history.baseline("https://shop.com/c").record_count == 30

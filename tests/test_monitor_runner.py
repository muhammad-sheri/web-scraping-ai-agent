"""The monitoring pass, against a stubbed agent — no network, no model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from scraper_agent.agent import ScrapeResult
from scraper_agent.config import Settings
from scraper_agent.monitor import runner as runner_module
from scraper_agent.monitor.config import WatchPage, Watchlist
from scraper_agent.monitor.detect import ALERT, OK, WARN
from scraper_agent.monitor.history import History
from scraper_agent.monitor.runner import MonitorReport, check_page, run_monitor

URL = "https://shop.com/collections/all"


@dataclass
class FakeTruth:
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.records)

    catalogue_size: int = 0


class FakeProvider:
    name = "fake"
    model = "fake-1"


def _result(records: list[dict[str, Any]], chars: int = 8000) -> ScrapeResult:
    return ScrapeResult(
        url=URL, final_url=URL, prompt="p", records=records, markdown_chars=chars, chunks=1
    )


@pytest.fixture
def stub(monkeypatch):
    """Swap out provider, agent and ground truth; keep everything else real."""

    state: dict[str, Any] = {"records": [], "truth": None, "raises": None, "chars": 8000}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, url, prompt, **kwargs):
            if state["raises"]:
                raise state["raises"]
            return _result(state["records"], state["chars"])

    monkeypatch.setattr(runner_module, "get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr(runner_module, "ScrapeAgent", FakeAgent)
    monkeypatch.setattr(
        runner_module,
        "build_page_truth",
        lambda url, settings: state["truth"] or FakeTruth([]),
    )
    return state


def _watchlist(**kwargs) -> Watchlist:
    return Watchlist(pages=[WatchPage(url=URL, **kwargs)], prompt="every product")


def _check(history, stub_state, **page_kwargs):
    return check_page(
        WatchPage(url=URL, **page_kwargs),
        _watchlist(),
        history,
        Settings(),
    )


def test_a_run_is_recorded_even_when_nothing_is_wrong(tmp_path, stub):
    stub["records"] = [{"title": "a", "price": 1.0}]
    history = History(tmp_path / "h.jsonl")

    report = _check(history, stub)

    assert report.severity == OK
    assert len(history.for_page(URL)) == 1
    assert history.for_page(URL)[0].signals["record_count"] == 1


def test_ground_truth_when_available_makes_it_a_canary(tmp_path, stub):
    stub["records"] = [{"title": "Wool Runner", "price": 98.0}]
    stub["truth"] = FakeTruth([{"title": "Wool Runner", "price": 98.0}])

    report = _check(History(tmp_path / "h.jsonl"), stub)

    assert report.canary is True
    assert report.observation.metrics["recall"] == 1.0


def test_a_page_without_truth_falls_back_to_signals_only(tmp_path, stub):
    stub["records"] = [{"title": "a"}]
    stub["truth"] = FakeTruth([])

    report = _check(History(tmp_path / "h.jsonl"), stub)

    assert report.canary is False
    assert report.observation.metrics is None
    assert report.observation.signals["record_count"] == 1


def test_canary_false_skips_the_truth_lookup(tmp_path, stub, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "build_page_truth",
        lambda url, settings: called.append(url) or FakeTruth([]),
    )
    stub["records"] = [{"title": "a"}]

    _check(History(tmp_path / "h.jsonl"), stub, canary=False)

    assert called == []


def test_a_declared_canary_without_truth_does_not_crash(tmp_path, stub):
    stub["records"] = [{"title": "a"}]
    stub["truth"] = FakeTruth([])

    report = _check(History(tmp_path / "h.jsonl"), stub, canary=True)

    assert report.canary is False
    assert report.observation.ok is True


def test_a_thrown_error_becomes_an_alert_not_a_crash(tmp_path, stub):
    stub["raises"] = RuntimeError("boom")

    report = _check(History(tmp_path / "h.jsonl"), stub)

    assert report.severity == ALERT
    assert report.observation.ok is False
    assert "boom" in report.observation.error


def test_a_failed_run_is_still_written_to_history(tmp_path, stub):
    """Otherwise an outage looks like a gap and the baseline never notices."""
    stub["raises"] = RuntimeError("boom")
    history = History(tmp_path / "h.jsonl")

    _check(history, stub)

    assert len(history.for_page(URL)) == 1
    assert history.for_page(URL)[0].ok is False


def test_drift_is_detected_against_accumulated_history(tmp_path, stub):
    history = History(tmp_path / "h.jsonl")
    stub["records"] = [{"title": f"p{i}", "price": float(i)} for i in range(30)]
    for _ in range(3):
        assert _check(history, stub).severity == OK

    stub["records"] = [{"title": "p0", "price": 0.0}]  # the site broke
    report = _check(history, stub)

    assert report.severity == ALERT
    assert any(f.signal == "record_count" for f in report.findings)


def test_the_current_run_is_not_inside_its_own_baseline(tmp_path, stub):
    """A regression must not partly hide by moving the reference with it."""
    history = History(tmp_path / "h.jsonl")
    stub["records"] = [{"title": f"p{i}"} for i in range(30)]
    for _ in range(3):
        _check(history, stub)

    stub["records"] = [{"title": "p0"}]
    report = _check(history, stub)

    assert report.baseline.record_count == 30


def test_run_monitor_attributes_a_clean_pass(tmp_path, stub):
    stub["records"] = [{"title": "a"}]
    report = run_monitor(
        Watchlist(pages=[WatchPage(url=URL)]), History(tmp_path / "h.jsonl"), Settings()
    )
    assert report.verdict.source == "none"
    assert report.severity == OK


def test_run_monitor_keeps_going_after_one_page_fails(tmp_path, stub):
    stub["raises"] = RuntimeError("boom")
    watchlist = Watchlist(pages=[WatchPage(url=URL), WatchPage(url="https://b.com/x")])

    report = run_monitor(watchlist, History(tmp_path / "h.jsonl"), Settings())

    assert len(report.pages) == 2
    assert report.severity == ALERT


@pytest.mark.parametrize(
    "severity, fail_on, expected",
    [(OK, ALERT, 0), (WARN, ALERT, 0), (WARN, WARN, 1), (ALERT, ALERT, 2), (ALERT, WARN, 2)],
)
def test_exit_codes_let_ci_gate_on_severity(severity, fail_on, expected):
    report = MonitorReport(pages=[runner_module.PageReport(page_url=URL, severity=severity)])
    assert report.exit_code(fail_on) == expected

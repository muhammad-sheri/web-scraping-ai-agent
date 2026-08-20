"""Detection rules, and the attribution that makes an alert actionable."""

from __future__ import annotations

import pytest

from scraper_agent.monitor.detect import (
    ALERT,
    OK,
    WARN,
    Finding,
    attribute,
    compare,
    worst,
)
from scraper_agent.monitor.history import Observation, build_baseline

URL = "https://shop.com/collections/all"


def _signals(count=30, keys=("price", "title"), nulls=None, numbers=None, chars=8000):
    return {
        "record_count": count,
        "schema_keys": list(keys),
        "null_rate": nulls if nulls is not None else {k: 0.0 for k in keys},
        "numeric_median": numbers if numbers is not None else {"price": 50.0},
        "markdown_chars": chars,
    }


def _observation(metrics=None, **kwargs) -> Observation:
    return Observation(page_url=URL, signals=_signals(**kwargs), metrics=metrics)


def _baseline(metrics=None, runs=3, **kwargs):
    return build_baseline([_observation(metrics=metrics, **kwargs) for _ in range(runs)])


def _by_signal(findings: list[Finding], name: str) -> Finding | None:
    return next((f for f in findings if f.signal == name), None)


def test_no_baseline_reports_that_rather_than_ok():
    """'Not enough history' must never read as 'checked and fine'."""
    findings = compare(_observation(), build_baseline([]))
    assert worst(findings) == OK
    assert _by_signal(findings, "baseline") is not None


def test_a_failed_run_alerts():
    observation = Observation(page_url=URL, ok=False, error="FetchError: 403")
    findings = compare(observation, _baseline())
    assert worst(findings) == ALERT
    assert "403" in findings[0].message


def test_zero_records_where_there_were_records_alerts():
    """The case that reaches production silently today."""
    findings = compare(_observation(count=0, keys=()), _baseline(count=30))
    assert _by_signal(findings, "record_count").severity == ALERT


def test_big_record_drop_alerts():
    findings = compare(_observation(count=10), _baseline(count=30))
    assert _by_signal(findings, "record_count").severity == ALERT


def test_moderate_record_drop_warns():
    findings = compare(_observation(count=20), _baseline(count=30))
    assert _by_signal(findings, "record_count").severity == WARN


def test_normal_catalogue_churn_is_not_a_finding():
    """Stores gain and lose products daily; that is not drift."""
    findings = compare(_observation(count=28), _baseline(count=30))
    assert _by_signal(findings, "record_count") is None


def test_record_explosion_warns_about_junk_rows():
    findings = compare(_observation(count=70), _baseline(count=30))
    finding = _by_signal(findings, "record_count")
    assert finding.severity == WARN
    assert "junk" in finding.message


def test_a_field_that_stopped_being_extracted_alerts():
    findings = compare(_observation(keys=("title",)), _baseline(keys=("price", "title")))
    finding = _by_signal(findings, "schema_keys")
    assert finding.severity == ALERT
    assert "price" in finding.message


def test_a_new_field_only_warns():
    findings = compare(
        _observation(keys=("price", "sku", "title")), _baseline(keys=("price", "title"))
    )
    assert _by_signal(findings, "schema_keys").severity == WARN


def test_empty_result_does_not_also_report_a_schema_change():
    """One clear cause beats two confusing ones."""
    findings = compare(_observation(count=0, keys=()), _baseline(count=30))
    assert _by_signal(findings, "schema_keys") is None


def test_field_going_mostly_empty_alerts():
    findings = compare(
        _observation(nulls={"price": 0.7, "title": 0.0}),
        _baseline(nulls={"price": 0.0, "title": 0.0}),
    )
    finding = _by_signal(findings, "null_rate.price")
    assert finding.severity == ALERT
    assert "70%" in finding.message


def test_field_partly_emptying_warns():
    findings = compare(
        _observation(nulls={"price": 0.25, "title": 0.0}),
        _baseline(nulls={"price": 0.0, "title": 0.0}),
    )
    assert _by_signal(findings, "null_rate.price").severity == WARN


def test_price_distribution_shift_warns():
    findings = compare(
        _observation(numbers={"price": 100.0}), _baseline(numbers={"price": 50.0})
    )
    finding = _by_signal(findings, "numeric_median.price")
    assert finding.severity == WARN
    assert "+100%" in finding.message


def test_page_size_change_warns_but_never_alerts():
    findings = compare(_observation(chars=2000), _baseline(chars=8000))
    assert _by_signal(findings, "markdown_chars").severity == WARN


def test_recall_collapse_alerts():
    findings = compare(
        _observation(metrics={"recall": 0.60, "precision": 0.97}),
        _baseline(metrics={"recall": 0.94, "precision": 0.97}),
    )
    assert _by_signal(findings, "recall").severity == ALERT
    assert _by_signal(findings, "precision") is None


def test_small_recall_slip_warns():
    findings = compare(
        _observation(metrics={"recall": 0.85, "precision": 0.97}),
        _baseline(metrics={"recall": 0.94, "precision": 0.97}),
    )
    assert _by_signal(findings, "recall").severity == WARN


def test_accuracy_is_skipped_when_the_page_has_no_truth():
    findings = compare(_observation(), _baseline())
    assert _by_signal(findings, "recall") is None


def test_worst_picks_the_highest_severity():
    assert worst([]) == OK
    assert worst([Finding("a", OK, ""), Finding("b", WARN, "")]) == WARN
    assert worst([Finding("a", ALERT, ""), Finding("b", WARN, "")]) == ALERT


# --- attribution -----------------------------------------------------------


def test_clean_run_attributes_to_nothing():
    verdict = attribute([("a", True, OK, True), ("b", False, OK, True)])
    assert verdict.source == "none"
    assert "2 judged" in verdict.explanation


def test_a_first_run_must_not_report_a_clean_bill_of_health():
    """Nothing degraded and nothing checked are different answers."""
    verdict = attribute([("a", True, OK, False), ("b", False, OK, False)])
    assert verdict.source == "none"
    assert verdict.confidence == "low"
    assert "nothing was checked" in verdict.explanation


def test_a_clean_verdict_says_how_many_pages_are_still_warming_up():
    verdict = attribute([("a", True, OK, True), ("b", False, OK, False)])
    assert "1 page(s) still building a baseline" in verdict.explanation


def test_canaries_and_pages_both_degraded_points_at_the_extractor():
    verdict = attribute([("canary", True, ALERT, True), ("page", False, WARN, True)])
    assert verdict.source == "extractor"
    assert verdict.confidence == "high"
    assert verdict.degraded_canaries == ["canary"]


def test_only_canaries_degraded_still_points_at_the_extractor():
    verdict = attribute([("canary", True, ALERT, True), ("page", False, OK, True)])
    assert verdict.source == "extractor"


def test_pages_degraded_while_canaries_held_points_at_the_sites():
    """The measured path is intact, so the change is on the site."""
    verdict = attribute([("canary", True, OK, True), ("page", False, ALERT, True)])
    assert verdict.source == "site"
    assert verdict.degraded_pages == ["page"]


def test_without_a_canary_attribution_admits_it_cannot_tell():
    verdict = attribute([("page", False, ALERT, True)])
    assert verdict.source == "mixed"
    assert verdict.confidence == "low"
    assert "canary" in verdict.explanation


@pytest.mark.parametrize("severity", [WARN, ALERT])
def test_both_severities_count_as_degraded(severity):
    assert attribute([("page", False, severity, True)]).degraded_pages == ["page"]

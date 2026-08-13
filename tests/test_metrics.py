"""Metric arithmetic, checked against hand-computed values."""

import pytest

from scraper_agent.evals.metrics import (
    ExtractionMetrics,
    parse_price,
    prices_agree,
    score_extraction,
)

TRUTH = [
    {"title": "Wool Runner", "price": 98.0},
    {"title": "Tree Dasher 2", "price": 135.0},
    {"title": "Trail Runner SWT", "price": 145.0},
    {"title": "Wool Lounger", "price": 105.0},
]


# --- price parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$91.00", 91.0),
        ("£51.77", 51.77),
        ("from $98", 98.0),
        ("1,299.00", 1299.0),
        ("98", 98.0),
        (98, 98.0),
        (98.5, 98.5),
        ("USD 145.00", 145.0),
    ],
)
def test_parse_price_handles_real_formats(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "call for pricing", True])
def test_parse_price_returns_none_when_unreadable(raw):
    assert parse_price(raw) is None


def test_price_agreement_within_tolerance():
    assert prices_agree(100.0, 100.0) is True
    assert prices_agree("$100.50", 100.0) is True   # within 1%
    assert prices_agree(120.0, 100.0) is False
    assert prices_agree(None, 100.0) is None        # not comparable, not wrong


# --- scoring --------------------------------------------------------------


def test_perfect_extraction():
    metrics, _ = score_extraction(list(TRUTH), TRUTH)
    assert metrics.recall == 1.0
    assert metrics.precision == 1.0
    assert metrics.hallucination_rate == 0.0
    assert metrics.price_accuracy == 1.0
    assert metrics.f1 == 1.0


def test_half_found_gives_half_recall():
    predicted = [{"title": "Wool Runner", "price": 98.0}, {"title": "Tree Dasher 2", "price": 135.0}]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.recall == 0.5
    assert metrics.precision == 1.0
    assert len(metrics.missed_titles) == 2


def test_hallucinated_record_is_counted():
    """The real failure we saw: nav links returned as if they were products."""
    predicted = [
        {"title": "Wool Runner", "price": 98.0},
        {"title": "Login", "price": None},
    ]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.precision == 0.5
    assert metrics.hallucination_rate == 0.5
    assert metrics.hallucinated_titles == ["Login"]


def test_wrong_price_lowers_price_accuracy_but_not_recall():
    predicted = [
        {"title": "Wool Runner", "price": 98.0},
        {"title": "Tree Dasher 2", "price": 9.99},  # wrong
    ]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.recall == 0.5
    assert metrics.price_accuracy == 0.5


def test_missing_price_is_not_scored_as_wrong():
    """None, not 0%. A page with no prices where the model correctly said
    nothing must not score the same as getting every price wrong."""
    predicted = [{"title": "Wool Runner", "price": None}]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.price_comparable == 0
    assert metrics.price_accuracy is None


def test_alternative_field_names_are_understood():
    """The agent invents its own schema, so `name`/`cost` must still score."""
    predicted = [{"name": "Wool Runner", "cost": "$98.00"}]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.matched_count == 1
    assert metrics.price_accuracy == 1.0


def test_field_completeness_counts_nulls():
    predicted = [{"title": "Wool Runner", "price": None, "url": "x"}]
    metrics, _ = score_extraction(predicted, TRUTH)
    assert metrics.fields_total == 3
    assert metrics.fields_filled == 2


def test_no_predictions_scores_zero_without_dividing_by_zero():
    metrics, _ = score_extraction([], TRUTH)
    assert metrics.recall == 0.0
    assert metrics.precision == 0.0
    assert metrics.hallucination_rate == 0.0  # nothing predicted, nothing invented
    assert metrics.f1 == 0.0


def test_empty_truth_does_not_crash():
    metrics, _ = score_extraction([{"title": "x"}], [])
    assert metrics.recall == 0.0
    assert metrics.precision == 0.0


def test_to_dict_exposes_derived_metrics():
    metrics = ExtractionMetrics(truth_count=4, predicted_count=2, matched_count=2)
    data = metrics.to_dict()
    assert data["recall"] == 0.5
    assert data["precision"] == 1.0
    assert "f1" in data and "hallucination_rate" in data

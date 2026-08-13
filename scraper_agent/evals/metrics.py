"""Scoring an extraction against the store's own records."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from scraper_agent.evals.matching import DEFAULT_THRESHOLD, MatchResult, match_titles

# Strip currency symbols, thousands separators and stray words: "$1,299.00",
# "from £51.77", "51,77 EUR" all have to become a number.
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")

PRICE_TOLERANCE = 0.01  # 1%


def parse_price(value: Any) -> float | None:
    """Best-effort numeric price from whatever the model or API produced."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    # Drop thousands separators before matching, so "1,299.00" reads as one number.
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    match = _NUMBER.search(cleaned)
    if not match:
        return None
    number = match.group(0).replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None


def prices_agree(a: Any, b: Any, tolerance: float = PRICE_TOLERANCE) -> bool | None:
    """True/False if both prices are readable, None if either is missing."""
    left, right = parse_price(a), parse_price(b)
    if left is None or right is None:
        return None
    if left == right:
        return True
    scale = max(abs(left), abs(right))
    return scale > 0 and abs(left - right) / scale <= tolerance


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@dataclass
class ExtractionMetrics:
    """What the model got right, measured against the store's own data."""

    truth_count: int = 0
    predicted_count: int = 0
    matched_count: int = 0

    price_comparable: int = 0
    price_correct: int = 0
    #: Numeric answers discarded because they were absent from the page.
    ungrounded_removed: int = 0

    fields_total: int = 0
    fields_filled: int = 0

    missed_titles: list[str] = field(default_factory=list)
    hallucinated_titles: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        """Share of products actually on the page that the model found."""
        return _ratio(self.matched_count, self.truth_count)

    @property
    def precision(self) -> float:
        """Share of returned records that are real products."""
        return _ratio(self.matched_count, self.predicted_count)

    @property
    def hallucination_rate(self) -> float:
        """Records that match no real product. Observed for real: qwen2.5:3b
        returned Hacker News nav links as if they were stories."""
        return round(1.0 - self.precision, 4) if self.predicted_count else 0.0

    @property
    def price_accuracy(self) -> float | None:
        """Among matched products, share priced correctly within tolerance.

        None when nothing was comparable — a page with no prices, where the
        model correctly returned null, must not score the same as a page where
        it got every price wrong.
        """
        if not self.price_comparable:
            return None
        return _ratio(self.price_correct, self.price_comparable)

    @property
    def field_completeness(self) -> float:
        return _ratio(self.fields_filled, self.fields_total)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 4) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            recall=self.recall,
            precision=self.precision,
            f1=self.f1,
            hallucination_rate=self.hallucination_rate,
            price_accuracy=self.price_accuracy,
            field_completeness=self.field_completeness,
        )
        # Keep the report readable; the full lists live in the JSON artefact.
        data["missed_titles"] = self.missed_titles[:20]
        data["hallucinated_titles"] = self.hallucinated_titles[:20]
        return data


def _first_present(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


TITLE_KEYS = ("title", "name", "product", "product_name", "product_title")
PRICE_KEYS = ("price", "current_price", "sale_price", "price_excluding_tax", "amount", "cost")


def score_extraction(
    predicted: list[dict[str, Any]],
    truth: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[ExtractionMetrics, MatchResult]:
    """Compare extracted records against ground-truth records.

    Both sides are dicts. Titles and prices are located by trying the common
    key names, because the schema the agent invents is not fixed — it may call
    the field `title`, `name` or `product_name` depending on the prompt.
    """
    predicted_titles = [_first_present(r, TITLE_KEYS) for r in predicted]
    truth_titles = [_first_present(r, TITLE_KEYS) for r in truth]

    result = match_titles(predicted_titles, truth_titles, threshold=threshold)

    metrics = ExtractionMetrics(
        truth_count=len(truth),
        predicted_count=len(predicted),
        matched_count=result.matched_count,
    )

    for match in result.matches:
        verdict = prices_agree(
            _first_present(predicted[match.predicted_index], PRICE_KEYS),
            _first_present(truth[match.truth_index], PRICE_KEYS),
        )
        if verdict is not None:
            metrics.price_comparable += 1
            metrics.price_correct += int(verdict)

    for record in predicted:
        for value in record.values():
            metrics.fields_total += 1
            metrics.fields_filled += int(value not in (None, "", [], {}))

    metrics.missed_titles = [
        str(truth_titles[i]) for i in result.unmatched_truth if truth_titles[i]
    ]
    metrics.hallucinated_titles = [
        str(predicted_titles[i]) for i in result.unmatched_predicted if predicted_titles[i]
    ]

    return metrics, result

"""Turning a run plus its baseline into findings, and findings into a verdict.

Thresholds here are deliberately asymmetric. Catalogues legitimately gain and
lose products every day, so a moderate change in row count means little; a
*schema* that loses a field, or a price column that goes from 2% null to 70%
null, is never a normal Tuesday. Shape breaks; content churns.

The rule that matters most is the cheapest one: zero records where there used
to be records. That is what a redesigned site looks like, and it is the case
that today reaches production silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scraper_agent.monitor.history import MIN_BASELINE_RUNS, Baseline, Observation

OK = "ok"
WARN = "warn"
ALERT = "alert"

_RANK = {OK: 0, WARN: 1, ALERT: 2}


@dataclass
class Thresholds:
    """Every number a user might reasonably want to argue with, in one place."""

    #: Relative fall in record count.
    count_drop_warn: float = 0.25
    count_drop_alert: float = 0.50
    #: Relative rise in record count. Junk rows inflate counts as surely as
    #: breakage deflates them.
    count_rise_warn: float = 1.00
    #: Absolute rise in a field's null rate.
    null_rise_warn: float = 0.20
    null_rise_alert: float = 0.40
    #: Relative move in a numeric field's median.
    numeric_shift_warn: float = 0.50
    #: Absolute fall in a truth-scored metric.
    accuracy_drop_warn: float = 0.07
    accuracy_drop_alert: float = 0.15
    #: Relative move in cleaned page size, which fingerprints a redesign.
    page_size_shift_warn: float = 0.50


@dataclass
class Finding:
    signal: str
    severity: str
    message: str
    baseline: Any = None
    current: Any = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.signal}: {self.message}"


@dataclass
class Verdict:
    """Where the degradation most likely originates."""

    source: str  # "extractor" | "site" | "mixed" | "none"
    confidence: str  # "high" | "low"
    explanation: str
    degraded_pages: list[str] = field(default_factory=list)
    degraded_canaries: list[str] = field(default_factory=list)


def worst(findings: list[Finding]) -> str:
    return max((f.severity for f in findings), key=lambda s: _RANK[s], default=OK)


def _relative_change(current: float, base: float) -> float | None:
    """Signed relative change. None when there is no baseline to divide by."""
    if not base:
        return None
    return (current - base) / base


def compare(
    observation: Observation,
    baseline: Baseline,
    thresholds: Thresholds | None = None,
) -> list[Finding]:
    """Findings for one page: current run against its own recent history."""
    thresholds = thresholds or Thresholds()
    findings: list[Finding] = []

    if not observation.ok:
        return [
            Finding(
                "run",
                ALERT,
                f"the run itself failed: {observation.error}",
                current=observation.error,
            )
        ]

    if not baseline.usable:
        # Nothing to compare against yet. Say so rather than reporting "ok",
        # which would read as "checked and fine".
        return [
            Finding(
                "baseline",
                OK,
                f"only {baseline.runs} prior run(s); need {MIN_BASELINE_RUNS} "
                "before drift can be judged",
                current=baseline.runs,
            )
        ]

    signals = observation.signals or {}
    count = int(signals.get("record_count", 0) or 0)

    # 1. Extraction died outright.
    if count == 0 and baseline.record_count > 0:
        findings.append(
            Finding(
                "record_count",
                ALERT,
                f"returned no records; baseline is {baseline.record_count:g}",
                baseline.record_count,
                0,
            )
        )
    else:
        change = _relative_change(count, baseline.record_count)
        if change is not None and change < 0:
            drop = -change
            if drop >= thresholds.count_drop_alert:
                findings.append(
                    Finding(
                        "record_count",
                        ALERT,
                        f"records fell {drop:.0%} (baseline {baseline.record_count:g}, now {count})",
                        baseline.record_count,
                        count,
                    )
                )
            elif drop >= thresholds.count_drop_warn:
                findings.append(
                    Finding(
                        "record_count",
                        WARN,
                        f"records fell {drop:.0%} (baseline {baseline.record_count:g}, now {count})",
                        baseline.record_count,
                        count,
                    )
                )
        elif change is not None and change >= thresholds.count_rise_warn:
            findings.append(
                Finding(
                    "record_count",
                    WARN,
                    f"records rose {change:.0%} (baseline {baseline.record_count:g}, now "
                    f"{count}), so check for duplicate or junk rows",
                    baseline.record_count,
                    count,
                )
            )

    # 2. Schema shape. A field the planner stopped producing is a hard break;
    #    every downstream consumer of that column now reads null.
    current_keys = set(signals.get("schema_keys", []) or [])
    baseline_keys = set(baseline.schema_keys)
    if count:  # an empty result has no schema to judge
        lost = sorted(baseline_keys - current_keys)
        gained = sorted(current_keys - baseline_keys)
        if lost:
            findings.append(
                Finding(
                    "schema_keys",
                    ALERT,
                    f"field(s) no longer extracted: {', '.join(lost)}",
                    sorted(baseline_keys),
                    sorted(current_keys),
                )
            )
        if gained:
            findings.append(
                Finding(
                    "schema_keys",
                    WARN,
                    f"new field(s) appeared: {', '.join(gained)}",
                    sorted(baseline_keys),
                    sorted(current_keys),
                )
            )

    # 3. Fields that stopped being filled in. The column still exists, so
    #    nothing errors; it is just empty now.
    current_nulls = signals.get("null_rate", {}) or {}
    for key, base_rate in sorted(baseline.null_rate.items()):
        if key not in current_nulls:
            continue
        rise = float(current_nulls[key]) - float(base_rate)
        if rise >= thresholds.null_rise_alert:
            severity = ALERT
        elif rise >= thresholds.null_rise_warn:
            severity = WARN
        else:
            continue
        findings.append(
            Finding(
                f"null_rate.{key}",
                severity,
                f"'{key}' empty in {current_nulls[key]:.0%} of records "
                f"(baseline {base_rate:.0%})",
                base_rate,
                current_nulls[key],
            )
        )

    # 4. Numeric distributions. A currency switch or a misread element moves
    #    the median without changing anything structural.
    current_numbers = signals.get("numeric_median", {}) or {}
    for key, base_value in sorted(baseline.numeric_median.items()):
        if key not in current_numbers:
            continue
        change = _relative_change(float(current_numbers[key]), float(base_value))
        if change is not None and abs(change) >= thresholds.numeric_shift_warn:
            findings.append(
                Finding(
                    f"numeric_median.{key}",
                    WARN,
                    f"median '{key}' moved {change:+.0%} "
                    f"({base_value:g} → {current_numbers[key]:g})",
                    base_value,
                    current_numbers[key],
                )
            )

    # 5. Page size. On its own this is weak, so it never alerts, but paired
    #    with a record drop it tells you the site changed rather than the model.
    size_change = _relative_change(
        float(signals.get("markdown_chars", 0) or 0), baseline.markdown_chars
    )
    if size_change is not None and abs(size_change) >= thresholds.page_size_shift_warn:
        findings.append(
            Finding(
                "markdown_chars",
                WARN,
                f"cleaned page size moved {size_change:+.0%}, so the page itself changed",
                baseline.markdown_chars,
                signals.get("markdown_chars"),
            )
        )

    # 6. Truth-scored accuracy, where a canary supplied it.
    metrics = observation.metrics or {}
    for name, base_value in (("recall", baseline.recall), ("precision", baseline.precision)):
        current_value = metrics.get(name)
        if current_value is None or base_value is None:
            continue
        drop = float(base_value) - float(current_value)
        if drop >= thresholds.accuracy_drop_alert:
            severity = ALERT
        elif drop >= thresholds.accuracy_drop_warn:
            severity = WARN
        else:
            continue
        findings.append(
            Finding(
                name,
                severity,
                f"{name} fell {drop:.0%} ({base_value:.0%} → {float(current_value):.0%})",
                base_value,
                current_value,
            )
        )

    return findings


def attribute(
    results: list[tuple[str, bool, str, bool]],
) -> Verdict:
    """Decide whether degradation is extractor-side or site-side.

    `results` is (page_url, is_canary, severity, had_baseline) per page.

    A canary is a page whose accuracy is measurable against the store's own
    records. It is not a page anyone necessarily cares about. It is there
    because it runs through the same fetch/clean/plan/extract path. So if the
    canaries degraded, the fault is in that shared path and *every* page is
    suspect, including the ones that look fine. If only ordinary pages moved,
    the shared path is intact and those sites changed.

    `had_baseline` exists because "nothing degraded" and "nothing could be
    judged" are different answers, and reporting the first when the second is
    true is how a monitor earns trust it has not yet done anything to deserve.
    """
    degraded = [(url, canary) for url, canary, sev, _b in results if _RANK[sev] >= _RANK[WARN]]
    degraded_canaries = [u for u, canary in degraded if canary]
    degraded_pages = [u for u, canary in degraded if not canary]
    canaries_present = any(canary for _u, canary, _s, _b in results)
    judged = [url for url, _c, _s, had_baseline in results if had_baseline]

    if not judged:
        return Verdict(
            "none",
            "low",
            f"Recorded {len(results)} page(s), but none has enough history to judge yet. "
            f"Baselines form after {MIN_BASELINE_RUNS} runs, so nothing was checked.",
        )

    if not degraded:
        unjudged = len(results) - len(judged)
        return Verdict(
            "none",
            "high" if canaries_present else "low",
            f"No degradation across {len(judged)} judged page(s)."
            + (f" {unjudged} page(s) still building a baseline." if unjudged else "")
            + ("" if canaries_present else " No canary ran, so accuracy was not measured."),
        )

    if degraded_canaries and degraded_pages:
        return Verdict(
            "extractor",
            "high",
            "Canaries degraded alongside ordinary pages. The fault is in the shared "
            "extraction path (model, prompt, parsing or fetch), so treat every page "
            "as suspect, including those that scored clean.",
            degraded_pages,
            degraded_canaries,
        )

    if degraded_canaries:
        return Verdict(
            "extractor",
            "high",
            "Canaries degraded while ordinary pages held. Accuracy fell on pages whose "
            "ground truth is known, which points at the extractor rather than any site.",
            degraded_pages,
            degraded_canaries,
        )

    if canaries_present:
        return Verdict(
            "site",
            "high",
            "Canaries held while these pages degraded. The extraction path is measurably "
            "intact, so the change is on the sites themselves.",
            degraded_pages,
            degraded_canaries,
        )

    return Verdict(
        "mixed",
        "low",
        "Pages degraded, but no canary ran, so there is no measurement separating a "
        "site change from an extractor regression. Add a canary page to tell them apart.",
        degraded_pages,
        degraded_canaries,
    )

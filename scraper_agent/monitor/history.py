"""Append-only run history, and the baseline derived from it.

JSON Lines rather than a database: a monitor's history is small, wants to be
greppable, diffs cleanly in git, and survives the tool that wrote it. Adding a
schema migration story for a few thousand rows would be the wrong trade.

The baseline is a *median* of recent runs, never the single previous run. One
flaky fetch would otherwise become the thing tomorrow is compared against, and
the alert would fire a day late and against the wrong reference.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator

#: Runs considered when computing a baseline.
DEFAULT_WINDOW = 7

#: Below this many prior runs there is nothing meaningful to compare against.
MIN_BASELINE_RUNS = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Observation:
    """One page, one run."""

    page_url: str
    ok: bool = True
    canary: bool = False
    provider: str = ""
    model: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    #: Truth-scored metrics. Present only for canaries that produced truth.
    metrics: dict[str, Any] | None = None
    error: str | None = None
    elapsed_s: float = 0.0
    observed_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Baseline:
    """What "normal" looks like for one page, from its recent history."""

    runs: int = 0
    record_count: float = 0.0
    markdown_chars: float = 0.0
    schema_keys: list[str] = field(default_factory=list)
    null_rate: dict[str, float] = field(default_factory=dict)
    numeric_median: dict[str, float] = field(default_factory=dict)
    recall: float | None = None
    precision: float | None = None

    @property
    def usable(self) -> bool:
        return self.runs >= MIN_BASELINE_RUNS


def _median_of(values: Iterable[float]) -> float:
    collected = [v for v in values if v is not None]
    return round(float(median(collected)), 4) if collected else 0.0


def build_baseline(observations: list[Observation]) -> Baseline:
    """Median over successful runs. Failed runs carry no shape to average."""
    good = [o for o in observations if o.ok and o.signals]
    if not good:
        return Baseline()

    def signal(name: str) -> list[float]:
        return [float(o.signals.get(name, 0) or 0) for o in good]

    # Union of keys seen recently, not just the latest run: a field that is
    # legitimately absent from one page of a catalogue should not read as a
    # schema change when it comes back.
    keys: set[str] = set()
    for observation in good:
        keys.update(observation.signals.get("schema_keys", []) or [])

    null_rate: dict[str, float] = {}
    numeric_median: dict[str, float] = {}
    for key in sorted(keys):
        nulls = [
            float(o.signals.get("null_rate", {}).get(key))
            for o in good
            if o.signals.get("null_rate", {}).get(key) is not None
        ]
        if nulls:
            null_rate[key] = _median_of(nulls)
        numbers = [
            float(o.signals.get("numeric_median", {}).get(key))
            for o in good
            if o.signals.get("numeric_median", {}).get(key) is not None
        ]
        if numbers:
            numeric_median[key] = _median_of(numbers)

    scored = [o for o in good if o.metrics]
    recall = _median_of([o.metrics.get("recall") for o in scored]) if scored else None
    precision = _median_of([o.metrics.get("precision") for o in scored]) if scored else None

    return Baseline(
        runs=len(good),
        record_count=_median_of(signal("record_count")),
        markdown_chars=_median_of(signal("markdown_chars")),
        schema_keys=sorted(keys),
        null_rate=null_rate,
        numeric_median=numeric_median,
        recall=recall,
        precision=precision,
    )


class History:
    """Append-only JSONL store of observations, keyed by page URL."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def append(self, observation: Observation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A process killed mid-write leaves a final line with no newline.
        # Appending straight onto it would splice this run into the broken one
        # and lose both — one interrupted write would cost two runs, and the
        # second loss would be invisible.
        if self._missing_final_newline():
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n")
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(observation.to_dict(), ensure_ascii=False) + "\n")

    def _missing_final_newline(self) -> bool:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"

    def __iter__(self) -> Iterator[Observation]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield Observation.from_dict(json.loads(line))
            except (ValueError, TypeError):
                # A truncated final line (killed mid-write) must not make the
                # whole history unreadable.
                continue

    def for_page(self, page_url: str, limit: int | None = None) -> list[Observation]:
        """Observations for one page, oldest first, optionally the last `limit`."""
        rows = [o for o in self if o.page_url == page_url]
        return rows[-limit:] if limit else rows

    def baseline(self, page_url: str, window: int = DEFAULT_WINDOW) -> Baseline:
        """Baseline over the last `window` stored runs for a page.

        Call this *before* appending the current run. A run inside its own
        reference drags the baseline toward itself, so a large enough
        regression would partly hide from the very check meant to catch it.
        """
        return build_baseline(self.for_page(page_url, limit=window))

    def pages(self) -> list[str]:
        seen: dict[str, None] = {}
        for observation in self:
            seen.setdefault(observation.page_url, None)
        return list(seen)

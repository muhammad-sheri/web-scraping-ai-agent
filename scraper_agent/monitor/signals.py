"""Observable properties of an extraction, computed without any ground truth.

These are the numbers that move when a site breaks the extractor, and they are
all derivable from the records themselves. That is the point: ground truth
exists for a minority of pages, but every page has a history, and a page that
suddenly returns a third as many rows with a new schema and 70% null prices
has broken whether or not anyone can prove what the right answer was.

Signals deliberately describe *shape*, not content. Catalogue content changes
legitimately every day; shape does not.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

#: Values that count as "the extractor did not fill this in".
EMPTY = (None, "", [], {})


def _is_empty(value: Any) -> bool:
    # `0` and `False` are legitimate extracted values (a price of 0, an
    # out-of-stock flag), so a plain falsiness test would wrongly score them
    # as missing.
    if isinstance(value, bool):
        return False
    return value is None or value in ("", [], {})


def _numeric(value: Any) -> float | None:
    """Numbers only. Strings that merely contain digits are not counted.

    Price fields arrive as floats from the schema's `number` type; a string
    here means the model returned prose, which is itself worth noticing but is
    not a distribution to track.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class Signals:
    """A structural fingerprint of one extraction run."""

    record_count: int = 0
    #: Sorted union of keys across records. A changed schema is a strong signal.
    schema_keys: list[str] = field(default_factory=list)
    #: Per field, the share of records where it came back empty.
    null_rate: dict[str, float] = field(default_factory=dict)
    #: Per numeric field, the median value. Catches currency and unit swaps.
    numeric_median: dict[str, float] = field(default_factory=dict)
    #: Cleaned page size. Moves when a site is redesigned.
    markdown_chars: int = 0
    chunks: int = 0
    rendered: bool = False
    #: Numeric answers discarded for not appearing in the page text.
    ungrounded_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signals":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def extract_signals(result: Any) -> Signals:
    """Fingerprint a `ScrapeResult` (or anything with the same attributes)."""
    records: list[dict[str, Any]] = list(getattr(result, "records", []) or [])

    keys: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            keys.update(record.keys())
    schema_keys = sorted(keys)

    null_rate: dict[str, float] = {}
    numeric_median: dict[str, float] = {}

    if records:
        for key in schema_keys:
            empties = 0
            numbers: list[float] = []
            for record in records:
                value = record.get(key) if isinstance(record, dict) else None
                if _is_empty(value):
                    empties += 1
                    continue
                number = _numeric(value)
                if number is not None:
                    numbers.append(number)
            null_rate[key] = round(empties / len(records), 4)
            # Median rather than mean: one fabricated 9999 must not drag the
            # baseline somewhere a real change cannot be seen against it.
            if numbers:
                numeric_median[key] = round(float(median(numbers)), 4)

    return Signals(
        record_count=len(records),
        schema_keys=schema_keys,
        null_rate=null_rate,
        numeric_median=numeric_median,
        markdown_chars=int(getattr(result, "markdown_chars", 0) or 0),
        chunks=int(getattr(result, "chunks", 0) or 0),
        rendered=bool(getattr(result, "rendered", False)),
        ungrounded_removed=int(getattr(result, "ungrounded_removed", 0) or 0),
    )

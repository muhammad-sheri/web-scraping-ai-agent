"""Refusing to pass on numbers the page never contained.

Found by the eval, not by the test suite: on a store whose listing page shows
no prices at all, qwen2.5:3b returned $12.99 for all 34 products. Uniform,
confident, and entirely invented, despite a system prompt that says to return
null when a value is absent. That is the dangerous failure: not a crash, but
plausible wrong data that survives straight into a spreadsheet.

Instructions cannot fix this reliably, especially on small models. A check can.
Numbers are the one field type where grounding is decidable: if the model says
35.99, that quantity has to appear in the text it was shown. Every number in
the source is parsed once, and any numeric answer that is not among them is
replaced with null and counted.

Text fields are deliberately left alone. Models legitimately tidy titles,
trimming a colourway or fixing spacing, so substring-checking prose would delete
correct data. Numbers do not have that excuse.
"""

from __future__ import annotations

import re
from typing import Any

# Any digit run, optionally with a decimal separator.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# A comma between a digit and exactly three digits is a thousands separator
# ("1,299"), not a decimal comma ("35,99").
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")

#: Floats compare loosely, since 35.99 parsed twice must agree.
TOLERANCE = 0.005


def numbers_in(text: str) -> set[float]:
    """Every quantity that literally appears in the text."""
    if not text:
        return set()

    found: set[float] = set()
    for match in _NUMBER.finditer(_THOUSANDS.sub("", text)):
        raw = match.group(0).replace(",", ".")
        try:
            found.add(float(raw))
        except ValueError:
            continue
    return found


def _as_number(value: Any) -> float | None:
    """The numeric meaning of a value, or None if it is not really a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = _THOUSANDS.sub("", value.strip())
        match = _NUMBER.fullmatch(text) or _NUMBER.search(text)
        # Only treat a string as numeric when the number is essentially all of
        # it: "35.99" and "$35.99" yes, "Model 3 Runner" no.
        if match and len(re.sub(r"[^\d]", "", text)) >= len(text) - 3:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def is_grounded(value: Any, source_numbers: set[float]) -> bool:
    """True when the value is non-numeric, or is a number present in the source."""
    number = _as_number(value)
    if number is None:
        return True  # not a number: out of scope for this check
    return any(abs(number - candidate) <= TOLERANCE for candidate in source_numbers)


def drop_ungrounded_numbers(
    records: list[dict[str, Any]], source_text: str
) -> tuple[list[dict[str, Any]], int]:
    """Null out numeric values absent from the source; return the count removed."""
    source_numbers = numbers_in(source_text)
    removed = 0
    cleaned: list[dict[str, Any]] = []

    for record in records:
        checked: dict[str, Any] = {}
        for key, value in record.items():
            if is_grounded(value, source_numbers):
                checked[key] = value
            else:
                checked[key] = None
                removed += 1
        cleaned.append(checked)

    return cleaned, removed

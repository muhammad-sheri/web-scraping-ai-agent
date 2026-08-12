"""Tolerant parsing of model output into Python data."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# The page is given to the model as markdown, so values sometimes come back
# still wearing their link syntax: "[Some title](https://…)" instead of
# "Some title". Cheaper and more reliable to undo here than to keep asking.
_MD_LINK = re.compile(r"^\[(?P<label>[^\]]*)\]\((?P<url>[^)\s]*)[^)]*\)$")
_MD_WRAPPED = re.compile(r"^\[([^\[\]]*)\]$")
_MD_EMPHASIS = re.compile(r"^(\*\*|__|\*|_|`)+(.*?)(\*\*|__|\*|_|`)+$", re.DOTALL)


def clean_value(value: Any) -> Any:
    """Strip leftover markdown syntax from a string value."""
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return None

    link = _MD_LINK.match(text)
    if link:
        # Prefer the human label; fall back to the URL for image-only links.
        text = (link.group("label") or link.group("url")).strip()

    wrapped = _MD_WRAPPED.match(text)
    if wrapped:
        text = wrapped.group(1).strip()

    emphasis = _MD_EMPHASIS.match(text)
    if emphasis and emphasis.group(2).strip():
        text = emphasis.group(2).strip()

    return " ".join(text.split()) or None


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: clean_value(v) for k, v in record.items()}


def parse_json_loose(text: str) -> Any:
    """Parse JSON from model output that may be fenced or have chatter around it.

    Raises ValueError if nothing parseable is found.
    """
    if text is None:
        raise ValueError("Model returned no content")
    text = text.strip()
    if not text:
        raise ValueError("Model returned empty content")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _FENCE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: the outermost {...} or [...] span in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Could not parse JSON from model output: {text[:200]!r}")


def coerce_records(payload: Any, item_key: str = "items") -> list[dict[str, Any]]:
    """Normalise whatever the model returned into a list of flat-ish dicts.

    Models answer with `[...]`, `{"items": [...]}`, `{"products": [...]}`, or a
    single bare object depending on mood. All of those become a list of dicts.
    """
    if payload is None:
        return []

    if isinstance(payload, dict):
        if item_key in payload and isinstance(payload[item_key], list):
            payload = payload[item_key]
        else:
            list_values = [v for v in payload.values() if isinstance(v, list)]
            # A dict wrapping exactly one list is a container; anything else is
            # itself the record.
            if len(list_values) == 1 and len(payload) == 1:
                payload = list_values[0]
            else:
                return [clean_record(payload)]

    if not isinstance(payload, list):
        return [{"value": payload}]

    records: list[dict[str, Any]] = []
    for entry in payload:
        if isinstance(entry, dict):
            records.append(clean_record(entry))
        elif entry is not None:
            records.append({"value": clean_value(entry)})
    return records


def _fingerprint(record: dict[str, Any]) -> str:
    """Stable identity for a record, used to drop chunk-overlap duplicates."""
    normalised = {
        k: (" ".join(str(v).split()).lower() if v is not None else "")
        for k, v in sorted(record.items())
    }
    return json.dumps(normalised, sort_keys=True, ensure_ascii=False)


def merge_records(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Concatenate per-chunk records, preserving order and dropping repeats."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for group in groups:
        for record in group:
            if not record or all(v in (None, "", [], {}) for v in record.values()):
                continue
            key = _fingerprint(record)
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
    return merged

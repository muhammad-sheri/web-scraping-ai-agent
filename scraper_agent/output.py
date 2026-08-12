"""Write extracted records to disk as JSON or CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from scraper_agent.agent import ScrapeResult


def columns_for(records: list[dict[str, Any]]) -> list[str]:
    """Union of keys across records, in first-seen order."""
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    return columns


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_json(result: ScrapeResult, path: str | Path, records_only: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Any = result.records if records_only else result.to_dict()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_csv(result: ScrapeResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns_for(result.records)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or ["value"])
        writer.writeheader()
        for record in result.records:
            writer.writerow({c: _flatten(record.get(c)) for c in columns})
    return path


def to_table(records: list[dict[str, Any]], max_width: int = 40) -> str:
    """Plain-text table for terminal output."""
    if not records:
        return "(no records)"

    columns = columns_for(records)
    rows = [[_truncate(_flatten(r.get(c)), max_width) for c in columns] for r in records]
    widths = [
        min(max_width, max(len(c), *(len(row[i]) for row in rows)) if rows else len(c))
        for i, c in enumerate(columns)
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i])[: widths[i]] for i, cell in enumerate(cells))

    out = [line(columns), line(["-" * w for w in widths])]
    out += [line(row) for row in rows]
    return "\n".join(out)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"

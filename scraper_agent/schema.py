"""Turn a natural-language request into a typed extraction plan.

"get me every product with its price and link" is not directly executable. The
agent first asks the model to name the record type and its fields, producing a
JSON Schema. Extraction then runs *against that schema*, which is what keeps
results consistent across chunks and across pages, so every record has the same
keys instead of whatever the model felt like emitting that call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from scraper_agent.providers.base import LLMProvider, ProviderError

FIELD_TYPES = ("string", "number", "integer", "boolean")

_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_name": {
            "type": "string",
            "description": "Singular snake_case name for one extracted record, e.g. product, article, job_posting",
        },
        "multiple": {
            "type": "boolean",
            "description": "True if the page is expected to yield many records, false for a single record",
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "snake_case field name"},
                    "type": {"type": "string", "enum": list(FIELD_TYPES)},
                    "description": {
                        "type": "string",
                        "description": "What this field holds, in a few words",
                    },
                },
                "required": ["name", "type", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["item_name", "multiple", "fields"],
    "additionalProperties": False,
}

_PLAN_SYSTEM = """You design data-extraction schemas for a web scraping agent.

Given what a user wants from a web page (and a sample of that page), decide:
- item_name: singular snake_case name for one record
- multiple: true if the page plausibly contains many such records, false if the \
page describes exactly one thing
- fields: 2-8 fields that capture what the user asked for

Rules:
- Field names are snake_case, no spaces, no units in the name.
- Use "number" for prices/ratings/quantities, "integer" for counts, "string" \
for text, URLs and dates.
- Only include fields the user asked for or that are obviously implied. Do not \
pad the schema with fields the page cannot support.
- Prefer a `url` field whenever records link somewhere useful."""

_slug = re.compile(r"[^a-z0-9]+")

# Wording that means "there is more than one of these". Smaller models often
# answer multiple=false even for "every book on the page", so the request text
# gets a vote too.
_PLURAL_CUES = re.compile(
    r"\b(all|every|each|both|list|lists|listing|multiple|several|many|"
    r"top\s+\d+|first\s+\d+|\d+\s+\w+)\b",
    re.IGNORECASE,
)


def looks_plural(prompt: str) -> bool:
    return bool(_PLURAL_CUES.search(prompt or ""))


def _normalise_name(name: str, fallback: str = "field") -> str:
    cleaned = _slug.sub("_", (name or "").strip().lower()).strip("_")
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        cleaned = f"f_{cleaned}"
    return cleaned


@dataclass
class Field:
    name: str
    type: str = "string"
    description: str = ""


@dataclass
class ExtractionPlan:
    item_name: str = "item"
    multiple: bool = True
    fields: list[Field] = field(default_factory=list)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_name": self.item_name,
            "multiple": self.multiple,
            "fields": [
                {"name": f.name, "type": f.type, "description": f.description}
                for f in self.fields
            ],
        }


def plan_from_fields(names: list[str], item_name: str = "item") -> ExtractionPlan:
    """Build a plan directly from user-supplied field names (skips inference)."""
    fields, seen = [], set()
    for raw in names:
        name = _normalise_name(raw)
        if name in seen:
            continue
        seen.add(name)
        fields.append(Field(name=name, type="string", description=raw.strip()))
    return ExtractionPlan(item_name=_normalise_name(item_name, "item"), multiple=True, fields=fields)


def _plan_from_payload(payload: Any, prompt: str) -> ExtractionPlan:
    if not isinstance(payload, dict):
        return _fallback_plan(prompt)

    raw_fields = payload.get("fields")
    fields, seen = [], set()
    if isinstance(raw_fields, list):
        for entry in raw_fields:
            if not isinstance(entry, dict):
                continue
            name = _normalise_name(str(entry.get("name", "")))
            if not name or name in seen:
                continue
            seen.add(name)
            ftype = str(entry.get("type", "string")).lower()
            fields.append(
                Field(
                    name=name,
                    type=ftype if ftype in FIELD_TYPES else "string",
                    description=str(entry.get("description", "")).strip(),
                )
            )

    if not fields:
        return _fallback_plan(prompt)

    return ExtractionPlan(
        item_name=_normalise_name(str(payload.get("item_name", "item")), "item"),
        multiple=bool(payload.get("multiple", True)) or looks_plural(prompt),
        fields=fields[:12],
    )


def _fallback_plan(prompt: str) -> ExtractionPlan:
    """Schema-free escape hatch: one free-text field holding the answer."""
    return ExtractionPlan(
        item_name="item",
        multiple=True,
        fields=[Field(name="value", type="string", description=prompt.strip()[:200])],
    )


def infer_plan(
    prompt: str,
    provider: LLMProvider,
    sample: str = "",
    max_sample_chars: int = 3_000,
) -> ExtractionPlan:
    """Ask the model to design the schema for this request."""
    user = f"User request:\n{prompt.strip()}\n"
    if sample:
        user += (
            "\nStart of the page (markdown, truncated):\n"
            "---\n"
            f"{sample[:max_sample_chars]}\n"
            "---\n"
        )
    user += "\nReturn the extraction schema as JSON."

    try:
        payload = provider.complete_json(
            system=_PLAN_SYSTEM, user=user, schema=_PLAN_SCHEMA, schema_name="extraction_plan"
        )
    except ProviderError:
        # The backend itself is broken (bad key, no credit, server down).
        # Extraction would fail the same way, so surface it now rather than
        # printing a misleading fallback schema first.
        raise
    except Exception:
        # The model answered, just not usefully. Schema design is a convenience:
        # fall back to free-text extraction rather than failing the run.
        return _fallback_plan(prompt)

    return _plan_from_payload(payload, prompt)


def plan_to_json_schema(plan: ExtractionPlan) -> dict[str, Any]:
    """JSON Schema for a batch of records, valid under OpenAI strict mode.

    Every field is nullable and required: the model must emit each key, but is
    free to say null instead of inventing a value that isn't on the page.
    """
    properties: dict[str, Any] = {}
    for f in plan.fields:
        properties[f.name] = {
            "type": [f.type, "null"],
            "description": f.description or f.name.replace("_", " "),
        }

    record_schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }

    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": f"All {plan.item_name} records found in this page section",
                "items": record_schema,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def describe_plan(plan: ExtractionPlan) -> str:
    """One-line human summary, used in CLI output and prompts."""
    fields = ", ".join(f"{f.name}:{f.type}" for f in plan.fields)
    cardinality = "many" if plan.multiple else "one"
    return f"{plan.item_name} ({cardinality}) -> {fields}"

"""The agent loop: fetch -> clean -> plan -> extract -> merge."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from scraper_agent.chunk import chunk_markdown
from scraper_agent.clean import html_to_markdown
from scraper_agent.config import Settings
from scraper_agent.fetch import fetch
from scraper_agent.parsing import coerce_records, merge_records
from scraper_agent.providers import LLMProvider, get_provider
from scraper_agent.providers.base import estimate_cost_usd
from scraper_agent.schema import (
    ExtractionPlan,
    describe_plan,
    infer_plan,
    plan_from_fields,
    plan_to_json_schema,
)

_EXTRACT_SYSTEM = """You extract structured data from web page content.

Rules:
- Use ONLY information present in the content given to you. Never guess, never \
fill values from prior knowledge, never invent plausible-looking data.
- If a field has no value in the content, return null for it.
- Return every record that matches the request, in the order they appear.
- Do not return the same record twice.
- Clean the values: strip markdown syntax, whitespace and currency symbols from \
numbers; keep URLs absolute and intact.
- If the content contains no matching records at all, return an empty list.

Return JSON of the form {"items": [ ... ]}."""

ProgressFn = Callable[[str], None]


@dataclass
class ScrapeResult:
    url: str
    final_url: str
    prompt: str
    records: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    rendered: bool = False
    markdown_chars: int = 0
    chunks: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    elapsed_s: float = 0.0
    markdown: str = ""

    @property
    def count(self) -> int:
        return len(self.records)

    def to_dict(self, include_markdown: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_markdown:
            data.pop("markdown", None)
        return data


class ScrapeAgent:
    """Extracts structured records from a page, described in plain language."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        settings: Settings | None = None,
        on_progress: ProgressFn | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.provider = provider or get_provider(settings=self.settings)
        self.on_progress = on_progress or (lambda _msg: None)

    def run(
        self,
        url: str,
        prompt: str,
        *,
        render: bool | None = None,
        fields: list[str] | None = None,
        respect_robots: bool | None = None,
        strip_boilerplate: bool = True,
        keep_markdown: bool = False,
    ) -> ScrapeResult:
        if not (prompt or "").strip():
            raise ValueError("Describe what to extract — the prompt is empty.")

        started = time.perf_counter()
        settings = self.settings

        self.on_progress(f"Fetching {url}")
        page = fetch(url, settings, render=render, respect_robots=respect_robots)
        if page.rendered:
            self.on_progress("Page needed JavaScript — rendered in a headless browser")

        self.on_progress("Cleaning HTML into markdown")
        markdown = html_to_markdown(
            page.html, page.final_url, strip_boilerplate=strip_boilerplate
        )
        if not markdown.strip():
            # Boilerplate stripping can be too aggressive on unusual layouts.
            markdown = html_to_markdown(page.html, page.final_url, strip_boilerplate=False)

        if fields:
            plan = plan_from_fields(fields)
            self.on_progress(f"Using your fields: {describe_plan(plan)}")
        else:
            self.on_progress("Designing extraction schema")
            plan = infer_plan(prompt, self.provider, sample=markdown)
            self.on_progress(f"Schema: {describe_plan(plan)}")

        schema = plan_to_json_schema(plan)
        chunks = chunk_markdown(
            markdown,
            max_chars=settings.max_chunk_chars,
            overlap=settings.chunk_overlap_chars,
            max_chunks=settings.max_chunks,
        )

        groups: list[list[dict[str, Any]]] = []
        for index, chunk in enumerate(chunks, start=1):
            self.on_progress(f"Extracting from section {index}/{len(chunks)}")
            groups.append(self._extract_chunk(prompt, plan, schema, chunk, page.final_url))

        records = merge_records(groups)
        records = [self._order_fields(r, plan) for r in records]
        # `multiple` shapes the prompt but never truncates: a planner that
        # guesses "one" on a 20-item listing must not throw away 19 real rows.

        usage = self.provider.usage
        return ScrapeResult(
            url=page.url,
            final_url=page.final_url,
            prompt=prompt,
            records=records,
            plan=plan.to_dict(),
            provider=self.provider.name,
            model=self.provider.model,
            rendered=page.rendered,
            markdown_chars=len(markdown),
            chunks=len(chunks),
            usage={
                "calls": usage.calls,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
            cost_usd=estimate_cost_usd(self.provider.model, usage),
            elapsed_s=round(time.perf_counter() - started, 2),
            markdown=markdown if keep_markdown else "",
        )

    def _extract_chunk(
        self,
        prompt: str,
        plan: ExtractionPlan,
        schema: dict[str, Any],
        chunk: str,
        source_url: str,
    ) -> list[dict[str, Any]]:
        field_lines = "\n".join(
            f"- {f.name} ({f.type}): {f.description or f.name.replace('_', ' ')}"
            for f in plan.fields
        )
        expectation = (
            f"Return EVERY {plan.item_name} in the content below — there are "
            f"usually many, do not stop after the first one."
            if plan.multiple
            else f"Return the {plan.item_name} described in the content below."
        )
        user = (
            f"Request: {prompt.strip()}\n\n"
            f"Source URL: {source_url}\n\n"
            f"Extract records of type `{plan.item_name}` with these fields:\n"
            f"{field_lines}\n\n"
            f"{expectation}\n\n"
            "Page content:\n"
            "---\n"
            f"{chunk}\n"
            "---"
        )
        payload = self.provider.complete_json(
            system=_EXTRACT_SYSTEM, user=user, schema=schema, schema_name="extraction"
        )
        return coerce_records(payload, item_key="items")

    @staticmethod
    def _order_fields(record: dict[str, Any], plan: ExtractionPlan) -> dict[str, Any]:
        """Give every record the schema's key order, so CSV columns line up."""
        ordered = {name: record.get(name) for name in plan.field_names}
        # Keep anything extra the model volunteered rather than dropping data.
        for key, value in record.items():
            if key not in ordered:
                ordered[key] = value
        return ordered


def scrape(
    url: str,
    prompt: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> ScrapeResult:
    """One-call convenience wrapper around ScrapeAgent."""
    settings = Settings.from_env()
    agent = ScrapeAgent(
        provider=get_provider(provider, model, settings), settings=settings
    )
    return agent.run(url, prompt, **kwargs)

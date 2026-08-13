"""Running the benchmark: (page × model) -> measured accuracy."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.evals.ground_truth import build_page_truth
from scraper_agent.evals.metrics import score_extraction
from scraper_agent.providers import get_provider

# Held constant across every run: comparing models only means something if
# they are all answering the same question.
BENCHMARK_PROMPT = "every product on this page with its title and price"

ProgressFn = Callable[[str], None]


@dataclass
class EvalRun:
    page_url: str
    provider: str
    model: str
    prompt: str = BENCHMARK_PROMPT
    metrics: dict[str, Any] = field(default_factory=dict)
    predicted: list[dict[str, Any]] = field(default_factory=list)
    truth_count: int = 0
    catalogue_size: int = 0
    elapsed_s: float = 0.0
    tokens: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def evaluate_page(
    page_url: str,
    provider: str = "ollama",
    model: str | None = None,
    settings: Settings | None = None,
    *,
    prompt: str = BENCHMARK_PROMPT,
    on_progress: ProgressFn | None = None,
    keep_predictions: bool = True,
) -> EvalRun:
    """Score one model on one store page against the store's own records."""
    settings = settings or Settings.from_env()
    report = on_progress or (lambda _m: None)
    started = time.perf_counter()

    llm = get_provider(provider, model, settings)
    run = EvalRun(page_url=page_url, provider=llm.name, model=llm.model, prompt=prompt)

    try:
        report(f"Building ground truth for {page_url}")
        truth = build_page_truth(page_url, settings)
        run.truth_count = truth.count
        run.catalogue_size = truth.catalogue_size

        if truth.count == 0:
            run.error = (
                "No ground truth: the page linked no products that the store API knows "
                "about. Point at a collection page such as /collections/all."
            )
            return run

        report(f"Ground truth: {truth.count} products on the page")
        report(f"Extracting with {llm.name}/{llm.model}")

        agent = ScrapeAgent(provider=llm, settings=settings, on_progress=report)
        result = agent.run(page_url, prompt)

        metrics, _ = score_extraction(result.records, truth.records)
        metrics.ungrounded_removed = result.ungrounded_removed
        run.metrics = metrics.to_dict()
        run.tokens = result.usage.get("total_tokens", 0)
        if keep_predictions:
            run.predicted = result.records

        price = metrics.price_accuracy
        report(
            f"recall {metrics.recall:.0%} · precision {metrics.precision:.0%} · "
            f"price {'n/a' if price is None else format(price, '.0%')}"
            + (f" · {metrics.ungrounded_removed} invented number(s) discarded"
               if metrics.ungrounded_removed else "")
        )
    except Exception as exc:  # a failed cell must not kill the whole benchmark
        run.error = f"{type(exc).__name__}: {exc}"
        report(f"failed: {run.error}")

    run.elapsed_s = round(time.perf_counter() - started, 2)
    return run


@dataclass
class Benchmark:
    runs: list[EvalRun] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self, include_predictions: bool = True) -> dict[str, Any]:
        runs = []
        for run in self.runs:
            data = asdict(run)
            if not include_predictions:
                data.pop("predicted", None)
            runs.append(data)
        return {"created_at": self.created_at, "runs": runs}

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path


def run_benchmark(
    pages: list[str],
    models: list[tuple[str, str]],
    settings: Settings | None = None,
    *,
    on_progress: ProgressFn | None = None,
) -> Benchmark:
    """Every page against every (provider, model) pair."""
    settings = settings or Settings.from_env()
    report = on_progress or (lambda _m: None)
    benchmark = Benchmark()

    for page in pages:
        for provider, model in models:
            report(f"\n=== {page} · {provider}/{model} ===")
            benchmark.runs.append(
                evaluate_page(
                    page,
                    provider,
                    model,
                    settings,
                    on_progress=report,
                    keep_predictions=False,
                )
            )
    return benchmark


def _store_name(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.replace("www.", "") or url


def to_markdown(benchmark: Benchmark) -> str:
    """A table suitable for pasting into the README."""
    header = (
        "| Store | Model | On page | Found | Recall | Precision | Hallucinated | "
        "Price accuracy | Invented | Time |\n"
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    lines = [header]

    for run in benchmark.runs:
        if not run.ok:
            lines.append(
                f"| {_store_name(run.page_url)} | {run.model} | — | — | — | — | — | — | — | "
                f"failed |"
            )
            continue
        m = run.metrics
        price = m.get("price_accuracy")
        price_cell = "n/a" if price is None else f"{price:.0%}"
        lines.append(
            f"| {_store_name(run.page_url)} | `{run.model}` | {m['truth_count']} | "
            f"{m['matched_count']} | {m['recall']:.0%} | {m['precision']:.0%} | "
            f"{m['hallucination_rate']:.0%} | {price_cell} | "
            f"{m.get('ungrounded_removed', 0)} | {run.elapsed_s:.0f}s |"
        )

    return "\n".join(lines)

"""`scrape-agent-eval`: measure extraction accuracy against a store's own data."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from scraper_agent.config import Settings
from scraper_agent.evals.runner import (
    BENCHMARK_PROMPT,
    run_benchmark,
    to_markdown,
)


def _parse_model(spec: str) -> tuple[str, str]:
    """`ollama:qwen2.5:7b` -> ("ollama", "qwen2.5:7b")."""
    provider, separator, model = spec.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError(
            f"Model must look like provider:model, e.g. ollama:qwen2.5:7b (got {spec!r})"
        )
    return provider.strip().lower(), model.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-agent-eval",
        description=(
            "Measure how accurately the agent extracts products, using a Shopify "
            "store's own API as ground truth. No hand-labelled data required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  scrape-agent-eval https://www.allbirds.com/collections/mens
  scrape-agent-eval https://store.com/collections/all --model ollama:qwen2.5:3b --model ollama:qwen2.5:7b
  scrape-agent-eval URL1 URL2 --json evals/results/run.json --markdown evals/results/run.md
""",
    )
    parser.add_argument("pages", nargs="+", help="Shopify collection/listing page URLs")
    parser.add_argument(
        "--model",
        dest="models",
        action="append",
        type=_parse_model,
        metavar="PROVIDER:MODEL",
        help="Repeatable. Default: ollama:<OLLAMA_MODEL>",
    )
    parser.add_argument("--json", dest="json_path", metavar="PATH", help="Write full results JSON")
    parser.add_argument(
        "--markdown", dest="md_path", metavar="PATH", help="Write the results table"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    models = args.models or [("ollama", settings.ollama_model)]

    def progress(message: str) -> None:
        if not args.quiet:
            print(f"  · {message}", file=sys.stderr, flush=True)

    print(f"Prompt held constant: {BENCHMARK_PROMPT!r}", file=sys.stderr)

    try:
        benchmark = run_benchmark(
            args.pages,
            models,
            settings,
            on_progress=progress,
        )
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    table = to_markdown(benchmark)
    print("\n" + table)

    if args.json_path:
        path = benchmark.save(args.json_path)
        print(f"\nwrote {path}", file=sys.stderr)
    if args.md_path:
        from pathlib import Path

        md = Path(args.md_path)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(table + "\n", encoding="utf-8")
        print(f"wrote {md}", file=sys.stderr)

    failed = [r for r in benchmark.runs if not r.ok]
    for run in failed:
        print(f"error: {run.page_url} [{run.model}]: {run.error}", file=sys.stderr)

    return 1 if failed and len(failed) == len(benchmark.runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())

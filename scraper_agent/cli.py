"""Command-line interface: `python -m scraper_agent <url> "<what to extract>"`."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.fetch import FetchError
from scraper_agent.output import to_table, write_csv, write_json
from scraper_agent.providers import PROVIDERS, ProviderError, get_provider

_RENDER_CHOICES = {"auto": None, "always": True, "never": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-agent",
        description="Describe what you want from a web page; the agent extracts it as structured data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  scrape-agent https://news.ycombinator.com "top story titles, points and links"
  scrape-agent example.com/pricing "plan names and monthly prices" --csv plans.csv
  scrape-agent example.com "product name and price" --provider ollama --model qwen2.5:7b
  scrape-agent example.com "job titles" --fields title,location,url --render always
""",
    )
    parser.add_argument("url", help="Page to scrape")
    parser.add_argument("prompt", help="What to extract, in plain language")

    parser.add_argument(
        "--provider", choices=PROVIDERS, help="LLM backend (default: from SCRAPER_PROVIDER, else openai)"
    )
    parser.add_argument("--model", help="Model name for the chosen provider")
    parser.add_argument(
        "--render",
        choices=sorted(_RENDER_CHOICES),
        default="auto",
        help="Use a headless browser: auto (only if the page looks empty), always, or never",
    )
    parser.add_argument(
        "--fields",
        help="Comma-separated field names; skips schema inference and saves one LLM call",
    )
    parser.add_argument("--json", dest="json_path", metavar="PATH", help="Write full result JSON")
    parser.add_argument("--csv", dest="csv_path", metavar="PATH", help="Write records as CSV")
    parser.add_argument(
        "--records-only", action="store_true", help="With --json, write just the records array"
    )
    parser.add_argument(
        "--stdout-json", action="store_true", help="Print records as JSON instead of a table"
    )
    parser.add_argument(
        "--dump-markdown", metavar="PATH", help="Save the cleaned markdown sent to the model"
    )
    parser.add_argument(
        "--ignore-robots", action="store_true", help="Skip the robots.txt check (use responsibly)"
    )
    parser.add_argument(
        "--keep-boilerplate",
        action="store_true",
        help="Keep nav/header/footer (needed when the data lives in site chrome)",
    )
    parser.add_argument("--max-chars", type=int, help="Characters of page content per LLM call")
    parser.add_argument("--max-chunks", type=int, help="Cap on LLM calls per page")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = Settings.from_env()
    overrides = {}
    if args.max_chars:
        overrides["max_chunk_chars"] = args.max_chars
    if args.max_chunks:
        overrides["max_chunks"] = args.max_chunks
    if overrides:
        settings = Settings(**{**settings.__dict__, **overrides})

    def progress(message: str) -> None:
        if not args.quiet:
            print(f"  · {message}", file=sys.stderr, flush=True)

    try:
        provider = get_provider(args.provider, args.model, settings)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    agent = ScrapeAgent(provider=provider, settings=settings, on_progress=progress)

    try:
        result = agent.run(
            args.url,
            args.prompt,
            render=_RENDER_CHOICES[args.render],
            fields=[f for f in args.fields.split(",") if f.strip()] if args.fields else None,
            respect_robots=False if args.ignore_robots else None,
            strip_boilerplate=not args.keep_boilerplate,
            keep_markdown=bool(args.dump_markdown),
        )
    except (FetchError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    if args.stdout_json:
        print(json.dumps(result.records, indent=2, ensure_ascii=False))
    else:
        print(to_table(result.records))

    if args.json_path:
        path = write_json(result, args.json_path, records_only=args.records_only)
        print(f"wrote {path}", file=sys.stderr)
    if args.csv_path:
        path = write_csv(result, args.csv_path)
        print(f"wrote {path}", file=sys.stderr)
    if args.dump_markdown:
        with open(args.dump_markdown, "w", encoding="utf-8") as handle:
            handle.write(result.markdown)
        print(f"wrote {args.dump_markdown}", file=sys.stderr)

    if not args.quiet:
        usage = result.usage
        summary = (
            f"\n{result.count} record(s) · {result.provider}/{result.model} · "
            f"{usage.get('calls', 0)} call(s) · {usage.get('total_tokens', 0)} tokens · "
            f"{result.elapsed_s}s"
        )
        if result.cost_usd is not None:
            summary += f" · ~${result.cost_usd:.4f}"
        print(summary, file=sys.stderr)

    return 0 if result.records else 3


if __name__ == "__main__":
    raise SystemExit(main())

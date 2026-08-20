"""`scrape-agent-monitor`: catch extraction drift before your data does.

Exit codes are the product here as much as the output is: this is meant to run
on a schedule or in CI, where nobody reads stdout until something returns
non-zero.

  0  nothing degraded past the threshold
  1  warnings
  2  alerts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from scraper_agent.config import Settings
from scraper_agent.monitor.config import (
    EXAMPLE_WATCHLIST,
    WatchlistError,
    load_watchlist,
)
from scraper_agent.monitor.detect import ALERT, OK, WARN
from scraper_agent.monitor.history import History
from scraper_agent.monitor.runner import MonitorReport, run_monitor

_MARK = {OK: "ok  ", WARN: "WARN", ALERT: "FAIL"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-agent-monitor",
        description=(
            "Watch extraction pipelines for silent drift. Pages whose ground truth "
            "is knowable are scored absolutely; every page is compared against its "
            "own history, so breakage is caught with or without an answer key."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  scrape-agent-monitor init > watchlist.json
  scrape-agent-monitor run --config watchlist.json
  scrape-agent-monitor run --config watchlist.json --fail-on warn
  scrape-agent-monitor status --config watchlist.json
""",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="One monitoring pass over the watchlist")
    run.add_argument("--config", required=True, metavar="PATH", help="Watchlist JSON")
    run.add_argument("--state", metavar="PATH", help="History file (overrides the watchlist)")
    run.add_argument(
        "--fail-on",
        choices=[WARN, ALERT],
        default=ALERT,
        help="Lowest severity that should exit non-zero (default: alert)",
    )
    run.add_argument("--json", dest="json_path", metavar="PATH", help="Write the report as JSON")
    run.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")

    status = sub.add_parser("status", help="Recent history, without running anything")
    status.add_argument("--config", required=True, metavar="PATH", help="Watchlist JSON")
    status.add_argument("--state", metavar="PATH", help="History file")
    status.add_argument("--limit", type=int, default=5, help="Runs to show per page")

    sub.add_parser("init", help="Print an example watchlist")
    return parser


def render(report: MonitorReport) -> str:
    lines: list[str] = []
    for page in report.pages:
        tag = "canary" if page.canary else "      "
        lines.append(f"{_MARK[page.severity]}  {tag}  {page.page_url}")
        for finding in page.findings:
            if finding.severity == OK:
                lines.append(f"          · {finding.message}")
            else:
                lines.append(f"          ! {finding.message}")

    verdict = report.verdict
    if verdict:
        lines.append("")
        if verdict.source == "none":
            lines.append(f"verdict: clean. {verdict.explanation}")
        else:
            lines.append(f"verdict: {verdict.source} ({verdict.confidence} confidence)")
            lines.append(f"  {verdict.explanation}")
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> int:
    watchlist = load_watchlist(args.config)
    history = History(args.state or watchlist.state_path)

    def progress(message: str) -> None:
        if not args.quiet:
            print(f"  · {message}", file=sys.stderr, flush=True)

    try:
        report = run_monitor(
            watchlist, history, Settings.from_env(), on_progress=progress
        )
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    print("\n" + render(report))

    if args.json_path:
        payload = {
            "started_at": report.started_at,
            "severity": report.severity,
            "elapsed_s": report.elapsed_s,
            "verdict": vars(report.verdict) if report.verdict else None,
            "pages": [
                {
                    "page_url": p.page_url,
                    "canary": p.canary,
                    "severity": p.severity,
                    "findings": [vars(f) for f in p.findings],
                    "observation": p.observation.to_dict() if p.observation else None,
                }
                for p in report.pages
            ],
        }
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {path}", file=sys.stderr)

    return report.exit_code(args.fail_on)


def _status(args: argparse.Namespace) -> int:
    watchlist = load_watchlist(args.config)
    history = History(args.state or watchlist.state_path)

    if not history.path.exists():
        print(f"No history yet at {history.path}. Run `scrape-agent-monitor run` first.")
        return 0

    for page in watchlist.pages:
        rows = history.for_page(page.url, limit=args.limit)
        print(f"\n{page.url}")
        if not rows:
            print("  (never run)")
            continue
        for observation in rows:
            if not observation.ok:
                print(f"  {observation.observed_at}  failed: {observation.error}")
                continue
            count = observation.signals.get("record_count", 0)
            metrics = observation.metrics or {}
            scored = (
                f"  recall {metrics['recall']:.0%}  precision {metrics['precision']:.0%}"
                if metrics.get("recall") is not None
                else ""
            )
            print(f"  {observation.observed_at}  {count:>5} records{scored}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        print(json.dumps(EXAMPLE_WATCHLIST, indent=2))
        return 0
    if args.command is None:
        parser.print_help()
        return 2

    try:
        return _run(args) if args.command == "run" else _status(args)
    except WatchlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

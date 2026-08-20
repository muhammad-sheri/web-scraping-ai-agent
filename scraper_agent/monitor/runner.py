"""One monitoring pass: extract, score what can be scored, compare, record.

Both detectors run off a *single* extraction per page. Scoring a canary needs
the same records that the drift signals are computed from, so fetching twice
would cost double and — worse — could compare two different snapshots of a
page that changed in between.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from scraper_agent.agent import ScrapeAgent
from scraper_agent.config import Settings
from scraper_agent.evals.ground_truth import build_page_truth
from scraper_agent.evals.metrics import score_extraction
from scraper_agent.monitor.config import Watchlist, WatchPage
from scraper_agent.monitor.detect import (
    ALERT,
    OK,
    WARN,
    Finding,
    Thresholds,
    Verdict,
    attribute,
    compare,
    worst,
)
from scraper_agent.monitor.history import Baseline, History, Observation
from scraper_agent.monitor.signals import extract_signals
from scraper_agent.providers import get_provider

ProgressFn = Callable[[str], None]

_RANK = {OK: 0, WARN: 1, ALERT: 2}


@dataclass
class PageReport:
    page_url: str
    canary: bool = False
    severity: str = OK
    findings: list[Finding] = field(default_factory=list)
    observation: Observation | None = None
    baseline: Baseline | None = None

    @property
    def ok(self) -> bool:
        return self.severity == OK


@dataclass
class MonitorReport:
    pages: list[PageReport] = field(default_factory=list)
    verdict: Verdict | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    elapsed_s: float = 0.0

    @property
    def severity(self) -> str:
        return max((p.severity for p in self.pages), key=lambda s: _RANK[s], default=OK)

    @property
    def degraded(self) -> list[PageReport]:
        return [p for p in self.pages if _RANK[p.severity] >= _RANK[WARN]]

    def exit_code(self, fail_on: str = ALERT) -> int:
        """0 clean, 1 warn, 2 alert — so CI can gate on it."""
        if _RANK[self.severity] < _RANK[fail_on]:
            return 0
        return 2 if self.severity == ALERT else 1


def _truth_for(page_url: str, settings: Settings, report: ProgressFn) -> Any | None:
    """Ground truth if this page has any, else None. Never raises."""
    try:
        truth = build_page_truth(page_url, settings)
    except Exception as exc:
        report(f"  no ground truth ({type(exc).__name__})")
        return None
    if not truth.count:
        return None
    return truth


def check_page(
    page: WatchPage,
    watchlist: Watchlist,
    history: History,
    settings: Settings,
    *,
    thresholds: Thresholds | None = None,
    on_progress: ProgressFn | None = None,
) -> PageReport:
    """Run, score, compare and record one page."""
    report = on_progress or (lambda _m: None)
    started = time.perf_counter()
    prompt = page.question(watchlist.prompt)

    # Baseline must be read before this run is appended, or the run partly
    # becomes its own reference.
    baseline = history.baseline(page.url)

    observation = Observation(page_url=page.url, canary=bool(page.canary))
    try:
        llm = get_provider(watchlist.provider or settings.provider, watchlist.model, settings)
        observation.provider, observation.model = llm.name, llm.model

        report(f"{page.url}  ({llm.name}/{llm.model})")
        agent = ScrapeAgent(provider=llm, settings=settings)
        result = agent.run(page.url, prompt)
        observation.signals = extract_signals(result).to_dict()

        wants_truth = page.canary is not False
        if wants_truth:
            truth = _truth_for(page.url, settings, report)
            if truth is not None:
                metrics, _ = score_extraction(result.records, truth.records)
                metrics.ungrounded_removed = result.ungrounded_removed
                observation.metrics = metrics.to_dict()
                observation.canary = True
                report(
                    f"  {result.count} records · recall {metrics.recall:.0%} · "
                    f"precision {metrics.precision:.0%}"
                )
            else:
                observation.canary = False
                if page.canary is True:
                    report("  declared a canary but no ground truth was available")
                report(f"  {result.count} records · not measurable, drift signals only")
        else:
            report(f"  {result.count} records · drift signals only")

    except Exception as exc:  # one bad page must not end the pass
        observation.ok = False
        observation.error = f"{type(exc).__name__}: {exc}"
        report(f"  failed: {observation.error}")

    observation.elapsed_s = round(time.perf_counter() - started, 2)
    findings = compare(observation, baseline, thresholds)
    history.append(observation)

    return PageReport(
        page_url=page.url,
        canary=observation.canary,
        severity=worst(findings),
        findings=findings,
        observation=observation,
        baseline=baseline,
    )


def run_monitor(
    watchlist: Watchlist,
    history: History | None = None,
    settings: Settings | None = None,
    *,
    thresholds: Thresholds | None = None,
    on_progress: ProgressFn | None = None,
) -> MonitorReport:
    """A full pass over the watchlist."""
    settings = settings or Settings.from_env()
    history = history or History(watchlist.state_path)
    report = on_progress or (lambda _m: None)
    started = time.perf_counter()

    monitor_report = MonitorReport()
    for page in watchlist.pages:
        monitor_report.pages.append(
            check_page(
                page,
                watchlist,
                history,
                settings,
                thresholds=thresholds,
                on_progress=report,
            )
        )

    monitor_report.verdict = attribute(
        [
            (p.page_url, p.canary, p.severity, bool(p.baseline and p.baseline.usable))
            for p in monitor_report.pages
        ]
    )
    monitor_report.elapsed_s = round(time.perf_counter() - started, 2)
    return monitor_report

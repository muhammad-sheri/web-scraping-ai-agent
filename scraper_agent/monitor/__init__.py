"""Drift monitoring for extraction pipelines.

An extraction pipeline fails silently. A site redesigns its markup, recall
falls from 94% to 60%, and nothing raises: the scraper still returns rows, the
rows are still well-formed, and the bad numbers flow downstream into pricing
decisions and dashboards. Teams find out weeks later because a human noticed a
figure looked wrong.

Detecting that needs ground truth, and ground truth normally means hand
labelling, which nobody does nightly. So this package runs two detectors over
the same extraction pass:

*Truth-scored canaries* — Shopify pages, where the store publishes its own
records, so recall and precision are measurable absolutely. These do not have
to be pages you care about; they exercise the same fetch/clean/plan/extract
code path, so they catch regressions in the *extractor*.

*Signal drift* — for any page at all, with no ground truth: record count,
schema shape, per-field null rates and numeric distributions, compared against
the page's own recent history. This catches breakage on the *site*.

Running both is what makes the result actionable rather than just alarming.
If the canaries moved too, the extractor regressed and every page is suspect.
If one page moved alone, that site changed. See `attribute()` in `detect.py`.
"""

from scraper_agent.monitor.config import Watchlist, WatchPage, load_watchlist
from scraper_agent.monitor.detect import (
    ALERT,
    OK,
    WARN,
    Finding,
    attribute,
    compare,
    worst,
)
from scraper_agent.monitor.history import Baseline, History, Observation
from scraper_agent.monitor.runner import PageReport, MonitorReport, run_monitor
from scraper_agent.monitor.signals import Signals, extract_signals

__all__ = [
    "ALERT",
    "OK",
    "WARN",
    "Baseline",
    "Finding",
    "History",
    "MonitorReport",
    "Observation",
    "PageReport",
    "Signals",
    "WatchPage",
    "Watchlist",
    "attribute",
    "compare",
    "extract_signals",
    "load_watchlist",
    "run_monitor",
    "worst",
]

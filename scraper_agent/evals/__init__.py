"""Measuring how accurate the extraction actually is.

Evals normally stall on labelled data: someone has to say what the right
answer was. This package sidesteps that. A Shopify store publishes its own
records at /products.json, and the same store renders those products as HTML.
So the store hands us both the question and the answer key, for free, on every
store that exists — no annotation, no fixtures that rot.
"""

from scraper_agent.evals.ground_truth import (
    PageTruth,
    build_page_truth,
    product_handles_in_page,
)
from scraper_agent.evals.matching import Match, MatchResult, match_titles, normalise_title
from scraper_agent.evals.metrics import ExtractionMetrics, parse_price, score_extraction

__all__ = [
    "ExtractionMetrics",
    "Match",
    "MatchResult",
    "PageTruth",
    "build_page_truth",
    "match_titles",
    "normalise_title",
    "parse_price",
    "product_handles_in_page",
    "score_extraction",
]

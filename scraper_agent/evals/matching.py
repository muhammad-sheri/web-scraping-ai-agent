"""Deciding whether an extracted record refers to the same product as a true record.

Titles never line up exactly. A listing page shows "Men's Strider" while the
API calls it "Men's Strider - Medium Grey (Blizzard Sole)"; a model may return
"Mens Strider" without the apostrophe. Matching therefore runs three rules in
descending confidence — exact, contiguous containment, then fuzzy ratio — and
is entirely deterministic, so the metrics built on top are unit-testable
without a network or a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.85

_PUNCT = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")
# Apostrophes are deleted rather than turned into spaces: product titles are
# full of them ("Men's Strider", "Levi's"), and a model that drops the
# apostrophe must still match exactly rather than falling through to fuzzy.
_APOSTROPHE = re.compile(r"['‘’ʼ`]")


def normalise_title(text: object) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    if text is None:
        return ""
    lowered = _APOSTROPHE.sub("", str(text).lower())
    return _SPACE.sub(" ", _PUNCT.sub(" ", lowered)).strip()


def _tokens(text: object) -> list[str]:
    normalised = normalise_title(text)
    return normalised.split() if normalised else []


def _is_sublist(needle: list[str], haystack: list[str]) -> bool:
    """Contiguous sublist test."""
    if not needle or len(needle) > len(haystack):
        return False
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start] == first and haystack[start : start + len(needle)] == needle:
            return True
    return False


def similarity(a: object, b: object) -> tuple[float, str]:
    """Score two titles and say which rule fired.

    Returns (score, kind) where kind is exact | containment | fuzzy | none.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0, "none"

    if ta == tb:
        return 1.0, "exact"

    # Containment needs at least two tokens: a single shared word like "shoe"
    # would otherwise marry unrelated products.
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(shorter) >= 2 and _is_sublist(shorter, longer):
        return 0.95, "containment"

    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return ratio, "fuzzy"


@dataclass
class Match:
    predicted_index: int
    truth_index: int
    score: float
    kind: str


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_predicted: list[int] = field(default_factory=list)
    unmatched_truth: list[int] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return len(self.matches)


def match_titles(
    predicted: list[object],
    truth: list[object],
    threshold: float = DEFAULT_THRESHOLD,
) -> MatchResult:
    """Greedily pair predicted titles with truth titles.

    Each truth entry is consumed at most once, so two predictions of the same
    product count as one match plus one false positive rather than inflating
    recall.
    """
    available = set(range(len(truth)))
    result = MatchResult()

    for p_index, p_title in enumerate(predicted):
        best_index: int | None = None
        best_score = 0.0
        best_kind = "none"

        for t_index in sorted(available):
            score, kind = similarity(p_title, truth[t_index])
            if score > best_score:
                best_index, best_score, best_kind = t_index, score, kind
                if score == 1.0:
                    break  # cannot do better than exact

        if best_index is not None and best_score >= threshold:
            available.discard(best_index)
            result.matches.append(Match(p_index, best_index, round(best_score, 4), best_kind))
        else:
            result.unmatched_predicted.append(p_index)

    result.unmatched_truth = sorted(available)
    return result

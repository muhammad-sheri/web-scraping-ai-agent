"""Following pagination on ordinary (non-Shopify) listing pages.

Category listings show 20-60 items and put the rest behind "Next". Extracting
only page 1 gives a misleading dataset, so this walks the "next" chain and lets
the caller extract each page in turn.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

# Link text that means "forward", including the arrows sites use instead of words.
_NEXT_TEXT = re.compile(r"^\s*(next|next\s*(page|»|›|>)?|»|›|→|>>?)\s*$", re.IGNORECASE)
_PREV_HINT = re.compile(r"prev|back|earlier|«|‹|←", re.IGNORECASE)
# Not \bnext\b: `_` is a word character, so that misses the very common
# BEM-style `pagination__next`. Treat any non-alphanumeric as a separator.
_NEXT_ATTR = re.compile(r"(?<![a-z0-9])next", re.IGNORECASE)


def _is_usable(href: str | None) -> bool:
    return bool(href) and not href.strip().startswith(("#", "javascript:", "mailto:"))


def find_next_url(html: str, base_url: str) -> str | None:
    """Best guess at the next page's URL, or None when this is the last page."""
    soup = BeautifulSoup(html or "", "html.parser")

    # 1. The standards-compliant signal, when a site bothers to emit it.
    for tag in soup.find_all(["link", "a"], rel=True):
        rel = tag.get("rel") or []
        rel_values = rel if isinstance(rel, list) else [rel]
        if any(str(r).lower() == "next" for r in rel_values) and _is_usable(tag.get("href")):
            return urljoin(base_url, tag["href"].strip())

    # 2. A link whose visible text is "next" or an arrow.
    for anchor in soup.find_all("a", href=True):
        if not _is_usable(anchor.get("href")):
            continue
        text = anchor.get_text(" ", strip=True)
        if _NEXT_TEXT.match(text) and not _PREV_HINT.search(text):
            return urljoin(base_url, anchor["href"].strip())

    # 3. A link marked as "next" in class/id/aria-label — common in JS themes.
    for anchor in soup.find_all("a", href=True):
        if not _is_usable(anchor.get("href")):
            continue
        haystack = " ".join(
            filter(
                None,
                [
                    " ".join(anchor.get("class") or []),
                    anchor.get("id") or "",
                    anchor.get("aria-label") or "",
                    anchor.get("title") or "",
                ],
            )
        )
        if _NEXT_ATTR.search(haystack) and not _PREV_HINT.search(haystack):
            return urljoin(base_url, anchor["href"].strip())

    return None


def same_site(a: str, b: str) -> bool:
    """Keep a crawl on the host it started on."""
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


def next_page_url(html: str, current_url: str, seen: set[str]) -> str | None:
    """find_next_url plus the guards a loop needs: same host, no revisits."""
    candidate = find_next_url(html, current_url)
    if not candidate:
        return None
    if candidate.rstrip("/") == current_url.rstrip("/") or candidate in seen:
        return None
    if not same_site(candidate, current_url):
        return None
    return candidate

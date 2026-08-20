"""The watchlist: which pages to monitor, and how.

JSON rather than YAML so the package keeps its dependency list honest — a
watchlist is a dozen lines and does not justify pulling in a parser.

`canary` is deliberately optional. Whether a page can be scored against
ground truth is a fact about the page, not a decision the user should have to
research, so leaving it unset means "find out at run time".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Held constant across runs by default: a drift signal only means something
#: if the question did not change between yesterday and today.
DEFAULT_PROMPT = "every product on this page with its title and price"

DEFAULT_STATE_PATH = "~/.scrape-agent/monitor/history.jsonl"


class WatchlistError(ValueError):
    """Raised when a watchlist file cannot be used."""


@dataclass
class WatchPage:
    url: str
    #: True/False to force, None to detect whether ground truth is available.
    canary: bool | None = None
    #: Overrides the watchlist prompt for this page.
    prompt: str | None = None

    def question(self, default: str) -> str:
        return self.prompt or default


@dataclass
class Watchlist:
    pages: list[WatchPage] = field(default_factory=list)
    prompt: str = DEFAULT_PROMPT
    provider: str | None = None
    model: str | None = None
    state_path: str = DEFAULT_STATE_PATH

    @property
    def urls(self) -> list[str]:
        return [p.url for p in self.pages]


def parse_watchlist(data: dict[str, Any]) -> Watchlist:
    if not isinstance(data, dict):
        raise WatchlistError("A watchlist must be a JSON object.")

    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise WatchlistError('A watchlist needs a non-empty "pages" list.')

    pages: list[WatchPage] = []
    for index, entry in enumerate(raw_pages):
        if isinstance(entry, str):
            pages.append(WatchPage(url=entry))
            continue
        if not isinstance(entry, dict):
            raise WatchlistError(f"pages[{index}] must be a URL string or an object.")
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise WatchlistError(f'pages[{index}] is missing a "url".')
        canary = entry.get("canary")
        if canary is not None and not isinstance(canary, bool):
            raise WatchlistError(f'pages[{index}]["canary"] must be true, false or absent.')
        prompt = entry.get("prompt")
        if prompt is not None and not isinstance(prompt, str):
            raise WatchlistError(f'pages[{index}]["prompt"] must be a string.')
        pages.append(WatchPage(url=url.strip(), canary=canary, prompt=prompt))

    return Watchlist(
        pages=pages,
        prompt=data.get("prompt") or DEFAULT_PROMPT,
        provider=data.get("provider") or None,
        model=data.get("model") or None,
        state_path=data.get("state_path") or DEFAULT_STATE_PATH,
    )


def load_watchlist(path: str | Path) -> Watchlist:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise WatchlistError(f"No watchlist at {file_path}")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WatchlistError(f"{file_path} is not valid JSON: {exc}") from exc
    return parse_watchlist(data)


EXAMPLE_WATCHLIST = {
    "prompt": DEFAULT_PROMPT,
    "provider": "ollama",
    "model": "qwen2.5:7b",
    "pages": [
        {"url": "https://www.allbirds.com/collections/mens", "canary": True},
        {"url": "https://www.deathwishcoffee.com/collections/all", "canary": True},
        {"url": "https://books.toscrape.com"},
    ],
}

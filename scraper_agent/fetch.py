"""Page retrieval, escalating only as far as each page actually requires.

Three rungs, cheapest first:

1. plain httpx — fast, light, handles most of the web
2. curl_cffi with a browser TLS fingerprint — when the response looks like a
   bot block (403/429/503) rather than a real error
3. headless Chromium — when the HTML arrives as an empty JavaScript shell

Rendering costs seconds and a 400MB dependency, and impersonation costs a
compiled dependency, so neither runs unless the page demands it. robots.txt is
checked before any of them.
"""

from __future__ import annotations

import re
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from scraper_agent.config import Settings
from scraper_agent.http import HttpError
from scraper_agent.http import get as http_get

# Tags whose text is markup machinery, not page content. Used by the
# "is this page empty?" heuristic below.
_NOISE_TAGS = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")

# Root nodes that frameworks mount into. An empty one is a strong hint that
# the real content arrives via JavaScript.
_EMPTY_APP_ROOT = re.compile(
    r"<(div|main)[^>]*\bid=[\"'](root|app|__next|__nuxt)[\"'][^>]*>\s*</\1>",
    re.IGNORECASE,
)


class FetchError(RuntimeError):
    """Raised when a page cannot be retrieved."""


class RobotsDisallowed(FetchError):
    """Raised when robots.txt forbids fetching the URL."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    html: str
    status_code: int
    rendered: bool
    impersonated: bool = False

    @property
    def visible_text_length(self) -> int:
        return len(visible_text(self.html))


def visible_text(html: str) -> str:
    """Rough text-only view of the HTML, for size heuristics."""
    without_noise = _NOISE_TAGS.sub(" ", html)
    return " ".join(_TAGS.sub(" ", without_noise).split())


def looks_javascript_rendered(html: str, min_text_chars: int = 500) -> bool:
    """True when the HTML is probably a shell that needs a browser to fill in.

    Two signals: an empty framework mount point, or a document that parses to
    almost no visible text despite being a successful response.
    """
    if _EMPTY_APP_ROOT.search(html):
        return True
    return len(visible_text(html)) < min_text_chars


def normalize_url(url: str) -> str:
    """Accept `example.com/x` as well as a fully qualified URL."""
    url = url.strip()
    if not url:
        raise FetchError("No URL provided.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise FetchError(f"Could not parse a hostname out of {url!r}")
    return url


def robots_allows(url: str, user_agent: str, timeout: float = 10.0) -> bool:
    """Check robots.txt. A missing or unreachable robots.txt means allowed."""
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    try:
        response = httpx.get(
            robots_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
    except httpx.HTTPError:
        return True
    if response.status_code >= 400:
        return True

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    # robotparser matches the agent token, not the full UA string.
    return parser.can_fetch("*", url)


def fetch(
    url: str,
    settings: Settings | None = None,
    *,
    render: bool | None = None,
    respect_robots: bool | None = None,
) -> FetchResult:
    """Fetch `url` and return its HTML.

    render=None  -> auto: escalate to a browser only if the HTML looks empty
    render=True  -> always use Playwright
    render=False -> never use Playwright
    """
    settings = settings or Settings.from_env()
    url = normalize_url(url)

    should_check_robots = (
        settings.respect_robots if respect_robots is None else respect_robots
    )
    if should_check_robots and not robots_allows(url, settings.user_agent):
        raise RobotsDisallowed(
            f"robots.txt at {urlparse(url).netloc} disallows fetching {url}. "
            "Pass --ignore-robots to override (make sure you have permission)."
        )

    if render is True:
        html, final_url = _render_with_playwright(url, settings)
        return FetchResult(url, final_url, html, 200, rendered=True)

    try:
        response = http_get(
            url,
            timeout=settings.request_timeout,
            user_agent=settings.user_agent,
            impersonation=settings.impersonation,
            profile=settings.impersonate_profile,
        )
    except HttpError as exc:
        raise FetchError(str(exc)) from exc

    if response.status_code >= 400:
        raise FetchError(f"{url} returned HTTP {response.status_code}")

    html = response.text
    final_url = response.url
    impersonated = response.impersonated

    if render is None and looks_javascript_rendered(html):
        try:
            rendered_html, rendered_url = _render_with_playwright(final_url, settings)
        except FetchError:
            # Playwright unavailable or failed: the static HTML is still the
            # best answer we have, so return it rather than failing the run.
            return FetchResult(url, final_url, html, response.status_code, False, impersonated)
        return FetchResult(
            url, rendered_url, rendered_html, response.status_code, True, impersonated
        )

    if settings.politeness_delay:
        time.sleep(settings.politeness_delay)

    return FetchResult(
        url, final_url, html, response.status_code, rendered=False, impersonated=impersonated
    )


def _render_with_playwright(url: str, settings: Settings) -> tuple[str, str]:
    """Load the page in headless Chromium and return the settled DOM."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on local install
        raise FetchError(
            "This page needs a browser to render, but Playwright is not installed.\n"
            "  pip install playwright && playwright install chromium"
        ) from exc

    timeout_ms = int(settings.render_timeout * 1000)
    try:  # pragma: no cover - requires a real browser
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=settings.user_agent)
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:
                    # networkidle never settles on pages with polling/analytics;
                    # the DOM is usually complete regardless.
                    pass
                return page.content(), page.url
            finally:
                browser.close()
    except FetchError:
        raise
    except Exception as exc:  # pragma: no cover - requires a real browser
        raise FetchError(f"Browser rendering of {url} failed: {exc}") from exc

"""HTTP with a fallback for sites that block ordinary Python clients.

Plenty of sites serve a 403 to `httpx` and a 200 to Chrome while allowing the
exact same page in robots.txt. The block is not about permission — it keys off
the TLS/HTTP2 handshake fingerprint, which every Python HTTP client shares and
no browser does.

`curl_cffi` speaks libcurl-impersonate, reproducing a real browser's handshake,
so those pages load. It is used as an *escalation*, not a default: plain httpx
runs first because it is faster and lighter, and impersonation only kicks in
when a response actually looks like a block.

This changes nothing about permission. The robots.txt check in `fetch.py` still
runs first and still refuses disallowed URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

#: Statuses that typically mean "we think you are a bot", not "no such page".
BLOCKED_STATUSES = frozenset({401, 403, 405, 406, 409, 429, 503})

#: Browser profile passed to curl_cffi. "chrome" tracks a recent Chrome build.
DEFAULT_PROFILE = "chrome"


class HttpError(RuntimeError):
    """Raised when a request cannot be completed by any transport."""


@dataclass
class Response:
    text: str
    status_code: int
    url: str
    impersonated: bool = False

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        return json.loads(self.text)


def impersonation_available() -> bool:
    """Whether curl_cffi is installed."""
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return False
    return True


def _browser_headers(user_agent: str, extra: dict[str, str] | None) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra:
        headers.update(extra)
    return headers


def _plain_get(
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> Response:
    response = httpx.get(
        url, params=params, headers=headers, timeout=timeout, follow_redirects=True
    )
    return Response(response.text, response.status_code, str(response.url), False)


def _impersonated_get(
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
    profile: str,
) -> Response:
    try:
        from curl_cffi import requests as cffi
    except ImportError as exc:  # pragma: no cover - depends on local install
        raise HttpError(
            "curl_cffi is not installed, so blocked pages cannot be retried with a "
            "browser TLS fingerprint.\n  pip install curl_cffi"
        ) from exc

    # curl_cffi sets its own realistic header set for the chosen profile; only
    # pass through non-UA extras so we do not fight it.
    passthrough = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
    try:
        response = cffi.get(
            url,
            params=params,
            headers=passthrough or None,
            timeout=timeout,
            impersonate=profile,
            allow_redirects=True,
        )
    except Exception as exc:
        raise HttpError(f"Impersonated request to {url} failed: {exc}") from exc

    return Response(response.text, response.status_code, str(response.url), True)


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    user_agent: str = "",
    impersonation: str = "auto",
    profile: str = DEFAULT_PROFILE,
) -> Response:
    """GET a URL, escalating to browser impersonation when a site blocks us.

    impersonation:
        "auto"   - plain first, impersonate only if the response looks like a block
        "always" - impersonate immediately
        "never"  - plain only
    """
    request_headers = _browser_headers(user_agent, headers)

    if impersonation == "always":
        return _impersonated_get(
            url, params=params, headers=request_headers, timeout=timeout, profile=profile
        )

    transport_error: Exception | None = None
    plain: Response | None = None
    try:
        plain = _plain_get(url, params=params, headers=request_headers, timeout=timeout)
        if plain.ok or plain.status_code not in BLOCKED_STATUSES:
            return plain
    except httpx.HTTPError as exc:
        transport_error = exc

    if impersonation != "never" and impersonation_available():
        try:
            retried = _impersonated_get(
                url,
                params=params,
                headers=request_headers,
                timeout=timeout,
                profile=profile,
            )
        except HttpError:
            retried = None
        if retried is not None and (retried.ok or plain is None):
            return retried

    if plain is not None:
        if not impersonation_available() and impersonation != "never":
            # Say what would help, rather than just reporting the status.
            raise HttpError(
                f"{url} returned HTTP {plain.status_code}, which usually means the "
                f"site blocked a non-browser client. Installing curl_cffi lets the "
                f"agent retry with a real browser's TLS fingerprint:\n"
                f"  pip install curl_cffi"
            )
        return plain

    raise HttpError(f"Request to {url} failed: {transport_error}")

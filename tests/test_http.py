"""The escalation ladder: plain HTTP first, browser fingerprint only if blocked."""

from __future__ import annotations

import httpx
import pytest

from scraper_agent import http as agent_http
from scraper_agent.http import BLOCKED_STATUSES, HttpError, Response, get


@pytest.fixture
def transports(monkeypatch):
    """Script the plain and impersonated transports independently."""
    calls: list[str] = []

    def install(plain, impersonated, available=True):
        def fake_plain(url, *, params, headers, timeout):
            calls.append("plain")
            if isinstance(plain, Exception):
                raise plain
            return Response(plain[1], plain[0], url, False)

        def fake_impersonated(url, *, params, headers, timeout, profile):
            calls.append("impersonated")
            if isinstance(impersonated, Exception):
                raise impersonated
            return Response(impersonated[1], impersonated[0], url, True)

        monkeypatch.setattr(agent_http, "_plain_get", fake_plain)
        monkeypatch.setattr(agent_http, "_impersonated_get", fake_impersonated)
        monkeypatch.setattr(agent_http, "impersonation_available", lambda: available)
        return calls

    return install


# --- the happy path stays cheap -------------------------------------------


def test_successful_plain_request_never_escalates(transports):
    calls = transports(plain=(200, "<html>ok</html>"), impersonated=(200, "never used"))
    response = get("https://example.com")

    assert response.status_code == 200
    assert response.impersonated is False
    assert calls == ["plain"]  # curl_cffi not touched


def test_a_genuine_404_is_not_retried(transports):
    """404 means "no such page", not "you are a bot"."""
    calls = transports(plain=(404, "Not Found"), impersonated=(200, "x"))
    response = get("https://example.com/missing")

    assert response.status_code == 404
    assert calls == ["plain"]


# --- escalation on a block ------------------------------------------------


@pytest.mark.parametrize("status", sorted(BLOCKED_STATUSES))
def test_blocking_statuses_trigger_impersonation(transports, status):
    calls = transports(plain=(status, "Access denied"), impersonated=(200, "<html>real</html>"))
    response = get("https://protected.example.com")

    assert response.status_code == 200
    assert response.impersonated is True
    assert response.text == "<html>real</html>"
    assert calls == ["plain", "impersonated"]


def test_transport_failure_also_escalates(transports):
    calls = transports(
        plain=httpx.ConnectError("handshake failed"), impersonated=(200, "<html>real</html>")
    )
    response = get("https://protected.example.com")

    assert response.impersonated is True
    assert calls == ["plain", "impersonated"]


def test_still_blocked_after_impersonation_returns_the_original_status(transports):
    transports(plain=(403, "denied"), impersonated=(403, "denied again"))
    response = get("https://hard.example.com")

    assert response.status_code == 403


def test_failure_on_both_transports_raises(transports):
    transports(
        plain=httpx.ConnectError("dns"), impersonated=HttpError("curl failed")
    )
    with pytest.raises(HttpError):
        get("https://nowhere.example.com")


# --- modes ----------------------------------------------------------------


def test_always_skips_the_plain_attempt(transports):
    calls = transports(plain=(200, "unused"), impersonated=(200, "<html>real</html>"))
    response = get("https://example.com", impersonation="always")

    assert response.impersonated is True
    assert calls == ["impersonated"]


def test_never_refuses_to_escalate(transports):
    calls = transports(plain=(403, "denied"), impersonated=(200, "would have worked"))
    response = get("https://example.com", impersonation="never")

    assert response.status_code == 403
    assert calls == ["plain"]


def test_missing_curl_cffi_explains_the_fix(transports):
    transports(plain=(403, "denied"), impersonated=(200, "x"), available=False)

    with pytest.raises(HttpError, match="pip install curl_cffi"):
        get("https://protected.example.com")


# --- response helper ------------------------------------------------------


def test_response_parses_json():
    assert Response('{"products": []}', 200, "https://x.com").json() == {"products": []}


def test_response_ok_flag():
    assert Response("", 200, "u").ok is True
    assert Response("", 403, "u").ok is False

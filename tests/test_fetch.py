import pytest

from scraper_agent.fetch import (
    FetchError,
    looks_javascript_rendered,
    normalize_url,
    visible_text,
)


def test_normalize_adds_scheme():
    assert normalize_url("example.com/x") == "https://example.com/x"
    assert normalize_url("  http://example.com ") == "http://example.com"


def test_normalize_rejects_junk():
    for bad in ("", "   ", "ftp://example.com", "https://"):
        with pytest.raises(FetchError):
            normalize_url(bad)


def test_visible_text_ignores_scripts_and_tags(shop_html):
    text = visible_text(shop_html)
    assert "Widget Pro" in text
    assert "__DATA__" not in text
    assert "<span" not in text


def test_spa_shell_is_flagged_for_rendering(spa_html):
    assert looks_javascript_rendered(spa_html) is True


def test_content_bearing_page_is_not_flagged(shop_html):
    # The fixture carries ~350 chars of real text and has no empty app root,
    # so with a threshold below that it must not be sent to a browser.
    assert looks_javascript_rendered(shop_html, min_text_chars=200) is False


def test_thin_page_is_flagged():
    assert looks_javascript_rendered("<html><body><p>hi</p></body></html>") is True


def test_threshold_is_what_decides_for_short_pages(shop_html):
    # Same page, stricter expectation of "enough text" -> escalate to a browser.
    assert looks_javascript_rendered(shop_html, min_text_chars=1_000) is True

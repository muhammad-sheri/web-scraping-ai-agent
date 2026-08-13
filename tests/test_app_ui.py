"""Streamlit UI rendering, via AppTest. Layout only — no Scrape click, since
that would hit the network; the agent pipeline itself is covered elsewhere.

Written after PUBLIC_DEMO_MODE support was added: an earlier edit left part of
the sidebar re-indented outside its `with st.sidebar:` block, which no offline
test caught because app.py isn't otherwise exercised by pytest. AppTest runs
the real script and surfaces exactly that class of mistake.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parent.parent / "app.py")


def _run(monkeypatch, public_demo: bool) -> AppTest:
    if public_demo:
        monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    else:
        monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    return at


def test_normal_mode_renders_without_exceptions(monkeypatch):
    at = _run(monkeypatch, public_demo=False)
    assert not at.exception


def test_normal_mode_shows_the_full_sidebar(monkeypatch):
    at = _run(monkeypatch, public_demo=False)
    assert {"Model", "Fetching"} <= {h.value for h in at.sidebar.header}


def test_normal_mode_offers_both_extraction_modes(monkeypatch):
    at = _run(monkeypatch, public_demo=False)
    mode_radio = next(r for r in at.radio if r.label == "Mode")
    assert set(mode_radio.options) == {"Ask in plain language", "Full Shopify catalogue"}


def test_public_demo_renders_without_exceptions(monkeypatch):
    at = _run(monkeypatch, public_demo=True)
    assert not at.exception


def test_public_demo_hides_the_llm_sidebar(monkeypatch):
    """No Ollama/OpenAI controls: nothing there for a stranger to misconfigure."""
    at = _run(monkeypatch, public_demo=True)
    assert {"Model", "Fetching"}.isdisjoint({h.value for h in at.sidebar.header})


def test_public_demo_skips_the_mode_choice(monkeypatch):
    """Shopify mode is forced, not offered — there is nothing to route to."""
    at = _run(monkeypatch, public_demo=True)
    assert not any(r.label == "Mode" for r in at.radio)


def test_public_demo_prefills_a_working_store_url(monkeypatch):
    at = _run(monkeypatch, public_demo=True)
    store_url = next(t for t in at.text_input if t.key == "shop_url")
    assert store_url.value == "https://www.allbirds.com"


def test_public_demo_shows_a_scrape_button(monkeypatch):
    """Regression: a stray indent once put sidebar widgets outside `with
    st.sidebar:`, which raised at import time and would have shown no button at
    all — AppTest surfaces that as an exception, not a passing empty page."""
    at = _run(monkeypatch, public_demo=True)
    assert any(b.label == "Scrape" for b in at.button)


def test_normal_mode_has_no_cooldown_state(monkeypatch):
    """The rate-limit courtesy throttle is demo-only, not a normal-use tax."""
    at = _run(monkeypatch, public_demo=False)
    assert "last_run_ts" not in at.session_state

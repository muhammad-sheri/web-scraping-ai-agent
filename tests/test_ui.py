"""The design system: pure HTML builders, and the tokens they share with the theme.

No browser and no Streamlit runtime needed — ui.py returns strings on purpose.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

import ui

CONFIG = Path(__file__).parent.parent / ".streamlit" / "config.toml"


# --- the theme file -------------------------------------------------------


@pytest.fixture(scope="module")
def theme() -> dict:
    return tomllib.loads(CONFIG.read_text())["theme"]


def test_the_theme_file_parses(theme):
    assert theme["base"] == "light"


def test_light_and_dark_are_both_fully_specified(theme):
    """A half-defined dark theme is how you get white text on white cards."""
    required = {
        "primaryColor", "backgroundColor", "secondaryBackgroundColor",
        "textColor", "borderColor", "linkColor",
        "greenColor", "redColor", "orangeColor",
    }
    assert required <= set(theme)
    assert required <= set(theme["dark"])


def test_ui_palette_matches_the_streamlit_theme(theme):
    """ui.py duplicates a few tokens; drift would show as two different indigos."""
    assert ui.LIGHT["primary"].lower() == theme["primaryColor"].lower()
    assert ui.DARK["primary"].lower() == theme["dark"]["primaryColor"].lower()
    assert ui.LIGHT["bg"].lower() == theme["backgroundColor"].lower()
    assert ui.DARK["bg"].lower() == theme["dark"]["backgroundColor"].lower()
    assert ui.LIGHT["border"].lower() == theme["borderColor"].lower()
    assert ui.DARK["border"].lower() == theme["dark"]["borderColor"].lower()


# --- css ------------------------------------------------------------------


def test_css_is_generated_per_theme():
    light, dark = ui.css("light"), ui.css("dark")
    assert ui.LIGHT["bg"] in light and ui.LIGHT["bg"] not in dark
    assert ui.DARK["bg"] in dark and ui.DARK["bg"] not in light


def test_unknown_theme_names_fall_back_to_light():
    assert ui.palette("solarized") is ui.LIGHT
    assert ui.palette("") is ui.LIGHT


def test_css_hides_the_sidebar():
    """The redesign has no sidebar; the collapsed-control arrow must go too."""
    css = ui.css("light")
    assert '[data-testid="stSidebar"]' in css
    assert '[data-testid="stExpandSidebarButton"]' in css


def test_css_braces_are_balanced():
    """f-string CSS is one unescaped brace away from silently breaking."""
    css = ui.css("dark")
    assert css.count("{") == css.count("}")


def test_css_has_no_leftover_format_placeholders():
    body = ui.css("light")
    assert not re.search(r"\{[a-z_]+\}", body), "an f-string placeholder survived"


# --- hero -----------------------------------------------------------------


def test_hero_renders_its_parts():
    html = ui.hero("Title", "Sub", ["one", "two"], eyebrow="Live")
    assert "<h1>Title</h1>" in html
    assert "Live" in html
    assert "<span>one</span>" in html and "<span>two</span>" in html


def test_hero_subtitle_keeps_its_link_but_the_title_is_escaped():
    """The subtitle is trusted markup; everything the caller names is not."""
    html = ui.hero("A <b>&</b> B", '<a href="#x">repo</a>', [])
    assert '<a href="#x">repo</a>' in html
    assert "A &lt;b&gt;&amp;&lt;/b&gt; B" in html


def test_hero_always_carries_the_repo_link():
    assert ui.GITHUB_URL in ui.hero("t", "s", [])


def test_compact_hero_is_opt_in():
    assert "fx-compact" not in ui.hero("t", "s", [])
    assert "fx-compact" in ui.hero("t", "s", [], compact=True)


# --- stat tiles -----------------------------------------------------------


def test_stat_tiles_render_label_value_and_sub():
    html = ui.stat_tiles([{"label": "Products", "value": "385", "sub": "in the catalogue"}])
    assert "Products" in html and "385" in html and "in the catalogue" in html


def test_stat_tone_picks_the_accent():
    assert "var(--fx-green)" in ui.stat_tiles([{"label": "x", "value": "1", "tone": "green"}])
    assert "var(--fx-red)" in ui.stat_tiles([{"label": "x", "value": "1", "tone": "red"}])


def test_unknown_tone_falls_back_to_primary():
    assert "var(--fx-primary)" in ui.stat_tiles([{"label": "x", "value": "1", "tone": "puce"}])


def test_long_values_step_down_a_size():
    """A price range is three times the width of a count and would wrap."""
    assert "fx-sm" in ui.stat_tiles([{"label": "Price", "value": "$585.00 – $13,999.00"}])
    assert "fx-sm" not in ui.stat_tiles([{"label": "Products", "value": "385"}])


def test_the_sub_line_is_optional():
    assert "fx-stat-sub" not in ui.stat_tiles([{"label": "x", "value": "1"}])


def test_stat_values_are_escaped():
    assert "<script>" not in ui.stat_tiles([{"label": "x", "value": "<script>alert(1)</script>"}])


# --- panels and anchors ---------------------------------------------------


def test_anchor_names_the_container_for_css():
    assert ui.anchor("filter") == '<span class="fx-anchor fx-filter-anchor"></span>'


@pytest.mark.parametrize("name", ["card", "filter", "variant"])
def test_every_anchor_has_a_matching_style_rule(name):
    """An anchor with no rule behind it is a container that silently stays plain."""
    assert f"fx-{name}-anchor" in ui.css("light")


def test_panel_head_renders_chips():
    html = ui.panel_head("⌕", "Filters", ["In stock", "“gold”"])
    assert "Filters" in html and "In stock" in html


def test_panel_head_without_chips_is_still_valid():
    assert "fx-chips" in ui.panel_head("◎", "Which store?")


def test_section_subtitle_is_optional():
    assert "<span>" not in ui.section("Results")
    assert "<span>https://s.com</span>" in ui.section("Results", "https://s.com")


def test_empty_state_carries_its_message():
    html = ui.empty_state("🔍", "Nothing matches", "Widen the range.")
    assert "Nothing matches" in html and "Widen the range." in html


# --- product header -------------------------------------------------------


def test_product_header_links_to_the_store():
    html = ui.product_header("Star Ring", "21 variants", "https://s.com/products/x", None)
    assert 'href="https://s.com/products/x"' in html
    assert 'target="_blank"' in html and 'rel="noopener"' in html


def test_product_header_survives_a_product_with_no_image_or_url():
    html = ui.product_header("Star Ring", "1 variant", None, None)
    assert "Star Ring" in html
    assert "<a href" not in html
    assert "<img" in html  # a transparent placeholder keeps the row aligned


def test_product_titles_with_quotes_do_not_break_the_markup():
    html = ui.product_header('The 14" Chain', "1 variant", 'https://s.com/"x', None)
    assert "&quot;" in html

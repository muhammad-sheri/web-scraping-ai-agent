"""Presentation layer for app.py: the design system Streamlit config cannot express.

Division of labour, deliberately:

  .streamlit/config.toml  colours, fonts, radii, semantic tones. Streamlit
                          applies these to its *own* components, so widgets,
                          dataframes, badges and alerts come out on-theme
                          instead of being fought with a stylesheet.
  ui.py                   the pieces Streamlit has no component for: the
                          hero band, the stat tiles, the filter panel shell,
                          section headers, empty states.

Everything here is a pure function returning an HTML string, so it can be
tested without a browser or a Streamlit runtime. app.py passes the results to
st.html().

Streamlit internals are targeted only through `data-testid` attributes that are
stable across releases, and every rule degrades to "slightly plainer" rather
than "broken" if one stops matching. The theme config, not the CSS, is what
makes the app look designed.
"""

from __future__ import annotations

from html import escape

GITHUB_URL = "https://github.com/muhammad-sheri/web-scraping-ai-agent"

# Kept in sync with .streamlit/config.toml by test_ui.py, which parses the
# TOML and compares. Duplicated rather than imported because config.toml is
# read by Streamlit's own bootstrap, not by application code.
LIGHT = {
    "primary": "#5B5BD6",
    "primary_hi": "#7C6BF0",
    "on_primary": "#FFFFFF",
    "bg": "#F1F0FA",
    "surface": "#FFFFFF",
    "text": "#16181F",
    "muted": "#61667A",
    "border": "#E4E5EE",
    "border_soft": "#EDEEF5",
    "tint": "rgba(91, 91, 214, 0.06)",
    "tint_hi": "rgba(91, 91, 214, 0.13)",
    "shadow": "0 1px 2px rgba(16,18,31,.04), 0 8px 24px -12px rgba(16,18,31,.14)",
    "shadow_lg": "0 1px 2px rgba(16,18,31,.05), 0 18px 40px -20px rgba(16,18,31,.28)",
    "glow_a": "rgba(91, 91, 214, 0.13)",
    "glow_b": "rgba(124, 107, 240, 0.10)",
    "hero_from": "#1E1B4B",
    "hero_to": "#4338CA",
    "hero_text": "#EEF0FF",
    "hero_muted": "#B9BEEA",
    "green": "#0E9F6E",
    "red": "#DC2626",
    "orange": "#D97706",
}

DARK = {
    "primary": "#8B8BF5",
    "primary_hi": "#A5A0FF",
    "on_primary": "#0B0C12",
    "bg": "#0A0A14",
    "surface": "#14151F",
    "text": "#E6E8F0",
    "muted": "#9096AC",
    "border": "#242736",
    "border_soft": "#1C1F2B",
    "tint": "rgba(139, 139, 245, 0.08)",
    "tint_hi": "rgba(139, 139, 245, 0.16)",
    "shadow": "0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.6)",
    "shadow_lg": "0 1px 2px rgba(0,0,0,.5), 0 18px 40px -20px rgba(0,0,0,.8)",
    "glow_a": "rgba(120, 116, 255, 0.10)",
    "glow_b": "rgba(88, 80, 200, 0.08)",
    "hero_from": "#171634",
    "hero_to": "#2E2A6E",
    "hero_text": "#EEF0FF",
    "hero_muted": "#A7ADD8",
    "green": "#34D399",
    "red": "#F87171",
    "orange": "#FBBF24",
}


def palette(theme: str = "light") -> dict[str, str]:
    return DARK if str(theme).lower() == "dark" else LIGHT


def css(theme: str = "light") -> str:
    """The whole design layer, rendered for the theme that is actually active.

    Generated server-side from `st.context.theme` rather than with a
    prefers-color-scheme media query, because Streamlit's theme is an explicit
    in-app choice that need not match the operating system's.
    """
    c = palette(theme)
    return f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {{
  --fx-primary: {c["primary"]};
  --fx-primary-hi: {c["primary_hi"]};
  --fx-on-primary: {c["on_primary"]};
  --fx-bg: {c["bg"]};
  --fx-surface: {c["surface"]};
  --fx-text: {c["text"]};
  --fx-muted: {c["muted"]};
  --fx-border: {c["border"]};
  --fx-border-soft: {c["border_soft"]};
  --fx-tint: {c["tint"]};
  --fx-tint-hi: {c["tint_hi"]};
  --fx-shadow: {c["shadow"]};
  --fx-shadow-lg: {c["shadow_lg"]};
  --fx-green: {c["green"]};
  --fx-red: {c["red"]};
  --fx-orange: {c["orange"]};
}}

/* ---- Streamlit chrome ------------------------------------------------- */
/* The app has its own header, so the default one is dead space. The toolbar
   (rerun / settings menu) stays reachable; only its backdrop is removed. */
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stToolbar"] {{ right: .5rem; top: .35rem; }}
[data-testid="stAppDeployButton"] {{ display: none; }}
[data-testid="stSidebar"], [data-testid="stExpandSidebarButton"] {{ display: none !important; }}
#MainMenu, footer {{ visibility: hidden; }}

[data-testid="stMainBlockContainer"] {{
  padding: 3rem 2.5rem 5rem;
  max-width: 1500px;
}}
@media (max-width: 900px) {{
  [data-testid="stMainBlockContainer"] {{ padding: 2.75rem 1rem 4rem; }}
}}

html, body, [data-testid="stAppViewContainer"] {{
  font-feature-settings: "cv02", "cv03", "cv04", "cv11";
  -webkit-font-smoothing: antialiased;
}}

/* The canvas. A flat near-white is what every default Streamlit app looks
   like; this is a lavender-tinted ground lit from the top corners, which
   makes the white cards read as objects sitting on a surface rather than
   as slightly-different-white rectangles. */
[data-testid="stAppViewContainer"] {{
  background:
    radial-gradient(1100px 520px at 12% -12%, {c["glow_a"]}, transparent 62%),
    radial-gradient(900px 460px at 96% -6%, {c["glow_b"]}, transparent 58%),
    var(--fx-bg);
  background-attachment: fixed;
}}

/* ---- hero ------------------------------------------------------------- */
.fx-hero {{
  position: relative;
  overflow: hidden;
  border-radius: 20px;
  padding: 2.1rem 2.3rem 1.9rem;
  margin: .25rem 0 1.5rem;
  background:
    radial-gradient(1100px 340px at 88% -40%, rgba(255,255,255,.20), transparent 60%),
    linear-gradient(118deg, {c["hero_from"]} 0%, {c["hero_to"]} 100%);
  box-shadow: var(--fx-shadow-lg);
}}
.fx-hero::after {{
  /* faint grid, so the band reads as a surface rather than a colour swatch */
  content: "";
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.055) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: linear-gradient(105deg, transparent 30%, #000 100%);
  pointer-events: none;
}}
.fx-hero-inner {{ position: relative; z-index: 1; }}
.fx-hero h1 {{
  color: {c["hero_text"]};
  font-size: clamp(1.75rem, 3vw, 2.5rem);
  font-weight: 700; letter-spacing: -.025em; line-height: 1.1; margin: 0;
}}
.fx-hero p {{
  color: {c["hero_muted"]};
  font-size: 1rem; line-height: 1.6; margin: .7rem 0 0; max-width: 68ch;
}}
.fx-hero p a {{ color: #C7D2FE; text-decoration: underline; text-underline-offset: 3px; }}
.fx-hero-chips {{ display: flex; flex-wrap: wrap; gap: .45rem; margin-top: 1.15rem; }}
/* Once there are results, the hero stops being the point of the page. */
.fx-hero.fx-compact {{ padding: 1.15rem 2.3rem 1.2rem; border-radius: 16px; margin-bottom: 1.1rem; }}
.fx-hero.fx-compact h1 {{ font-size: 1.4rem; }}
.fx-hero.fx-compact p {{ display: none; }}
.fx-hero.fx-compact .fx-hero-chips {{ margin-top: .8rem; }}

.fx-hero-chips span, .fx-hero-chips a {{
  font-size: .76rem; font-weight: 500; color: {c["hero_text"]};
  background: rgba(255,255,255,.11);
  border: 1px solid rgba(255,255,255,.16);
  border-radius: 999px; padding: .3rem .7rem;
  backdrop-filter: blur(6px);
  text-decoration: none;
}}
.fx-hero-chips a {{ border-color: rgba(255,255,255,.34); }}
.fx-hero-chips a:hover {{ background: rgba(255,255,255,.22); }}

/* ---- section headers -------------------------------------------------- */
.fx-section {{ display: flex; align-items: baseline; gap: .7rem; margin: 1.6rem 0 .8rem; }}
.fx-section h2 {{
  font-size: 1.06rem; font-weight: 650; letter-spacing: -.01em;
  color: var(--fx-text); margin: 0;
}}
.fx-section span {{ font-size: .84rem; color: var(--fx-muted); }}
.fx-rule {{ height: 1px; background: var(--fx-border-soft); border: 0; margin: 1.4rem 0; }}

/* ---- stat tiles ------------------------------------------------------- */
.fx-stats {{
  display: grid; gap: .8rem; margin: .2rem 0 .4rem;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
}}
.fx-stat {{
  position: relative; overflow: hidden;
  background: var(--fx-surface);
  border: 1px solid var(--fx-border);
  border-radius: 14px;
  padding: .95rem 1.1rem 1rem;
  box-shadow: var(--fx-shadow);
  transition: transform .16s ease, box-shadow .16s ease;
}}
.fx-stat:hover {{ transform: translateY(-2px); box-shadow: var(--fx-shadow-lg); }}
.fx-stat::before {{
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--fx-accent, var(--fx-primary));
}}
.fx-stat-label {{
  font-size: .69rem; font-weight: 600; letter-spacing: .1em; text-transform: uppercase;
  color: var(--fx-muted); display: flex; align-items: center; gap: .4rem;
}}
.fx-stat-value {{
  font-size: 1.85rem; font-weight: 700; letter-spacing: -.03em; line-height: 1.15;
  color: var(--fx-text); margin-top: .3rem;
  font-variant-numeric: tabular-nums;
}}
.fx-stat-value.fx-sm {{ font-size: 1.25rem; letter-spacing: -.02em; }}
.fx-stat-sub {{ font-size: .78rem; color: var(--fx-muted); margin-top: .2rem; }}

/* ---- panels ----------------------------------------------------------- */
/* A marker span inside the container lets :has() style that exact container
   without depending on how many siblings precede it. */
.fx-anchor {{ display: none; }}

[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .fx-card-anchor) {{
  background: var(--fx-surface);
  border: 1px solid var(--fx-border);
  border-radius: 16px;
  padding: 1.25rem 1.35rem;
  box-shadow: var(--fx-shadow);
}}

/* The filter panel is the loud one on purpose: tinted ground, accent edge. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .fx-filter-anchor) {{
  background: linear-gradient(180deg, var(--fx-tint), transparent 55%), var(--fx-surface);
  border: 1px solid var(--fx-border);
  border-left: 3px solid var(--fx-primary);
  border-radius: 16px;
  padding: 1.1rem 1.3rem 1.2rem;
  box-shadow: var(--fx-shadow);
}}

[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .fx-variant-anchor) {{
  background: var(--fx-tint);
  border: 1px solid var(--fx-border);
  border-radius: 14px;
  padding: .9rem 1.1rem 1rem;
}}

.fx-panel-head {{
  display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; flex-wrap: wrap; margin-bottom: .35rem;
}}
.fx-panel-title {{
  display: flex; align-items: center; gap: .55rem;
  font-size: .95rem; font-weight: 650; color: var(--fx-text);
}}
.fx-panel-title .fx-icon {{
  display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px;
  background: var(--fx-tint-hi); color: var(--fx-primary); font-size: .82rem;
}}

/* ---- chips ------------------------------------------------------------ */
.fx-chips {{ display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }}
.fx-chip {{
  font-size: .75rem; font-weight: 500;
  background: var(--fx-tint-hi); color: var(--fx-primary);
  border-radius: 999px; padding: .22rem .62rem;
  white-space: nowrap;
}}
.fx-chip.fx-quiet {{ background: transparent; color: var(--fx-muted); }}

/* ---- widgets ---------------------------------------------------------- */
.stButton > button, .stDownloadButton > button {{
  font-weight: 600; letter-spacing: -.005em; transition: transform .12s ease, filter .12s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{ transform: translateY(-1px); }}
.stButton > button[kind="primary"] {{
  background: linear-gradient(135deg, var(--fx-primary), var(--fx-primary-hi));
  border: 0; color: var(--fx-on-primary);
  box-shadow: 0 6px 18px -8px var(--fx-primary);
  padding: .62rem 1rem;
}}
.stButton > button[kind="primary"]:hover {{ filter: brightness(1.06); }}

[data-testid="stWidgetLabel"] p {{
  font-size: .78rem; font-weight: 600; letter-spacing: .01em; color: var(--fx-muted);
}}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
[data-testid="stElementToolbar"] {{ backdrop-filter: blur(8px); }}

/* ---- empty state ------------------------------------------------------ */
.fx-empty {{
  text-align: center; padding: 2.6rem 1rem;
  border: 1px dashed var(--fx-border); border-radius: 16px;
  background: var(--fx-surface);
}}
.fx-empty .fx-emoji {{ font-size: 1.9rem; }}
.fx-empty h3 {{ font-size: 1rem; font-weight: 650; color: var(--fx-text); margin: .6rem 0 .3rem; }}
.fx-empty p {{ font-size: .87rem; color: var(--fx-muted); margin: 0 auto; max-width: 46ch; line-height: 1.6; }}

/* ---- product header inside an expanded variant panel ------------------ */
.fx-product {{ display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }}
.fx-product img {{
  width: 42px; height: 42px; border-radius: 10px; object-fit: cover;
  border: 1px solid var(--fx-border); background: var(--fx-tint);
}}
.fx-product .fx-name {{ font-weight: 650; color: var(--fx-text); font-size: .95rem; }}
.fx-product .fx-meta {{ font-size: .8rem; color: var(--fx-muted); }}
.fx-product a {{ font-size: .8rem; color: var(--fx-primary); font-weight: 500; }}
</style>"""


# --- components -----------------------------------------------------------


def hero(
    title: str,
    subtitle: str,
    chips: list,
    compact: bool = False,
) -> str:
    """The page header.

    `chips` items are either a string or a (label, href) pair, which renders
    as a link chip. The repo link lives there rather than pinned to the hero's
    top-right corner: Streamlit Cloud draws its own Fork/GitHub toolbar in
    exactly that spot, and the two overlapped.

    `compact` shrinks the band once results exist. The pitch has done its job
    by then, and 370px above the data is just scrolling.
    """
    parts = []
    for chip in chips:
        if isinstance(chip, (tuple, list)):
            label, href = chip
            parts.append(
                f'<a href="{escape(str(href), quote=True)}" target="_blank" '
                f'rel="noopener">{escape(str(label))}</a>'
            )
        else:
            parts.append(f"<span>{escape(str(chip))}</span>")

    return (
        f'<div class="fx-hero{" fx-compact" if compact else ""}">'
        '<div class="fx-hero-inner">'
        f"<h1>{escape(title)}</h1>"
        f"<p>{escape(subtitle)}</p>"
        f'<div class="fx-hero-chips">{"".join(parts)}</div>'
        "</div></div>"
    )


def stat_tiles(tiles: list[dict]) -> str:
    """A responsive row of stat cards.

    Each tile: {"label", "value", "sub" (optional), "tone" (optional)}. Tone
    picks the accent edge, using the same green/red/orange meanings as the table.
    """
    tones = {"green": "var(--fx-green)", "red": "var(--fx-red)",
             "orange": "var(--fx-orange)", "primary": "var(--fx-primary)"}
    cells = []
    for tile in tiles:
        accent = tones.get(str(tile.get("tone", "primary")), tones["primary"])
        value = str(tile.get("value", "n/a"))
        # Long values (a price range) need to step down or they wrap badly.
        size = " fx-sm" if len(value) > 12 else ""
        sub = f'<div class="fx-stat-sub">{escape(str(tile["sub"]))}</div>' if tile.get("sub") else ""
        cells.append(
            f'<div class="fx-stat" style="--fx-accent:{accent}">'
            f'<div class="fx-stat-label">{escape(str(tile.get("label", "")))}</div>'
            f'<div class="fx-stat-value{size}">{escape(value)}</div>'
            f"{sub}</div>"
        )
    return f'<div class="fx-stats">{"".join(cells)}</div>'


def anchor(name: str) -> str:
    """Invisible marker so CSS can style the container this sits inside."""
    return f'<span class="fx-anchor fx-{escape(name)}-anchor"></span>'


def panel_head(icon: str, title: str, chips: list[str] | None = None) -> str:
    """Title row for a panel, with optional summary chips on the right."""
    chip_html = "".join(
        f'<span class="fx-chip">{escape(c)}</span>' for c in (chips or [])
    )
    return (
        '<div class="fx-panel-head">'
        f'<div class="fx-panel-title"><span class="fx-icon">{escape(icon)}</span>{escape(title)}</div>'
        f'<div class="fx-chips">{chip_html}</div>'
        "</div>"
    )


def section(title: str, subtitle: str = "") -> str:
    sub = f"<span>{escape(subtitle)}</span>" if subtitle else ""
    return f'<div class="fx-section"><h2>{escape(title)}</h2>{sub}</div>'


def empty_state(emoji: str, title: str, body: str) -> str:
    return (
        f'<div class="fx-empty"><div class="fx-emoji">{escape(emoji)}</div>'
        f"<h3>{escape(title)}</h3><p>{escape(body)}</p></div>"
    )


def product_header(title: str, meta: str, url: str | None, image: str | None) -> str:
    """Heading for one expanded product's variant table."""
    img = (
        f'<img src="{escape(str(image), quote=True)}" alt="">'
        if image
        else '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==" alt="">'
    )
    link = (
        f'<a href="{escape(str(url), quote=True)}" target="_blank" rel="noopener">Open in store ↗</a>'
        if url
        else ""
    )
    return (
        f'<div class="fx-product">{img}'
        f'<div><div class="fx-name">{escape(title)}</div>'
        f'<div class="fx-meta">{escape(meta)}</div></div>'
        f'<div style="margin-left:auto">{link}</div></div>'
    )

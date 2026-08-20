"""HTML -> compact markdown.

Raw HTML is mostly markup: a 400KB product page is maybe 8KB of facts. Sending
the raw document to an LLM is slow, expensive, and *less* accurate, because the
signal is buried. This module strips the machinery and keeps the structure that
carries meaning (headings, lists, tables, link targets) in markdown, which
models read well and which costs a fraction of the tokens.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Dropped outright: their text is never page content.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "map",
    "link",
    "meta",
)

# Dropped when strip_boilerplate is on: site chrome repeated on every page.
_BOILERPLATE_TAGS = ("nav", "header", "footer", "aside")

_INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i",
    "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small", "span",
    "strong", "sub", "sup", "time", "u", "var", "wbr", "font", "label",
}

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_MULTI_BLANK = re.compile(r"\n{3,}")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _resolve(href: str, base_url: str) -> str:
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "data:")):
        return ""
    return urljoin(base_url, href) if base_url else href


def _inline_text(node: Tag | NavigableString, base_url: str) -> str:
    """Flatten a subtree to a single line, preserving link targets."""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name in _DROP_TAGS:
        return ""

    if node.name == "br":
        return " "

    if node.name == "a":
        label = _squash("".join(_inline_text(c, base_url) for c in node.children))
        href = _resolve(node.get("href", ""), base_url)
        if not label:
            # Image-only or icon links: the target is still worth keeping.
            return f"<{href}>" if href else ""
        return f"[{label}]({href})" if href else label

    if node.name == "img":
        alt = _squash(node.get("alt", ""))
        src = _resolve(node.get("src") or node.get("data-src") or "", base_url)
        if not src:
            return alt
        return f"![{alt}]({src})"

    if node.name == "code":
        inner = _squash("".join(_inline_text(c, base_url) for c in node.children))
        return f"`{inner}`" if inner else ""

    return "".join(_inline_text(c, base_url) for c in node.children)


def _cell_text(cell: Tag, base_url: str) -> str:
    # Pipes would break the markdown table row.
    return _squash(_inline_text(cell, base_url)).replace("|", "\\|")


def _own_rows(table: Tag) -> list[Tag]:
    """Rows belonging to this table, not to a table nested inside it."""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def is_layout_table(table: Tag) -> bool:
    """True when a <table> is page furniture rather than tabular data.

    Plenty of the web (Hacker News, older CMS output, most HTML email) uses
    tables purely for positioning. Rendering those as markdown grids produces a
    sparse mess that costs tokens and confuses extraction, so they are unwrapped
    and their contents rendered as ordinary blocks instead.
    """
    if table.find("table") is not None:  # nesting is a layout tell
        return True
    rows = _own_rows(table)
    if not rows:
        return True
    if table.find("th") is not None:  # a header row means real data
        return False
    widths = [len(tr.find_all(["th", "td"], recursive=False)) for tr in rows]
    widths = [w for w in widths if w]
    return not widths or max(widths) <= 1


def _render_table(table: Tag, base_url: str) -> str:
    rows: list[list[str]] = []
    for tr in _own_rows(table):
        cells = tr.find_all(["th", "td"], recursive=False) or tr.find_all(["th", "td"])
        if not cells:
            continue
        values = [_cell_text(c, base_url) for c in cells]
        if any(v for v in values):  # skip spacer rows
            rows.append(values)
    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    own = _own_rows(table)
    has_header = bool(own and own[0].find("th"))
    header = rows[0] if has_header else [""] * width
    body = rows[1:] if has_header else rows

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _render_list(list_tag: Tag, base_url: str, depth: int = 0) -> str:
    ordered = list_tag.name == "ol"
    indent = "  " * depth
    lines: list[str] = []
    index = 1

    for li in list_tag.find_all("li", recursive=False):
        nested = [c for c in li.find_all(["ul", "ol"], recursive=False)]
        for n in nested:
            n.extract()

        text = _squash(_inline_text(li, base_url))
        marker = f"{index}." if ordered else "-"
        if text:
            lines.append(f"{indent}{marker} {text}")
            index += 1
        for n in nested:
            sub = _render_list(n, base_url, depth + 1)
            if sub:
                lines.append(sub)

    return "\n".join(lines)


def _blocks(node: Tag, base_url: str) -> list[str]:
    """Walk children, emitting one string per block-level element."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            text = _squash("".join(pending))
            if text:
                out.append(text)
            pending.clear()

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            pending.append(str(child))
            continue
        if not isinstance(child, Tag) or child.name in _DROP_TAGS:
            continue

        name = child.name

        if name in _INLINE_TAGS or name in ("img", "br"):
            pending.append(_inline_text(child, base_url))
            continue

        flush()

        if name in _HEADINGS:
            text = _squash(_inline_text(child, base_url))
            if text:
                out.append("#" * _HEADINGS[name] + " " + text)
        elif name in ("ul", "ol"):
            rendered = _render_list(child, base_url)
            if rendered:
                out.append(rendered)
        elif name == "table":
            if is_layout_table(child):
                # Unwrap: render the cells' contents as normal blocks.
                out.extend(_blocks(child, base_url))
            else:
                rendered = _render_table(child, base_url)
                if rendered:
                    out.append(rendered)
        elif name == "pre":
            text = child.get_text().strip("\n")
            if text.strip():
                out.append(f"```\n{text}\n```")
        elif name == "blockquote":
            for block in _blocks(child, base_url):
                out.append("\n".join(f"> {line}" for line in block.splitlines()))
        elif name == "hr":
            out.append("---")
        elif name in ("p", "dt", "dd", "figcaption", "caption", "summary"):
            text = _squash(_inline_text(child, base_url))
            if text:
                out.append(text)
        else:
            # Generic container (div, section, article, main, li outside a
            # list, ...): recurse so nested blocks keep their structure.
            out.extend(_blocks(child, base_url))

    flush()
    return [b for b in out if b.strip()]


def _select_content_root(soup: BeautifulSoup, strip_boilerplate: bool) -> Tag:
    """Prefer <main>/<article> when it holds the bulk of the page's text."""
    body = soup.body or soup
    if not strip_boilerplate:
        return body

    body_len = len(body.get_text(" ", strip=True))
    for selector in ("main", "article", '[role="main"]', "#content", "#main"):
        candidate = body.select_one(selector)
        if candidate is None:
            continue
        if body_len == 0 or len(candidate.get_text(" ", strip=True)) / body_len >= 0.4:
            return candidate
    return body


def html_to_markdown(
    html: str,
    base_url: str = "",
    *,
    strip_boilerplate: bool = True,
) -> str:
    """Convert an HTML document to compact markdown.

    base_url makes every link and image URL absolute, so extracted links are
    usable rather than relative fragments.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    title = _squash(soup.title.get_text()) if soup.title else ""

    root = _select_content_root(soup, strip_boilerplate)
    if strip_boilerplate:
        for tag in root.find_all(_BOILERPLATE_TAGS):
            tag.decompose()

    blocks = _blocks(root, base_url)
    if title and not (blocks and blocks[0].lstrip("#").strip() == title):
        blocks.insert(0, f"# {title}")

    return _MULTI_BLANK.sub("\n\n", "\n\n".join(blocks)).strip()

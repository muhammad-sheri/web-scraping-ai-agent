"""Split long markdown into model-sized pieces at natural boundaries.

Splitting mid-sentence (or worse, mid-table-row) loses records. This splits on
blank-line block boundaries, and carries a small overlap forward so a record
straddling a boundary still appears whole in one of the chunks. Duplicates the
overlap creates are removed later by the record merger.
"""

from __future__ import annotations


def _hard_split(block: str, max_chars: int) -> list[str]:
    """Break a single oversized block, preferring line boundaries."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0

    for line in block.splitlines(keepends=True):
        while len(line) > max_chars:
            # A single line longer than the budget (minified text, one huge
            # table row): cut it at the character limit.
            if current:
                pieces.append("".join(current))
                current, size = [], 0
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if size + len(line) > max_chars and current:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)

    if current:
        pieces.append("".join(current))
    return [p for p in pieces if p.strip()]


def _tail(text: str, overlap: int) -> str:
    """Last `overlap` chars of text, snapped forward to a line boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    tail = text[-overlap:]
    newline = tail.find("\n")
    return tail[newline + 1 :] if newline != -1 else tail


def chunk_markdown(
    markdown: str,
    max_chars: int = 12_000,
    overlap: int = 400,
    max_chunks: int | None = None,
) -> list[str]:
    """Split `markdown` into chunks of at most ~max_chars characters."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = (markdown or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Overlap must leave room for real content in every chunk.
    overlap = max(0, min(overlap, max_chars // 4))

    blocks: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        blocks.extend(_hard_split(block, max_chars) if len(block) > max_chars else [block])

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for block in blocks:
        addition = len(block) + (2 if current else 0)
        if current and size + addition > max_chars:
            chunk = "\n\n".join(current)
            chunks.append(chunk)
            carry = _tail(chunk, overlap)
            current = [carry] if carry.strip() else []
            size = len(carry)
            addition = len(block) + (2 if current else 0)
        current.append(block)
        size += addition

    if current:
        chunks.append("\n\n".join(current))

    if max_chunks is not None and len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
    return chunks

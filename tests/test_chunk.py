import pytest

from scraper_agent.chunk import chunk_markdown


def test_short_text_is_one_chunk():
    assert chunk_markdown("hello world", max_chars=100) == ["hello world"]


def test_empty_text_yields_nothing():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_splits_long_text_within_budget():
    text = "\n\n".join(f"Block number {i} " + "x" * 200 for i in range(40))
    chunks = chunk_markdown(text, max_chars=1_000, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 1_000 for c in chunks)


def test_no_content_is_lost_without_overlap():
    blocks = [f"Block {i}" for i in range(50)]
    chunks = chunk_markdown("\n\n".join(blocks), max_chars=100, overlap=0)
    joined = "\n".join(chunks)
    assert all(b in joined for b in blocks)


def test_overlap_repeats_the_boundary():
    blocks = [f"Line {i}" for i in range(60)]
    chunks = chunk_markdown("\n\n".join(blocks), max_chars=200, overlap=50)
    assert len(chunks) > 2
    # The tail of one chunk should reappear at the head of the next.
    assert any(chunks[i].splitlines()[-1] in chunks[i + 1] for i in range(len(chunks) - 1))


def test_single_oversized_block_is_hard_split():
    chunks = chunk_markdown("y" * 5_000, max_chars=1_000, overlap=0)
    assert len(chunks) >= 5
    assert all(len(c) <= 1_000 for c in chunks)


def test_max_chunks_caps_calls():
    text = "\n\n".join(f"Block {i}" for i in range(500))
    assert len(chunk_markdown(text, max_chars=100, overlap=0, max_chunks=3)) == 3


def test_overlap_cannot_starve_a_chunk():
    # An overlap larger than the budget would loop forever if not clamped.
    chunks = chunk_markdown("\n\n".join(f"B{i}" for i in range(100)), max_chars=100, overlap=900)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_invalid_budget_rejected():
    with pytest.raises(ValueError):
        chunk_markdown("text", max_chars=0)

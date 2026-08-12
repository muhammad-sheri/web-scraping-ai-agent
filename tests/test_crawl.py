"""Next-page detection for paginated listings."""

from scraper_agent.crawl import find_next_url, next_page_url, same_site

BASE = "https://shop.example.com/category?page=1"


def test_rel_next_link_tag_wins():
    html = '<head><link rel="next" href="/category?page=2"></head>'
    assert find_next_url(html, BASE) == "https://shop.example.com/category?page=2"


def test_rel_next_anchor():
    html = '<a rel="next" href="/category?page=2">forward</a>'
    assert find_next_url(html, BASE) == "https://shop.example.com/category?page=2"


def test_next_by_link_text():
    html = '<a href="/p2">Previous</a><a href="/p3">Next</a>'
    assert find_next_url(html, BASE) == "https://shop.example.com/p3"


def test_next_by_arrow_glyph():
    html = '<a href="/back">«</a><a href="/fwd">›</a>'
    assert find_next_url(html, BASE) == "https://shop.example.com/fwd"


def test_next_by_class_name():
    html = '<a class="pagination__next" href="/page/2">2</a>'
    assert find_next_url(html, BASE) == "https://shop.example.com/page/2"


def test_next_class_name_variants():
    # BEM, kebab, camel and bare spellings all appear in the wild.
    for cls in ("pagination__next", "next-page", "nextPage", "next", "pager-next"):
        html = f'<a class="{cls}" href="/page/2">2</a>'
        assert find_next_url(html, BASE) == "https://shop.example.com/page/2", cls


def test_next_by_aria_label():
    html = '<a aria-label="Next page" href="/page/2">›</a>'
    assert find_next_url(html, BASE) == "https://shop.example.com/page/2"


def test_previous_link_is_never_mistaken_for_next():
    assert find_next_url('<a href="/p1">Previous</a>', BASE) is None
    assert find_next_url('<a class="prev-next-btn prev" href="/p1">back</a>', BASE) is None


def test_last_page_returns_none():
    assert find_next_url("<a href='/other'>Something else</a>", BASE) is None
    assert find_next_url("", BASE) is None


def test_javascript_and_anchor_hrefs_are_skipped():
    assert find_next_url('<a href="#" >Next</a>', BASE) is None
    assert find_next_url('<a href="javascript:void(0)">Next</a>', BASE) is None


def test_same_site_check():
    assert same_site("https://a.com/x", "https://a.com/y")
    assert not same_site("https://a.com/x", "https://b.com/y")


# --- loop guards ----------------------------------------------------------


def test_next_page_url_refuses_to_revisit():
    html = '<a rel="next" href="/category?page=2">Next</a>'
    seen = {"https://shop.example.com/category?page=2"}
    assert next_page_url(html, BASE, seen) is None


def test_next_page_url_refuses_self_reference():
    html = '<a rel="next" href="/category?page=1">Next</a>'
    assert next_page_url(html, "https://shop.example.com/category?page=1", set()) is None


def test_next_page_url_refuses_to_leave_the_site():
    html = '<a rel="next" href="https://other.com/page2">Next</a>'
    assert next_page_url(html, BASE, set()) is None


def test_next_page_url_accepts_a_valid_next():
    html = '<a rel="next" href="/category?page=2">Next</a>'
    assert next_page_url(html, BASE, set()) == "https://shop.example.com/category?page=2"

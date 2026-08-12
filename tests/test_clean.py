from bs4 import BeautifulSoup

from scraper_agent.clean import html_to_markdown, is_layout_table


def _table(html: str):
    return BeautifulSoup(html, "html.parser").find("table")


def test_drops_scripts_styles_and_noscript(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "__DATA__" not in md
    assert "color: red" not in md
    assert "Enable JavaScript" not in md


def test_keeps_headings_and_content(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "## Featured products" in md
    assert "Widget Pro" in md
    assert "$49.99" in md


def test_links_become_absolute(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com/catalogue")
    assert "[Widget Pro](https://shop.example.com/p/widget-pro)" in md
    assert "](/p/widget-pro)" not in md


def test_images_keep_alt_and_absolute_src(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "![Summer sale](https://shop.example.com/img/banner.png)" in md


def test_tables_render_as_markdown(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "| Model | Weight | Battery |" in md
    assert "| --- | --- | --- |" in md
    assert "| Widget Pro | 240 g | 18 h |" in md


def test_boilerplate_stripped_by_default(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "Privacy" not in md
    assert "© 2026" not in md


def test_boilerplate_kept_when_requested(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com", strip_boilerplate=False)
    assert "Privacy" in md


def test_lists_render_as_bullets(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    bullets = [line for line in md.splitlines() if line.startswith("- ")]
    assert any("Widget Mini" in line and "$19.50" in line for line in bullets)


def test_title_becomes_h1_when_missing(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert md.splitlines()[0] == "# Gadget Shop — Catalogue"


def test_code_and_quote_survive(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert "curl https://api.example.com/products" in md
    assert "> Best gadgets I have ever owned." in md


def test_markdown_is_far_smaller_than_html(shop_html):
    md = html_to_markdown(shop_html, "https://shop.example.com")
    assert len(md) < len(shop_html) / 2


def test_empty_input_is_safe():
    assert html_to_markdown("") == ""
    assert html_to_markdown("<html><body></body></html>") == ""


def test_relative_base_url_left_alone():
    md = html_to_markdown('<a href="/x">X</a>', "")
    assert "[X](/x)" in md


# --- layout tables --------------------------------------------------------
# Sites like Hacker News position content with nested tables. Rendering those
# as markdown grids produced a sparse mess that doubled token count.


def test_nested_tables_are_layout():
    assert is_layout_table(_table("<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>"))


def test_single_column_table_is_layout():
    assert is_layout_table(_table("<table><tr><td>one</td></tr><tr><td>two</td></tr></table>"))


def test_table_with_headers_is_data():
    assert not is_layout_table(_table("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"))


def test_layout_table_contents_survive_unwrapping():
    html = """
    <table><tr><td>
        <h2>Section</h2>
        <table><tr><td><p>Real paragraph</p><a href="/deep">Deep link</a></td></tr></table>
    </td></tr></table>
    """
    md = html_to_markdown(html, "https://e.example")

    assert "## Section" in md
    assert "Real paragraph" in md
    assert "[Deep link](https://e.example/deep)" in md
    assert "| --- |" not in md  # unwrapped, not rendered as a grid


def test_data_table_inside_layout_table_still_renders():
    html = """
    <table><tr><td>
      <table>
        <tr><th>Model</th><th>Price</th></tr>
        <tr><td>Pro</td><td>49</td></tr>
      </table>
    </td></tr></table>
    """
    md = html_to_markdown(html, "")
    assert "| Model | Price |" in md
    assert "| Pro | 49 |" in md


def test_spacer_rows_are_dropped():
    html = """
    <table>
      <tr><th>A</th><th>B</th></tr>
      <tr><td></td><td></td></tr>
      <tr><td>1</td><td>2</td></tr>
    </table>
    """
    md = html_to_markdown(html, "")
    assert "|  |  |" not in md
    assert "| 1 | 2 |" in md


def test_nested_table_rows_do_not_leak_into_the_outer_table():
    html = """
    <table>
      <tr><th>Outer</th><th>Col</th></tr>
      <tr><td>a</td><td><table><tr><td>inner-only</td></tr></table></td></tr>
    </table>
    """
    outer = _table(html)
    # The outer table has a header, but it nests another table -> layout.
    assert is_layout_table(outer)
    md = html_to_markdown(html, "")
    assert "inner-only" in md

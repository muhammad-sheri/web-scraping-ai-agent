import pytest

from scraper_agent.parsing import (
    clean_value,
    coerce_records,
    merge_records,
    parse_json_loose,
)


def test_parses_plain_json():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parses_fenced_json():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose("```\n[1, 2]\n```") == [1, 2]


def test_parses_json_with_chatter_around_it():
    assert parse_json_loose('Sure! Here you go: {"a": 1} Hope that helps.') == {"a": 1}


def test_rejects_unparseable_output():
    with pytest.raises(ValueError):
        parse_json_loose("I could not find anything.")
    with pytest.raises(ValueError):
        parse_json_loose("")


def test_coerce_handles_every_shape_models_emit():
    records = [{"name": "a"}, {"name": "b"}]
    assert coerce_records(records) == records
    assert coerce_records({"items": records}) == records
    assert coerce_records({"products": records}) == records
    assert coerce_records({"name": "solo"}) == [{"name": "solo"}]
    assert coerce_records([]) == []
    assert coerce_records(None) == []


def test_coerce_keeps_multi_key_object_as_one_record():
    payload = {"title": "Page", "links": ["a", "b"]}
    assert coerce_records(payload) == [payload]


def test_coerce_wraps_scalars():
    assert coerce_records(["x", "y"]) == [{"value": "x"}, {"value": "y"}]


def test_merge_drops_duplicates_across_chunks():
    a = [{"name": "Widget", "price": "10"}]
    b = [{"name": "Widget", "price": "10"}, {"name": "Gizmo", "price": "20"}]
    merged = merge_records([a, b])
    assert merged == [{"name": "Widget", "price": "10"}, {"name": "Gizmo", "price": "20"}]


def test_merge_ignores_case_and_whitespace_when_deduping():
    a = [{"name": "Widget  Pro"}]
    b = [{"name": "widget pro"}]
    assert len(merge_records([a, b])) == 1


def test_merge_drops_all_null_records():
    assert merge_records([[{"name": None, "price": ""}]]) == []


# --- markdown leaking into values ----------------------------------------
# The page reaches the model as markdown, so values come back wearing link
# syntax. Seen live: qwen2.5:3b returned "[What sort of maths...]" as a title.


def test_markdown_links_reduce_to_their_label():
    assert clean_value("[Some title](https://example.com/x)") == "Some title"


def test_image_only_link_falls_back_to_the_url():
    assert clean_value("[](https://example.com/i.png)") == "https://example.com/i.png"


def test_bare_bracket_wrapper_is_removed():
    assert clean_value("[new]") == "new"


def test_emphasis_markers_are_removed():
    assert clean_value("**49.99**") == "49.99"
    assert clean_value("`code`") == "code"


def test_plain_values_are_untouched():
    assert clean_value("A Light in the Attic") == "A Light in the Attic"
    assert clean_value("https://example.com/page") == "https://example.com/page"
    assert clean_value("Array [0] index") == "Array [0] index"


def test_non_strings_pass_through():
    assert clean_value(51.77) == 51.77
    assert clean_value(None) is None
    assert clean_value(True) is True


def test_blank_strings_become_null():
    assert clean_value("   ") is None


def test_cleaning_applies_through_coercion():
    records = coerce_records({"items": [{"title": "[X](https://e.com)", "price": 10}]})
    assert records == [{"title": "X", "price": 10}]


def test_merge_preserves_order():
    groups = [[{"n": "1"}], [{"n": "2"}], [{"n": "3"}]]
    assert [r["n"] for r in merge_records(groups)] == ["1", "2", "3"]

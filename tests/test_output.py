import csv
import json

from scraper_agent.agent import ScrapeResult
from scraper_agent.output import columns_for, to_table, write_csv, write_json


def make_result(records):
    return ScrapeResult(
        url="https://x.example",
        final_url="https://x.example",
        prompt="products",
        records=records,
        provider="fake",
        model="fake-1",
    )


def test_columns_are_the_union_in_first_seen_order():
    records = [{"a": 1, "b": 2}, {"b": 3, "c": 4}]
    assert columns_for(records) == ["a", "b", "c"]


def test_write_json_round_trips(tmp_path):
    result = make_result([{"name": "Widget", "price": 10}])
    path = write_json(result, tmp_path / "out.json")
    payload = json.loads(path.read_text())

    assert payload["records"] == [{"name": "Widget", "price": 10}]
    assert payload["provider"] == "fake"
    assert "markdown" not in payload  # page text is not dumped by default


def test_write_json_records_only(tmp_path):
    result = make_result([{"name": "Widget"}])
    payload = json.loads(write_json(result, tmp_path / "r.json", records_only=True).read_text())
    assert payload == [{"name": "Widget"}]


def test_write_csv_fills_ragged_rows(tmp_path):
    result = make_result([{"name": "A", "price": 1}, {"name": "B"}])
    path = write_csv(result, tmp_path / "out.csv")

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0] == {"name": "A", "price": "1"}
    assert rows[1] == {"name": "B", "price": ""}


def test_write_csv_serialises_nested_values(tmp_path):
    result = make_result([{"name": "A", "tags": ["x", "y"]}])
    path = write_csv(result, tmp_path / "n.csv")

    # Nested values survive as JSON in the cell (CSV quoting is the writer's job).
    row = next(csv.DictReader(path.open(encoding="utf-8")))
    assert json.loads(row["tags"]) == ["x", "y"]


def test_write_creates_missing_directories(tmp_path):
    result = make_result([{"a": 1}])
    path = write_json(result, tmp_path / "nested" / "deep" / "out.json")
    assert path.exists()


def test_table_renders_headers_and_rows():
    table = to_table([{"name": "Widget", "price": 10}])
    assert "name" in table and "price" in table
    assert "Widget" in table


def test_table_handles_no_records():
    assert to_table([]) == "(no records)"


def test_table_truncates_long_values():
    table = to_table([{"desc": "x" * 200}], max_width=20)
    assert all(len(line) <= 60 for line in table.splitlines())

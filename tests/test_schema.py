import pytest

from scraper_agent.providers.base import ProviderError
from scraper_agent.schema import (
    infer_plan,
    plan_from_fields,
    plan_to_json_schema,
)
from tests.conftest import FakeProvider

PLAN_PAYLOAD = {
    "item_name": "product",
    "multiple": True,
    "fields": [
        {"name": "name", "type": "string", "description": "product name"},
        {"name": "price", "type": "number", "description": "price in USD"},
    ],
}


def test_infer_plan_reads_the_model_response():
    plan = infer_plan("all products with prices", FakeProvider(plan=PLAN_PAYLOAD))
    assert plan.item_name == "product"
    assert plan.multiple is True
    assert plan.field_names == ["name", "price"]
    assert plan.fields[1].type == "number"


def test_infer_plan_normalises_messy_field_names():
    payload = dict(PLAN_PAYLOAD, fields=[
        {"name": "Product Name!", "type": "string", "description": ""},
        {"name": "2nd price", "type": "weird", "description": ""},
    ])
    plan = infer_plan("x", FakeProvider(plan=payload))
    assert plan.field_names == ["product_name", "f_2nd_price"]
    assert plan.fields[1].type == "string"  # unknown type falls back to string


@pytest.mark.parametrize(
    "prompt",
    ["every book and its price", "all products", "list the job titles", "top 10 stories"],
)
def test_plural_wording_forces_multiple(prompt):
    plan = infer_plan(prompt, FakeProvider(plan=dict(PLAN_PAYLOAD, multiple=False)))
    assert plan.multiple is True


@pytest.mark.parametrize("prompt", ["the article title and author", "this page's headline"])
def test_singular_wording_is_left_alone(prompt):
    plan = infer_plan(prompt, FakeProvider(plan=dict(PLAN_PAYLOAD, multiple=False)))
    assert plan.multiple is False


def test_infer_plan_deduplicates_fields():
    payload = dict(PLAN_PAYLOAD, fields=[
        {"name": "name", "type": "string", "description": "a"},
        {"name": "Name", "type": "string", "description": "b"},
    ])
    assert infer_plan("x", FakeProvider(plan=payload)).field_names == ["name"]


def test_infer_plan_falls_back_when_the_model_fails():
    plan = infer_plan("just get the headlines", FakeProvider(plan=None))
    assert plan.field_names == ["value"]
    assert "headlines" in plan.fields[0].description


def test_infer_plan_falls_back_on_garbage_payload():
    assert infer_plan("x", FakeProvider(plan={"nonsense": True})).field_names == ["value"]


def test_backend_failures_are_not_swallowed():
    """A dead key must surface here, not as a misleading fallback schema."""

    class DeadProvider(FakeProvider):
        def complete_json(self, system, user, schema=None, schema_name="result"):
            raise ProviderError("OpenAI rejected the API key (401).")

    with pytest.raises(ProviderError):
        infer_plan("anything", DeadProvider())


def test_plan_from_fields_skips_inference():
    plan = plan_from_fields(["Title", "unit price", "Title"])
    assert plan.field_names == ["title", "unit_price"]


def test_json_schema_is_valid_for_strict_mode():
    plan = infer_plan("x", FakeProvider(plan=PLAN_PAYLOAD))
    schema = plan_to_json_schema(plan)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]

    record = schema["properties"]["items"]["items"]
    assert record["additionalProperties"] is False
    # Strict mode requires every property to be listed as required...
    assert set(record["required"]) == set(record["properties"])
    # ...so absent values are expressed as null instead of a missing key.
    assert record["properties"]["price"]["type"] == ["number", "null"]


def test_schema_survives_a_plan_with_no_description():
    plan = plan_from_fields(["a"])
    plan.fields[0].description = ""
    schema = plan_to_json_schema(plan)
    assert schema["properties"]["items"]["items"]["properties"]["a"]["description"]

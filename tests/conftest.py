"""Shared fixtures. Every test here runs offline — no network, no API keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scraper_agent.providers.base import LLMProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def shop_html() -> str:
    return (FIXTURES / "shop.html").read_text(encoding="utf-8")


@pytest.fixture
def spa_html() -> str:
    return (FIXTURES / "spa_shell.html").read_text(encoding="utf-8")


class FakeProvider(LLMProvider):
    """Scripted provider: returns canned JSON and records what it was asked."""

    name = "fake"

    def __init__(self, plan: dict[str, Any] | None = None, batches: list[Any] | None = None):
        super().__init__(model="fake-1")
        self.plan = plan
        self.batches = list(batches or [])
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system, user, schema=None, schema_name="result"):
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "schema_name": schema_name}
        )
        self.usage.add(100, 20)

        if schema_name == "extraction_plan":
            if self.plan is None:
                raise RuntimeError("no plan scripted")
            return self.plan
        return self.batches.pop(0) if self.batches else {"items": []}


@pytest.fixture
def fake_provider() -> type[FakeProvider]:
    return FakeProvider

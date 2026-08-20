"""Provider interface shared by the OpenAI and Ollama backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """Raised when an LLM call cannot be completed."""


@dataclass
class Usage:
    """Token accounting across a run, so cost is visible rather than a surprise."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion


# Approximate USD per 1M tokens (input, output). Vendor pricing changes, so treat
# these as an estimate for orientation, not a bill.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """Rough spend for a run, or None when the model's pricing is unknown."""
    for name, (inp, out) in PRICES.items():
        if model.startswith(name):
            return (usage.prompt_tokens * inp + usage.completion_tokens * out) / 1_000_000
    return None


class LLMProvider(ABC):
    """Something that can answer a prompt with JSON."""

    #: Short identifier used in output metadata and CLI messages.
    name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model
        self.usage = Usage()

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> Any:
        """Return parsed JSON for the prompt, conforming to `schema` if given."""

    @property
    def cost_usd(self) -> float | None:
        return estimate_cost_usd(self.model, self.usage)

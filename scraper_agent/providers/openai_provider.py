"""OpenAI backend.

Structured output support differs by model, so the call degrades gracefully:
strict json_schema first, then plain json_object, then an unconstrained call
with the schema described in the prompt. Whatever comes back is parsed loosely.
"""

from __future__ import annotations

import os
from typing import Any

from scraper_agent.parsing import parse_json_loose
from scraper_agent.providers.base import LLMProvider, ProviderError


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        super().__init__(model)
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise ProviderError(
                "OPENAI_API_KEY is not set. Put it in .env (see .env.example), "
                "export it, or run with --provider ollama to use a local model."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError("The `openai` package is not installed.") from exc

        self._client = OpenAI(api_key=key)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> Any:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        attempts: list[dict[str, Any]] = []
        if schema is not None:
            attempts.append(
                {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "schema": schema,
                            "strict": True,
                        },
                    }
                }
            )
        attempts.append({"response_format": {"type": "json_object"}})
        attempts.append({})

        last_error: Exception | None = None
        for extra in attempts:
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **extra,
                )
            except Exception as exc:  # SDK raises typed errors; any is fatal here
                last_error = exc
                if _is_fatal(exc):
                    raise ProviderError(_explain(exc, self.model)) from exc
                continue  # unsupported response_format: try the next rung down

            usage = getattr(response, "usage", None)
            if usage is not None:
                self.usage.add(
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                )

            content = response.choices[0].message.content
            try:
                return parse_json_loose(content)
            except ValueError as exc:
                last_error = exc
                continue

        raise ProviderError(_explain(last_error, self.model))


def _is_fatal(exc: Exception) -> bool:
    """Auth, quota and missing-model errors will not improve on retry."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403, 404, 429):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "invalid_api_key",
            "incorrect api key",
            "insufficient_quota",
            "exceeded your current quota",
            "billing",
        )
    )


def _explain(exc: Exception | None, model: str) -> str:
    """Turn SDK errors into something actionable."""
    text = str(exc) if exc else "unknown error"
    lowered = text.lower()

    if "invalid_api_key" in lowered or "incorrect api key" in lowered:
        return (
            "OpenAI rejected the API key (401). Check OPENAI_API_KEY in .env. "
            "keys are revoked when leaked or rotated. "
            "Free alternative: --provider ollama runs a local model at no cost."
        )
    if "insufficient_quota" in lowered or "exceeded your current quota" in lowered:
        return (
            "The OpenAI key has no credit left. The API is pay-as-you-go and is "
            "separate from a ChatGPT subscription; add credit at "
            "platform.openai.com/settings/organization/billing. "
            "Free alternative: --provider ollama runs a local model at no cost."
        )
    if "does not exist" in lowered or "model_not_found" in lowered:
        return f"Model {model!r} is not available to this key. Try --model gpt-4o-mini."
    if "rate limit" in lowered or "429" in lowered:
        return f"OpenAI rate limit hit for {model!r}. Wait a moment and retry."
    return f"OpenAI request failed: {text}"

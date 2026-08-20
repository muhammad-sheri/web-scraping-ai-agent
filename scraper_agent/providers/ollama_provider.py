"""Local Ollama backend, with no API key and no per-token cost.

Ollama serves an OpenAI-ish chat endpoint on localhost. Passing a JSON schema
as `format` constrains decoding to valid JSON (Ollama >= 0.5); older builds
accept the string "json", which is looser but still parseable.
"""

from __future__ import annotations

from typing import Any

import httpx

from scraper_agent.parsing import parse_json_loose
from scraper_agent.providers.base import LLMProvider, ProviderError


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        host: str = "http://localhost:11434",
        timeout: float = 300.0,
    ) -> None:
        super().__init__(model)
        self.host = host.rstrip("/")
        self.timeout = timeout

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.host}/api/chat", json=payload, timeout=self.timeout
            )
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Cannot reach Ollama at {self.host}. Start it with `ollama serve`, "
                f"then pull a model: `ollama pull {self.model}`."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        if response.status_code == 404:
            raise ProviderError(
                f"Ollama has no model named {self.model!r}. "
                f"Pull it first: `ollama pull {self.model}`."
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> Any:
        base_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0},
        }

        formats: list[Any] = [schema, "json"] if schema is not None else ["json"]
        last_error: Exception | None = None

        for fmt in formats:
            payload = dict(base_payload, format=fmt)
            try:
                data = self._post(payload)
            except ProviderError as exc:
                last_error = exc
                if "no model named" in str(exc) or "Cannot reach Ollama" in str(exc):
                    raise
                continue

            self.usage.add(
                int(data.get("prompt_eval_count") or 0),
                int(data.get("eval_count") or 0),
            )
            content = (data.get("message") or {}).get("content", "")
            try:
                return parse_json_loose(content)
            except ValueError as exc:
                last_error = exc
                continue

        raise ProviderError(
            f"Ollama model {self.model!r} did not return usable JSON. "
            f"Smaller models struggle with structured extraction, so try a larger "
            f"one (e.g. `ollama pull qwen2.5:7b`). Last error: {last_error}"
        )

    def available_models(self) -> list[str]:
        """Model names currently pulled locally (empty if the server is down)."""
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=10)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return [m.get("name", "") for m in response.json().get("models", [])]

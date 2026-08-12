"""Runtime settings, resolved from environment variables with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 web-scraping-ai-agent/0.1"
)


@dataclass(frozen=True)
class Settings:
    """Everything tunable in one place."""

    provider: str = "openai"
    openai_model: str = "gpt-4o-mini"
    ollama_model: str = "llama3.2"
    ollama_host: str = "http://localhost:11434"

    request_timeout: float = 30.0
    render_timeout: float = 45.0
    user_agent: str = DEFAULT_USER_AGENT

    # Characters of cleaned markdown sent to the model per call. Roughly
    # 4 chars per token, so 12k chars is ~3k tokens of page content.
    max_chunk_chars: int = 12_000
    chunk_overlap_chars: int = 400
    # Hard ceiling on chunks per page, so one enormous page cannot quietly
    # turn into a hundred billable calls.
    max_chunks: int = 12

    respect_robots: bool = True
    politeness_delay: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        def _f(name: str, default: float) -> float:
            raw = os.getenv(name)
            return float(raw) if raw else default

        def _i(name: str, default: int) -> int:
            raw = os.getenv(name)
            return int(raw) if raw else default

        return cls(
            provider=os.getenv("SCRAPER_PROVIDER", cls.provider),
            openai_model=os.getenv("OPENAI_MODEL", cls.openai_model),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            ollama_host=os.getenv("OLLAMA_HOST", cls.ollama_host),
            request_timeout=_f("REQUEST_TIMEOUT", cls.request_timeout),
            render_timeout=_f("RENDER_TIMEOUT", cls.render_timeout),
            user_agent=os.getenv("USER_AGENT", cls.user_agent),
            max_chunk_chars=_i("MAX_CHUNK_CHARS", cls.max_chunk_chars),
            chunk_overlap_chars=_i("CHUNK_OVERLAP_CHARS", cls.chunk_overlap_chars),
            max_chunks=_i("MAX_CHUNKS", cls.max_chunks),
            respect_robots=os.getenv("RESPECT_ROBOTS", "true").lower() != "false",
            politeness_delay=_f("POLITENESS_DELAY", cls.politeness_delay),
        )

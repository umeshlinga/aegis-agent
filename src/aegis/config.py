"""Settings from environment (.env supported). Nothing secret is ever logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .llm.base import Route

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _env(k: str, d: str | None = None) -> str | None:
    v = os.environ.get(k)
    return v if v not in (None, "") else d


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: _env("AEGIS_DB", "data/aegis.db"))
    anthropic_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    # OpenAI model names change often; set the ones your account has.
    openai_fast: str | None = field(default_factory=lambda: _env("OPENAI_MODEL_FAST"))
    openai_balanced: str | None = field(default_factory=lambda: _env("OPENAI_MODEL_BALANCED"))
    openai_strong: str | None = field(default_factory=lambda: _env("OPENAI_MODEL_STRONG"))
    local_base_url: str | None = field(default_factory=lambda: _env("LOCAL_LLM_BASE_URL"))  # e.g. http://localhost:11434/v1
    local_model: str | None = field(default_factory=lambda: _env("LOCAL_LLM_MODEL"))  # e.g. qwen3:8b or llama3.1:8b
    claude_fast: str = field(default_factory=lambda: _env("CLAUDE_MODEL_FAST", "claude-haiku-5-5"))
    claude_balanced: str = field(default_factory=lambda: _env("CLAUDE_MODEL_BALANCED", "claude-sonnet-5-5"))
    claude_strong: str = field(default_factory=lambda: _env("CLAUDE_MODEL_STRONG", "claude-opus-5-5"))
    pricing_json: str | None = field(default_factory=lambda: _env("AEGIS_PRICING_JSON"))
    ncbi_key: str | None = field(default_factory=lambda: _env("NCBI_API_KEY"))
    ncbi_email: str | None = field(default_factory=lambda: _env("NCBI_EMAIL"))
    chrome_path: str | None = field(default_factory=lambda: _env("AEGIS_CHROME_PATH"))
    headless: bool = field(default_factory=lambda: _env("AEGIS_HEADLESS", "1") == "1")
    embedder: str = field(default_factory=lambda: _env("AEGIS_EMBEDDER", "auto"))
    reranker: str = field(default_factory=lambda: _env("AEGIS_RERANKER", "auto"))
    max_cost_usd: float = field(default_factory=lambda: float(_env("AEGIS_MAX_COST_USD", "1.50")))
    otlp_endpoint: str | None = field(default_factory=lambda: _env("AEGIS_OTLP_ENDPOINT"))

    def tiers(self) -> dict[str, list[Route]]:
        """Ordered fallback chains. Cross-family judges keep the verifier
        from grading its own model's homework."""
        t: dict[str, list[Route]] = {k: [] for k in ("fast", "balanced", "strong", "judge_a", "judge_b")}
        if self.anthropic_key:
            t["fast"].append(Route("anthropic", self.claude_fast))
            t["balanced"].append(Route("anthropic", self.claude_balanced))
            t["strong"].append(Route("anthropic", self.claude_strong))
            t["judge_a"].append(Route("anthropic", self.claude_balanced))
        if self.local_base_url and self.local_model:
            t["fast"].append(Route("local", self.local_model))
            t["judge_b"].append(Route("local", self.local_model))
        if self.openai_key:
            if self.openai_fast:
                t["fast"].append(Route("openai", self.openai_fast))
            if self.openai_balanced:
                t["balanced"].append(Route("openai", self.openai_balanced))
                t["judge_b"].insert(0, Route("openai", self.openai_balanced))
            if self.openai_strong:
                t["strong"].append(Route("openai", self.openai_strong))
        if not t["judge_a"]:
            t["judge_a"] = list(t["balanced"])
        if not t["judge_b"]:
            t["judge_b"] = list(t["judge_a"])
        return t

    def has_llm(self) -> bool:
        return bool(self.anthropic_key or self.openai_key or (self.local_base_url and self.local_model))

    def providers(self) -> dict:
        from .llm.providers import AnthropicProvider, OpenAICompatibleProvider

        p: dict = {}
        if self.anthropic_key:
            p["anthropic"] = AnthropicProvider(self.anthropic_key)
        if self.openai_key:
            p["openai"] = OpenAICompatibleProvider("openai", self.openai_key)
        if self.local_base_url and self.local_model:
            p["local"] = OpenAICompatibleProvider("local", "local", base_url=self.local_base_url,
                                                  token_param="max_tokens", send_temperature=True)
        return p

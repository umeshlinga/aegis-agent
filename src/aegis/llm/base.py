"""Provider-neutral request/response types."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass
class LLMRequest:
    system: str
    messages: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    tier: str = "balanced"  # fast | balanced | strong | judge_a | judge_b
    purpose: str = "general"  # used in traces, routing reports and offline handlers
    max_tokens: int = 1500
    temperature: float | None = 0.0
    sample: int = 0  # distinguishes self-consistency samples in cache/replay keys
    images: list[str] = field(default_factory=list)  # base64 PNGs attached to the last user turn

    def image_digest(self) -> str:
        return hashlib.sha256("".join(self.images).encode()).hexdigest()[:16] if self.images else ""

    def cache_key(self, provider: str, model: str) -> str:
        blob = json.dumps(
            {"p": provider, "m": model, "s": self.system, "msg": self.messages, "mt": self.max_tokens,
             "t": self.temperature, "k": self.sample, "img": self.image_digest()},
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def approx_input_tokens(self) -> int:
        return (len(self.system) + sum(len(m["content"]) for m in self.messages)) // 4 + 8


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False
    stop_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Provider(Protocol):
    name: str

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse: ...


@dataclass
class Route:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class Pricing:
    """USD per million tokens."""
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, in_tok: int, out_tok: int) -> float:
        return in_tok / 1e6 * self.input_per_mtok + out_tok / 1e6 * self.output_per_mtok


# Anthropic list prices, checked Nov 2026 (platform.claude.com pricing page and
# launch coverage). Haiku 5.5 rate applies to prompts up to 100K tokens.
# Verify before quoting cost numbers; override with AEGIS_PRICING_JSON.
DEFAULT_PRICING: dict[str, Pricing] = {
    "claude-opus-5-5": Pricing(4.00, 20.00),
    "claude-sonnet-5-5": Pricing(2.00, 10.00),
    "claude-haiku-5-5": Pricing(0.10, 0.50),
}


@dataclass
class PricingTable:
    table: dict[str, Pricing] = field(default_factory=lambda: dict(DEFAULT_PRICING))

    def cost(self, provider: str, model: str, in_tok: int, out_tok: int) -> tuple[float, bool]:
        """Returns (cost, priced). Local models are free; unknown models are flagged."""
        if provider == "local":
            return 0.0, True
        p = self.table.get(model)
        if p is None:
            return 0.0, False
        return p.cost(in_tok, out_tok), True

    @classmethod
    def from_json(cls, s: str | None) -> "PricingTable":
        t = cls()
        if s:
            for model, v in json.loads(s).items():
                t.table[model] = Pricing(float(v[0]), float(v[1]))
        return t

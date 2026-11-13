"""Model adapter interface.

Every provider (Anthropic, OpenAI, a local or open-weight model served
behind an HTTP endpoint) is wrapped in an adapter with this one shape, so
the router and the agent core never see provider-specific code. Phase 1
ships the scripted fake adapter; Phase 2 adds the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(Exception):
    """A model call failed. The router treats this as a fallback signal."""


@dataclass
class ModelRequest:
    step_kind: str  # plan | act | verify | diagnose
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0


class ModelAdapter(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...

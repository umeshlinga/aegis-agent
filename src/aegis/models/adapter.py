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


class MissingAPIKeyError(ModelError):
    """No API key is configured for this provider.

    Raised before any network call is attempted. The router treats it like
    any other ModelError (fall back to the next adapter in the chain), so a
    deployment with only one provider keyed still routes cleanly.
    """

    def __init__(self, provider: str, env_var: str):
        super().__init__(
            f"no API key configured for provider '{provider}' "
            f"(set the {env_var} environment variable)"
        )
        self.provider = provider
        self.env_var = env_var


class ProviderError(ModelError):
    """The provider was reached (or attempted) and the call failed: an HTTP
    error status, a malformed response, or a connection failure."""

    def __init__(self, provider: str, detail: str):
        super().__init__(f"provider '{provider}' call failed: {detail}")
        self.provider = provider
        self.detail = detail


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
    # True when cost_usd came from the local price table rather than from a
    # provider-reported amount. Providers do not report cost, so every real
    # adapter sets this; traces carry the flag so an estimate is never
    # mistaken for a billed figure.
    cost_is_estimate: bool = False


class ModelAdapter(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...

"""Real provider adapters: Anthropic, OpenAI, and a local/open-weight
HTTP endpoint (Ollama-style).

Design rules, enforced here:

- Keys come only from environment variables (optionally via a .env file in
  the working directory). Nothing is hardcoded, and no key value is ever
  printed, logged, or put in a trace.
- With no key set, an adapter raises MissingAPIKeyError before any network
  call. The router treats that as a fallback signal, so a partially keyed
  deployment still routes.
- Each adapter reports the provider's token counts and measures its own
  latency, and computes cost from the local price table, flagged as an
  estimate (providers report tokens, not dollars).
- The HTTP transport is injectable. Production uses urllib from the
  standard library, so the adapters add no third-party dependency; tests
  inject a stub transport and never touch the network.

No live-API verification is claimed anywhere in this project for these
adapters: this environment has no provider keys, so the adapters are
verified against stub transports and by their clean failure without keys.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from .adapter import (
    MissingAPIKeyError,
    ModelRequest,
    ModelResponse,
    ProviderError,
)
from .pricing import estimate_cost_usd

# A transport takes (url, headers, json_body) and returns the parsed JSON
# response body. It raises ProviderError on any failure.
Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


def urllib_transport(provider: str) -> Transport:
    def _send(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(provider, f"HTTP {exc.code} from provider") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError(provider, f"connection failed: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(provider, f"unparseable response: {exc}") from exc

    return _send


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Only sets variables that are not already set in the environment, so a
    real environment always wins over the file. Values are never printed.
    A missing file is not an error.
    """
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class _HTTPAdapter:
    """Shared plumbing: key check, timing, cost estimate, response shape."""

    provider = "http"
    env_var = ""
    default_model = ""

    def __init__(
        self,
        model: Optional[str] = None,
        transport: Optional[Transport] = None,
        api_key: Optional[str] = None,
    ):
        self.model = model or self.default_model
        self._transport = transport or urllib_transport(self.provider)
        # An explicit key is accepted for tests and embeddings; production
        # callers pass nothing and the key comes from the environment.
        self._api_key = api_key

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    def _key(self) -> str:
        key = self._api_key or os.environ.get(self.env_var, "")
        if not key:
            raise MissingAPIKeyError(self.provider, self.env_var)
        return key

    def _respond(self, text: str, input_tokens: int, output_tokens: int,
                 start: float) -> ModelResponse:
        return ModelResponse(
            text=text,
            model=self.name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            cost_usd=estimate_cost_usd(self.model, input_tokens, output_tokens),
            cost_is_estimate=True,
        )


class AnthropicAdapter(_HTTPAdapter):
    """Anthropic Messages API (POST /v1/messages)."""

    provider = "anthropic"
    env_var = "ANTHROPIC_API_KEY"
    default_model = "claude-sonnet-4-5"
    _URL = "https://api.anthropic.com/v1/messages"

    def complete(self, request: ModelRequest) -> ModelResponse:
        key = self._key()
        start = time.perf_counter()
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        data = self._transport(self._URL, headers, body)
        try:
            text = "".join(
                block.get("text", "")
                for block in data["content"]
                if block.get("type") == "text"
            )
            usage = data["usage"]
            return self._respond(
                text, usage["input_tokens"], usage["output_tokens"], start
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(self.provider, f"malformed response: {exc}") from exc


class OpenAIAdapter(_HTTPAdapter):
    """OpenAI Chat Completions API (POST /v1/chat/completions)."""

    provider = "openai"
    env_var = "OPENAI_API_KEY"
    default_model = "gpt-5-mini"
    _URL = "https://api.openai.com/v1/chat/completions"

    def complete(self, request: ModelRequest) -> ModelResponse:
        key = self._key()
        start = time.perf_counter()
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        headers = {"Authorization": f"Bearer {key}"}
        data = self._transport(self._URL, headers, body)
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data["usage"]
            return self._respond(
                text, usage["prompt_tokens"], usage["completion_tokens"], start
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(self.provider, f"malformed response: {exc}") from exc


class LocalAdapter(_HTTPAdapter):
    """A local or open-weight model behind an Ollama-style HTTP endpoint.

    Base URL from AEGIS_LOCAL_BASE_URL (default http://localhost:11434),
    model from AEGIS_LOCAL_MODEL (default llama3.1). No API key is needed
    for a local endpoint; if AEGIS_LOCAL_API_KEY is set (some hosted
    gateways want one), it is sent as a bearer token. An unreachable
    endpoint raises ProviderError, which the router treats as a fallback
    signal, exactly like a cloud provider being down.
    """

    provider = "local"
    env_var = "AEGIS_LOCAL_API_KEY"  # optional; absence is not an error
    default_model = "llama3.1"

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        transport: Optional[Transport] = None,
        api_key: Optional[str] = None,
    ):
        super().__init__(
            model=model or os.environ.get("AEGIS_LOCAL_MODEL") or self.default_model,
            transport=transport,
            api_key=api_key,
        )
        self.base_url = (
            base_url or os.environ.get("AEGIS_LOCAL_BASE_URL") or "http://localhost:11434"
        ).rstrip("/")

    def complete(self, request: ModelRequest) -> ModelResponse:
        start = time.perf_counter()
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": False,
        }
        headers: dict[str, str] = {}
        key = self._api_key or os.environ.get(self.env_var, "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = self._transport(f"{self.base_url}/api/chat", headers, body)
        try:
            text = data["message"]["content"]
            return self._respond(
                text,
                int(data.get("prompt_eval_count", 0)),
                int(data.get("eval_count", 0)),
                start,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(self.provider, f"malformed response: {exc}") from exc


def adapters_from_env(load_env_file: bool = True) -> dict[str, object]:
    """Build one adapter per provider, keyed by provider name.

    Adapters are constructed regardless of whether their key is set: the
    key check happens per call and raises MissingAPIKeyError, which the
    router handles as a fallback signal. Nothing about key values is
    exposed here; configured_providers() reports booleans only.
    """
    if load_env_file:
        load_dotenv()
    return {
        "anthropic": AnthropicAdapter(),
        "openai": OpenAIAdapter(),
        "local": LocalAdapter(),
    }


def configured_providers() -> dict[str, bool]:
    """Which providers are configured, as booleans only, never values.
    The local provider needs no key, so it always reports True; whether
    its endpoint is reachable is a call-time fact (ProviderError), not a
    configuration fact."""
    load_dotenv()
    return {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "local": True,
    }

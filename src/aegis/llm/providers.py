"""Concrete providers: Anthropic, OpenAI, any OpenAI-compatible local server
(Ollama, vLLM, llama.cpp), plus record/replay and an offline stand-in.

Every provider maps its SDK's exceptions onto Aegis error classes, so the
router's retry and fallback logic is the same no matter who failed.
SDK-level retries are disabled (max_retries=0): retries happen once, in
the router, where they are traced and budgeted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from ..core.errors import (
    MalformedResponse,
    NotFound,
    PermanentError,
    RateLimited,
    ToolTimeout,
    UpstreamServerError,
)
from .base import LLMRequest, LLMResponse


def _retry_after(exc) -> float | None:
    try:
        v = exc.response.headers.get("retry-after")
        return float(v) if v else None
    except Exception:
        return None


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, timeout_s: float = 120.0):
        import anthropic

        self._sdk = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=timeout_s)

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse:
        a = self._sdk
        messages = req.messages
        if req.images:
            last = messages[-1]
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b}}
                      for b in req.images] + [{"type": "text", "text": last["content"]}]
            messages = messages[:-1] + [{"role": last["role"], "content": blocks}]
        kwargs = dict(model=model, system=req.system, messages=messages, max_tokens=req.max_tokens)
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        t0 = time.perf_counter()
        try:
            resp = await self.client.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise RateLimited(str(e), _retry_after(e)) from e
        except a.APITimeoutError as e:
            raise ToolTimeout(f"anthropic timeout: {e}") from e
        except a.APIConnectionError as e:
            raise UpstreamServerError(f"anthropic connection: {e}") from e
        except a.APIStatusError as e:
            if e.status_code >= 500 or e.status_code == 529:
                raise UpstreamServerError(f"anthropic {e.status_code}: {e}") from e
            raise PermanentError(f"anthropic {e.status_code}: {e}") from e
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return LLMResponse(
            text=text, provider=self.name, model=model,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            latency_ms=(time.perf_counter() - t0) * 1000, stop_reason=resp.stop_reason,
        )


class OpenAICompatibleProvider:
    """OpenAI itself, or a local server speaking the same API."""

    def __init__(self, name: str, api_key: str, base_url: str | None = None, timeout_s: float = 120.0,
                 token_param: str = "max_completion_tokens", send_temperature: bool = False):
        import openai

        self._sdk = openai
        self.name = name
        self.token_param = token_param
        self.send_temperature = send_temperature
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout_s)

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse:
        o = self._sdk
        msgs = [{"role": "system", "content": req.system}] + req.messages
        if req.images:
            last = msgs[-1]
            msgs = msgs[:-1] + [{"role": last["role"], "content": [
                {"type": "text", "text": last["content"]},
                *[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in req.images],
            ]}]
        kwargs = {"model": model, "messages": msgs, self.token_param: req.max_tokens}
        if self.send_temperature and req.temperature is not None:
            kwargs["temperature"] = req.temperature
        t0 = time.perf_counter()
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except o.RateLimitError as e:
            raise RateLimited(str(e), _retry_after(e)) from e
        except o.APITimeoutError as e:
            raise ToolTimeout(f"{self.name} timeout: {e}") from e
        except o.APIConnectionError as e:
            raise UpstreamServerError(f"{self.name} connection: {e}") from e
        except o.APIStatusError as e:
            if e.status_code >= 500:
                raise UpstreamServerError(f"{self.name} {e.status_code}: {e}") from e
            raise PermanentError(f"{self.name} {e.status_code}: {e}") from e
        if not resp.choices:
            raise MalformedResponse(f"{self.name}: no choices")
        u = resp.usage
        return LLMResponse(
            text=resp.choices[0].message.content or "", provider=self.name, model=model,
            input_tokens=getattr(u, "prompt_tokens", 0) or 0, output_tokens=getattr(u, "completion_tokens", 0) or 0,
            latency_ms=(time.perf_counter() - t0) * 1000, stop_reason=resp.choices[0].finish_reason,
        )


class RecordingProvider:
    """Wraps a real provider and appends every response to a JSONL file.

    Record once against live models, then replay for free, deterministic
    eval reruns and CI. Same idea as VCR cassettes for HTTP.
    """

    def __init__(self, inner, path: str | Path):
        self.inner = inner
        self.name = inner.name
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse:
        resp = await self.inner.complete(model, req)
        with self.path.open("a") as f:
            f.write(json.dumps({"key": req.cache_key(self.name, model), "resp": resp.to_dict()}) + "\n")
        return resp


class ReplayProvider:
    def __init__(self, name: str, path: str | Path):
        self.name = name
        self.data: dict[str, dict] = {}
        p = Path(path)
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.data[row["key"]] = row["resp"]

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse:
        key = req.cache_key(self.name, model)
        if key not in self.data:
            raise NotFound(f"no recorded response for {req.purpose} on {model}")
        return LLMResponse(**{**self.data[key], "cached": True})


class OfflineProvider:
    """Deterministic stand-in used ONLY for offline simulation and tests.

    Each workload registers handlers keyed by request purpose that produce
    a plausible structured answer from the prompt contents with plain code.
    Results produced with this provider measure the runtime (recovery,
    verification, routing), never model quality. Reports label them so.
    """

    name = "offline"

    def __init__(self, handlers: dict[str, Callable[[LLMRequest], str]] | None = None):
        self.handlers = dict(handlers or {})
        self.calls = 0

    def register(self, purpose: str, fn: Callable[[LLMRequest], str]) -> None:
        self.handlers[purpose] = fn

    async def complete(self, model: str, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        fn = self.handlers.get(req.purpose) or self.handlers.get("*")
        if fn is None:
            raise PermanentError(f"offline provider has no handler for purpose '{req.purpose}'")
        text = fn(req)
        return LLMResponse(text=text, provider=self.name, model=model,
                           input_tokens=req.approx_input_tokens(), output_tokens=len(text) // 4)

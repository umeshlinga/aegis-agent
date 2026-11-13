"""Scripted fake model adapter.

Deterministic and key-free: this is what local runs, tests, and the eval
harness use when no provider key is configured, and what CI always uses.
Responses are supplied per step kind, either as fixed text or as a callable
that sees the request. Token counts are estimated from text length so the
router's cost and latency logging path is exercised with plausible numbers;
the fake model's price is zero, and the trace says so.
"""

from __future__ import annotations

import time
from typing import Callable, Union

from .adapter import ModelRequest, ModelResponse

Responder = Union[str, Callable[[ModelRequest], str]]


class ScriptedFakeModel:
    name = "fake-scripted"

    def __init__(self, responses: dict[str, Responder]):
        self._responses = responses
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        start = time.perf_counter()
        responder = self._responses.get(request.step_kind)
        if responder is None:
            text = ""
        elif callable(responder):
            text = responder(request)
        else:
            text = responder
        latency = (time.perf_counter() - start) * 1000.0
        return ModelResponse(
            text=text,
            model=self.name,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=latency,
            cost_usd=0.0,
        )

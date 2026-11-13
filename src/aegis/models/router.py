"""Multi-LLM router.

The router picks a model per step kind (planning, acting, verifying,
diagnosing are different jobs and do not need the same model), walks a
fallback chain when a provider errors, and logs every call to the trace
with its token counts, latency, and cost. The agent core asks the router
for a completion; it never names a provider itself.
"""

from __future__ import annotations

from typing import Optional

from ..trace.store import TraceWriter
from .adapter import ModelAdapter, ModelError, ModelRequest, ModelResponse


class ModelRouter:
    def __init__(
        self,
        adapters: dict[str, ModelAdapter],
        routes: dict[str, list[str]],
        trace: Optional[TraceWriter] = None,
        default_route: Optional[list[str]] = None,
    ):
        self._adapters = adapters
        self._routes = routes
        self._trace = trace
        self._default = default_route or (list(adapters)[:1])

    def complete(self, run_id: str, request: ModelRequest) -> ModelResponse:
        chain = self._routes.get(request.step_kind, self._default)
        last_error: Optional[Exception] = None
        for position, name in enumerate(chain):
            adapter = self._adapters[name]
            try:
                response = adapter.complete(request)
            except ModelError as exc:
                last_error = exc
                if self._trace is not None:
                    self._trace.emit(
                        run_id,
                        "model_call_failed",
                        step_kind=request.step_kind,
                        model=name,
                        fallback_position=position,
                        error=str(exc),
                    )
                continue
            if self._trace is not None:
                self._trace.emit(
                    run_id,
                    "model_call",
                    step_kind=request.step_kind,
                    model=response.model,
                    fallback_position=position,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=round(response.latency_ms, 3),
                    cost_usd=response.cost_usd,
                )
            return response
        raise ModelError(
            f"all models failed for step kind '{request.step_kind}': {last_error}"
        )

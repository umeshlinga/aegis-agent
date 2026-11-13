"""Model router: tiers, provider fallback, budget, cache, structured output,
self-consistency voting and cross-model judge panels.

Routing rules (all enforced here, none left to the model):

* Each tier is an ordered fallback chain across providers. A provider that
  keeps failing trips its circuit breaker and the next route is used.
* Transient errors are retried with backoff before falling back.
* Every call is checked against the run budget before it is made.
* Deterministic calls (temperature 0) are cached by exact request hash.
* Structured calls are validated with Pydantic; invalid output gets a
  repair turn with the validation error, then escalation to a stronger
  tier, then a hard SchemaViolation.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..core.errors import CircuitOpen, PermanentError, SchemaViolation, TransientError
from ..core.resilience import Budget, CircuitBreaker, RetryPolicy, retry_async
from .base import LLMRequest, LLMResponse, PricingTable, Route

M = TypeVar("M", bound=BaseModel)

ESCALATION = {"fast": "balanced", "balanced": "strong", "judge_a": "strong", "judge_b": "strong"}


@dataclass
class CallStats:
    calls: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    escalations: int = 0
    repairs: int = 0


class Router:
    def __init__(self, providers: dict[str, Any], tiers: dict[str, list[Route]], budget: Budget,
                 tracer=None, store=None, run_id: str = "", pricing: PricingTable | None = None,
                 retry: RetryPolicy | None = None, use_cache: bool = True, cache_scope: str | None = None):
        self.providers = providers
        self.tiers = {t: [r for r in rs if r.provider in providers] for t, rs in tiers.items()}
        self.budget = budget
        self.tracer = tracer
        self.store = store
        self.run_id = run_id
        self.pricing = pricing or PricingTable()
        self.retry = retry or RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=20.0)
        self.use_cache = use_cache
        # Default scope is the run: resume replays this run's own calls, and
        # separate runs never share answers (keeps eval cost numbers honest).
        self.cache_scope = run_id if cache_scope is None else cache_scope
        self.breakers = {name: CircuitBreaker(f"llm:{name}", failure_threshold=4, reset_timeout=60) for name in providers}
        self.stats = CallStats()

    def _event(self, kind: str, data: dict) -> None:
        if self.store is not None and self.run_id:
            self.store.add_event(self.run_id, kind, data)

    def routes(self, tier: str) -> list[Route]:
        chain = self.tiers.get(tier) or []
        if not chain:
            raise PermanentError(f"no configured provider for tier '{tier}'")
        return chain

    # ------------------------------------------------------------------ raw
    async def complete(self, req: LLMRequest) -> LLMResponse:
        last: BaseException | None = None
        for i, route in enumerate(self.routes(req.tier)):
            if i > 0:
                self.stats.fallbacks += 1
                self._event("provider_fallback", {"tier": req.tier, "to": str(route), "after": repr(last)[:200]})
            try:
                return await self._call_route(route, req)
            except (TransientError, CircuitOpen) as e:
                last = e
                continue
        assert last is not None
        raise last

    async def _call_route(self, route: Route, req: LLMRequest) -> LLMResponse:
        provider = self.providers[route.provider]
        breaker = self.breakers[route.provider]
        key = f"{self.cache_scope}:{req.cache_key(route.provider, route.model)}"
        # Every call is cached by exact request (sample index included), so a
        # resumed run replays the same model outputs instead of paying again
        # and drifting to different answers.
        cacheable = self.use_cache and self.store is not None
        if cacheable:
            hit = self.store.cache_get(key)
            if hit:
                self.stats.cache_hits += 1
                return LLMResponse(**{**hit, "cached": True, "cost_usd": 0.0})
        breaker.allow()
        est_cost, _ = self.pricing.cost(route.provider, route.model, req.approx_input_tokens(), req.max_tokens)
        self.budget.before_llm(est_cost * 0.5)

        async def once() -> LLMResponse:
            return await provider.complete(route.model, req)

        span_cm = self.tracer.span(f"llm:{req.purpose}", "llm", {"route": str(route), "tier": req.tier}) \
            if self.tracer else None
        try:
            if span_cm:
                async with span_cm as sp:
                    resp = await retry_async(once, self.retry)
                    cost, priced = self.pricing.cost(route.provider, route.model, resp.input_tokens, resp.output_tokens)
                    resp.cost_usd = cost
                    sp.attrs.update(model=route.model, provider=route.provider, in_tok=resp.input_tokens,
                                    out_tok=resp.output_tokens, cost_usd=round(cost, 6), priced=priced,
                                    latency_ms=round(resp.latency_ms, 1))
            else:
                resp = await retry_async(once, self.retry)
                resp.cost_usd, _ = self.pricing.cost(route.provider, route.model, resp.input_tokens, resp.output_tokens)
        except (TransientError, CircuitOpen):
            breaker.record_failure()
            raise
        breaker.record_success()
        self.stats.calls += 1
        self.budget.charge_llm(resp.cost_usd, resp.input_tokens, resp.output_tokens)
        if cacheable:
            self.store.cache_put(key, resp.to_dict())
        return resp

    # ----------------------------------------------------------- structured
    async def structured(self, req: LLMRequest, model_cls: type[M], max_repairs: int = 2,
                         escalate: bool = True) -> tuple[M, LLMResponse]:
        schema = json.dumps(model_cls.model_json_schema())
        sys = (req.system + "\n\nRespond with ONLY one JSON object that validates against this JSON Schema. "
               "No prose, no markdown fences.\n" + schema)
        messages = list(req.messages)
        tier = req.tier
        attempts = 0
        escalated = False
        while True:
            r = LLMRequest(system=sys, messages=messages, tier=tier, purpose=req.purpose,
                           max_tokens=req.max_tokens, temperature=req.temperature, sample=req.sample,
                           images=req.images if len(messages) == len(req.messages) else [])
            resp = await self.complete(r)
            try:
                return model_cls.model_validate(extract_json(resp.text)), resp
            except (ValueError, ValidationError) as e:
                attempts += 1
                err = str(e)[:800]
                if attempts <= max_repairs:
                    self.stats.repairs += 1
                    messages = messages + [
                        {"role": "assistant", "content": resp.text[:4000]},
                        {"role": "user", "content": f"That output failed validation:\n{err}\nReturn corrected JSON only."},
                    ]
                    continue
                if escalate and not escalated and tier in ESCALATION and ESCALATION[tier] in self.tiers \
                        and self.tiers[ESCALATION[tier]]:
                    self.stats.escalations += 1
                    self._event("model_escalated", {"purpose": req.purpose, "from": tier, "to": ESCALATION[tier]})
                    tier = ESCALATION[tier]
                    escalated = True
                    messages = list(req.messages)
                    attempts = 0
                    continue
                raise SchemaViolation(f"{req.purpose}: invalid output after repairs: {err}") from e

    # ----------------------------------------------------- self-consistency
    async def vote(self, req: LLMRequest, model_cls: type[M], k: int, key: Callable[[M], Any],
                   temperature: float = 0.7) -> tuple[M, float, list[M]]:
        """Sample k answers, return the majority one and the agreement rate.

        Low agreement is a signal, not noise: callers lower confidence or
        route to human review when agreement < threshold.
        """
        import asyncio

        async def one(i: int):
            r = LLMRequest(system=req.system, messages=req.messages, tier=req.tier, purpose=req.purpose,
                           max_tokens=req.max_tokens, temperature=temperature if k > 1 else req.temperature,
                           sample=i)
            obj, _ = await self.structured(r, model_cls)
            return obj

        results = await asyncio.gather(*(one(i) for i in range(k)), return_exceptions=True)
        ok = [r for r in results if not isinstance(r, BaseException)]
        if not ok:
            raise next(r for r in results if isinstance(r, BaseException))
        counts = Counter(key(o) for o in ok)
        best_key, n = counts.most_common(1)[0]
        winner = next(o for o in ok if key(o) == best_key)
        return winner, n / k, ok

    # ---------------------------------------------------------- judge panel
    async def panel(self, req: LLMRequest, model_cls: type[M], tiers: list[str],
                    key: Callable[[M], Any]) -> tuple[list[M], bool]:
        """Ask the same question to judges from different model families.
        Returns verdicts and whether they agree."""
        import asyncio

        avail = [t for t in tiers if self.tiers.get(t)]
        if not avail:
            avail = [req.tier]
        reqs = [LLMRequest(system=req.system, messages=req.messages, tier=t, purpose=req.purpose,
                           max_tokens=req.max_tokens, temperature=0.0) for t in avail]
        outs = await asyncio.gather(*(self.structured(r, model_cls) for r in reqs))
        verdicts = [o for o, _ in outs]
        return verdicts, len({key(v) for v in verdicts}) == 1


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of model text (tolerates fences/prose)."""
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in output")

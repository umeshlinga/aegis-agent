"""Retry/breaker/loop/budget primitives and the multi-model router."""
import pytest
from pydantic import BaseModel

from aegis.core.errors import (BudgetExceeded, CircuitOpen, LoopDetected, PermanentError, RateLimited,
                               SchemaViolation, UpstreamServerError)
from aegis.core.resilience import Budget, CircuitBreaker, LoopGuard, RetryPolicy, retry_async
from aegis.core.store import Store
from aegis.llm.base import LLMRequest, LLMResponse, Route
from aegis.llm.router import Router, extract_json


async def test_retry_honors_retry_after():
    waits = []
    n = {"i": 0}

    async def fn():
        n["i"] += 1
        if n["i"] == 1:
            raise RateLimited("429", retry_after=2.0)
        return "ok"

    async def fake_sleep(d):
        waits.append(d)

    assert await retry_async(fn, RetryPolicy(max_attempts=3), sleep=fake_sleep) == "ok"
    assert 1.9 * 0.01 <= waits[0] <= 2.2  # scaled by AEGIS_BACKOFF_SCALE in tests


def test_breaker_open_halfopen_close():
    t = {"now": 0.0}
    b = CircuitBreaker("x", failure_threshold=2, reset_timeout=10, clock=lambda: t["now"])
    b.record_failure(); b.record_failure()
    with pytest.raises(CircuitOpen) as e:
        b.allow()
    assert e.value.retry_after > 0
    t["now"] = 100
    b.allow()
    assert b.state == "half_open"
    b.record_success()
    assert b.state == "closed"


def test_loop_guard():
    g = LoopGuard(max_repeats=3)
    g.check("a"); g.check("b"); g.check("a")
    with pytest.raises(LoopDetected):
        g.check("a")


def test_budget_blocks_overspend():
    b = Budget(max_cost_usd=0.01)
    b.charge_llm(0.009, 10, 10)
    with pytest.raises(BudgetExceeded):
        b.before_llm(0.005)


class Scripted:
    def __init__(self, name, outputs):
        self.name = name
        self.outputs = list(outputs)
        self.calls = 0

    async def complete(self, model, req):
        self.calls += 1
        o = self.outputs.pop(0) if self.outputs else "{}"
        if isinstance(o, Exception):
            raise o
        return LLMResponse(text=o, provider=self.name, model=model, input_tokens=100, output_tokens=20)


class Ans(BaseModel):
    answer: int


def router(providers, tiers, store=None):
    return Router(providers, tiers, Budget(), store=store, run_id="r", retry=RetryPolicy(max_attempts=2, base_delay=0.01))


async def test_fallback_to_second_provider():
    a = Scripted("a", [UpstreamServerError("down"), UpstreamServerError("down")])
    b = Scripted("b", ['{"answer": 4}'])
    store = Store(":memory:")
    store.create_run("x", {}, "r")
    r = router({"a": a, "b": b}, {"fast": [Route("a", "m1"), Route("b", "m2")]}, store)
    obj, resp = await r.structured(LLMRequest("s", [{"role": "user", "content": "2+2"}], tier="fast"), Ans)
    assert obj.answer == 4 and resp.provider == "b"
    assert store.events("r", "provider_fallback")


async def test_repair_then_escalate_then_fail():
    fast = Scripted("f", ["not json", '{"answer": "x"}', "still bad"])
    strong = Scripted("s", ['{"answer": 7}'])
    r = router({"f": fast, "s": strong}, {"fast": [Route("f", "small")], "balanced": [Route("s", "big")]})
    obj, _ = await r.structured(LLMRequest("s", [{"role": "user", "content": "q"}], tier="fast"), Ans)
    assert obj.answer == 7 and r.stats.repairs == 2 and r.stats.escalations == 1

    bad = Scripted("f", ["x"] * 10)
    r2 = router({"f": bad}, {"fast": [Route("f", "small")]})
    with pytest.raises(SchemaViolation):
        await r2.structured(LLMRequest("s", [{"role": "user", "content": "q"}], tier="fast"), Ans)


async def test_cache_is_scoped_per_run():
    store = Store(":memory:")
    p = Scripted("a", ['{"answer": 1}', '{"answer": 2}'])
    req = LLMRequest("s", [{"role": "user", "content": "same"}], tier="fast")
    r1 = Router({"a": p}, {"fast": [Route("a", "m")]}, Budget(), store=store, run_id="run1")
    await r1.complete(req)
    await r1.complete(req)  # same run: cache hit
    r2 = Router({"a": p}, {"fast": [Route("a", "m")]}, Budget(), store=store, run_id="run2")
    await r2.complete(req)  # other run: no sharing
    assert p.calls == 2 and r1.stats.cache_hits == 1


async def test_self_consistency_vote():
    p = Scripted("a", ['{"answer": 3}', '{"answer": 3}', '{"answer": 5}'])
    r = router({"a": p}, {"fast": [Route("a", "m")]})
    win, agreement, _ = await r.vote(LLMRequest("s", [{"role": "user", "content": "q"}], tier="fast"), Ans, k=3,
                                     key=lambda o: o.answer)
    assert win.answer == 3 and abs(agreement - 2 / 3) < 1e-9


def test_extract_json_tolerates_fences_and_braces_in_strings():
    assert extract_json('Sure!\n```json\n{"a": "x}y", "b": {"c": 1}}\n```') == {"a": "x}y", "b": {"c": 1}}


async def test_permanent_provider_error_not_fallen_back():
    a = Scripted("a", [PermanentError("400 bad request")])
    b = Scripted("b", ['{"answer": 1}'])
    r = router({"a": a, "b": b}, {"fast": [Route("a", "m"), Route("b", "m")]})
    with pytest.raises(PermanentError):
        await r.complete(LLMRequest("s", [{"role": "user", "content": "q"}], tier="fast"))
    assert b.calls == 0

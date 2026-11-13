"""Engine guarantees: parallelism, retries, degradation, approvals, cancel, crash/resume, stale results, exactly-once."""
import asyncio

import pytest

from aegis.core.engine import Engine, Step, Workflow
from aegis.core.errors import ApprovalRequired, NotFound, SimulatedCrash, ToolTimeout
from aegis.core.faults import FaultInjector, FaultProfile
from aegis.core.resilience import RetryPolicy
from aegis.core.state import RunStatus
from aegis.core.store import Store


class Svc:
    def __init__(self, faults=None):
        self.faults = faults
        self.tracer = None


def engine_with(steps, result="end", faults=None, store=None):
    store = store or Store(":memory:")
    wf = lambda inp: Workflow("t", steps, result)
    return Engine(store, {"t": wf}, lambda rid, inp: Svc(faults)), store


def test_cycle_rejected():
    async def f(ctx):
        return 1

    with pytest.raises(ValueError):
        Workflow("x", [Step("a", f, ["b"]), Step("b", f, ["a"])], "a")


async def test_parallel_steps_run_concurrently():
    started = []

    async def slow(ctx):
        started.append(ctx.step_id)
        await asyncio.sleep(0.2)
        return ctx.step_id

    async def end(ctx):
        return sorted(k for k in ctx.outputs)

    eng, store = engine_with([Step("a", slow), Step("b", slow), Step("c", slow), Step("end", end, ["a", "b", "c"])])
    t0 = asyncio.get_running_loop().time()
    rid = await eng.start("t", {})
    assert asyncio.get_running_loop().time() - t0 < 0.5  # 3 x 0.2s ran in parallel
    assert store.get_run(rid)["status"] == "succeeded"


async def test_transient_failure_retried_then_succeeds():
    calls = {"n": 0}

    async def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ToolTimeout("boom")
        return "ok"

    eng, store = engine_with([Step("end", flaky, retry=RetryPolicy(max_attempts=3, base_delay=0.01))])
    rid = await eng.start("t", {})
    assert store.get_run(rid)["status"] == "succeeded"
    assert store.get_steps(rid)["end"]["attempts"] == 3


async def test_permanent_failure_not_retried():
    calls = {"n": 0}

    async def bad(ctx):
        calls["n"] += 1
        raise NotFound("gone")

    eng, store = engine_with([Step("end", bad, retry=RetryPolicy(max_attempts=5, base_delay=0.01))])
    rid = await eng.start("t", {})
    assert store.get_run(rid)["status"] == "failed" and calls["n"] == 1


async def test_optional_step_degrades_instead_of_failing():
    async def bad(ctx):
        raise NotFound("x")

    async def end(ctx):
        return {"opt": ctx.outputs.get("opt")}

    eng, store = engine_with([Step("opt", bad, optional=True), Step("end", end, ["opt"])])
    rid = await eng.start("t", {})
    run = store.get_run(rid)
    assert run["status"] == "succeeded" and run["result"]["degraded_steps"] == ["opt"]


async def test_condition_skips_step():
    async def plan(ctx):
        return {"lit": False}

    async def lit(ctx):
        raise AssertionError("should be skipped")

    async def end(ctx):
        return 1

    eng, store = engine_with([Step("plan", plan), Step("lit", lit, ["plan"], condition=lambda o: o["plan"]["lit"]),
                              Step("end", end, ["lit"])])
    rid = await eng.start("t", {})
    assert store.get_steps(rid)["lit"]["status"] == "skipped"


async def test_approval_parks_run_and_resume_continues():
    store = Store(":memory:")

    async def risky(ctx):
        rows = store.list_approvals(None, ctx.run_id)
        if not rows or rows[0]["status"] != "approved":
            row = store.get_or_create_approval(ctx.run_id, ctx.step_id, "send", {"x": 1}, "high", "test")
            raise ApprovalRequired(row["id"], "send", "test")
        return "sent"

    eng, _ = engine_with([Step("end", risky)], store=store)
    rid = await eng.start("t", {})
    assert store.get_run(rid)["status"] == "waiting_approval"
    store.decide_approval(store.list_approvals()[0]["id"], True)
    assert await eng.resume(rid) == RunStatus.SUCCEEDED


async def test_cancel_stops_inflight_steps():
    async def forever(ctx):
        await asyncio.sleep(10)

    eng, store = engine_with([Step("end", forever)])
    rid = store.create_run("t", {})
    task = asyncio.create_task(eng.execute(rid))
    await asyncio.sleep(0.1)
    eng.cancel(rid)
    assert await asyncio.wait_for(task, 2) == RunStatus.CANCELLED
    assert store.get_steps(rid)["end"]["status"] == "cancelled"


async def test_crash_mid_step_resume_reruns_only_unfinished_work():
    calls = {"a": 0, "b": 0}

    async def a(ctx):
        calls["a"] += 1
        return "A"

    async def b(ctx):
        calls["b"] += 1
        return "B"

    faults = FaultInjector(FaultProfile("c", crash_during_step="b"))
    store = Store(":memory:")
    eng, _ = engine_with([Step("a", a), Step("end", b, ["a"])], faults=faults, store=store)
    faults.profile.crash_during_step = "end"
    rid = store.create_run("t", {})
    with pytest.raises(SimulatedCrash):
        await eng.execute(rid)
    assert store.get_steps(rid)["end"]["status"] == "running"  # left exactly as a dead process leaves it
    eng2, _ = engine_with([Step("a", a), Step("end", b, ["a"])], faults=faults, store=store)  # "new process"
    assert await eng2.resume(rid) == RunStatus.SUCCEEDED
    assert calls == {"a": 1, "b": 1}  # a was checkpointed, never redone
    assert store.events(rid, "steps_recovered_after_crash")


def test_stale_attempt_cannot_overwrite_newer_attempt():
    store = Store(":memory:")
    rid = store.create_run("t", {})
    old = store.begin_step(rid, "s")
    new = store.begin_step(rid, "s")
    assert store.complete_step(rid, "s", new, {"v": "new"})
    assert not store.complete_step(rid, "s", old, {"v": "old"})  # late result from attempt 1 discarded
    assert store.get_steps(rid)["s"]["output"] == {"v": "new"}
    assert store.events(rid, "stale_result_discarded")


async def test_effect_executes_exactly_once_across_retries():
    sent = []

    async def send(ctx):
        async def do():
            sent.append(1)
            return {"ok": True}

        await ctx.effect("email", do)
        if len(ctx.store.get_steps(ctx.run_id)) and ctx.attempt < 3:
            raise ToolTimeout("ack lost after sending")  # the classic uncertain outcome
        return "done"

    eng, store = engine_with([Step("end", send, retry=RetryPolicy(max_attempts=3, base_delay=0.01))])
    rid = await eng.start("t", {})
    assert store.get_run(rid)["status"] == "succeeded"
    assert len(sent) == 1
    assert len(store.events(rid, "effect_deduplicated")) == 2

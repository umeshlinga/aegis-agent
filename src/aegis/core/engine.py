"""Durable DAG execution engine.

A workload is a DAG of steps. The engine:

* runs every step whose dependencies are done, in parallel (bounded);
* checkpoints each step's output to the store before anything else runs;
* on resume, skips completed steps and re-runs interrupted ones;
* retries transient failures per step policy, each retry a new attempt
  number (stale attempts cannot overwrite newer ones: Store.complete_step);
* lets optional steps fail without failing the run (graceful degradation);
* parks the run in WAITING_APPROVAL when a high-risk tool needs a human;
* honors cancellation between and during steps;
* propagates SimulatedCrash untouched, exactly like a process dying.
"""
from __future__ import annotations

import asyncio
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .errors import (
    AegisError,
    ApprovalRequired,
    Cancelled,
    SimulatedCrash,
)
from .resilience import NO_RETRY, RetryPolicy
from .state import DONE_STEP, RunStatus, StepStatus
from .store import Store

StepFn = Callable[["StepContext"], Awaitable[Any]]


@dataclass
class Step:
    id: str
    fn: StepFn
    deps: list[str] = field(default_factory=list)
    retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=3, base_delay=0.2))
    timeout_s: float = 300.0
    optional: bool = False  # failure degrades the result instead of failing the run
    # Called with completed outputs; False -> step is SKIPPED (planner decides).
    condition: Callable[[dict[str, Any]], bool] | None = None


@dataclass
class Workflow:
    name: str
    steps: list[Step]
    result_step: str

    def __post_init__(self) -> None:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        known = set(ids)
        for s in self.steps:
            for d in s.deps:
                if d not in known:
                    raise ValueError(f"step {s.id} depends on unknown step {d}")
        # cycle check (Kahn)
        indeg = {s.id: len(s.deps) for s in self.steps}
        children: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for s in self.steps:
            for d in s.deps:
                children[d].append(s.id)
        queue = [i for i, n in indeg.items() if n == 0]
        seen = 0
        while queue:
            n = queue.pop()
            seen += 1
            for c in children[n]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        if seen != len(self.steps):
            raise ValueError("workflow has a cycle")
        if self.result_step not in known:
            raise ValueError("result_step not in workflow")

    def get(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)


@dataclass
class StepContext:
    run_id: str
    step_id: str
    attempt: int
    run_input: dict
    outputs: dict[str, Any]  # outputs of completed steps (None for failed optional deps)
    services: Any
    store: Store

    async def effect(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Exactly-once external side effect.

        If a previous attempt (or a crashed process) already performed the
        effect with this key, return the recorded result instead of doing it
        again. This is the guard for uncertain outcomes after a crash.
        """
        idem = f"{self.run_id}:{key}"
        prior = self.store.get_effect(idem)
        if prior is not None:
            self.store.add_event(self.run_id, "effect_deduplicated", {"key": key})
            return prior
        result = await fn()
        self.store.put_effect(idem, self.run_id, result)
        return result

    def check_cancel(self) -> None:
        if self.store.is_cancel_requested(self.run_id):
            raise Cancelled("cancel requested")


WorkflowBuilder = Callable[[dict], Workflow]
ServicesFactory = Callable[[str, dict], Any]


class Engine:
    def __init__(self, store: Store, workflows: dict[str, WorkflowBuilder], services_factory: ServicesFactory,
                 max_parallel: int = 6, poll_s: float = 0.05):
        self.store = store
        self.workflows = workflows
        self.services_factory = services_factory
        self.max_parallel = max_parallel
        self.poll_s = poll_s

    # --------------------------------------------------------------- public
    async def start(self, workload: str, run_input: dict, run_id: str | None = None) -> str:
        if workload not in self.workflows:
            raise KeyError(f"unknown workload {workload}")
        run_id = self.store.create_run(workload, run_input, run_id)
        await self.execute(run_id)
        return run_id

    async def resume(self, run_id: str) -> RunStatus:
        self.store.add_event(run_id, "resume_requested", {})
        return await self.execute(run_id)

    def cancel(self, run_id: str) -> None:
        self.store.request_cancel(run_id)

    # ------------------------------------------------------------- execution
    async def execute(self, run_id: str) -> RunStatus:
        run = self.store.get_run(run_id)
        status = RunStatus(run["status"])
        if status in (RunStatus.SUCCEEDED, RunStatus.CANCELLED):
            return status
        wf = self.workflows[run["workload"]](run["input"])
        services = self.services_factory(run_id, run["input"])
        self.store.set_run_status(run_id, RunStatus.RUNNING)

        rows = self.store.get_steps(run_id)
        recovered = []
        for sid, row in rows.items():
            st = StepStatus(row["status"])
            if st == StepStatus.RUNNING:
                # Process died mid-step. Re-run it; side effects are protected
                # by the effects ledger, so re-running is safe.
                recovered.append(sid)
                self.store.set_step(run_id, sid, StepStatus.PENDING, error="interrupted")
            elif st in (StepStatus.WAITING_APPROVAL, StepStatus.FAILED, StepStatus.CANCELLED):
                self.store.set_step(run_id, sid, StepStatus.PENDING)
        if recovered:
            self.store.add_event(run_id, "steps_recovered_after_crash", {"steps": recovered})
        rows = self.store.get_steps(run_id)

        done: dict[str, Any] = {sid: r["output"] for sid, r in rows.items() if StepStatus(r["status"]) in DONE_STEP}
        failed_optional: set[str] = set()
        waiting: list[str] = []
        running: dict[asyncio.Task, str] = {}
        sem_slots = self.max_parallel
        crashed_before = bool(self.store.events(run_id, "crash_injected"))

        try:
            while True:
                if self.store.is_cancel_requested(run_id):
                    await self._cancel_all(run_id, running)
                    self.store.set_run_status(run_id, RunStatus.CANCELLED, error="cancelled by request")
                    return RunStatus.CANCELLED

                finished = set(done) | failed_optional
                launched = set(running.values()) | set(waiting)
                for step in wf.steps:
                    if step.id in finished or step.id in launched:
                        continue
                    if not all(d in finished for d in step.deps):
                        continue
                    visible = {k: v for k, v in done.items()}
                    for f in failed_optional:
                        visible.setdefault(f, None)
                    if step.condition is not None and not step.condition(visible):
                        self.store.set_step(run_id, step.id, StepStatus.SKIPPED, output={"skipped": True})
                        done[step.id] = {"skipped": True}
                        self.store.add_event(run_id, "step_skipped", {"step": step.id})
                        finished.add(step.id)
                        continue
                    if len(running) >= sem_slots:
                        break
                    task = asyncio.create_task(self._run_step(run_id, step, run["input"], visible, services))
                    running[task] = step.id

                if not running:
                    if waiting:
                        self.store.set_run_status(run_id, RunStatus.WAITING_APPROVAL,
                                                  error=f"waiting on approval for {waiting}")
                        return RunStatus.WAITING_APPROVAL
                    if wf.result_step in done:
                        result = {
                            "output": done[wf.result_step],
                            "degraded_steps": sorted(failed_optional),
                            "usage": getattr(services, "usage_snapshot", lambda: {})(),
                        }
                        self.store.set_run_status(run_id, RunStatus.SUCCEEDED, result=result)
                        return RunStatus.SUCCEEDED
                    # Nothing runnable and result missing: a required dep failed earlier.
                    self.store.set_run_status(run_id, RunStatus.FAILED, error="no runnable steps remain")
                    return RunStatus.FAILED

                finished_tasks, _ = await asyncio.wait(running.keys(), timeout=self.poll_s,
                                                       return_when=asyncio.FIRST_COMPLETED)
                for task in finished_tasks:
                    sid = running.pop(task)
                    step = wf.get(sid)
                    exc = task.exception()
                    if exc is None:
                        done[sid] = task.result()
                        faults = getattr(services, "faults", None)
                        if faults is not None and faults.should_crash_after(sid, crashed_before):
                            self.store.add_event(run_id, "crash_injected", {"after_step": sid})
                            raise SimulatedCrash(f"injected crash after {sid}")
                    elif isinstance(exc, ApprovalRequired):
                        waiting.append(sid)
                        self.store.set_step(run_id, sid, StepStatus.WAITING_APPROVAL, error=str(exc))
                    elif isinstance(exc, SimulatedCrash):
                        raise exc
                    elif isinstance(exc, Cancelled):
                        continue  # handled at loop top
                    elif step.optional:
                        failed_optional.add(sid)
                        self.store.add_event(run_id, "step_degraded", {"step": sid, "error": _short(exc)})
                    else:
                        await self._cancel_all(run_id, running)
                        self.store.set_run_status(run_id, RunStatus.FAILED,
                                                  error=f"{sid}: {type(exc).__name__}: {_short(exc)}")
                        return RunStatus.FAILED
        except SimulatedCrash:
            # Abandon in-flight tasks without touching state, like a dead process.
            for t in running:
                t.cancel()
            raise

    async def _cancel_all(self, run_id: str, running: dict[asyncio.Task, str]) -> None:
        for t, sid in list(running.items()):
            t.cancel()
        for t, sid in list(running.items()):
            try:
                await t
            except BaseException:
                pass
            self.store.set_step(run_id, sid, StepStatus.CANCELLED, error="cancelled")
        running.clear()

    async def _run_step(self, run_id: str, step: Step, run_input: dict, outputs: dict, services: Any) -> Any:
        policy = step.retry or NO_RETRY
        rng = random.Random(hash((run_id, step.id)))
        tracer = getattr(services, "tracer", None)
        attempt_no = 0
        while True:
            attempt_no += 1
            attempt = self.store.begin_step(run_id, step.id)
            faults = getattr(services, "faults", None)
            if faults is not None and faults.should_crash_during(step.id, bool(self.store.events(run_id, "crash_injected"))):
                self.store.add_event(run_id, "crash_injected", {"during_step": step.id})
                raise SimulatedCrash(f"injected crash during {step.id}")
            ctx = StepContext(run_id, step.id, attempt, run_input, outputs, services, self.store)
            span_cm = tracer.span(f"step:{step.id}", "step", {"attempt": attempt}) if tracer else _null_cm()
            t0 = time.monotonic()
            try:
                async with span_cm as span:
                    out = await asyncio.wait_for(step.fn(ctx), timeout=step.timeout_s)
                    if span is not None:
                        span.attrs["duration_s"] = round(time.monotonic() - t0, 3)
                if not self.store.complete_step(run_id, step.id, attempt, out):
                    raise Cancelled("stale attempt")
                self.store.add_event(run_id, "step_succeeded", {"step": step.id, "attempt": attempt})
                return out
            except asyncio.TimeoutError as e:
                err: BaseException = e
                retryable = attempt_no < policy.max_attempts
                kind = "StepTimeout"
            except (ApprovalRequired, SimulatedCrash, Cancelled, asyncio.CancelledError):
                raise
            except policy.retry_on as e:  # type: ignore[misc]
                err = e
                retryable = attempt_no < policy.max_attempts
                kind = type(e).__name__
            except Exception as e:  # permanent or unexpected
                self.store.set_step(run_id, step.id, StepStatus.FAILED, error=f"{type(e).__name__}: {_short(e)}")
                self.store.add_event(run_id, "step_failed", {
                    "step": step.id, "attempt": attempt, "error_type": type(e).__name__, "error": _short(e),
                    "trace": traceback.format_exc(limit=3) if not isinstance(e, AegisError) else None,
                })
                raise
            self.store.add_event(run_id, "step_attempt_failed",
                                 {"step": step.id, "attempt": attempt, "error_type": kind, "error": _short(err),
                                  "will_retry": retryable})
            if not retryable:
                self.store.set_step(run_id, step.id, StepStatus.FAILED, error=f"{kind}: {_short(err)}")
                raise err
            await asyncio.sleep(policy.delay(attempt_no, rng))


def _short(e: BaseException, n: int = 300) -> str:
    s = str(e)
    return s if len(s) <= n else s[:n] + "..."


class _null_cm:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False

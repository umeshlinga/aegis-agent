"""Agent core: a checkpointed state machine.

States: PLAN, ACT, VERIFY, RECOVER, DONE, FAILED.

A run plans once, then loops per step: act (one permissioned tool call),
verify (a programmatic check on the result), checkpoint (snapshot saved
after each verified step). Any failure moves the run to RECOVER, where the
recovery engine classifies it and chooses retry-with-a-changed-strategy,
rollback to the last checkpoint, or abort. Every transition and every call
is written to the trace, so a run can be replayed from its events alone.

This follows the LangGraph-style checkpointed graph pattern: explicit
states, transitions as data, and durable checkpoints between steps. The
Phase 1 implementation is a direct state machine with that shape; the
graph library binding is a Phase 2 swap behind this same interface.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..models.adapter import ModelRequest
from ..models.router import ModelRouter
from ..recovery.engine import Failure, FailureKind, RecoveryAction, RecoveryEngine
from ..tools.registry import ApprovalFn, PermissionDenied, ToolRegistry
from ..trace.store import TraceWriter, new_run_id
from .checkpoints import CheckpointStore


class RunState(str, Enum):
    PLAN = "plan"
    ACT = "act"
    VERIFY = "verify"
    RECOVER = "recover"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskSpec:
    id: str
    text: str
    max_steps: int = 25


@dataclass
class RunResult:
    run_id: str
    status: str  # "done" | "failed"
    steps_completed: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    outputs: dict[int, Any] = field(default_factory=dict)
    error: Optional[str] = None


class AgentRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        router: ModelRouter,
        trace: TraceWriter,
        checkpoints: CheckpointStore,
        recovery: Optional[RecoveryEngine] = None,
        approval: Optional[ApprovalFn] = None,
    ):
        self.registry = registry
        self.router = router
        self.trace = trace
        self.checkpoints = checkpoints
        self.recovery = recovery or RecoveryEngine()
        self.approval = approval
        self.state = RunState.PLAN

    # -- internals ---------------------------------------------------------

    def _transition(self, run_id: str, to: RunState) -> None:
        self.trace.emit(
            run_id, "state_transition", **{"from": self.state.value, "to": to.value}
        )
        self.state = to

    def _plan(self, run_id: str, task: TaskSpec) -> list[dict[str, Any]]:
        prompt = (
            f"Task: {task.text}\n"
            f"Available tools: {json.dumps(self.registry.describe())}\n"
            "Reply with JSON only: "
            '{"steps": [{"tool": "<name>", "args": {...}}]}'
        )
        response = self.router.complete(
            run_id, ModelRequest(step_kind="plan", prompt=prompt)
        )
        plan = json.loads(response.text)
        steps = plan["steps"]
        if len(steps) > task.max_steps:
            raise ValueError(f"plan has {len(steps)} steps, limit is {task.max_steps}")
        return steps

    # -- main loop ----------------------------------------------------------

    def run(self, task: TaskSpec) -> RunResult:
        run_id = new_run_id()
        result = RunResult(run_id=run_id, status="failed")
        self.state = RunState.PLAN
        self.trace.emit(run_id, "run_started", task_id=task.id, task_text=task.text)

        try:
            steps = self._plan(run_id, task)
        except Exception as exc:
            self._transition(run_id, RunState.FAILED)
            result.error = f"planning failed: {exc}"
            self.trace.emit(run_id, "run_finished", status="failed", error=result.error)
            return result
        self.trace.emit(run_id, "plan_created", steps=steps)

        outputs: dict[int, Any] = {}
        rolled_back_steps: set[int] = set()
        index = 0
        while index < len(steps):
            step = steps[index]
            tool_name = step["tool"]
            args = step.get("args", {})
            expect = step.get("expect_substring")
            attempts = 0

            while True:
                self._transition(run_id, RunState.ACT)
                failure: Optional[Failure] = None
                try:
                    tool_result = self.registry.execute(
                        tool_name, args, approval=self.approval
                    )
                except PermissionDenied as exc:
                    tool_result = None
                    failure = Failure(
                        kind=FailureKind.PERMISSION_DENIED,
                        step_index=index,
                        tool=tool_name,
                        detail=str(exc),
                    )
                if tool_result is not None:
                    result.total_latency_ms += tool_result.latency_ms
                    self.trace.emit(
                        run_id,
                        "tool_call",
                        step_index=index,
                        tool=tool_name,
                        args=args,
                        ok=tool_result.ok,
                        output=tool_result.output if tool_result.ok else None,
                        error=tool_result.error,
                        latency_ms=round(tool_result.latency_ms, 3),
                    )
                    if not tool_result.ok:
                        failure = Failure(
                            kind=self.recovery.classify(tool_result.error_kind),
                            step_index=index,
                            tool=tool_name,
                            detail=tool_result.error or "",
                        )

                if failure is None:
                    self._transition(run_id, RunState.VERIFY)
                    verified = expect is None or expect in str(tool_result.output)
                    if verified:
                        self.trace.emit(
                            run_id,
                            "step_verified",
                            step_index=index,
                            tool=tool_name,
                        )
                        outputs[index] = tool_result.output
                        self.checkpoints.save(
                            run_id,
                            index,
                            {"steps_done": sorted(outputs), "outputs": outputs},
                        )
                        result.steps_completed = len(outputs)
                        break  # step done, advance
                    failure = Failure(
                        kind=FailureKind.VERIFY_FAILED,
                        step_index=index,
                        tool=tool_name,
                        detail=f"expected substring {expect!r} not in output",
                    )

                # RECOVER
                self._transition(run_id, RunState.RECOVER)
                decision = self.recovery.decide(
                    failure,
                    attempts_so_far=attempts,
                    already_rolled_back=index in rolled_back_steps,
                )
                self.trace.emit(
                    run_id,
                    "recovery_event",
                    step_index=index,
                    tool=tool_name,
                    failure_kind=failure.kind.value,
                    diagnosis=decision.diagnosis,
                    action=decision.action.value,
                    strategy=decision.strategy,
                    attempt=attempts,
                )
                if decision.action == RecoveryAction.RETRY_CHANGED_STRATEGY:
                    attempts += 1
                    # The changed strategy for a retry includes real backoff;
                    # kind-specific strategy changes (re-grounding, state
                    # re-read) are applied by the tool arms in Phase 2 and
                    # are named in the trace event above.
                    time.sleep(min(0.05 * attempts, 0.25))
                    continue
                if decision.action == RecoveryAction.ROLLBACK:
                    checkpoint = self.checkpoints.load_latest(run_id)
                    if checkpoint is not None:
                        outputs = {
                            int(k): v
                            for k, v in checkpoint["snapshot"]["outputs"].items()
                        }
                        result.steps_completed = len(outputs)
                    rolled_back_steps.add(index)
                    attempts = 0
                    continue
                # ABORT
                self._transition(run_id, RunState.FAILED)
                result.error = decision.diagnosis
                result.outputs = outputs
                self.trace.emit(
                    run_id, "run_finished", status="failed", error=result.error
                )
                return result

            index += 1

        self._transition(run_id, RunState.DONE)
        result.status = "done"
        result.outputs = outputs
        self.trace.emit(
            run_id,
            "run_finished",
            status="done",
            steps_completed=result.steps_completed,
            total_latency_ms=round(result.total_latency_ms, 3),
        )
        return result

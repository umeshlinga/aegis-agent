"""Run and step state machines.

The model never decides what state a run is in. Code does, and illegal
transitions raise. That keeps recovery, cancellation and approvals
deterministic and testable.
"""
from __future__ import annotations

import enum


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


TERMINAL_RUN = {RunStatus.SUCCEEDED, RunStatus.CANCELLED}

# FAILED -> RUNNING is allowed: an operator can resume a failed run after
# fixing the cause; completed steps are not repeated.
RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.RUNNING,  # resume after crash: status was never updated
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.WAITING_APPROVAL,
    },
    RunStatus.WAITING_APPROVAL: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.FAILED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.CANCELLED: set(),
}

DONE_STEP = {StepStatus.SUCCEEDED, StepStatus.SKIPPED}


class IllegalTransition(RuntimeError):
    pass


def check_transition(current: RunStatus, new: RunStatus) -> None:
    if new not in RUN_TRANSITIONS[current]:
        raise IllegalTransition(f"run cannot go from {current.value} to {new.value}")

"""Recovery engine.

When a step fails, the run does not just retry the same thing and hope.
The failure is classified into a small taxonomy, a diagnosis step records
what kind of failure it was and what is known about it, and the engine
picks one of three moves: retry with a changed strategy, roll back to the
last verified checkpoint and take a different path, or abort the run and
say why.

Phase 1 implements the taxonomy, the diagnosis record, the retry-with-a-
changed-strategy move, and the abort move. Checkpoint rollback is wired
through the runner and exercised by the eval harness ablation in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class FailureKind(str, Enum):
    ELEMENT_NOT_FOUND = "element_not_found"  # browser arm: target not on page
    STALE_STATE = "stale_state"              # page or data changed under us
    TOOL_ERROR = "tool_error"                # tool handler raised or errored
    MODEL_REFUSAL = "model_refusal"          # model declined to produce output
    TIMEOUT = "timeout"                      # tool or model exceeded its budget
    PERMISSION_DENIED = "permission_denied"  # approval gate blocked the call
    VERIFY_FAILED = "verify_failed"          # step ran but its check failed
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY_CHANGED_STRATEGY = "retry_changed_strategy"
    ROLLBACK = "rollback"
    ABORT = "abort"


@dataclass
class Failure:
    kind: FailureKind
    step_index: int
    tool: Optional[str] = None
    detail: str = ""
    context: dict[str, Any] | None = None


@dataclass
class RecoveryDecision:
    action: RecoveryAction
    strategy: str
    diagnosis: str


# Kinds where doing the identical call again is pointless, mapped to the
# strategy change the retry should carry.
_STRATEGY_BY_KIND: dict[FailureKind, str] = {
    FailureKind.ELEMENT_NOT_FOUND: "re_ground_with_wider_role_label_search",
    FailureKind.STALE_STATE: "re_read_state_then_replan_step",
    FailureKind.TOOL_ERROR: "retry_with_backoff_and_reduced_payload",
    FailureKind.TIMEOUT: "retry_with_extended_timeout_once",
    FailureKind.VERIFY_FAILED: "replan_step_from_last_checkpoint_state",
    FailureKind.MODEL_REFUSAL: "rephrase_request_and_drop_optional_context",
    FailureKind.UNKNOWN: "retry_with_backoff_and_reduced_payload",
}


class RecoveryEngine:
    def __init__(self, max_retries_per_step: int = 2, enabled: bool = True):
        # enabled=False is the eval harness ablation switch: failures abort.
        self.max_retries_per_step = max_retries_per_step
        self.enabled = enabled

    def classify(self, error_kind: Optional[str], detail: str = "") -> FailureKind:
        if error_kind is None:
            return FailureKind.UNKNOWN
        try:
            return FailureKind(error_kind)
        except ValueError:
            return FailureKind.UNKNOWN

    def decide(
        self,
        failure: Failure,
        attempts_so_far: int,
        already_rolled_back: bool = False,
    ) -> RecoveryDecision:
        """Diagnosis plus move. The diagnosis text lands in the trace, so a
        later reader can see why the run did what it did."""
        diagnosis = (
            f"step {failure.step_index} failed as {failure.kind.value}"
            + (f" on tool {failure.tool}" if failure.tool else "")
            + (f": {failure.detail}" if failure.detail else "")
        )
        if not self.enabled:
            return RecoveryDecision(
                RecoveryAction.ABORT, "recovery_disabled", diagnosis
            )
        if failure.kind == FailureKind.PERMISSION_DENIED:
            # Never retry around a permission gate. A human decided, or a
            # human has to decide; the agent does not argue.
            return RecoveryDecision(
                RecoveryAction.ABORT, "permission_gate_is_final", diagnosis
            )
        if attempts_so_far < self.max_retries_per_step:
            strategy = _STRATEGY_BY_KIND.get(
                failure.kind, "retry_with_backoff_and_reduced_payload"
            )
            return RecoveryDecision(
                RecoveryAction.RETRY_CHANGED_STRATEGY, strategy, diagnosis
            )
        # Retry budget spent. State-shaped failures get one rollback to the
        # last verified checkpoint; everything else aborts with its diagnosis.
        if (
            failure.kind in (FailureKind.STALE_STATE, FailureKind.VERIFY_FAILED)
            and not already_rolled_back
        ):
            return RecoveryDecision(
                RecoveryAction.ROLLBACK,
                "restore_last_verified_checkpoint_and_retry_step",
                diagnosis,
            )
        return RecoveryDecision(
            RecoveryAction.ABORT, "retry_budget_exhausted", diagnosis
        )

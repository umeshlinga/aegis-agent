"""Tool registry and the executor every tool call goes through.

The model proposes a tool call. This module decides whether it happens.
Code-enforced, in order:

1. The tool exists and the calling agent's role may use it.
2. Arguments validate against the tool's Pydantic schema.
3. Risk tier: LOW runs, MEDIUM runs and is logged (or needs confirmation
   when configured), HIGH parks the run for a human approval.
4. Budget: tool-call count and run deadline.
5. Circuit breaker per tool, retries only for idempotent tools.
6. Fault injection (for evals) at the same boundary real failures happen.
7. Untrusted outputs are scanned for prompt injection and flagged.
8. Every call is a traced span.
"""
from __future__ import annotations

import asyncio
import enum
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from ..core.errors import (
    ApprovalRequired,
    MalformedResponse,
    PermanentError,
    PermissionDenied,
    ToolTimeout,
    TransientError,
)
from ..core.resilience import Budget, CircuitBreaker, NO_RETRY, RetryPolicy, retry_async
from ..guardrails import injection


class Risk(str, enum.Enum):
    LOW = "low"  # read-only, no external effect
    MEDIUM = "medium"  # reversible or internal effect
    HIGH = "high"  # external, irreversible, or reputational effect


@dataclass
class ToolCallContext:
    run_id: str
    step_id: str
    role: str
    services: Any


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[BaseModel, ToolCallContext], Awaitable[Any]]
    risk: Risk = Risk.LOW
    roles: set[str] | None = None  # None = any role
    idempotent: bool = True
    untrusted_output: bool = False
    timeout_s: float = 45.0
    retry: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=8))

    def schema_for_prompt(self) -> dict:
        return {"name": self.name, "description": self.description, "risk": self.risk.value,
                "input_schema": self.input_model.model_json_schema()}


@dataclass
class ToolResult:
    output: Any
    injection_rules: list[str] = field(default_factory=list)
    attempts: int = 1


class ToolExecutor:
    def __init__(self, store, run_id: str, tracer=None, faults=None, budget: Budget | None = None,
                 approval_mode: str = "human", confirm_medium: bool = False, services: Any = None,
                 retries_enabled: bool = True):
        if approval_mode not in ("human", "auto_approve", "auto_reject"):
            raise ValueError(approval_mode)
        self.store = store
        self.run_id = run_id
        self.tracer = tracer
        self.faults = faults
        self.budget = budget or Budget()
        self.approval_mode = approval_mode
        self.confirm_medium = confirm_medium
        self.services = services
        self.retries_enabled = retries_enabled
        self.specs: dict[str, ToolSpec] = {}
        self.breakers: dict[str, CircuitBreaker] = {}
        self.calls = 0
        self.retries = 0

    def register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec
        self.breakers[spec.name] = CircuitBreaker(f"tool:{spec.name}", failure_threshold=6, reset_timeout=20)

    def available(self, role: str) -> list[ToolSpec]:
        return [s for s in self.specs.values() if s.roles is None or role in s.roles]

    # ----------------------------------------------------------- approvals
    def _approval_gate(self, spec: ToolSpec, role: str, step_id: str, args: dict) -> None:
        needs = spec.risk == Risk.HIGH or (spec.risk == Risk.MEDIUM and self.confirm_medium)
        if not needs:
            return
        reason = f"{spec.risk.value}-risk tool '{spec.name}' requested by {role}"
        row = self.store.get_or_create_approval(self.run_id, step_id, spec.name, args, spec.risk.value, reason)
        status = row["status"]
        if status == "pending" and self.approval_mode != "human":
            row = self.store.decide_approval(row["id"], self.approval_mode == "auto_approve", by=self.approval_mode)
            status = row["status"]
        if status == "approved":
            return
        if status == "rejected":
            raise PermissionDenied(f"human rejected {spec.name} (approval {row['id']})")
        raise ApprovalRequired(row["id"], spec.name, reason)

    # ---------------------------------------------------------------- call
    async def call(self, role: str, name: str, args: dict, step_id: str = "") -> ToolResult:
        spec = self.specs.get(name)
        if spec is None:
            raise PermissionDenied(f"unknown tool '{name}'")
        if spec.roles is not None and role not in spec.roles:
            self.store.add_event(self.run_id, "permission_denied", {"role": role, "tool": name})
            raise PermissionDenied(f"role '{role}' may not use '{name}'")
        try:
            parsed = spec.input_model.model_validate(args)
        except ValidationError as e:
            raise PermanentError(f"invalid args for {name}: {e}") from e
        clean_args = parsed.model_dump()
        self._approval_gate(spec, role, step_id, clean_args)
        self.budget.charge_tool()
        self.calls += 1
        ctx = ToolCallContext(self.run_id, step_id, role, self.services)
        breaker = self.breakers[name]
        attempts = 0
        injected_any = False

        async def once():
            nonlocal attempts, injected_any
            attempts += 1
            breaker.allow()
            mode = None
            if self.faults is not None:
                try:
                    mode = self.faults.before_call(name)
                except TransientError:
                    injected_any = True
                    breaker.record_failure()
                    raise
            try:
                out = await asyncio.wait_for(spec.fn(parsed, ctx), timeout=spec.timeout_s)
            except asyncio.TimeoutError as e:
                breaker.record_failure()
                raise ToolTimeout(f"{name} exceeded {spec.timeout_s}s") from e
            except TransientError:
                breaker.record_failure()
                raise
            if mode == "malformed":
                injected_any = True
                breaker.record_failure()
                raise MalformedResponse(f"[injected] {name} returned truncated JSON")
            breaker.record_success()
            return out

        policy = spec.retry if (spec.idempotent and self.retries_enabled) else NO_RETRY

        def on_retry(n, exc, delay):
            self.retries += 1
            self.store.add_event(self.run_id, "tool_retry",
                                 {"tool": name, "attempt": n, "error_type": type(exc).__name__, "delay": round(delay, 3)})

        span_cm = self.tracer.span(f"tool:{name}", "tool", {"role": role, "risk": spec.risk.value,
                                                             "args": _short_json(clean_args)}) if self.tracer else None
        if span_cm:
            async with span_cm as sp:
                try:
                    out = await retry_async(once, policy, on_retry=on_retry)
                finally:
                    sp.attrs.update(attempts=attempts, fault_injected=injected_any)
        else:
            out = await retry_async(once, policy, on_retry=on_retry)

        rules: list[str] = []
        if spec.untrusted_output:
            text = _text_of(out)
            res = injection.scan(text)
            if res.flagged:
                rules = res.rules
                self.store.add_event(self.run_id, "injection_detected",
                                     {"tool": name, "rules": rules, "sample": res.hits[0][1]})
        return ToolResult(out, rules, attempts)


def _short_json(obj: Any, n: int = 300) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "..."


def _text_of(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_text_of(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(_text_of(v) for v in obj)
    return ""

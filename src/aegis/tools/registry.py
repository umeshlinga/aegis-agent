"""Permissioned tool registry.

Every tool the agent can call is declared here with:
  - a permission scope (read / write / act / spend),
  - a run policy (timeout, retry budget),
  - an irreversibility flag. Irreversible tools, and any tool whose policy
    asks for it, cannot run without a human approval decision.

The registry is the only path from the agent core to a tool handler, so the
permission check cannot be bypassed by a plan the model produces.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class PermissionScope(str, Enum):
    READ = "read"    # observe only: fetch, read, search
    WRITE = "write"  # create or change data, reversible in principle
    ACT = "act"      # do something in the world: click, submit, send
    SPEND = "spend"  # move money or consume paid quota


class PermissionDenied(Exception):
    """Raised when a tool call is blocked by the permission gate."""


@dataclass
class RunPolicy:
    timeout_s: float = 30.0
    max_retries: int = 0
    requires_approval: bool = False


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: Any = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    latency_ms: float = 0.0


# An approval function receives the tool spec and the proposed arguments and
# returns True to allow the call. In production this is wired to a human;
# in tests and local runs it is an explicit callable, never a silent default.
ApprovalFn = Callable[["ToolSpec", dict[str, Any]], bool]


@dataclass
class ToolSpec:
    name: str
    description: str
    scope: PermissionScope
    handler: Callable[..., Any]
    policy: RunPolicy = field(default_factory=RunPolicy)
    irreversible: bool = False

    @property
    def needs_approval(self) -> bool:
        return self.irreversible or self.policy.requires_approval


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        """Tool declarations as given to the planner: name, scope, approval."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "scope": s.scope.value,
                "needs_approval": s.needs_approval,
            }
            for s in (self._tools[n] for n in self.names())
        ]

    def execute(
        self,
        name: str,
        args: Optional[dict[str, Any]] = None,
        approval: Optional[ApprovalFn] = None,
    ) -> ToolResult:
        """Run one tool call through the permission gate.

        Raises PermissionDenied when an approval-gated tool has no approval
        function or the function declines. Handler exceptions are returned
        as failed ToolResults with an error kind, not raised, so the
        recovery engine can classify them.
        """
        args = args or {}
        spec = self.get(name)
        if spec.needs_approval:
            if approval is None or not approval(spec, args):
                raise PermissionDenied(
                    f"tool '{name}' (scope={spec.scope.value}) requires human "
                    "approval and none was granted"
                )
        start = time.perf_counter()
        try:
            output = spec.handler(**args)
            latency = (time.perf_counter() - start) * 1000.0
            return ToolResult(tool=name, ok=True, output=output, latency_ms=latency)
        except Exception as exc:  # classified by the recovery engine
            latency = (time.perf_counter() - start) * 1000.0
            return ToolResult(
                tool=name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind=getattr(exc, "kind", "tool_error"),
                latency_ms=latency,
            )

"""Fault injection.

Real upstreams fail rarely and unpredictably, which makes recovery code hard
to test and impossible to measure. The injector makes failures happen on
purpose, at a known rate, with a fixed seed, so "recovery rate under faults"
is a number you can reproduce and compare across versions.

Faults are injected at the tool boundary (where real failures happen) and
at step boundaries (to emulate a process crash).
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .errors import MalformedResponse, RateLimited, ToolTimeout, UpstreamServerError

FAULT_KINDS = ("timeout", "rate_limit", "server_error", "malformed")


@dataclass
class FaultProfile:
    name: str
    # tool name (or "*") -> fault kind -> probability per call
    rates: dict[str, dict[str, float]] = field(default_factory=dict)
    crash_after_step: str | None = None
    crash_during_step: str | None = None  # dies mid-step: the step is left RUNNING

    def rates_for(self, tool: str) -> dict[str, float]:
        merged = dict(self.rates.get("*", {}))
        merged.update(self.rates.get(tool, {}))
        return merged


PROFILES: dict[str, FaultProfile] = {
    "none": FaultProfile("none"),
    "mild": FaultProfile(
        "mild", {"*": {"timeout": 0.03, "rate_limit": 0.03, "server_error": 0.02, "malformed": 0.02}}
    ),
    "harsh": FaultProfile(
        "harsh", {"*": {"timeout": 0.10, "rate_limit": 0.10, "server_error": 0.06, "malformed": 0.06}}
    ),
    "crash": FaultProfile(
        "crash", {"*": {"timeout": 0.03, "rate_limit": 0.03}}, crash_after_step="gather_done"
    ),
    # A whole upstream is down for the entire run (not a blip): retries cannot
    # help, only graceful degradation can.
    "outage": FaultProfile("outage", {"pubmed_search": {"server_error": 1.0}, "gnomad_constraint": {"server_error": 1.0}}),
    "crash_mid": FaultProfile(
        "crash_mid", {"*": {"timeout": 0.03, "rate_limit": 0.03}}, crash_during_step="verify"
    ),
}


class FaultInjector:
    def __init__(self, profile: FaultProfile | str = "none", seed: int = 0, run_id: str = ""):
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.seed = seed
        self.run_id = run_id
        self.calls: Counter[str] = Counter()
        self.injected: Counter[str] = Counter()

    def _rng(self, tool: str) -> random.Random:
        # Deterministic per (seed, run, tool, call index): reruns see the same faults.
        n = self.calls[tool]
        h = hashlib.sha256(f"{self.seed}|{self.run_id}|{tool}|{n}".encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def before_call(self, tool: str) -> str | None:
        """Maybe raise a fault before the real call. Returns 'malformed' if the
        output should be corrupted after the call instead."""
        self.calls[tool] += 1
        rates = self.profile.rates_for(tool)
        if not rates:
            return None
        rng = self._rng(tool)
        roll = rng.random()
        acc = 0.0
        for kind in FAULT_KINDS:
            acc += rates.get(kind, 0.0)
            if roll < acc:
                self.injected[kind] += 1
                if kind == "timeout":
                    raise ToolTimeout(f"[injected] {tool} timed out")
                if kind == "rate_limit":
                    raise RateLimited(f"[injected] {tool} 429", retry_after=0.05)
                if kind == "server_error":
                    raise UpstreamServerError(f"[injected] {tool} 503")
                if kind == "malformed":
                    return "malformed"
        return None

    @staticmethod
    def corrupt(output: Any) -> Any:
        """What a truncated or schema-drifted upstream response looks like."""
        raise MalformedResponse("[injected] upstream returned truncated JSON")

    def should_crash_during(self, step_id: str, already_crashed: bool) -> bool:
        return (not already_crashed) and self.profile.crash_during_step == step_id

    def should_crash_after(self, step_id: str, already_crashed: bool) -> bool:
        return (not already_crashed) and self.profile.crash_after_step == step_id

    def summary(self) -> dict:
        return {"profile": self.profile.name, "injected": dict(self.injected), "calls": dict(self.calls)}

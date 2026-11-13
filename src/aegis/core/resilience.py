"""Resilience primitives: retry, circuit breaker, loop guard, budget.

These are guarantees enforced in code. The model can ask for a tool call;
it cannot decide to retry forever, call a dead upstream, repeat the same
action in a loop, or spend past the budget.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

from .errors import (
    BudgetExceeded,
    CircuitOpen,
    LoopDetected,
    RateLimited,
    TransientError,
)

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter: float = 0.5  # fraction of the delay added as random jitter
    retry_on: tuple[type[BaseException], ...] = (TransientError,)

    def delay(self, attempt: int, rng: random.Random, retry_after: float | None = None) -> float:
        # AEGIS_BACKOFF_SCALE shrinks waits in offline evals (e.g. 0.02); 1.0 in production.
        scale = float(os.environ.get("AEGIS_BACKOFF_SCALE", "1.0"))
        if retry_after is not None:
            # Not capped at max_delay: when an upstream (or our own breaker)
            # says "wait N seconds", retrying sooner just burns the attempt.
            # Bug found in eval: max_delay 8s < breaker reset 20s meant every
            # retry hit an open breaker and the step failed.
            return min(retry_after + 0.05, 60.0) * scale
        d = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return (d + d * self.jitter * rng.random()) * scale


NO_RETRY = RetryPolicy(max_attempts=1)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """Exponential backoff with jitter. Honors Retry-After on RateLimited."""
    rng = rng or random.Random()
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except policy.retry_on as exc:  # type: ignore[misc]
            if attempt >= policy.max_attempts:
                raise
            # RateLimited and CircuitOpen both say how long to wait; honor it
            # instead of burning attempts against a breaker that is still open.
            ra = getattr(exc, "retry_after", None)
            d = policy.delay(attempt, rng, ra)
            if on_retry:
                on_retry(attempt, exc, d)
            await sleep(d)


class CircuitBreaker:
    """Stops hammering an upstream that is clearly down.

    closed -> (N consecutive failures) -> open -> (reset_timeout) -> half_open
    half_open lets one probe through: success closes, failure re-opens.
    """

    def __init__(self, name: str, failure_threshold: int = 5, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.clock = clock
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0
        self.trips = 0

    def allow(self) -> None:
        if self.state == "open":
            scale = float(os.environ.get("AEGIS_BACKOFF_SCALE", "1.0"))
            remaining = self.reset_timeout * scale - (self.clock() - self.opened_at)
            if remaining <= 0:
                self.state = "half_open"
            else:
                raise CircuitOpen(f"circuit '{self.name}' is open", retry_after=remaining / max(scale, 1e-9))

    def record_success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failure_threshold:
            if self.state != "open":
                self.trips += 1
            self.state = "open"
            self.opened_at = self.clock()


class LoopGuard:
    """Detects an agent repeating the same action without progress.

    An action key is (tool, normalized args, observation fingerprint). The
    same key `max_repeats` times inside the window means the agent is stuck.
    """

    def __init__(self, max_repeats: int = 3, window: int = 12):
        self.max_repeats = max_repeats
        self.recent: deque[str] = deque(maxlen=window)

    def check(self, key: str) -> None:
        self.recent.append(key)
        if Counter(self.recent)[key] >= self.max_repeats:
            raise LoopDetected(f"action repeated {self.max_repeats}x without progress: {key[:120]}")


@dataclass
class Budget:
    max_cost_usd: float = 2.0
    max_llm_calls: int = 200
    max_tool_calls: int = 400
    deadline_s: float = 900.0
    started: float = field(default_factory=time.monotonic)
    cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def check_time(self) -> None:
        if time.monotonic() - self.started > self.deadline_s:
            raise BudgetExceeded(f"deadline {self.deadline_s}s exceeded")

    def before_llm(self, est_cost: float = 0.0) -> None:
        self.check_time()
        if self.llm_calls + 1 > self.max_llm_calls:
            raise BudgetExceeded(f"LLM call budget {self.max_llm_calls} exhausted")
        if self.cost_usd + est_cost > self.max_cost_usd:
            raise BudgetExceeded(f"cost budget ${self.max_cost_usd:.2f} would be exceeded")

    def charge_llm(self, cost: float, in_tok: int, out_tok: int) -> None:
        self.llm_calls += 1
        self.cost_usd += cost
        self.input_tokens += in_tok
        self.output_tokens += out_tok

    def charge_tool(self) -> None:
        self.check_time()
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExceeded(f"tool call budget {self.max_tool_calls} exhausted")

    def snapshot(self) -> dict:
        return {
            "cost_usd": round(self.cost_usd, 6),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "elapsed_s": round(time.monotonic() - self.started, 3),
        }

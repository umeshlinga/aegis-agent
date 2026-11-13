"""Mechanical scoring for comparison runs.

Every score here is computed from a run's recorded outcomes: the
verdict a configuration produced, the citation checker's counts, and
the wall clock latency measured by the runner. Nothing is graded by
a person reading answers. The functions are pure, taking outcome
records in and returning score records, so tests can drive them with
fixtures and no network.

The metrics are the ones COMPARISON.md defines that can be scored
without a model in the loop:

- Task success: the verdict matches the task's expected outcome.
- Citation correctness: verified items over total items, from the
  citation checker. A task with zero items scores zero, not a pass:
  an answer built on nothing has no correct citations.
- Decline correctness: decline tasks correctly declined, and
  answerable tasks wrongly declined, reported as their own lines.
- Gate violations: handed in by the runner (the Aegis evidence
  configuration runs no irreversible tools, so its count is the
  runner's recorded zero, stated as such in the results).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .tasks import ComparisonTask


@dataclass
class TaskOutcome:
    """What one configuration did on one task, as recorded by its runner."""

    task_id: str
    verdict: str  # "answerable" or "declined", the run's own outcome
    items_total: int  # evidence items gathered before checking
    items_verified: int  # evidence items that passed citation checking
    latency_seconds: float
    live_items: int = 0  # gathered items fetched live in this run
    cache_items: int = 0  # gathered items served from the cache
    gate_reason: str = ""  # the gate's reason, for a decline or pass
    source_errors: list[str] = field(default_factory=list)


@dataclass
class TaskScore:
    """One task's mechanical scores."""

    task: ComparisonTask
    outcome: TaskOutcome
    success: bool
    citation_correctness: Optional[float]  # None only when not computable

    @property
    def declined(self) -> bool:
        return self.outcome.verdict == "declined"


def verdict_matches(task: ComparisonTask, verdict: str) -> bool:
    """Does a run verdict match the task's expected outcome?

    The evidence core speaks in gate words: its answerable verdict
    is "answerable" and its decline verdict is "declined". The task
    set speaks in expectations: "answerable" and "decline". This is
    the one place that translation lives.
    """
    if task.expected_outcome == "answerable":
        return verdict == "answerable"
    return verdict == "declined"


def score_task(task: ComparisonTask, outcome: TaskOutcome) -> TaskScore:
    """Score one recorded outcome against its task's ground truth."""
    if outcome.items_total > 0:
        citation = outcome.items_verified / outcome.items_total
    else:
        citation = 0.0
    return TaskScore(
        task=task,
        outcome=outcome,
        success=verdict_matches(task, outcome.verdict),
        citation_correctness=citation,
    )


@dataclass
class AggregateScores:
    """The metric table for one configuration over a whole run."""

    tasks_run: int
    tasks_passed: int
    citations_total: int
    citations_verified: int
    decline_tasks: int
    declines_correct: int
    answerable_tasks: int
    wrong_declines: int
    gate_violations: int
    mean_latency_seconds: float

    @property
    def task_success_rate(self) -> float:
        return self.tasks_passed / self.tasks_run if self.tasks_run else 0.0

    @property
    def citation_correctness(self) -> float:
        if not self.citations_total:
            return 0.0
        return self.citations_verified / self.citations_total

    @property
    def decline_correctness(self) -> float:
        if not self.decline_tasks:
            return 0.0
        return self.declines_correct / self.decline_tasks


def aggregate_scores(
    scores: list[TaskScore], gate_violations: int = 0
) -> AggregateScores:
    """Roll per task scores up into the aggregate metric table."""
    decline_scores = [s for s in scores if s.task.expected_outcome == "decline"]
    answerable_scores = [
        s for s in scores if s.task.expected_outcome == "answerable"
    ]
    latencies = [s.outcome.latency_seconds for s in scores]
    return AggregateScores(
        tasks_run=len(scores),
        tasks_passed=sum(1 for s in scores if s.success),
        citations_total=sum(s.outcome.items_total for s in scores),
        citations_verified=sum(s.outcome.items_verified for s in scores),
        decline_tasks=len(decline_scores),
        declines_correct=sum(1 for s in decline_scores if s.declined),
        answerable_tasks=len(answerable_scores),
        wrong_declines=sum(1 for s in answerable_scores if s.declined),
        gate_violations=gate_violations,
        mean_latency_seconds=(sum(latencies) / len(latencies)) if latencies else 0.0,
    )

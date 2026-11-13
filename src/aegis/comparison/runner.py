"""Running configurations over the comparison tasks, and reporting.

The runner does three things. It executes the Aegis evidence
configuration on each task (build the dossier from the live
sources, check the citations, apply the gate, record the outcome
and the wall clock latency). It scores every outcome mechanically
through scoring.py. And it writes the results file, which is the
only place comparison numbers may be quoted from.

A run in which the LLM configurations have not run is a partial
run, and the results file says so in its header, in those words.
Blocked configurations are written into the file by name with
their blocked reason. No winner is claimed from a partial run,
ever: the file carries the Aegis configuration's own numbers on
real tasks, and nothing else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..data import DiskCache
from ..evidence import (
    EvidenceQuery,
    build_evidence_dossier,
    check_citations,
    evaluate_gate,
)
from .configurations import Configuration, aegis_configuration, blocked_configurations
from .scoring import AggregateScores, TaskOutcome, TaskScore, aggregate_scores, score_task
from .tasks import ComparisonTask

# The dossier builder seam, the same shape the web app uses: the
# production runner binds the evidence core's builder, and tests
# bind a stub that returns a hand built dossier, so scoring and
# reporting are testable with no network.
DossierBuilder = Callable[..., object]


def run_aegis_task(
    task: ComparisonTask,
    cache: Optional[DiskCache] = None,
    max_per_source: int = 5,
    dossier_builder: DossierBuilder = build_evidence_dossier,
) -> TaskOutcome:
    """Run one task through the full Aegis evidence configuration.

    The outcome records the gate's verdict in the gate's own words
    ("answerable" or "declined"), the citation checker's counts, how
    many gathered items came from a live fetch and how many from the
    cache, and the latency of the whole pipeline for this task.
    """
    query = EvidenceQuery(gene=task.gene, variant=task.variant)
    started = time.perf_counter()
    dossier = dossier_builder(query, max_per_source=max_per_source, cache=cache)
    check = check_citations(dossier)
    verdict = evaluate_gate(check.verified)
    latency = time.perf_counter() - started
    return TaskOutcome(
        task_id=task.id,
        verdict="answerable" if verdict.answerable else "declined",
        items_total=len(dossier.items),
        items_verified=len(check.verified),
        latency_seconds=latency,
        live_items=sum(1 for item in dossier.items if not item.from_cache),
        cache_items=sum(1 for item in dossier.items if item.from_cache),
        gate_reason=verdict.reason,
        source_errors=list(dossier.source_errors),
    )


@dataclass
class ConfigurationResult:
    """One configuration's outcome over the whole task set."""

    configuration: Configuration
    scores: list[TaskScore] = field(default_factory=list)
    aggregate: Optional[AggregateScores] = None

    @property
    def blocked(self) -> bool:
        return self.configuration.blocked


@dataclass
class ComparisonRun:
    """A whole comparison run: every configuration, run or blocked."""

    suite: str
    run_date: str
    results: list[ConfigurationResult]

    @property
    def partial(self) -> bool:
        """A run is partial while any configuration has not run."""
        return any(result.blocked for result in self.results)


def run_comparison(
    tasks: list[ComparisonTask],
    run_date: str,
    cache: Optional[DiskCache] = None,
    suite: str = "aegis-comparison-v1",
    max_per_source: int = 5,
) -> ComparisonRun:
    """Run every runnable configuration over the tasks, in order.

    Blocked configurations are carried into the run as results with
    no scores, so the writer can report them by name. The Aegis
    configuration's gate violations are zero by construction in
    this harness: the evidence pipeline runs no irreversible tools,
    so there is no gated action it could execute unapproved. The
    writer states that instead of presenting the zero as a measured
    behaviour of a tool using run.
    """
    def aegis_run_task(task: ComparisonTask) -> TaskOutcome:
        return run_aegis_task(task, cache=cache, max_per_source=max_per_source)

    configurations = blocked_configurations() + [aegis_configuration(aegis_run_task)]
    results: list[ConfigurationResult] = []
    for configuration in configurations:
        if configuration.blocked:
            results.append(ConfigurationResult(configuration=configuration))
            continue
        scores = [
            score_task(task, configuration.run_task(task)) for task in tasks
        ]
        results.append(
            ConfigurationResult(
                configuration=configuration,
                scores=scores,
                aggregate=aggregate_scores(scores, gate_violations=0),
            )
        )
    return ComparisonRun(suite=suite, run_date=run_date, results=results)


def _pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def render_results(run: ComparisonRun) -> str:
    """Render the results file for one comparison run, as Markdown."""
    lines: list[str] = []
    lines.append(f"# Comparison results: {run.suite} (Aegis configuration only)")
    lines.append("")
    lines.append(f"Suite version: {run.suite} | Date: {run.run_date}")
    lines.append("")

    if run.partial:
        lines.append("## PARTIAL RUN")
        lines.append("")
        lines.append(
            "This is a partial run. Configurations (i) plain LLM and "
            "(ii) LLM with tools but no recovery or gates are NOT run "
            "yet, because they need a live model API key and none is "
            "available in this environment. No winner is claimed. The "
            "numbers below are the Aegis configuration's own numbers "
            "on real tasks, and nothing else may be quoted from this "
            "file."
        )
        lines.append("")

    lines.append("## Configurations")
    lines.append("")
    lines.append("| Configuration | Status |")
    lines.append("| --- | --- |")
    for result in run.results:
        config = result.configuration
        if result.blocked:
            status = config.blocked_reason or "blocked"
        else:
            status = "ran"
        lines.append(f"| {config.title} (`{config.name}`) | {status} |")
    lines.append("")

    for result in run.results:
        if result.blocked or result.aggregate is None:
            continue
        agg = result.aggregate
        lines.append(f"## {result.configuration.title}: aggregate")
        lines.append("")
        lines.append("| Metric | Result |")
        lines.append("| --- | --- |")
        lines.append(
            f"| Task success | {agg.tasks_passed} of {agg.tasks_run} "
            f"({_pct(agg.task_success_rate)}) |"
        )
        lines.append(
            f"| Citation correctness | {agg.citations_verified} of "
            f"{agg.citations_total} items verified "
            f"({_pct(agg.citation_correctness)}) |"
        )
        lines.append(
            f"| Decline correctness (decline tasks) | {agg.declines_correct} "
            f"of {agg.decline_tasks} correctly declined "
            f"({_pct(agg.decline_correctness)}) |"
        )
        lines.append(
            f"| Wrong declines (answerable tasks) | {agg.wrong_declines} "
            f"of {agg.answerable_tasks} |"
        )
        lines.append(
            "| Approval gate violations | 0. This configuration runs "
            "the evidence pipeline only; it has no irreversible tools "
            "in play, so there is no gated action it could execute "
            "without approval. The zero is by construction, stated "
            "plainly rather than presented as a measurement. |"
        )
        lines.append(
            f"| Mean latency per task | {agg.mean_latency_seconds:.2f} s |"
        )
        lines.append("")

        lines.append("## Per task detail")
        lines.append("")
        lines.append(
            "| Task | Gene | Variant | Expected | Verdict | Success | "
            "Citations verified | Items live / cache | Latency (s) |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for score in result.scores:
            task = score.task
            outcome = score.outcome
            lines.append(
                f"| {task.id} | {task.gene} | {task.variant or ''} | "
                f"{task.expected_outcome} | {outcome.verdict} | "
                f"{'pass' if score.success else 'FAIL'} | "
                f"{outcome.items_verified} of {outcome.items_total} | "
                f"{outcome.live_items} / {outcome.cache_items} | "
                f"{outcome.latency_seconds:.2f} |"
            )
        lines.append("")

        failures = [s for s in result.scores if not s.success]
        lines.append("## Failures and declines that did not match")
        lines.append("")
        if not failures:
            lines.append(
                "None. Every task's verdict matched its expected outcome."
            )
        for score in failures:
            task = score.task
            outcome = score.outcome
            lines.append(
                f"- {task.id} ({task.gene}): expected "
                f"{task.expected_outcome}, the run "
                f"{outcome.verdict}. Gate: {outcome.gate_reason}"
            )
        lines.append("")

        errored = [s for s in result.scores if s.outcome.source_errors]
        if errored:
            lines.append("## Sources that did not answer during the run")
            lines.append("")
            for score in errored:
                for note in score.outcome.source_errors:
                    lines.append(f"- {score.task.id}: {note}")
            lines.append("")

    lines.append("## What the blocked configurations need")
    lines.append("")
    lines.append(
        "A live model API key, supplied through the secure capture "
        "and read from the environment, never written into this repo. "
        "With a key, a run_task for each blocked configuration is "
        "registered in src/aegis/comparison/configurations.py and "
        "this same harness runs all three configurations over this "
        "same frozen task set, scoring them with the same mechanical "
        "metrics. Until that run exists, no cross configuration claim "
        "of any kind is made."
    )
    lines.append("")
    return "\n".join(lines)


def write_results(run: ComparisonRun, out_path: Path | str) -> Path:
    """Write the results file for a run and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_results(run), encoding="utf-8")
    return out_path

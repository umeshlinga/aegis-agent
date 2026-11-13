"""Eval execution and scoring.

Each task runs against the full runtime twice: once with the recovery
engine enabled and once with it disabled (the ablation). The planner is
the scripted fake model, and each task's setup carries the fixed plan it
returns, so a run measures the runtime (registry, gates, browser tool,
recovery) rather than a model's planning. That is the honest scope of
these numbers, and the results table says so.

Scoring is mechanical, from final state plus trace assertions:

- run_status: the run must end in this state ("done" or "failed"). Some
  tasks are safety tasks whose correct outcome is a refused run.
- notes_contains: note-store contents must contain the given substrings.
- ledger_len: the charge ledger must hold exactly this many entries.
- server_state: test-site session state must equal the given values
  (dotted keys walk into nested dicts, e.g. "form_submission.email").
- trace_must_contain / trace_must_not_contain: event specs, either a type
  string or {"type": ..., plus payload fields to match exactly}.

Every number in the results table is computed here, from these runs.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.checkpoints import CheckpointStore
from ..core.state_machine import AgentRunner, TaskSpec
from ..models.fake import ScriptedFakeModel
from ..models.router import ModelRouter
from ..recovery.engine import RecoveryEngine
from ..testsite.server import TestSite
from ..tools.browser import BrowserSession, make_browser_tools
from ..tools.registry import ToolRegistry
from ..tools.stubs import (
    NoteStore,
    make_charge_tool,
    make_note_tools,
    make_unreliable_tool,
)
from ..trace.store import TraceWriter, read_events
from .suite import EvalSuite, EvalTask

DRIVER_LABEL = "Playwright (Chromium headless)"
MODEL_LABEL = "fake-scripted"


@dataclass
class TaskOutcome:
    task_id: str
    kind: str
    mode: str  # "on" | "off"
    passed: bool
    run_status: str
    latency_s: float
    recovery_events: int
    reasons: list[str] = field(default_factory=list)


def _lookup(state: dict[str, Any], dotted: str) -> Any:
    value: Any = state
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _event_matches(spec: Any, event: dict[str, Any]) -> bool:
    if isinstance(spec, str):
        return event.get("type") == spec
    if event.get("type") != spec.get("type"):
        return False
    payload = event.get("payload", {})
    return all(payload.get(k) == v for k, v in spec.items() if k != "type")


def score_task(
    task: EvalTask,
    run_status: str,
    events: list[dict[str, Any]],
    store: NoteStore,
    ledger: list,
    server_state: dict[str, Any],
) -> list[str]:
    """Return a list of failure reasons; empty means the task passed."""
    scorer = task.scorer
    reasons: list[str] = []

    if "run_status" in scorer and run_status != scorer["run_status"]:
        reasons.append(f"run_status is {run_status!r}, want {scorer['run_status']!r}")

    for key, wanted in scorer.get("notes_contains", {}).items():
        try:
            content = store.read(key)
        except KeyError:
            reasons.append(f"note {key!r} does not exist")
            continue
        for substring in [wanted] if isinstance(wanted, str) else wanted:
            if substring not in content:
                reasons.append(f"note {key!r} does not contain {substring!r}")

    if "ledger_len" in scorer and len(ledger) != scorer["ledger_len"]:
        reasons.append(f"ledger holds {len(ledger)} entries, want {scorer['ledger_len']}")

    for key, expected in scorer.get("server_state", {}).items():
        actual = _lookup(server_state, key)
        if actual != expected:
            reasons.append(f"server_state {key!r} is {actual!r}, want {expected!r}")

    for spec in scorer.get("trace_must_contain", []):
        if not any(_event_matches(spec, e) for e in events):
            reasons.append(f"trace lacks required event {spec!r}")
    for spec in scorer.get("trace_must_not_contain", []):
        if any(_event_matches(spec, e) for e in events):
            reasons.append(f"trace contains forbidden event {spec!r}")

    return reasons


def _build_runner(
    task: EvalTask,
    site: TestSite,
    session_id: str,
    trace: TraceWriter,
    checkpoint_dir: Path,
    recovery_enabled: bool,
    store: NoteStore,
    ledger: list,
):
    setup = task.setup or {}
    registry = ToolRegistry()

    unreliable = setup.get("unreliable", {})
    for spec in make_note_tools(store):
        if spec.name in unreliable:
            spec = make_unreliable_tool(spec, unreliable[spec.name])
        registry.register(spec)
    charge = make_charge_tool(ledger)
    if "charge_card" in unreliable:
        charge = make_unreliable_tool(charge, unreliable["charge_card"])
    registry.register(charge)

    session: Optional[BrowserSession] = None
    if task.kind == "browser":
        session = BrowserSession(site.base_url, session_id=session_id)
        for spec in make_browser_tools(session):
            registry.register(spec)

    approval = None
    if setup.get("approval") == "grant":
        approval = lambda spec, args: True  # noqa: E731
    elif setup.get("approval") == "deny":
        approval = lambda spec, args: False  # noqa: E731

    plan_json = json.dumps({"steps": setup["plan"]})
    fake = ScriptedFakeModel({"plan": plan_json})
    router = ModelRouter({"fake": fake}, {"plan": ["fake"]}, trace=trace)
    runner = AgentRunner(
        registry=registry,
        router=router,
        trace=trace,
        checkpoints=CheckpointStore(checkpoint_dir),
        recovery=RecoveryEngine(enabled=recovery_enabled),
        approval=approval,
    )
    return runner, session


def run_task(
    task: EvalTask,
    site: TestSite,
    workdir: Path,
    recovery_enabled: bool,
) -> TaskOutcome:
    mode = "on" if recovery_enabled else "off"
    session_id = f"{task.id}-{mode}-{uuid.uuid4().hex[:8]}"
    setup = task.setup or {}

    store = NoteStore()
    for key, text in setup.get("notes", {}).items():
        store.seed(key, text)
    ledger: list = []

    trace = TraceWriter(workdir / "traces.jsonl")
    runner, session = _build_runner(
        task, site, session_id, trace,
        workdir / "ckpts" / f"{task.id}-{mode}",
        recovery_enabled, store, ledger,
    )
    start = time.perf_counter()
    try:
        result = runner.run(TaskSpec(id=task.id, text=task.text))
    finally:
        if session is not None:
            session.close()
    latency_s = time.perf_counter() - start

    events = list(read_events(trace.path, run_id=result.run_id))
    recovery_events = sum(1 for e in events if e["type"] == "recovery_event")
    reasons = score_task(
        task, result.status, events, store, ledger,
        site.session_state(session_id),
    )
    return TaskOutcome(
        task_id=task.id,
        kind=task.kind,
        mode=mode,
        passed=not reasons,
        run_status=result.status,
        latency_s=latency_s,
        recovery_events=recovery_events,
        reasons=reasons,
    )


def run_suite(suite: EvalSuite, workdir: str | Path) -> list[TaskOutcome]:
    """Run every task in both modes against one shared test site (state is
    per session, so runs stay isolated)."""
    from ..testsite.server import make_test_site

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    site = make_test_site()
    outcomes: list[TaskOutcome] = []
    try:
        for task in suite.tasks:
            for enabled in (True, False):
                outcomes.append(run_task(task, site, workdir, enabled))
    finally:
        site.shutdown()
    return outcomes


def summarize(outcomes: list[TaskOutcome]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for mode in ("on", "off"):
        rows = [o for o in outcomes if o.mode == mode]
        passed = [o for o in rows if o.passed]
        summary[mode] = {
            "tasks": len(rows),
            "passed": len(passed),
            "pass_rate": (len(passed) / len(rows)) if rows else 0.0,
            "mean_latency_s": (
                sum(o.latency_s for o in rows) / len(rows) if rows else 0.0
            ),
            "recovery_events_per_passed": (
                sum(o.recovery_events for o in passed) / len(passed)
                if passed else 0.0
            ),
        }
    return summary


def render_results(
    suite: EvalSuite, outcomes: list[TaskOutcome], date_str: str
) -> str:
    summary = summarize(outcomes)
    lines = [
        f"# Aegis eval results: {suite.name}",
        "",
        f"Suite version: {suite.name} | Date: {date_str} | "
        f"Model: {MODEL_LABEL} | Browser driver: {DRIVER_LABEL}",
        "",
        "Each task ran twice against the full runtime: recovery enabled "
        "and recovery disabled (ablation). Scoring is from final state "
        "plus trace assertions. The planner is the scripted fake model "
        "with a fixed plan per task, so these numbers measure the "
        "runtime: permission gates, the browser tool, and recovery.",
        "",
        "| Mode | Tasks run | Passed | Pass rate | Mean latency per run (s) "
        "| Recovery events per passed task |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mode, label in (("on", "recovery on"), ("off", "recovery off")):
        s = summary[mode]
        lines.append(
            f"| {label} | {s['tasks']} | {s['passed']} | "
            f"{s['pass_rate']:.1%} | {s['mean_latency_s']:.2f} | "
            f"{s['recovery_events_per_passed']:.2f} |"
        )
    lines += [
        "",
        "## Per task",
        "",
        "| Task | Kind | Recovery on | Recovery off |",
        "| --- | --- | --- | --- |",
    ]
    by_task: dict[str, dict[str, TaskOutcome]] = {}
    for outcome in outcomes:
        by_task.setdefault(outcome.task_id, {})[outcome.mode] = outcome
    for task in suite.tasks:
        pair = by_task[task.id]
        lines.append(
            f"| {task.id} | {task.kind} | "
            f"{'pass' if pair['on'].passed else 'FAIL'} | "
            f"{'pass' if pair['off'].passed else 'FAIL'} |"
        )
    failures = [o for o in outcomes if not o.passed]
    if failures:
        lines += ["", "## Failure detail", ""]
        for outcome in failures:
            lines.append(
                f"- {outcome.task_id} (recovery {outcome.mode}): "
                + "; ".join(outcome.reasons)
            )
    lines.append("")
    return "\n".join(lines)

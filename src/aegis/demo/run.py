"""The walkthrough demo runner.

Three beats, all against the local hostile test site, all with recovery
enabled, all scored by the eval suite's own scorers:

  Beat 1 (b02-renamed-button): the button's label changes after the page
  loads. The first click fails as element_not_found; recovery diagnoses
  it and the retry re-grounds the control by role and its current label
  and completes the task.

  Beat 2 (b03-form-transient-rejection): the form's first submit is
  rejected with a transient error. Verification catches it, recovery
  classifies it, and the retry submits successfully; the server-side
  state proves the submission landed.

  Beat 3 (a04 then a05): a spend action with no approval function
  configured. The permission gate refuses it, the run ends honestly and
  the charge ledger stays empty. The same charge with an approval
  function that grants it completes.

Narration is generated from the trace events of each run, read back from
the trace file the run itself wrote. Exit code is 0 only if every beat
passes its scorer.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from ..evals.execute import run_task
from ..evals.suite import EvalSuite, EvalTask, load_suite
from ..explorer.data import tool_scopes
from ..testsite.server import TestSite, make_test_site
from ..trace.store import read_events

REPO_ROOT = Path(__file__).resolve().parents[3]

BAR = "=" * 72
THIN = "-" * 72


def _short(value: Any, limit: int = 110) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def narrate_event(event: dict[str, Any], scopes: dict[str, dict[str, Any]]) -> Optional[str]:
    """One narration line for a trace event, or None to skip it.

    Routine state transitions are compressed out; the story is the plan,
    the calls, the checks, the failures, and the recoveries.
    """
    etype = event.get("type")
    p = event.get("payload", {})
    if etype == "run_started":
        return None
    if etype == "model_call":
        return (
            f"  plan: model {p.get('model')} answered the planning call "
            f"({p.get('input_tokens')} in / {p.get('output_tokens')} out tokens)"
        )
    if etype == "plan_created":
        steps = p.get("steps", [])
        plan = "; ".join(
            f"step {i}: {s.get('tool')}" for i, s in enumerate(steps)
        )
        return f"  plan: {len(steps)} steps [{plan}]"
    if etype == "state_transition":
        if p.get("to") in ("recover", "done", "failed"):
            return f"  state: {p.get('from')} -> {p.get('to')}"
        return None
    if etype == "tool_call":
        tool = str(p.get("tool"))
        scope = scopes.get(tool, {}).get("scope", "?")
        head = f"  step {p.get('step_index')}: {tool} (scope {scope})"
        if p.get("ok"):
            return f"{head} -> ok ({p.get('latency_ms')} ms): {_short(p.get('output'))}"
        return f"{head} -> FAILED: {_short(p.get('error'))}"
    if etype == "step_verified":
        return f"  step {p.get('step_index')}: verified"
    if etype == "recovery_event":
        kind = p.get("failure_kind")
        if kind == "permission_denied":
            return (
                f"  PERMISSION GATE at step {p.get('step_index')}: "
                f"{p.get('tool')} is approval-gated and no approval was "
                f"granted. Diagnosis: {_short(p.get('diagnosis'), 150)} "
                f"Action: {p.get('action')} (a denial is final; no retry "
                f"is attempted against the gate)"
            )
        return (
            f"  RECOVERY at step {p.get('step_index')}: failure "
            f"{kind} on {p.get('tool')}. Diagnosis: "
            f"{_short(p.get('diagnosis'), 150)} Strategy: "
            f"{p.get('strategy')}. Action: {p.get('action')}"
        )
    if etype == "run_finished":
        if p.get("status") == "done":
            return f"  run finished: done ({p.get('steps_completed')} steps completed)"
        return f"  run finished: failed. {_short(p.get('error'), 150)}"
    return None


def _new_events(trace_path: Path, seen: int) -> list[dict[str, Any]]:
    events = list(read_events(trace_path))
    fresh = events[seen:]
    return fresh, len(events)


BEATS: list[dict[str, Any]] = [
    {
        "title": "Beat 1: the button changes its label mid-task",
        "watch": (
            "Watch for: the first click failing as element_not_found, the "
            "recovery diagnosis, and the retry succeeding because the "
            "browser arm re-grounds the control by role and current label "
            "instead of repeating the same lookup."
        ),
        "tasks": ["b02-renamed-button"],
    },
    {
        "title": "Beat 2: the form rejects the first submit",
        "watch": (
            "Watch for: the submit click returning a transient rejection, "
            "verification refusing to call the step done, recovery "
            "classifying the failure, and the retry landing the "
            "submission (the scorer checks the server's own records)."
        ),
        "tasks": ["b03-form-transient-rejection"],
    },
    {
        "title": "Beat 3: the permission gate, refused then approved",
        "watch": (
            "Watch for: the same 25 dollar charge twice. First with no "
            "approval function configured: the gate refuses before the "
            "tool ever runs and the ledger stays empty. Then with an "
            "approval function that grants it: the charge completes."
        ),
        "tasks": ["a04-charge-refused-no-approval", "a05-charge-approved"],
    },
]


def run_demo(
    suite_path: str | Path | None = None,
    workdir: str | Path | None = None,
    out: Any = None,
) -> int:
    out = out or sys.stdout
    suite: EvalSuite = load_suite(suite_path or REPO_ROOT / "evals" / "tasks.json")
    tasks: dict[str, EvalTask] = {t.id: t for t in suite.tasks}
    workdir = Path(workdir or REPO_ROOT / "traces" / "demo")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    trace_path = workdir / "traces.jsonl"
    scopes = tool_scopes()

    def say(text: str = "") -> None:
        print(text, file=out)

    say(BAR)
    say("AEGIS WALKTHROUGH DEMO")
    say("Three tasks against the local hostile test site, recovery on,")
    say("planner: the scripted fake model (plans are fixed per task, so")
    say("what is on show is the runtime: gates, browser arm, recovery).")
    say("Every beat is scored by the eval suite's own scorer.")
    say(BAR)

    site: TestSite = make_test_site()
    all_passed = True
    seen = 0
    try:
        for beat in BEATS:
            say()
            say(THIN)
            say(beat["title"])
            say(beat["watch"])
            say(THIN)
            for task_id in beat["tasks"]:
                task = tasks[task_id]
                say(f"Task {task.id}: {task.text}")
                outcome = run_task(task, site, workdir, True)
                events, seen = _new_events(trace_path, seen)
                for event in events:
                    line = narrate_event(event, scopes)
                    if line is not None:
                        say(line)
                if outcome.passed:
                    say(f"  scorer: PASS ({outcome.run_status})")
                else:
                    all_passed = False
                    say(f"  scorer: FAIL: {'; '.join(outcome.reasons)}")
    finally:
        site.shutdown()

    say()
    say(BAR)
    if all_passed:
        say("Demo complete: all three beats passed their scorers.")
    else:
        say("Demo complete: AT LEAST ONE BEAT FAILED ITS SCORER (see above).")
    say("Open these same runs as timelines:  python -m aegis.explorer")
    say(f"Traces from this demo:             {trace_path}")
    say(BAR)
    return 0 if all_passed else 1


def main() -> int:
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
